"""Couchbase Server's management REST API (port 8091): node and bucket health that the SDK doesn't report."""
import httpx

from src.exceptions import CouchbaseRequestException
from src.integrations.cb import password, reads_bucket_name, username
from src.utils.get_env import get_env_value


REQUEST_TIMEOUT_SECONDS = 10
FULL_PERCENT = 90  # RAM quota or disk this full stalls writes and Sync Gateway imports

management_url = (get_env_value("CB_MANAGEMENT_URL") or "").rstrip("/")


async def _get(path: str):
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, auth=(username, password)) as client:
        try:
            response = await client.get(f"{management_url}/{path}")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise CouchbaseRequestException(f"GET {path}: {e}") from e


async def cluster_problems() -> list[str]:
    """What's wrong with the cluster holding the reads bucket, in words for a ticket; empty if it's healthy."""
    pool = await _get("pools/default")
    bucket = await _get(f"pools/default/buckets/{reads_bucket_name}")
    problems = [
        f"Node {node['hostname']} is {node['status']} ({node['clusterMembership']})"
        for node in pool["nodes"] if node["status"] != "healthy" or node["clusterMembership"] != "active"
    ]
    if pool.get("rebalanceStatus") not in (None, "none"):
        problems.append(f"Rebalance is {pool['rebalanceStatus']}")
    # every replica needs its own node
    active_nodes = sum(node["clusterMembership"] == "active" for node in pool["nodes"])
    if active_nodes < bucket["replicaNumber"] + 1:
        problems.append(f"{active_nodes} active node(s) for {bucket['replicaNumber']} replica(s) of {reads_bucket_name}")
    ram_used = bucket["basicStats"]["quotaPercentUsed"]
    if ram_used >= FULL_PERCENT:
        problems.append(f"{reads_bucket_name} uses {ram_used:.0f}% of its RAM quota")
    hdd = pool["storageTotals"]["hdd"]
    disk_used = 100 * hdd["used"] / hdd["total"]
    if disk_used >= FULL_PERCENT:
        problems.append(f"Disk is {disk_used:.0f}% full")
    return problems
