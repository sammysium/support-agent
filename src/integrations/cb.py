
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, QueryOptions
import couchbase.subdocument as SD
from couchbase.exceptions import DocumentNotFoundException
from datetime import timedelta
import threading

from src.utils.log import log_me
from src.utils.get_env import get_env_value


username = get_env_value("CB_USERNAME")
password = get_env_value("CB_PASSWORD")
reads_bucket_name = get_env_value("CB_READS_BUCKET_NAME")
cb_url = get_env_value("CB_URL")

QUERY_LIMIT = 20

# Sync Gateway's own indexes (it can't serve channels without them) and the ones our queries use
REQUIRED_INDEXES = [
    "sg_access_x1", "sg_allDocs_x1", "sg_channels_x1", "sg_roleAccess_x1", "sg_syncDocs_x1", "sg_tombstones_x1",
    "idx_type_officeId", "idx_type_clientId", "idx_type_loanProductId",
]


def sync_cas_to_int(imported: str) -> int:
    """Sync Gateway writes _sync.cas as a little-endian hex string ("0x...")."""
    return int.from_bytes(bytes.fromhex(imported.removeprefix("0x")), "little")


class CouchBaseDatabase:
    """
    Shared, reused connection — this is the Couchbase SDK's documented
    usage pattern: create one Cluster object and keep it for the app's
    whole lifetime, never reconnect per call. `CouchBaseDatabase()`
    everywhere else in the codebase now just returns this same instance
    after the first real connection, via __new__.

    Reconnecting on every single call (the old behavior) leaked a native
    connection per call, forever, and calling it concurrently across
    FastAPI's thread pool (every tool call runs in its own worker
    thread) crashed the process outright — a `libc++abi:
    bad_function_call` abort in the SDK's C++ core, not a catchable
    Python exception, so it killed the whole server with nothing to show
    for it in the logs.
    """

    _instance: "CouchBaseDatabase | None" = None
    _lock = threading.Lock()

    connected = False

    def __new__(cls):
        # Double-checked locking: the lock only needs to guard the one
        # moment the singleton doesn't exist yet — every call after that
        # just reads cls._instance, no locking needed.
        log_me("cb new called")
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # __init__ runs unconditionally after __new__, even when __new__
        # returned the already-existing singleton — so the actual connect
        # step needs its own double-checked lock too, or two threads
        # racing in before the first connection exists could both pass
        # this check and both call Cluster.connect() concurrently.
        log_me(f"connected {self.connected}")
        if self.connected:
            return

        with self._lock:
            if self.connected:
                return

            auth = PasswordAuthenticator(
                username,
                password,
            )
            log_me(f"connecting to couchbase as {username}")

            self.cluster = Cluster.connect(cb_url, ClusterOptions(auth))

            self.cluster.wait_until_ready(timedelta(seconds=5))

            self.offline_reads_bucket = self.cluster.bucket(reads_bucket_name)

            self.reads_collection  =  self.offline_reads_bucket.default_collection()

            self.connected = True
            log_me(f"connected to couchbase as {username}")


    def get_document_with_sync(self, doc_id: str) -> tuple[dict, int, dict | None] | None:
        """(document, current CAS, Sync Gateway's _sync xattr or None if SG never imported it); None if no such doc.
        _sync holds the revision SG serves ("rev"), the doc's channels ("channels": {name: null while in it})
        and the CAS SG saw at import ("cas")."""
        try:
            document = self.reads_collection.get(doc_id)
        except DocumentNotFoundException:
            return None
        result = self.reads_collection.lookup_in(doc_id, [SD.get("_sync", xattr=True)])
        sync = result.content_as[dict](0) if result.exists(0) else None
        return document.content_as[dict], document.cas, sync

    def run_raw_query(self, query: str):
        """
        run the query as is. danger zone is the caller.
        """
        result = self.cluster.query(query)
        return list(result.rows())


    def query_reads_statement(self, statement: str, **named_parameters) -> list[dict]:
        """Run a full N1QL statement written by our own code, with "{bucket}" where the bucket goes.
        Every value must be a $named parameter; only identifiers we validated may be inlined."""
        if not self.connected:
            raise ValueError("Failed to connect to couchbase")
        result = self.cluster.query(
            statement.format(bucket=f"`{reads_bucket_name}`"), QueryOptions(named_parameters=named_parameters)
        )
        return list(result.rows())

    def missing_indexes(self) -> list[str]:
        """REQUIRED_INDEXES the reads bucket lacks or that aren't online (building, deferred...), e.g. "sg_channels_x1 (deferred)"."""
        rows = self.run_raw_query(
            f'SELECT name, state FROM system:indexes WHERE keyspace_id = "{reads_bucket_name}"')
        states = {row["name"]: row["state"] for row in rows}
        return [
            name if name not in states else f"{name} ({states[name]})"
            for name in REQUIRED_INDEXES if states.get(name) != "online"
        ]

    def office_documents(self, office_id: int) -> tuple[int, str | None]:
        """(how many documents Couchbase holds for the office, newest syncTs among them)."""
        rows = self.query_reads_statement(
            "SELECT COUNT(*) AS documents, MAX(d.syncTs) AS newest FROM {bucket} d WHERE d.type IS VALUED AND d.officeId = $officeId",
            officeId=office_id)
        return rows[0]["documents"], rows[0]["newest"]

