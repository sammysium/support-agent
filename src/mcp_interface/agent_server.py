from mcp.server.mcpserver import MCPServer

from src.enums.actions import DOCUMENT_TYPES, SEARCH_GUIDES, RecommendedAction
from src.utils.record_search import search_couchbase
from src.utils.sync_diagnosis import diagnose_no_syncing
from src.utils.remote_config_utils import REMOTE_CONFIG_DIAGNOSIS, FeatureKey, feature_is_enabled
from src.models.IncomingRequestPayload import IncomingRequestPayload
from src.models.CountryConfig import CountryConfig
from src.models.responses.OutgoingResponsePayload import ActionNote, MCPToolDiagnosisResponse, RecordSearch


app = MCPServer("")

# the model only knows the codes and field names it is shown, so record_is_missing's description lists them
SEARCH_GUIDE_CODES = ", ".join(f'"{code}" ({document})' for code, document in SEARCH_GUIDES.items())
SEARCHABLE_FIELDS = "\n".join(
    f"        {document}: {', '.join(details['SEARCHABLE_FIELDS'])}" for document, details in DOCUMENT_TYPES.items())


@app.tool()
async def feature_is_disabled(
    request: IncomingRequestPayload,
    server_config: CountryConfig,
    disabled_feature_key: FeatureKey,
) -> list[MCPToolDiagnosisResponse]:
    """
    Diagnose why a field officer (FO) can't see a button or action in FieldSmart.

    Use this when the user reports a missing button, action or screen,
    e.g. "I can't find the add new client button". It is about UI visibility
    controlled by Firebase remote config (CountryConfig), not about data problems.

    disabled_feature_key: pick the feature from the allowed values. Most features
    are a plain name; a few live in a section and are written "section.feature".
    e.g. "I can't see the add new order button" -> "create_client_order"
         "I can't see the add new group button" -> "group_actions_configs.create_group"
         "I can't capture the client's signature at delivery" ->
             "input_distribution.signature_collection_enabled"
    Sections:
      * group_actions_configs: group management (create, rename, change leader,
        add/remove members).
      * input_distribution: Input Distribution (IDS), handing farm inputs over to
        clients at delivery. Not about creating or editing orders.
      * site_level_reports: site report cards.
      * edit_client_options: status combinations in which a client can be edited.
    """

    device_status = feature_is_enabled(request.country_config, disabled_feature_key)  # what's on the tablet
    remote_status = feature_is_enabled(server_config, disabled_feature_key)  # live Firebase remote config

    message, actions = REMOTE_CONFIG_DIAGNOSIS[(device_status, remote_status)]

    notes = {
        RecommendedAction.CREATE_SERVICE_DESK_TICKET: f"Enable {disabled_feature_key} feature for {request.country} in {request.user_env}",
        RecommendedAction.RESTART_DEVICE: "Restart your device so it picks up the latest settings.",
        RecommendedAction.ESCALALTE_TO_DEVELOPER: "This has been reported to the developers.",
    }
    return [MCPToolDiagnosisResponse(
        message=message,
        recommended_actions=[ActionNote(action=action, note=notes[action]) for action in actions],
        jira_ticket_title=f"{disabled_feature_key} disabled for {request.country} in {request.user_env}")]


@app.tool(description=f"""
    Investigate why the field officer can't see a specific record on their device,
    such as a client, group, repayment or order.

    search_guide_code: the kind of record that is missing, one of {SEARCH_GUIDE_CODES}.
        It is what the officer can't find, even when they describe it through another record
        (a repayment described by its client).
    parameters: what the officer told you about the record, as {{"Document.field": value}}.
        Use only these fields, exactly as written, prefixed with their document name:
{SEARCHABLE_FIELDS}
        Fields may come from more than one document. Take values only from the officer's
        message; never invent them.

    E.g. "I can't find my client named Amina" ->
             search_guide_code="find_client", parameters={{"Client.displayName": "Amina"}}
         "I can't find my client with account number 1042" ->
             search_guide_code="find_client", parameters={{"Client.accountNo": "1042"}}
         "I can't find my client Amina, account number 1042" ->
             search_guide_code="find_client", parameters={{"Client.displayName": "Amina", "Client.accountNo": "1042"}}
         "I can't find the group Tuinuane" ->
             search_guide_code="find_group", parameters={{"Group.name": "Tuinuane"}}
         "I can't find the loan of my client with account number 1042" ->
             search_guide_code="find_loan", parameters={{"Client.accountNo": "1042"}}
         "I can't find the order with order ref 8888" ->
             search_guide_code="find_order", parameters={{"ClientOrder.orderRef": "8888"}}
         "I can't find the order for my client with account number 1042" ->
             search_guide_code="find_order", parameters={{"Client.accountNo": "1042"}}
         "I can't find the order of my client with client id 899" ->
             search_guide_code="find_order", parameters={{"ClientOrder.clientId": 899}}
         "I can't find Peter's repayment of 500 KES" ->
             search_guide_code="find_repayment", parameters={{"Client.displayName": "Peter", "Repayment.amount": 500}}
         "I can't find the repayment with transaction code QWE12RT" ->
             search_guide_code="find_repayment", parameters={{"Repayment.mnoTransactionCode": "QWE12RT"}}

    Not for "nothing is syncing" or empty lists in general; use data_no_syncing for that.
    """)
def record_is_missing(
    request: IncomingRequestPayload,
    search_guide_code: str,
    parameters: dict
) -> list[MCPToolDiagnosisResponse]:

    return [search_couchbase(RecordSearch(search_guide_code=search_guide_code, parameters=parameters), [request.site_id])]

@app.tool()
async def data_no_syncing(
    request: IncomingRequestPayload,
) -> list[MCPToolDiagnosisResponse]:
    """
    Call this tool when syncing is not working overall. This is not specific record but generally and it can be clients, groups, repayments, offers, loan products. Anything really.

    E.g. "data is not syncing"; "empty clients list"
    """
    # reads only for now
    return [await diagnose_no_syncing(request)]


if __name__ == "__main__":
  app.run()