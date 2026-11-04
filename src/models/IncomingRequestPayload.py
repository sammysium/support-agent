
from pydantic import BaseModel

from src.models.FormulaConfig import FormulaConfig
from src.models.CountryConfig import CountryConfig


class TabletMetaData(BaseModel):
    freeRamMb: int
    freeRamMb: int
    totalRamMb: int
    batteryPercentage: int
    osVersion: str
    apiLevel: int
    manufacturer: str
    model: str
    freeStorageGb: int
    freeStorageMb: int
    totalStorageGb: int

class IncomingRequestPayload(BaseModel):
    fo_email: str
    site_id: int
    site_name: str
    sent_from_route: str
    issue: str
    country_config: CountryConfig
    country_formulas: FormulaConfig | None = None
    version: str
    country: str
    tablet_metadata: TabletMetaData
    reads_sync_status: str
    writes_sync_status: str
    user_env: str

class RequestEnablingFeaturePayload(BaseModel):
    chat_id: str
    fo_email: str
    site_id: int
    site_name: str
    requested_service: str
    country: str
    env: str


class ChatFeedback(BaseModel):
    """The officer ends the chat: "That solved it" (solved) or "I still need help" (not solved; they start over)."""
    chat_id: str
    solved: bool


class NoMatchingRecord(BaseModel):
    """None of the records shown is the one the officer is looking for: search the next stage."""
    chat_id: str


class DeviceDocument(BaseModel):
    """The picked record as the device holds it, looked up by its Couchbase id."""
    id: str
    rev: str  # the revision the device synced
    syncTs: str | None = None


class RecordPick(BaseModel):
    """The officer picked one of the records we found: it is the one they couldn't see."""
    chat_id: str
    doc_id: str  # Couchbase id of the picked record
    device_doc: DeviceDocument | None = None  # None: the device doesn't have it
