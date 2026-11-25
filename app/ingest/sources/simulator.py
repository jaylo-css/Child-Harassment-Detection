"""Live simulator source: operator-typed messages or scenario replay at 1x-20x.

The dashboard simulator page drives the same path from the browser over /ws/ingest; this class
is the Python equivalent, used by tests and scripts.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.ingest.sources.scenarios import load_scenario
from app.models import MessageEvent, utcnow

_END = object()


class SimulatorSource:
    name = "simulator"

    def __init__(self, conversation_id: str = "sim-1", platform: str = "Simulator") -> None:
        self.conversation_id = conversation_id
        self.platform = platform
        self._queue: asyncio.Queue = asyncio.Queue()

    def push(self, text: str, as_child: bool) -> None:
        """Operator-typed message."""
        self._queue.put_nowait(self._event(text, as_child))

    def close(self) -> None:
        self._queue.put_nowait(_END)

    def _event(self, text: str, as_child: bool) -> MessageEvent:
        return MessageEvent(
            platform=self.platform,
            conversation_id=self.conversation_id,
            sender_hash="child" if as_child else "stranger",
            is_child=as_child,
            text=text,
            timestamp=utcnow(),
        )

    async def stream(self) -> AsyncIterator[MessageEvent]:
        while True:
            item = await self._queue.get()
            if item is _END:
                return
            yield item

    async def replay(self, scenario: str, speed: float = 1.0, max_delay: float | None = None) -> None:
        """Push a scenario's messages with their delays divided by `speed` (clamped to 1-20x)."""
        speed = min(20.0, max(1.0, speed))
        d = load_scenario(scenario)
        self.platform = d.get("platform", self.platform)
        for m in d["messages"]:
            delay = float(m.get("delay", 1.0)) / speed
            if max_delay is not None:
                delay = min(delay, max_delay)
            await asyncio.sleep(delay)
            self.push(m["text"], m["as"] == "child")
        self.close()
