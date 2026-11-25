"""Real-time engine: per-conversation incremental state, scores each new message.

ingestion (HTTP / WebSocket) --put_nowait--> asyncio.Queue --worker--> process()
  process(): signal rules on the new message, classify only the newest window, update the
  trajectory, decide on an alert (level rise + per-conversation cooldown; HIGH/CRITICAL jumps
  bypass the cooldown), persist state, publish SSE events.

LLM engine: the baseline scores first; when risk >= `llm_trigger_level` an async LLM call
refines the newest window and the score is recomputed when it returns.

State is kept in memory and persisted to SQLite (`conversation_state`) after every message, so a
restart resumes where it left off. Idle conversations are evicted after
`conversation_ttl_seconds`; on eviction the raw-text buffer is dropped from the persisted state.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from app.alerts.broker import Broker
from app.alerts.builder import build_alert
from app.config import Settings, get_settings
from app.db import session_scope
from app.detection import engines
from app.detection.scorer import ConversationScorer
from app.detection.trajectory import level_rank
from app.models import (
    Alert,
    AlertPayload,
    Conversation,
    ConversationStateRow,
    Message,
    MessageEvent,
    ScoredMessage,
    TimelinePoint,
    utcnow,
)

log = logging.getLogger(__name__)
MAX_SEEN_EVENTS = 50_000


def conversation_key(source: str, platform: str, conversation_id: str) -> str:
    return f"{source}:{platform}:{conversation_id}"[:200]


@dataclass
class Runtime:
    key: str
    scorer: ConversationScorer
    source: str
    device_id: str | None
    last_activity: float
    last_alert_rank: int = 0
    last_alert_at: float | None = None
    llm_pending: bool = False


@dataclass
class ProcessResult:
    conversation_key: str
    point: TimelinePoint
    alert: AlertPayload | None
    latency_ms: float
    stages: list[str] = field(default_factory=list)


class StreamEngine:
    def __init__(
        self,
        settings_fn: Callable[[], Settings] = get_settings,
        broker: Broker | None = None,
        clock: Callable[[], float] = time.time,
        persist: bool = True,
        queue_size: int = 10_000,
    ) -> None:
        self.settings_fn = settings_fn
        self.broker = broker or Broker()
        self.clock = clock
        self.persist = persist
        self.queue: asyncio.Queue[tuple[MessageEvent, str, str | None]] = asyncio.Queue(queue_size)
        self.runtimes: dict[str, Runtime] = {}
        self.latencies: deque[float] = deque(maxlen=1000)
        self.suppressed_alerts = 0
        self._tasks: list[asyncio.Task] = []
        self._bg: set[asyncio.Task] = set()
        self._seen_events: OrderedDict[tuple[str, str], None] = OrderedDict()
        self.duplicate_events = 0

    # -- lifecycle ------------------------------------------------------------------------
    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._worker()), asyncio.create_task(self._evictor())]

    async def stop(self) -> None:
        for t in self._tasks + list(self._bg):
            t.cancel()
        for t in self._tasks + list(self._bg):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        for rt in list(self.runtimes.values()):
            self._save_state(rt)

    def submit(self, event: MessageEvent, source: str, device_id: str | None = None) -> str:
        """Non-blocking enqueue. Raises asyncio.QueueFull under extreme bursts.

        Events re-sent by a device (e.g. its ack was lost) are recognized by event_id, acknowledged
        again by the caller, but not scored twice."""
        key = conversation_key(device_id or source, event.platform, event.conversation_id)
        if event.event_id:
            seen_key = (device_id or source, event.event_id)
            if seen_key in self._seen_events:
                self.duplicate_events += 1
                return key
            self.queue.put_nowait((event, source, device_id))
            self._seen_events[seen_key] = None
            if len(self._seen_events) > MAX_SEEN_EVENTS:
                self._seen_events.popitem(last=False)
            return key
        self.queue.put_nowait((event, source, device_id))
        return key

    async def drain(self) -> None:
        """Wait until every queued event has been processed (tests / shutdown)."""
        await self.queue.join()

    async def _worker(self) -> None:
        while True:
            event, source, device_id = await self.queue.get()
            try:
                self.process(event, source, device_id)
            except Exception:  # never let one bad event kill the worker
                log.exception("failed to score event")
            finally:
                self.queue.task_done()

    async def _evictor(self) -> None:
        while True:
            await asyncio.sleep(60)
            self.evict_idle()

    # -- core -----------------------------------------------------------------------------
    def process(self, event: MessageEvent, source: str, device_id: str | None = None) -> ProcessResult:
        s = self.settings_fn()
        key = conversation_key(device_id or source, event.platform, event.conversation_id)
        t0 = time.perf_counter()
        rt = self._runtime(key, event.platform, source, device_id, s)
        msg = ScoredMessage(
            index=rt.scorer.next_index,
            sender_hash=event.sender_hash,
            is_child=event.is_child,
            text=event.text,
            timestamp=event.timestamp,
        )
        point = rt.scorer.process(msg)
        now = self.clock()
        rt.last_activity = now
        alert = self._maybe_alert(rt, point.level, now, s)
        latency = (time.perf_counter() - t0) * 1000
        self.latencies.append(latency)

        if self.persist:
            self._persist_message(rt, msg, point, alert)
        self._publish_score(rt, point)
        if alert is not None:
            self.broker.publish("alert", alert.model_dump(mode="json"))
        self._maybe_llm(rt, point, s)
        return ProcessResult(key, point, alert, latency, rt.scorer.stages_detected())

    def _runtime(self, key: str, platform: str, source: str, device_id: str | None, s: Settings) -> Runtime:
        rt = self.runtimes.get(key)
        if rt is not None:
            return rt
        clf, det = engines.baseline(s), engines.detector(s)
        scorer = None
        meta: dict[str, Any] = {}
        if self.persist:
            with session_scope() as sess:
                row = sess.get(ConversationStateRow, key)
                if row is not None:
                    scorer = ConversationScorer.from_state(row.state, s, clf, det)
                    meta = row.state.get("runtime", {})
        if scorer is None:
            scorer = ConversationScorer(s, clf, det, key, platform)
        rt = Runtime(
            key=key,
            scorer=scorer,
            source=source,
            device_id=device_id,
            last_activity=self.clock(),
            last_alert_rank=int(meta.get("last_alert_rank", 0)),
            last_alert_at=meta.get("last_alert_at"),
        )
        self.runtimes[key] = rt
        return rt

    def _maybe_alert(self, rt: Runtime, level: str, now: float, s: Settings) -> AlertPayload | None:
        rank = level_rank(level)
        cooled = rt.last_alert_at is None or now - rt.last_alert_at >= s.alert_cooldown_seconds
        if rank <= rt.last_alert_rank:
            if cooled and rank < rt.last_alert_rank:
                rt.last_alert_rank = rank  # risk has subsided for a full cooldown: re-arm
            return None
        if rank == 0:
            return None
        if rank < level_rank("HIGH") and not cooled:
            self.suppressed_alerts += 1
            return None
        rt.last_alert_rank = rank
        rt.last_alert_at = now
        sc = rt.scorer
        return build_alert(rt.key, sc.platform, level, sc.score, sc.stages_detected(), sc.excerpts)

    # -- LLM refinement -------------------------------------------------------------------
    def _maybe_llm(self, rt: Runtime, point: TimelinePoint, s: Settings) -> None:
        llm = engines.llm(s)
        if llm is None or rt.llm_pending or level_rank(point.level) < level_rank(s.llm_trigger_level):
            return
        ob = rt.scorer.observations[-1]
        if ob.is_child or not ob.probs:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # called outside the event loop (sync scripts/tests): skip refinement
            return
        window = list(rt.scorer.buffer)
        rt.llm_pending = True
        task = loop.create_task(self._refine(rt, ob.index, dict(ob.probs), window, s))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _refine(self, rt: Runtime, index: int, base_probs: dict, window: list, s: Settings) -> None:
        try:
            llm = engines.llm(s)
            res = await asyncio.to_thread(llm.classify_detailed, window) if llm else None
            if res is None:
                return
            combined = engines.combine(s.engine, base_probs, res.stage_probs)
            point = rt.scorer.refine(index, combined, s.engine)
            if point is None:
                return
            alert = self._maybe_alert(rt, rt.scorer.level, self.clock(), s)
            if self.persist:
                self._save_state(rt)
                if alert is not None:
                    with session_scope() as sess:
                        self._store_alert(sess, alert)
                        sess.commit()
            self._publish_score(rt, rt.scorer.timeline[-1], refined=True)
            if alert is not None:
                self.broker.publish("alert", alert.model_dump(mode="json"))
        except Exception:
            log.exception("LLM refinement failed")
        finally:
            rt.llm_pending = False

    # -- persistence ----------------------------------------------------------------------
    def _state_blob(self, rt: Runtime, include_buffer: bool = True) -> dict:
        blob = rt.scorer.to_state(include_buffer=include_buffer)
        blob["runtime"] = {"last_alert_rank": rt.last_alert_rank, "last_alert_at": rt.last_alert_at}
        return blob

    def _save_state(self, rt: Runtime, include_buffer: bool = True) -> None:
        if not self.persist:
            return
        with session_scope() as sess:
            self._upsert_state(sess, rt, include_buffer)
            sess.commit()

    def _upsert_state(self, sess: Session, rt: Runtime, include_buffer: bool = True) -> None:
        row = sess.get(ConversationStateRow, rt.key)
        blob = self._state_blob(rt, include_buffer)
        if row is None:
            row = ConversationStateRow(conversation_id=rt.key, state=blob)
        else:
            row.state = blob
            row.updated_at = utcnow()
        sess.add(row)

    def _store_alert(self, sess: Session, a: AlertPayload) -> None:
        sess.add(
            Alert(
                id=a.id,
                conversation_id=a.conversation_id,
                platform=a.platform,
                level=a.level,
                score=a.score,
                created_at=a.created_at,
                stages=a.stages,
                excerpts=a.excerpts,
                suggested_actions=a.suggested_actions,
                summary=a.summary,
            )
        )

    def _persist_message(
        self, rt: Runtime, msg: ScoredMessage, point: TimelinePoint, alert: AlertPayload | None
    ) -> None:
        sc = rt.scorer
        with session_scope() as sess:
            conv = sess.get(Conversation, rt.key)
            if conv is None:
                conv = Conversation(id=rt.key, platform=sc.platform, source=rt.source, device_id=rt.device_id)
            conv.last_activity = utcnow()
            conv.current_score = point.score
            conv.current_level = point.level
            conv.n_messages = sc.next_index
            sess.add(conv)
            sess.add(
                Message(
                    conversation_id=rt.key,
                    index=msg.index,
                    sender_hash=msg.sender_hash,
                    is_child=msg.is_child,
                    timestamp=msg.timestamp or utcnow(),
                    n_signals=len(sc.observations[-1].signals),
                    score_after=point.score,
                )
            )
            self._upsert_state(sess, rt)
            if alert is not None:
                self._store_alert(sess, alert)
            sess.commit()

    def _publish_score(self, rt: Runtime, point: TimelinePoint, refined: bool = False) -> None:
        self.broker.publish(
            "score",
            {
                "conversation_id": rt.key,
                "index": point.index,
                "score": point.score,
                "level": point.level,
                "new_stages": point.new_stages,
                "stages": rt.scorer.stages_detected(),
                "refined": refined,
            },
        )

    # -- eviction -------------------------------------------------------------------------
    def evict_idle(self, now: float | None = None) -> list[str]:
        s = self.settings_fn()
        now = self.clock() if now is None else now
        evicted = []
        for key, rt in list(self.runtimes.items()):
            if now - rt.last_activity >= s.conversation_ttl_seconds:
                self._save_state(rt, include_buffer=False)  # raw text discarded
                del self.runtimes[key]
                evicted.append(key)
        return evicted

    def stats(self) -> dict[str, Any]:
        lat = list(self.latencies)
        return {
            "conversations_in_memory": len(self.runtimes),
            "queue_depth": self.queue.qsize(),
            "suppressed_alerts": self.suppressed_alerts,
            "duplicate_events": self.duplicate_events,
            "latency_ms_p50": round(statistics.median(lat), 2) if lat else None,
            "latency_ms_p95": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 2) if lat else None,
        }
