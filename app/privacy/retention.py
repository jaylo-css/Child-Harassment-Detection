"""Retention purge. Runs on startup and daily (see app.main)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, delete, select

from app.models import (
    Alert,
    AssessmentRow,
    Conversation,
    ConversationStateRow,
    Message,
    PairingCode,
    utcnow,
)


def purge(
    s: Session, retention_days: int, idle_ttl_seconds: int, now: datetime | None = None
) -> dict[str, int]:
    now = now or utcnow()
    cutoff = now - timedelta(days=retention_days)
    counts: dict[str, int] = {}
    for name, model, col in (
        ("alerts", Alert, Alert.created_at),
        ("assessments", AssessmentRow, AssessmentRow.created_at),
        ("messages", Message, Message.created_at),
        ("conversation_states", ConversationStateRow, ConversationStateRow.updated_at),
        ("conversations", Conversation, Conversation.last_activity),
    ):
        res = s.exec(delete(model).where(col < cutoff))
        counts[name] = res.rowcount or 0
    res = s.exec(delete(PairingCode).where((PairingCode.expires_at < now) | (PairingCode.used == True)))  # noqa: E712
    counts["pairing_codes"] = res.rowcount or 0

    # Raw text must not outlive the rolling window: drop buffers of idle conversations
    # (covers state left behind by a crash before in-memory eviction ran).
    idle_cutoff = now - timedelta(seconds=idle_ttl_seconds)
    stripped = 0
    for row in s.exec(
        select(ConversationStateRow).where(ConversationStateRow.updated_at < idle_cutoff)
    ).all():
        if row.state.get("buffer"):
            row.state = {**row.state, "buffer": []}
            s.add(row)
            stripped += 1
    counts["buffers_stripped"] = stripped
    s.commit()
    return counts
