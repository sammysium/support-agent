import asyncio
from urllib.parse import quote

from src.enums.actions import DOCUMENT_TYPES, JOINS, SEARCH_GUIDES, DataStage
from src.integrations.cb import QUERY_LIMIT, CouchBaseDatabase
from src.integrations.fineract import fineract_get, get_country_id, get_country_office_ids, page_items
from src.models.IncomingRequestPayload import IncomingRequestPayload
from src.models.responses.OutgoingResponsePayload import MCPToolDiagnosisResponse, RecordSearch
from src.utils.log import log_me


def _path(document: str, field: str) -> str:
    # backticked, since names like Group are N1QL reserved words
    return f"`{document}`.`{field}`"


def search_couchbase(search: RecordSearch, office_ids: list[int], where: str = "your office") -> MCPToolDiagnosisResponse:
    """Couchbase records in office_ids matching what the officer said. The first search uses only their own
    office; widening to the country passes every office in it."""
    primary = SEARCH_GUIDES.get(search.search_guide_code)
    if primary is None:
        log_me(f"no search guide for {search.search_guide_code}")
        return MCPToolDiagnosisResponse(
            message=f"Searching with {search.search_guide_code} isn't supported yet.",
            recommended_actions=[],
            jira_ticket_title="")

    # primary first; every other document the officer mentioned is joined to it
    documents = [primary]
    wheres = [f"{_path(primary, 'type')} = $type_{primary}", f"{_path(primary, 'officeId')} IN $officeIds"]
    named_parameters = {f"type_{primary}": DOCUMENT_TYPES[primary]["DOC_TYPE"], "officeIds": office_ids}
    conditions: dict[str, list[str]] = {}  # per document, combined below with AND or, for MATCH_ANY, OR

    for position, (key, value) in enumerate(search.parameters.items()):
        document, _, field = key.partition(".")
        details = DOCUMENT_TYPES.get(document)
        # field names come from the model and are inlined, so only known ones get through
        if details is None or field not in details["SEARCHABLE_FIELDS"]:
            log_me(f"skipping {key}: not a searchable field")
            continue
        if document != primary and (primary, document) not in JOINS:
            log_me(f"skipping {key}: no join from {primary} to {document}")
            continue
        if document not in documents:
            documents.append(document)
            named_parameters[f"type_{document}"] = details["DOC_TYPE"]

        if details["SEARCHABLE_FIELDS"][field]["match"] == "partial":
            condition = f"LOWER({_path(document, field)}) LIKE '%' || LOWER($p{position}) || '%'"
        else:
            # the model may send 1042 for "1042" (or the reverse); compare as strings
            condition = f"TOSTRING({_path(document, field)}) = TOSTRING($p{position})"
        conditions.setdefault(document, []).append(condition)
        named_parameters[f"p{position}"] = value

    # without a single usable parameter the query would list every record in the office
    if not conditions:
        log_me(f"search_couchbase: nothing searchable in {search.parameters}")
        return MCPToolDiagnosisResponse(
            message=f"Couldn't search for the {primary}: none of {list(search.parameters)} can be searched on.",
            recommended_actions=[],
            jira_ticket_title="",
            document=primary)

    rankings = []
    for document, document_conditions in conditions.items():
        if DOCUMENT_TYPES[document].get("MATCH_ANY"):
            wheres.append(f"({' OR '.join(document_conditions)})")
            # records matching more of what the officer said come first
            rankings.extend(f"(CASE WHEN {condition} THEN 1 ELSE 0 END)" for condition in document_conditions)
        else:
            wheres.extend(document_conditions)
    order_by = f" ORDER BY {' + '.join(rankings)} DESC" if len(rankings) > 1 else ""

    selects = []
    for document in documents:
        selects.append(f"META(`{document}`).id AS `{document}.id`")
        selects.extend(f"{_path(document, field)} AS `{document}.{field}`" for field in DOCUMENT_TYPES[document]["SELECTABLE_FIELDS"])

    joins = "".join(
        f" JOIN {{bucket}} AS `{document}` ON {JOINS[(primary, document)]} AND {_path(document, 'type')} = $type_{document}"
        for document in documents[1:])

    statement = (f"SELECT {', '.join(selects)} FROM {{bucket}} AS `{primary}`{joins}"
                 f" WHERE {' AND '.join(wheres)}{order_by} LIMIT {QUERY_LIMIT}")
    log_me(f"search_couchbase query {statement} {named_parameters}")

    results = CouchBaseDatabase().query_reads_statement(statement, **named_parameters)
    log_me(f"search_couchbase found {len(results)}: {results}")

    # {"Client.displayName": ...} -> {"Client": {"displayName": ...}}, so the UI reads record[document]
    records = []
    for row in results:
        record = {}
        for key, value in row.items():
            document, _, field = key.partition(".")
            record.setdefault(document, {})[field] = value
        records.append(record)

    message = (f"Found {len(records)} matching {primary} record(s) in {where}." if records
               else f"No matching {primary} record was found in {where}.")
    return MCPToolDiagnosisResponse(
        stage=DataStage.couchbase,
        message=message,
        recommended_actions=[],
        jira_ticket_title="",
        document=primary,
        records=records,
        search=search)


