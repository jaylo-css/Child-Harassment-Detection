"""Domain (Pydantic) models and persistence (SQLModel) tables.

Data-minimization notes:
* DB tables never store full message text. `Message` rows hold metadata only.
* `ConversationStateRow` holds the scorer state; its rolling buffer contains at most
  `window_size` raw messages and is overwritten as new messages arrive (and wiped on eviction).
* `Alert.excerpts` hold at most 2 redacted excerpts of at most 120 characters.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, NaiveDatetime, field_validator
from sqlalchemy import JSON, Column
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

MAX_EXCERPTS = 2
MAX_EXCERPT_CHARS = 120


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


# --------------------------------------------------------------------------------------------
# Domain models (in memory / API payloads)
# --------------------------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A parsed message from an uploaded log, before child/suspect assignment."""

    index: int
    sender: str
    text: str
    timestamp: datetime | None = None


class MessageEvent(BaseModel):
    """One message from a real-time source (Android companion, simulator, ...)."""

    platform: str = Field(max_length=64)
    conversation_id: str = Field(min_length=1, max_length=128)
    sender_hash: str = Field(min_length=1, max_length=128)
    is_child: bool
    text: str = Field(max_length=4000)
    timestamp: datetime = Field(default_factory=utcnow)
    event_id: str | None = Field(default=None, max_length=128)

    @field_validator("timestamp")
    @classmethod
    def _naive_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is not None:
            v = v.astimezone(UTC).replace(tzinfo=None)
        return v


class ScoredMessage(BaseModel):
    """Message as seen by the scorer (pipeline + stream share this)."""

    index: int
    sender_hash: str
    is_child: bool
    text: str
    timestamp: datetime | None = None


class SignalHit(BaseModel):
    signal: str
    stage: str
    weight: float
    message_id: int
    span: tuple[int, int]  # character span in the *normalized* message text


class WindowResult(BaseModel):
    start: int
    end: int  # inclusive message index
    stage_probs: dict[str, float]
    engine: str
    rationale: str | None = None
    evidence_message_ids: list[int] = Field(default_factory=list)


class TimelinePoint(BaseModel):
    index: int
    score: float
    level: str
    new_stages: list[str] = Field(default_factory=list)


class Assessment(BaseModel):
    conversation_id: str
    platform: str | None = None
    engine: str
    score: float
    level: str
    stage_evidence: dict[str, float]
    stages_detected: list[str]
    stage_first_seen: dict[str, int]
    timeline: list[TimelinePoint]
    signals: list[SignalHit]
    top_signals: list[SignalHit]
    top_windows: list[WindowResult]
    windows: list[WindowResult] = Field(default_factory=list)
    components: dict[str, Any] = Field(default_factory=dict)
    explanation: list[str] = Field(default_factory=list)
    first_alert_index: int | None = None
    n_messages: int = 0


class AlertPayload(BaseModel):
    """What a guardian sees. Never a transcript."""

    id: str = Field(default_factory=new_id)
    conversation_id: str
    platform: str | None
    level: str
    score: float
    created_at: datetime = Field(default_factory=utcnow)
    stages: list[str]
    stage_labels: list[str]
    excerpts: list[str] = Field(default_factory=list, max_length=MAX_EXCERPTS)
    suggested_actions: list[str] = Field(default_factory=list)
    summary: str = ""

    @field_validator("excerpts")
    @classmethod
    def _short(cls, v: list[str]) -> list[str]:
        for e in v:
            if len(e) > MAX_EXCERPT_CHARS:
                raise ValueError(f"excerpt longer than {MAX_EXCERPT_CHARS} chars")
        return v


# --------------------------------------------------------------------------------------------
# Persistence tables
# --------------------------------------------------------------------------------------------


class Guardian(SQLModel, table=True):
    id: str = SQLField(default_factory=new_id, primary_key=True)
    display_name: str = "Guardian"
    created_at: NaiveDatetime = SQLField(default_factory=utcnow)


class Conversation(SQLModel, table=True):
    id: str = SQLField(primary_key=True)  # "<source>:<platform>:<hashed conversation id>"
    platform: str | None = None
    source: str = "device"  # device | simulator | upload
    device_id: str | None = SQLField(default=None, index=True)
    created_at: NaiveDatetime = SQLField(default_factory=utcnow)
    last_activity: NaiveDatetime = SQLField(default_factory=utcnow, index=True)
    current_score: float = 0.0
    current_level: str = "LOW"
    n_messages: int = 0


class Message(SQLModel, table=True):
    """Message metadata only - no text is stored."""

    id: int | None = SQLField(default=None, primary_key=True)
    conversation_id: str = SQLField(index=True)
    index: int
    sender_hash: str
    is_child: bool
    timestamp: NaiveDatetime
    n_signals: int = 0
    score_after: float = 0.0
    created_at: NaiveDatetime = SQLField(default_factory=utcnow, index=True)


class AssessmentRow(SQLModel, table=True):
    __tablename__ = "assessment"
    id: str = SQLField(default_factory=new_id, primary_key=True)
    conversation_id: str = SQLField(index=True)
    created_at: NaiveDatetime = SQLField(default_factory=utcnow, index=True)
    engine: str
    score: float
    level: str
    stages_detected: list[str] = SQLField(default_factory=list, sa_column=Column(JSON))
    timeline: list[dict] = SQLField(default_factory=list, sa_column=Column(JSON))
    components: dict = SQLField(default_factory=dict, sa_column=Column(JSON))
    explanation: list[str] = SQLField(default_factory=list, sa_column=Column(JSON))


class Alert(SQLModel, table=True):
    id: str = SQLField(default_factory=new_id, primary_key=True)
    conversation_id: str = SQLField(index=True)
    guardian_id: str | None = None
    platform: str | None = None
    level: str
    score: float
    created_at: NaiveDatetime = SQLField(default_factory=utcnow, index=True)
    stages: list[str] = SQLField(default_factory=list, sa_column=Column(JSON))
    excerpts: list[str] = SQLField(default_factory=list, sa_column=Column(JSON))
    suggested_actions: list[str] = SQLField(default_factory=list, sa_column=Column(JSON))
    summary: str = ""
    acknowledged: bool = False


class Device(SQLModel, table=True):
    id: str = SQLField(default_factory=new_id, primary_key=True)
    name: str = "Device"
    platform: str = "android"
    token_hash: str = SQLField(index=True)
    status: str = "active"  # active | revoked
    created_at: NaiveDatetime = SQLField(default_factory=utcnow)
    last_seen: NaiveDatetime | None = None
    last_message_at: NaiveDatetime | None = None


class PairingCode(SQLModel, table=True):
    code_hash: str = SQLField(primary_key=True)
    created_at: NaiveDatetime = SQLField(default_factory=utcnow)
    expires_at: NaiveDatetime
    used: bool = False
    device_name: str | None = None


class ConversationStateRow(SQLModel, table=True):
    __tablename__ = "conversation_state"
    conversation_id: str = SQLField(primary_key=True)
    updated_at: NaiveDatetime = SQLField(default_factory=utcnow, index=True)
    state: dict = SQLField(default_factory=dict, sa_column=Column(JSON))


class AppSetting(SQLModel, table=True):
    key: str = SQLField(primary_key=True)
    value: Any = SQLField(default=None, sa_column=Column(JSON))
