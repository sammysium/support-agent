from typing import Callable

from src.enums.actions import DOCUMENT_TYPES, SEARCH_GUIDES, DataStage
from src.models.IncomingRequestPayload import IncomingRequestPayload
from src.models.responses.OutgoingResponsePayload import MCPToolDiagnosisResponse, RecordSearch
from src.persistence.models import IssueStatus
from src.repos.IssueRepo import DatabaseRepo
from src.utils.record_search import SEARCH_STAGES, run_search_stage

SOURCE_NAMES = {DataStage.fineract: "Fineract", DataStage.odoo: "Odoo"}
SEARCHABLE_SOURCES = {DataStage.fineract}  # Odoo has no client yet

Emit = Callable[[str], None]


def stage_progress(stage: str, source: DataStage, request: IncomingRequestPayload) -> str:
    """What the officer sees while a stage runs."""
    return {
        "couchbase_office": "Checking your office",
        "couchbase_country": f"Checking other offices in {request.country}",
        "source_office": f"Checking {SOURCE_NAMES[source]} for your office",
        "source_country": f"Checking {SOURCE_NAMES[source]} for the whole of {request.country}",
    }[stage]


def save_search_stage(repo: DatabaseRepo, chat_id: str, stage: str, response: MCPToolDiagnosisResponse):
    """The search, the stage it reached and the records shown there, so the next step continues from it."""
    response.search_stage = stage
    repo.update_chat(chat_id, {"search": {
        **response.search.model_dump(mode="json"), "stage": stage, "records": response.records}})


def finish_search(repo: DatabaseRepo, chat_id: str, response: MCPToolDiagnosisResponse) -> MCPToolDiagnosisResponse:
    """Nothing left to search: the answer closes the chat."""
    response.search_stage = "done"
    repo.update_chat(chat_id, {"diagnosis": response.model_dump(mode="json")})
    repo.update_issue_status(chat_id, IssueStatus.completed)
    return response


async def walk_search(
    repo: DatabaseRepo, chat_id: str, search: RecordSearch, request: IncomingRequestPayload, position: int, emit: Emit,
) -> MCPToolDiagnosisResponse:
    """Run the stages from SEARCH_STAGES[position] on, telling the officer each one, and stop at the first that
    finds records: an empty stage has nothing to pick from, so the officer is never asked about it. Each stage is
    saved on the chat, so their "None of these" continues after it."""
    document = SEARCH_GUIDES[search.search_guide_code]
    source = DOCUMENT_TYPES[document]["SOURCE"]
    for stage in SEARCH_STAGES[position:]:
        if stage.startswith("source") and source not in SEARCHABLE_SOURCES:
            return finish_search(repo, chat_id, MCPToolDiagnosisResponse(
                message=f"We couldn't find this {document} on our servers, and searching {SOURCE_NAMES[source]} isn't available yet.",
                recommended_actions=[],
                jira_ticket_title="",
                document=document,
                stage=source))
        emit(stage_progress(stage, source, request))
        response = await run_search_stage(stage, search, request)
        # a stage that couldn't search at all (e.g. a repayment without the client) ends the walk with its reason
        if response.search is None:
            return finish_search(repo, chat_id, response)
        save_search_stage(repo, chat_id, stage, response)
        if response.records:
            return response
    return finish_search(repo, chat_id, MCPToolDiagnosisResponse(
        message=f"We couldn't find this {document} anywhere in {request.country}. Check the details and search again.",
        recommended_actions=[],
        jira_ticket_title="",
        document=document))
