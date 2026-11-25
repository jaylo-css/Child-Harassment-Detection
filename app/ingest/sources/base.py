"""MessageSource protocol: anything that yields an async stream of MessageEvents.

Sources are read-only: they observe messages and never reply to, contact, or engage the other
party.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.models import MessageEvent


@runtime_checkable
class MessageSource(Protocol):
    name: str

    def stream(self) -> AsyncIterator[MessageEvent]: ...


async def pump(
    source: MessageSource, engine, source_name: str | None = None, device_id: str | None = None
) -> int:
    """Feed every event from `source` into a StreamEngine queue. Returns the number of events."""
    n = 0
    async for ev in source.stream():
        engine.submit(ev, source_name or source.name, device_id)
        n += 1
    return n
