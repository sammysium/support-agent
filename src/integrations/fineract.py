"""HTTP client for Fineract, the source of truth for clients, groups, loans and repayments.
Endpoints follow the Fineract Postman collection; mock_apis serves the same API locally (no login there:
leave FINERACT_TOKEN_URL empty)."""
import asyncio
import time

import httpx

from src.exceptions import FineractRequestException
from src.utils.get_env import get_env_value


REQUEST_TIMEOUT_SECONDS = 15

# the API root, i.e. what the collection calls {{fineractBEUrlSSL}}/fineract-provider/api/v1
base_url = (get_env_value("FINERACT_BASE_URL") or "").rstrip("/")
tenant_id = get_env_value("FINERACT_TENANT_ID") or "default"
# Keycloak password grant
token_url = get_env_value("FINERACT_TOKEN_URL")
client_id = get_env_value("FINERACT_CLIENT_ID") or "fineract"
username = get_env_value("FINERACT_USERNAME")
password = get_env_value("FINERACT_PASSWORD")

_token: str | None = None
_token_expires_at = 0.0
_token_lock = asyncio.Lock()


async def _get_token(client: httpx.AsyncClient) -> str:
    """Keycloak access token, reused until shortly before it expires."""
    global _token, _token_expires_at
    async with _token_lock:
        if _token and time.monotonic() < _token_expires_at:
            return _token
        if not (username and password):
            raise FineractRequestException("FINERACT_USERNAME and FINERACT_PASSWORD must be set with FINERACT_TOKEN_URL")
        try:
            response = await client.post(token_url, data={
                "grant_type": "password", "client_id": client_id,
                "username": username, "password": password, "scope": "email",
            })
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise FineractRequestException(f"getting a token: {e}") from e
        body = response.json()
        _token = body["access_token"]
        _token_expires_at = time.monotonic() + body.get("expires_in", 300) - 30
        return _token


async def fineract_get(path: str, params: dict | None = None, none_if_not_found: bool = False):
    """GET {base_url}/{path}. None for a 404 when none_if_not_found (e.g. no client with that account number)."""
    if not base_url:
        raise FineractRequestException("FINERACT_BASE_URL must be set")
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        headers = {"Accept": "application/json", "Fineract-Platform-TenantId": tenant_id}
        if token_url:
            headers["Authorization"] = f"Bearer {await _get_token(client)}"
        try:
            response = await client.get(f"{base_url}/{path}", params=params or {}, headers=headers)
            if none_if_not_found and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise FineractRequestException(f"GET {path}: {e}") from e


def page_items(body) -> list[dict]:
    # paged lists come as {"totalFilteredRecords", "pageItems"}, unpaged ones as a plain list
    return body if isinstance(body, list) else body.get("pageItems", [])


async def get_country_id(office_id: int) -> int:
    office = await fineract_get(f"offices/{office_id}")
    if office.get("countryId") is None:
        raise FineractRequestException(f"office {office_id} has no countryId")
    return office["countryId"]


async def get_country_office_ids(office_id: int) -> list[int]:
    """Every office in the same country as office_id, office_id included."""
    offices = await fineract_get("offices", {"countryId": await get_country_id(office_id)})
    return [office["id"] for office in page_items(offices)]
