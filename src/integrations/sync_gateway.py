"""HTTP client for Sync Gateway's admin API (port 4985), which serves the reads database to devices."""
import httpx

from src.exceptions import SyncGatewayRequestException
from src.utils.get_env import get_env_value


REQUEST_TIMEOUT_SECONDS = 10

admin_url = (get_env_value("SG_ADMIN_URL") or "").rstrip("/")
# what devices connect to (port 4984, usually behind a proxy or load balancer)
public_url = (get_env_value("SG_PUBLIC_URL") or "").rstrip("/")
reads_db = get_env_value("SG_READS_DB")
# admin API credentials; leave empty where the admin API has no auth
admin_username = get_env_value("SG_ADMIN_USERNAME")
admin_password = get_env_value("SG_ADMIN_PASSWORD")


async def sg_admin_get(path: str, params: dict | None = None, none_if_not_found: bool = False):
    """GET {admin_url}/{reads_db}/{path}. None for a 404 when none_if_not_found (e.g. no such user)."""
    auth = (admin_username, admin_password) if admin_username else None
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, auth=auth) as client:
        try:
            response = await client.get(f"{admin_url}/{reads_db}/{path}", params=params or {})
            if none_if_not_found and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise SyncGatewayRequestException(f"GET {path}: {e}") from e


async def public_url_answers() -> bool:
    """Whether the URL devices sync through answers at all; the admin API can be up while it isn't."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        try:
            return (await client.get(f"{public_url}/")).status_code == 200
        except httpx.HTTPError:
            return False


async def get_database_state() -> str | None:
    """The reads database's state ("Online" while it serves devices), or None if Sync Gateway doesn't answer.
    It goes "Offline" when Sync Gateway loses its Couchbase bucket."""
    try:
        return (await sg_admin_get("")).get("state")
    except SyncGatewayRequestException:
        return None


async def get_user(name: str) -> dict | None:
    """The Sync Gateway user (devices log in with basic auth as it): "disabled", "admin_channels", "all_channels"."""
    return await sg_admin_get(f"_user/{name}", none_if_not_found=True)


async def channel_has_documents(channel: str) -> bool:
    changes = await sg_admin_get("_changes", {"filter": "sync_gateway/bychannel", "channels": channel, "limit": 1})
    return bool(changes.get("results"))
