"""In-process pub/sub for Server-Sent Events (alerts and live score updates)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class Broker:
    def __init__(self, maxsize: int = 256) -> None:
        self._subs: set[tuple[asyncio.Queue, frozenset[str]]] = set()
        self._maxsize = maxsize

    def subscribe(self, topics: set[str]) -> tuple[asyncio.Queue, frozenset[str]]:
        sub = (asyncio.Queue(self._maxsize), frozenset(topics))
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: tuple[asyncio.Queue, frozenset[str]]) -> None:
        self._subs.discard(sub)

    @property
    def n_subscribers(self) -> int:
        return len(self._subs)

    def publish(self, topic: str, data: dict[str, Any]) -> None:
        for q, topics in list(self._subs):
            if topic in topics:
                try:
                    q.put_nowait((topic, data))
                except asyncio.QueueFull:  # slow consumer: drop oldest
                    try:
                        q.get_nowait()
                        q.put_nowait((topic, data))
                    except (asyncio.QueueEmpty, asyncio.QueueFull):
                        pass


def sse_format(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def sse_stream(
    broker: Broker,
    topics: set[str],
    max_events: int | None = None,
    heartbeat: float = 15.0,
    filter_fn=None,
) -> AsyncIterator[str]:
    sub = broker.subscribe(topics)
    sent = 0
    try:
        yield sse_format("hello", {"topics": sorted(topics)})
        sent += 1
        while max_events is None or sent < max_events:
            try:
                topic, data = await asyncio.wait_for(sub[0].get(), timeout=heartbeat)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if filter_fn is not None and not filter_fn(topic, data):
                continue
            yield sse_format(topic, data)
            sent += 1
    finally:
        broker.unsubscribe(sub)
