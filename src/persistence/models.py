from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel
from enum import Enum


class IssueStatus(str, Enum):
    processing = "processing"
    completed = "completed"
    failed = "failed"
    unsolved = "unsolved"  # the officer said the answer didn't help ("I still need help")



# JSONB on Postgres, so chats can be queried by what's inside them
JSON_COLUMN = JSON().with_variant(JSONB(), "postgresql")


class Issue(SQLModel, table=True):
    """One turn of a chat: what the app sent (the opening report, a follow-up message, a pick) and what we
    answered. All turns of a chat share its chat_id; the opening one is the oldest."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_type=DateTime(timezone=True))
    chat_id: str = Field(index=True, primary_key = True)
    incoming: dict[str, Any] = Field(sa_type=JSON_COLUMN)
    updates: dict[str, Any] | None = Field(default=None, sa_type=JSON_COLUMN)
    # a plain string column, not a Postgres enum type: a new status doesn't need an ALTER TYPE
    issue_status: IssueStatus = Field(
        default=IssueStatus.processing,
        sa_type=SAEnum(IssueStatus, native_enum=False, length=20),
    )