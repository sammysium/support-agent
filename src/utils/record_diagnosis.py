import json

from src.enums.actions import DataStage, RecommendedAction
from src.exceptions import DeviceDocumentMismatchException, DocumentNotFoundException
from src.integrations.cb import CouchBaseDatabase, sync_cas_to_int
from src.models.IncomingRequestPayload import DeviceDocument, IncomingRequestPayload
from src.models.responses.OutgoingResponsePayload import ActionNote, MCPToolDiagnosisResponse

NOTES = {
    RecommendedAction.RESTART_DEVICE: "Restart your device so it syncs again.",
    RecommendedAction.ESCALALTE_TO_DEVELOPER: "This has been reported to the developers.",
}


def office_channel(office_id: int) -> str:
    # Sync Gateway routes every record to its office's channel; a device syncs its own office's channel
    return f"FsOffice_{office_id}"


def diagnose_pick(
    request: IncomingRequestPayload, stage: str, document_name: str, record: dict, device_doc: DeviceDocument | None,
) -> tuple[MCPToolDiagnosisResponse, dict[str, str | None]]:
    """Where the record the officer picked stops on its way to their screen. record is the one we showed them,
    from Couchbase or the source system; its id is the Couchbase id either way. Returns the diagnosis and the
    evidence for a ticket."""
    doc_id = record["id"]
    if device_doc is not None and device_doc.id != doc_id:
        raise DeviceDocumentMismatchException(doc_id=doc_id, device_doc_id=device_doc.id)
    found = CouchBaseDatabase().get_document_with_sync(doc_id)
    if found is not None:
        return diagnose_couchbase_pick(request, doc_id, found, device_doc)
    if stage.startswith("couchbase"):
        raise DocumentNotFoundException(doc_id=doc_id)
    return diagnose_source_only(request, document_name, record, device_doc)


def diagnose_source_only(
    request: IncomingRequestPayload, document_name: str, record: dict, device_doc: DeviceDocument | None,
) -> tuple[MCPToolDiagnosisResponse, dict[str, str | None]]:
    """The source system (Fineract) has the record but Couchbase doesn't."""
    doc_id = record["id"]
    evidence = {
        "Fineract record": json.dumps(record, indent=2),
        "Device document": device_doc.model_dump_json(indent=2) if device_doc is not None else None,
    }
    if record.get("officeId") == request.site_id:
        return MCPToolDiagnosisResponse(
            stage=DataStage.fineract,
            message=f"This {document_name} is in Fineract, but it never reached our servers, so no device can get it.",
            recommended_actions=[ActionNote(action=RecommendedAction.ESCALALTE_TO_DEVELOPER, note=NOTES[RecommendedAction.ESCALALTE_TO_DEVELOPER])],
            jira_ticket_title=f"In Fineract, missing from Couchbase: {doc_id}"), evidence
    office = record.get("officeName") or record.get("officeId")
    return MCPToolDiagnosisResponse(
        stage=DataStage.fineract,
        message=f"This {document_name} belongs to office {office} in Fineract, not your office {request.site_name}.",
        recommended_actions=[ActionNote(
            action=RecommendedAction.CREATE_SERVICE_DESK_TICKET,
            note=f"Move {document_name} {doc_id} from office {office} to {request.site_name} ({request.site_id})")],
        jira_ticket_title=f"{document_name} belongs to another office: {doc_id}"), evidence


def diagnose_couchbase_pick(
    request: IncomingRequestPayload, doc_id: str, found: tuple[dict, int, dict | None], device_doc: DeviceDocument | None,
) -> tuple[MCPToolDiagnosisResponse, dict[str, str | None]]:
    """Couchbase has the record: Sync Gateway import -> office -> office channel -> device -> app."""
    document, cas, sync = found

    evidence = {
        "Couchbase document": json.dumps(document, indent=2),
        "Sync Gateway metadata": json.dumps(sync, indent=2) if sync is not None else None,
        "Device document": device_doc.model_dump_json(indent=2) if device_doc is not None else None,
    }
    channels = (sync or {}).get("channels") or {}
    office_id = document.get("officeId")

    def diagnosis(stage: DataStage, message: str, action: RecommendedAction | None, title: str, note: str | None = None):
        actions = [] if action is None else [ActionNote(action=action, note=note or NOTES[action])]
        return MCPToolDiagnosisResponse(
            stage=stage, message=message, recommended_actions=actions,
            jira_ticket_title=f"{title}: {doc_id}", records=[], document=None), evidence

    # SG serves what it imported; a different CAS means the latest write never reached it
    if sync is None or sync_cas_to_int(sync["cas"]) != cas:
        return diagnosis(
            DataStage.couchbase,
            "This record is on our servers, but its latest version hasn't been picked up for syncing, so it can't reach any device.",
            RecommendedAction.ESCALALTE_TO_DEVELOPER, "Sync Gateway hasn't imported the latest version")

    if office_id != request.site_id:
        return diagnosis(
            DataStage.couchbase,
            f"This record belongs to office {office_id}, not your office {request.site_name}.",
            RecommendedAction.CREATE_SERVICE_DESK_TICKET, "Record belongs to another office",
            note=f"Move {doc_id} from office {office_id} to {request.site_name} ({request.site_id})")

    # a channel maps to null while the record is in it, and to its removal otherwise
    channel = office_channel(request.site_id)
    if channel not in channels or channels[channel] is not None:
        return diagnosis(
            DataStage.sync_gateway,
            "This record is in your office, but it isn't being shared with your office's devices.",
            RecommendedAction.ESCALALTE_TO_DEVELOPER, f"Record missing from channel {channel}")

    if device_doc is None:
        return diagnosis(
            DataStage.sync_gateway,
            "This record is ready to sync to your office, but it hasn't reached your device yet.",
            RecommendedAction.RESTART_DEVICE, "Record not synced to device")

    if device_doc.rev != sync["rev"]:
        return diagnosis(
            DataStage.sync_gateway,
            f"Your device has an older version of this record ({device_doc.rev}) than the server ({sync['rev']}).",
            RecommendedAction.RESTART_DEVICE, "Device has a stale revision")

    return diagnosis(
        DataStage.app,
        "Your device has the latest version of this record, so the app should be showing it.",
        RecommendedAction.ESCALALTE_TO_DEVELOPER, "Synced record not shown in the app")