# the order the search widens in: record_is_missing runs the first, /record-search/next each one after.
# "source" is the system the record comes from (DOCUMENT_TYPES[...]["SOURCE"]), Fineract or Odoo
SEARCH_STAGES = ["couchbase_office", "couchbase_country", "source_office", "source_country"]


async def run_search_stage(stage: str, search: RecordSearch, request: IncomingRequestPayload) -> MCPToolDiagnosisResponse:
    if stage == "couchbase_office":
        return await asyncio.to_thread(search_couchbase, search, [request.site_id])
    if stage == "couchbase_country":
        office_ids = await get_country_office_ids(request.site_id)
        return await asyncio.to_thread(search_couchbase, search, office_ids, f"{request.country}")

    # the source stages: only Fineract has a client so far (record_flow stops before an Odoo stage)
    primary = SEARCH_GUIDES[search.search_guide_code]
    return await search_fineract(search, primary, request, whole_country=stage == "source_country")


def _date(value) -> str | None:
    # Fineract dates are [yyyy, m, d]
    return "-".join(str(part) for part in value) if isinstance(value, list) else value


def _status(item: dict) -> str | None:
    return (item.get("status") or {}).get("value")


def _doc_id(document: str, office_id, fineract_id) -> str:
    # the id Couchbase (and so the device) gives the same record: {type}_{officeId}_{fineract id}
    return f"{DOCUMENT_TYPES[document]['DOC_TYPE']}_{office_id}_{fineract_id}"


# Fineract item -> the record we show, with the field names our Couchbase records use
FINERACT_RECORDS = {
    "Client": lambda item: {
        "id": _doc_id("Client", item.get("officeId"), item["id"]), "clientId": item["id"],
        "displayName": item.get("displayName"), "accountNo": item.get("accountNo"), "status": _status(item),
        "officeId": item.get("officeId"), "officeName": item.get("officeName"),
    },
    "Group": lambda item: {
        "id": _doc_id("Group", item.get("officeId"), item["id"]), "groupId": item["id"],
        "name": item.get("name"), "accountNo": item.get("accountNo"), "status": _status(item),
        "officeId": item.get("officeId"), "officeName": item.get("officeName"),
    },
    "Loan": lambda item: {
        "id": _doc_id("Loan", item.get("clientOfficeId"), item["id"]), "loanId": item["id"],
        "accountNo": item.get("accountNo"), "clientName": item.get("clientName"), "clientAccountNo": item.get("clientAccountNo"),
        "loanProductName": item.get("loanProductName"), "status": _status(item), "principal": item.get("principal"),
        "totalOutstanding": (item.get("summary") or {}).get("totalOutstanding"), "officeId": item.get("clientOfficeId"),
    },
    "Repayment": lambda item: {
        "id": _doc_id("Repayment", item.get("officeId"), item["transactionId"]), "transactionId": item["transactionId"],
        "amount": item.get("amount"), "clientAccountNo": item.get("clientAccountNo"), "clientName": item.get("clientName"),
        "mnoTransactionCode": item.get("mnoTransactionCode"), "paymentDate": _date(item.get("paymentDate")),
        "officeId": item.get("officeId"), "officeName": item.get("officeName"),
    },
}


def _same_number(value, wanted) -> bool:
    # the model may send 500, "500" or 500.0
    try:
        return float(value) == float(wanted)
    except (TypeError, ValueError):
        return False


def _contains(value, text) -> bool:
    return str(text).lower() in str(value or "").lower()


