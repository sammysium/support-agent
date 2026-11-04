from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import Session
from uuid import uuid4
from src.persistence.Database import create_db_and_tables, engine
from src.persistence.models import Issue, IssueStatus
from src.repos.IssueRepo import DatabaseRepo, DatabaseRepoDep
from src.enums.actions import SEARCH_GUIDES, RecommendedAction
from src.mcp_interface.agent_client import MCPClient
from src.exceptions import AppException, ChatNotFound, NoSearchInChatException, RecordNotOfferedException, RemoteConfigNotFoundException
from src.integrations.firebase import get_remote_country_config, get_remote_country_formula_config, get_remote_parameters
from src.models.AgentContext import AgentContext
from src.llm.OllamaLLM import OllamaLLM
from src.models.IncomingRequestPayload import ChatFeedback, IncomingRequestPayload, NoMatchingRecord, RecordPick, RequestEnablingFeaturePayload
from src.utils.record_diagnosis import diagnose_pick
from src.utils.record_flow import save_search_stage, walk_search
from src.utils.record_search import SEARCH_STAGES
from src.utils.sse import stream_work
from src.utils.jira import create_new_ticket, create_service_ticket

from src.models.responses.OutgoingResponsePayload import EnableFeatureRequestResponse, MCPToolDiagnosisResponse, OutgoingResponsePayload, RecordSearch

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    async with MCPClient() as mcp_client:
        app.state.mcp_client = mcp_client
        app.state.mcp_tools = await mcp_client.list_tools()
        yield

app = FastAPI(lifespan=lifespan)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

@app.get("/")
def main():
    return {"message": "Hello World for shizzy"}

TOOL_PROGRESS = {
    "record_is_missing": "Checking your office",
    "feature_is_disabled": "Checking your app settings",
    "data_no_syncing": "Checking the sync server",
}


@app.post("/start-session", status_code=201, response_class=StreamingResponse)
async def start_session(incoming_request: IncomingRequestPayload, bg: BackgroundTasks, repo: DatabaseRepoDep):
    """
    Diagnose the officer's report. Streams Server-Sent Events: `progress` ({"message"}) for each thing being
    checked, then one `result` (OutgoingResponsePayload) or `error` ({"detail"}).
    A missing record whose office search finds nothing keeps widening (other offices, then Fineract) until a
    stage has records to pick from or nothing is left.
    """
    context = build_agent_context(request=incoming_request)

    chat_id = str(uuid4())

    repo.save_chat(
        Issue(
            chat_id = chat_id,
            incoming = incoming_request.model_dump(mode="json")
        )
    )

    async def work(emit):
        emit("Understanding your issue")
        llm = OllamaLLM(
            context=context,
            on_tool_call=lambda name: emit(TOOL_PROGRESS.get(name, "Looking into it")),
        )
        mcp_response = await llm.process()
        responses = mcp_response.responses
        # its own session: the streamed work outlives the request's dependencies
        with Session(engine) as session:
            chat_repo = DatabaseRepo(session)
            for position, response in enumerate(responses):
                escalate(bg, response, {
                    "Config on device": incoming_request.model_dump_json(indent=2),
                    "Live remote config": context.server_config.model_dump_json(indent=2),
                    "Live formula": (
                        context.formula_config.model_dump_json(indent=2)
                        if context.formula_config is not None else None
                    ),
                })
                # the later search stages repeat this search, without the LLM
                if response.search is not None:
                    save_search_stage(chat_repo, chat_id, SEARCH_STAGES[0], response)
                    if not response.records:
                        responses[position] = await walk_search(chat_repo, chat_id, response.search, incoming_request, 1, emit)
        return OutgoingResponsePayload(result = responses, chat_id=chat_id)

    return stream_work(work, status_code=201)

@app.post("/chat-feedback", status_code=204)
async def chat_feedback(feedback: ChatFeedback, repo: DatabaseRepoDep):
    """
    The officer closes the chat, saying whether the answer helped. Not solved closes it too: "I still need help"
    starts a new report, so the unsolved one stays queryable on its own.
    """
    repo.update_chat(feedback.chat_id, {"feedback": {"solved": feedback.solved}})
    repo.update_issue_status(feedback.chat_id, IssueStatus.completed if feedback.solved else IssueStatus.unsolved)


