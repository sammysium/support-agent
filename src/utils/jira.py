import httpx
import base64
from src.exceptions import JiraRequestException
from src.models.IncomingRequestPayload import RequestEnablingFeaturePayload
from src.models.responses.OutgoingResponsePayload import EnableFeatureRequestResponse
from src.utils.log import log_me
from src.utils.get_env import get_env_value


def generate_token() -> str:
    token = get_env_value("JIRA_TOKEN")
    email = get_env_value("JIRA_EMAIL")
    credentials = f"{email}:{token}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return encoded_credentials


def _text(text: str) -> list[dict]:
    return [{"type": "text", "text": text}]


def _adf_doc(message: str, sections: dict[str, str | None]) -> dict:
    """Jira's document format: the message as a paragraph, then each section
    (heading -> JSON text) as a heading followed by a JSON code block.
    Jira rejects the whole document if a text node is null or empty, so a
    missing section is written as JSON `null`."""
    content = [{"type": "paragraph", "content": _text(message)}]
    for heading, body in sections.items():
        content.append({"type": "heading", "attrs": {"level": 3}, "content": _text(heading)})
        content.append({"type": "codeBlock", "attrs": {"language": "json"}, "content": _text(body or "null")})
    return {"type": "doc", "version": 1, "content": content}


async def _post(url: str, payload: dict, kind: str) -> httpx.Response:
    headers = {"Authorization": f"Basic {generate_token()}"}
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)

    if response.is_error:
        log_me(f"Jira {kind} creation failed ({response.status_code}): {response.text}")
    else:
        log_me(f"Jira {kind} created: {response.json().get('issueKey') or response.json().get('key')}")
    return response


async def create_new_ticket(title: str, jira_desc: str, sections: dict[str, str | None]):
    """create a jira bug. jira documentation is shites..."""
    content = {
        "fields": {
            "project": {"key": get_env_value("JIRA_PROJECT")},
            "summary": title,
            "description": _adf_doc(jira_desc, sections),
            "issuetype": {"name": "Bug"},
        }
    }
    await _post(get_env_value("JIRA_URL"), content, "ticket")


async def create_service_ticket(requested_service: RequestEnablingFeaturePayload) -> EnableFeatureRequestResponse:
    """Raise a Jira Service Management request, e.g. asking for a disabled feature to be turned on.
    Returns the ticket key (for the officer to quote when following up) and its portal link."""
    content = {
        "serviceDeskId": get_env_value("JIRA_SERVICE_DESK_ID"),
        "requestTypeId": get_env_value("JIRA_SERVICE_REQUEST_TYPE_ID"),
        "isAdfRequest": True,  # lets the description use code blocks, like create_new_ticket
        "requestFieldValues": {
            "summary": requested_service.requested_service[:255],  # Jira's summary limit
            "description": _adf_doc(
                requested_service.requested_service,
                {"Request": requested_service.model_dump_json(indent=2)},
            ),
        },
    }
    response = await _post(get_env_value("JIRA_SERVICE_DESK_URL"), content, "service request")
    if response.is_error:
        raise JiraRequestException(f"{response.status_code} {response.text}")

    created = response.json()
    return EnableFeatureRequestResponse(ticket_key=created["issueKey"], url=created["_links"]["web"])