async def _fineract_clients(parameters: dict, account_no, scope: dict, office_id: int, country_id: int) -> list[dict]:
    """Fineract clients matching the officer's client details: by account number (looked up country-wide, then
    kept to the office unless the whole country is searched) or by name. Account number matches first."""
    found = []
    if account_no:
        client = await fineract_get(f"clients/{country_id}/{quote(str(account_no).strip(), safe='')}",
                                    {"includeOnlyBasicData": "true"}, none_if_not_found=True)
        if client is not None and ("officeId" not in scope or client.get("officeId") == office_id):
            found.append(client)
    name = parameters.get("Client.displayName")
    if name:
        body = await fineract_get("clients", {**scope, "displayName": name, "limit": QUERY_LIMIT})
        # guard: not every Fineract version filters on displayName
        found += [item for item in page_items(body) if _contains(item.get("displayName"), name)]
    unique = {}
    for client in found:
        unique.setdefault(client["id"], client)
    return list(unique.values())[:QUERY_LIMIT]


async def search_fineract(search: RecordSearch, primary: str, request: IncomingRequestPayload, whole_country: bool) -> MCPToolDiagnosisResponse:
    """The record in Fineract, in the officer's office or, with whole_country, anywhere in their country."""
    parameters = search.parameters
    country_id = await get_country_id(request.site_id)
    scope = {"countryId": country_id} if whole_country else {"officeId": request.site_id}
    where = request.country if whole_country else "your office"

    def in_scope(office_id) -> bool:
        return whole_country or office_id == request.site_id

    items: list[dict] = []
    if primary == "Client":
        items = await _fineract_clients(parameters, parameters.get("Client.accountNo"), scope, request.site_id, country_id)

    elif primary == "Group":
        name = parameters.get("Group.name")
        if name:
            body = await fineract_get("groups", {**scope, "paged": "true", "name": name, "limit": QUERY_LIMIT})
            items = [item for item in page_items(body) if _contains(item.get("name"), name)]

    elif primary == "Loan":
        if parameters.get("Loan.accountNo"):
            body = await fineract_get("loans", {"accountNo": str(parameters["Loan.accountNo"]).strip(), "limit": QUERY_LIMIT})
            items += page_items(body)
        clients = await _fineract_clients(parameters, parameters.get("Client.accountNo"), scope, request.site_id, country_id)
        for client in clients:
            # client ids come from Fineract and are ints, so they are safe in sqlSearch
            body = await fineract_get("loans", {"sqlSearch": f"c.id={int(client['id'])}", "limit": QUERY_LIMIT})
            items += page_items(body)
        items = [item for item in items if in_scope(item.get("clientOfficeId"))]

    elif primary == "Repayment":
        # Fineract lists repayments by the client's account number, so the client comes first
        account_numbers = [parameters[key] for key in ("Repayment.clientAccountNo", "Client.accountNo") if parameters.get(key)]
        if not account_numbers and parameters.get("Client.displayName"):
            clients = await _fineract_clients(parameters, None, scope, request.site_id, country_id)
            account_numbers = [client["accountNo"] for client in clients if client.get("accountNo")]
        if not account_numbers:
            return MCPToolDiagnosisResponse(
                stage=DataStage.fineract,
                message="To search Fineract for a repayment we need the client's account number or name.",
                recommended_actions=[],
                jira_ticket_title="",
                document=primary)
        for account_number in dict.fromkeys(str(number).strip() for number in account_numbers):
            body = await fineract_get("clients/repayments", {**scope, "countryId": country_id, "accountNumber": account_number})
            items += page_items(body)
        amount = parameters.get("Repayment.amount")
        code = parameters.get("Repayment.mnoTransactionCode")
        items = [item for item in items
                 if (amount is None or _same_number(item.get("amount"), amount))
                 and (code is None or str(item.get("mnoTransactionCode") or "").lower() == str(code).lower())]

    records = [{primary: FINERACT_RECORDS[primary](item)} for item in items[:QUERY_LIMIT]]
    message = (f"Found {len(records)} matching {primary} record(s) in Fineract, in {where}." if records
               else f"No matching {primary} record was found in Fineract, in {where}.")
    return MCPToolDiagnosisResponse(
        stage=DataStage.fineract,
        message=message,
        recommended_actions=[],
        jira_ticket_title="",
        document=primary,
        records=records,
        search=search)