@app.post("/enable-feature-service-request", status_code=201, response_model=EnableFeatureRequestResponse)
async def request_service(requested_service: RequestEnablingFeaturePayload, repo: DatabaseRepoDep ):
    repo.update_issue_status(
        chat_id= requested_service.chat_id,
        new_status= IssueStatus.completed
    )
    return await create_service_ticket(requested_service)


@app.post("/record-search/next", status_code=200, response_class=StreamingResponse)
async def next_search_stage(detail: NoMatchingRecord, repo: DatabaseRepoDep):
    """
    None of the records shown is the one the officer is looking for. Continue the chat's search after the stage
    that showed them (Couchbase office -> Couchbase country -> source office -> source country), skipping stages
    that find nothing. Streams like /start-session: `progress` events, then `result` or `error`. When no stage
    is left, the record doesn't exist (search_stage "done") and the chat is closed.
    """
    issue = repo.get_chat(detail.chat_id)
    if issue is None:
        raise ChatNotFound(chat_id=detail.chat_id)
    saved = (issue.updates or {}).get("search")
    if saved is None:
        raise NoSearchInChatException(chat_id=detail.chat_id)
    request = IncomingRequestPayload.model_validate(issue.incoming)
    search = RecordSearch(search_guide_code=saved["search_guide_code"], parameters=saved["parameters"])
    position = SEARCH_STAGES.index(saved["stage"]) + 1

    async def work(emit):
        with Session(engine) as session:
            response = await walk_search(DatabaseRepo(session), detail.chat_id, search, request, position, emit)
        return OutgoingResponsePayload(result=[response], chat_id=detail.chat_id)

    return stream_work(work)

@app.post("/record-search/select", status_code=200, response_model=OutgoingResponsePayload)
async def select_record(pick: RecordPick, bg: BackgroundTasks, repo: DatabaseRepoDep):
    """
    The officer picked one of the records we showed (from Couchbase or the source system): it is the one they
    can't see. The app has already looked it up on the device by its id; find where it stops on the way to the screen.
    """
    issue = repo.get_chat(pick.chat_id)
    if issue is None:
        raise ChatNotFound(chat_id=pick.chat_id)
    saved = (issue.updates or {}).get("search")
    if saved is None:
        raise NoSearchInChatException(chat_id=pick.chat_id)
    request = IncomingRequestPayload.model_validate(issue.incoming)

    document = SEARCH_GUIDES[saved["search_guide_code"]]
    record = next((row[document] for row in saved.get("records", []) if row.get(document, {}).get("id") == pick.doc_id), None)
    if record is None:
        raise RecordNotOfferedException(doc_id=pick.doc_id)

    response, evidence = diagnose_pick(request, saved["stage"], document, record, pick.device_doc)
    escalate(bg, response, {"Request": request.model_dump_json(indent=2), **evidence})

    repo.update_chat(pick.chat_id, {"pick": pick.model_dump(mode="json"), "diagnosis": response.model_dump(mode="json")})
    repo.update_issue_status(pick.chat_id, IssueStatus.completed)
    return OutgoingResponsePayload(result=[response], chat_id=pick.chat_id)


def escalate(bg: BackgroundTasks, response: MCPToolDiagnosisResponse, sections: dict[str, str | None]):
    """File a Jira bug in the background when the diagnosis escalates to the developers."""
    if any(note.action == RecommendedAction.ESCALALTE_TO_DEVELOPER for note in response.recommended_actions):
        bg.add_task(create_new_ticket, response.jira_ticket_title, response.message, sections)


def build_agent_context(request: IncomingRequestPayload) -> AgentContext:
    """What every tool call gets: the officer's request and the live configs. Fails fast (400) on an unknown
    country, before anything runs in the background."""
    remote_parameters = get_remote_parameters()
    server_remote_config = get_remote_country_config(country=request.country, user_env=request.user_env, parameters=remote_parameters)
    if server_remote_config is None:
        raise RemoteConfigNotFoundException()
    return AgentContext(
        request=request,
        server_config=server_remote_config,
        # only some countries have formulas: without them, calculation issues are diagnosed as "no formula"
        formula_config=get_remote_country_formula_config(request.country, parameters=remote_parameters),
        mcp_tools=app.state.mcp_tools,
        mcp_client=app.state.mcp_client,
    )

