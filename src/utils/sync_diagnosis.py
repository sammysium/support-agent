import asyncio
from datetime import datetime, timedelta, timezone

from src.enums.actions import DataStage, RecommendedAction
from src.integrations import sync_gateway
from src.integrations.cb import CouchBaseDatabase
from src.integrations.cb_admin import cluster_problems
from src.models.IncomingRequestPayload import IncomingRequestPayload
from src.models.responses.OutgoingResponsePayload import ActionNote, MCPToolDiagnosisResponse
from src.utils.record_diagnosis import NOTES, office_channel

# the device's reads replication status, as the app's sync badge shows it
OFFLINE = "offline"
AUTH_ERROR = "auth_error"

# an office whose newest record is older than this hasn't had anything from Fineract in a while
STALE_AFTER = timedelta(hours=48)
# below this Couchbase Lite can't save what it downloads
MIN_FREE_STORAGE_MB = 500


def diagnosis(stage: DataStage, message: str, action: RecommendedAction | None, title: str, note: str | None = None):
    return MCPToolDiagnosisResponse(
        stage=stage, message=message,
        recommended_actions=[] if action is None else [ActionNote(action=action, note=note or NOTES[action])],
        jira_ticket_title=title)


async def diagnose_no_syncing(request: IncomingRequestPayload) -> MCPToolDiagnosisResponse:
    """Why the officer's reads don't sync, server side: Sync Gateway (public URL, then database) -> their user -> their office channel ->
    Couchbase indexes -> cluster -> Fineract pipeline, after the tablet's storage. The app checks its internet and clock
    before it reports anything, since without them it couldn't reach us."""
    email, channel = request.fo_email, office_channel(request.site_id)

    free_mb = request.tablet_metadata.freeStorageMb
    if free_mb < MIN_FREE_STORAGE_MB:
        return diagnosis(DataStage.app, f"Your tablet is almost full ({free_mb} MB free), so it can't save new data. "
                         "Delete photos, videos or apps you don't need, then restart the app.",
                         None, f"Device storage full ({free_mb} MB free): {email}")

    if not await sync_gateway.public_url_answers():
        return diagnosis(DataStage.sync_gateway, "The sync server can't be reached, so no device can sync right now.",
                         RecommendedAction.ESCALALTE_TO_DEVELOPER, "Sync Gateway public URL is not reachable")

    state = await sync_gateway.get_database_state()
    if state is None:
        return diagnosis(DataStage.sync_gateway, "The sync server isn't responding, so no device can sync right now.",
                         RecommendedAction.ESCALALTE_TO_DEVELOPER, "Sync Gateway is not responding")
    if state != "Online":
        return diagnosis(DataStage.sync_gateway, "The sync server is up but can't serve data right now.",
                         RecommendedAction.ESCALALTE_TO_DEVELOPER, f"Sync Gateway reads database is {state}")

    if request.reads_sync_status == OFFLINE:
        # the server answers us, so it's the tablet that can't reach it
        return diagnosis(DataStage.app, "The sync server is working, but your tablet can't reach it. "
                         "Check your internet connection and that your tablet's date and time are correct.",
                         RecommendedAction.RESTART_DEVICE, f"Device offline: {email}",
                         note="Restart your device once you're connected.")

    user = await sync_gateway.get_user(email)
    if user is None:
        return diagnosis(DataStage.sync_gateway, "You don't have a sync account yet, so your tablet can't download data.",
                         RecommendedAction.CREATE_SERVICE_DESK_TICKET, f"No scope for {email}",
                         note=f"Give scope to {email} with access so that he can sync data from his/her hchannel {channel}")
    if user.get("disabled"):
        return diagnosis(DataStage.sync_gateway, "Your sync account is disabled, so your tablet can't download data.",
                         RecommendedAction.CREATE_SERVICE_DESK_TICKET, f"Sync Gateway user disabled: {email}",
                         note=f"Enable sync user {email}")
    if request.reads_sync_status == AUTH_ERROR:
        # the account is fine, so the password the tablet saved at login is out of date
        return diagnosis(DataStage.app, "Your sync account is fine, but your tablet is using an old password.",
                         RecommendedAction.LOG_IN_AGAIN, f"Device has stale sync credentials: {email}",
                         note="Log out and log in again so your tablet gets your current password.")
    if channel not in (user.get("all_channels") or []):
        return diagnosis(DataStage.sync_gateway,
                         f"Your sync account doesn't have access to {request.site_name}'s data.",
                         RecommendedAction.CREATE_SERVICE_DESK_TICKET, f"User lacks office channel: {email} {channel}",
                         note=f"Give sync user {email} access to {channel} ({request.site_name})")

    database = CouchBaseDatabase()
    documents, newest = await asyncio.to_thread(database.office_documents, request.site_id)
    if not await sync_gateway.channel_has_documents(channel):
        if documents:
            return diagnosis(DataStage.couchbase, f"Our servers have {request.site_name}'s data, but it isn't ready for devices yet.",
                             RecommendedAction.ESCALALTE_TO_DEVELOPER,
                             f"{documents} documents for office {request.site_id} in Couchbase, none in {channel}")
        return diagnosis(DataStage.fineract, f"No data for {request.site_name} has reached our servers yet.",
                         RecommendedAction.ESCALALTE_TO_DEVELOPER, f"No Couchbase documents for office {request.site_id}")

    missing = await asyncio.to_thread(database.missing_indexes)
    if missing:
        return diagnosis(DataStage.couchbase, "Our servers are having a problem, so syncing is slow or failing.",
                         RecommendedAction.ESCALALTE_TO_DEVELOPER, f"Couchbase indexes missing or offline: {', '.join(missing)}")

    problems = await cluster_problems()
    if problems:
        return diagnosis(DataStage.couchbase, "Our servers are having a problem, so syncing is slow or failing.",
                         RecommendedAction.ESCALALTE_TO_DEVELOPER, f"Couchbase cluster: {'; '.join(problems)}")

    if datetime.now(timezone.utc) - datetime.fromisoformat(newest) > STALE_AFTER:
        return diagnosis(DataStage.fineract, f"Syncing works, but nothing new for {request.site_name} has reached our servers since {newest[:10]}.",
                         RecommendedAction.ESCALALTE_TO_DEVELOPER, f"No new data for office {request.site_id} since {newest}")

    return diagnosis(DataStage.app, "The sync server and your account look fine.",
                     RecommendedAction.RESTART_DEVICE, f"Sync looks fine server side, device reads {request.reads_sync_status}: {email}")
