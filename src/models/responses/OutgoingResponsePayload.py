from pydantic import BaseModel

from src.enums.actions import DataStage, RecommendedAction

class ActionNote(BaseModel):
    action: RecommendedAction
    note: str  # what the UI shows with the action, e.g. the service desk request text


class RecordSearch(BaseModel):
    """What record_is_missing searched for, kept on the chat so later stages repeat the same search."""
    search_guide_code: str
    parameters: dict


class MCPToolDiagnosisResponse(BaseModel):
    message: str
    recommended_actions: list[ActionNote]  # in priority order
    jira_ticket_title: str
    document: str | None = None  # record searches only: the kind of record searched for, e.g. "Client"
    records: list[dict[str, dict]] = list()  # each row keyed by document, e.g. {"Repayment": {...}, "Client": {...}}
    stage: DataStage  = DataStage.app
    search: RecordSearch | None = None  # record searches only
    # record searches only: which search this answers (couchbase_office, couchbase_country, source_office,
    # source_country), or "done" once every stage came up empty
    search_stage: str | None = None


class OutgoingResponsePayload(BaseModel):
    result: list[MCPToolDiagnosisResponse] = list()
    chat_id: str


class EnableFeatureRequestResponse(BaseModel):
    ticket_key: str  # e.g. "SUP-12", what the officer quotes when following up
    url: str


