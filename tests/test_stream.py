import asyncio
import uuid

import pytest

from app.config import get_settings
from app.db import session_scope
from app.detection.pipeline import analyze
from app.detection.stream import StreamEngine
from app.detection.trajectory import level_rank
from app.ingest.sources.base import pump
from app.ingest.sources.scenarios import scenario_messages
from app.ingest.sources.simulator import SimulatorSource
from app.models import ConversationStateRow, MessageEvent

LATENCY_TARGET_MS = 300


class FakeClock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def events(name: str, conv_id: str):
    for m in scenario_messages(name):
        yield MessageEvent(
            platform="Roblox",
            conversation_id=conv_id,
            sender_hash=m.sender_hash,
            is_child=m.is_child,
            text=m.text,
            timestamp=m.timestamp,
        )


def test_stream_timeline_matches_batch():
    eng = StreamEngine(persist=False)
    stream_scores = [eng.process(ev, "test").point.score for ev in events("grooming", "c-match")]
    batch = analyze(scenario_messages("grooming"))
    assert len(stream_scores) == len(batch.timeline)
    for s, b in zip(stream_scores, batch.timeline, strict=True):
        assert abs(s - b.score) <= 0.5


def test_alert_fires_at_expected_message_and_escalates():
    eng = StreamEngine(persist=False, clock=FakeClock())
    alerts = []
    for ev in events("grooming", "c-alert"):
        r = eng.process(ev, "test")
        if r.alert:
            alerts.append((r.point.index, r.alert.level))
    batch = analyze(scenario_messages("grooming"))
    assert alerts, "expected at least one alert"
    assert alerts[0][0] == batch.first_alert_index
    # with a frozen clock the cooldown is active, yet HIGH/CRITICAL jumps still alert immediately
    assert level_rank(alerts[-1][1]) >= level_rank("HIGH")
    ranks = [level_rank(lv) for _, lv in alerts]
    assert ranks == sorted(set(ranks)), "each alert must be a level rise"


def test_benign_stream_never_alerts():
    eng = StreamEngine(persist=False)
    assert not any(eng.process(ev, "test").alert for ev in events("benign", "c-benign"))


def test_cooldown_suppresses_duplicates():
    s = get_settings()
    clock = FakeClock()
    eng = StreamEngine(persist=False, clock=clock)
    eng.process(next(events("benign", "c-cool")), "test")
    rt = next(iter(eng.runtimes.values()))
    seq = ["MEDIUM", "LOW", "MEDIUM", "LOW", "MEDIUM"]
    fired = []
    for lv in seq:
        clock.t += 10
        fired.append(eng._maybe_alert(rt, lv, clock.t, s) is not None)
    assert fired == [True, False, False, False, False]
    assert eng.suppressed_alerts == 0  # duplicates at the same level are simply not re-raised
    # risk subsides for a full cooldown -> re-armed
    clock.t += s.alert_cooldown_seconds + 1
    assert eng._maybe_alert(rt, "LOW", clock.t, s) is None
    clock.t += 1
    assert eng._maybe_alert(rt, "MEDIUM", clock.t, s) is not None
    # HIGH right after (inside cooldown) is raised immediately
    clock.t += 1
    assert eng._maybe_alert(rt, "HIGH", clock.t, s) is not None


def test_medium_rise_within_cooldown_is_suppressed():
    s = get_settings()
    clock = FakeClock()
    eng = StreamEngine(persist=False, clock=clock)
    eng.process(next(events("benign", "c-cool2")), "test")
    rt = next(iter(eng.runtimes.values()))
    assert eng._maybe_alert(rt, "HIGH", clock.t, s) is not None
    rt.last_alert_rank = 0  # simulate a reset while still inside the cooldown window
    clock.t += 5
    assert eng._maybe_alert(rt, "MEDIUM", clock.t, s) is None
    assert eng.suppressed_alerts == 1


def test_latency_under_target():
    eng = StreamEngine(persist=True)
    for ev in events("grooming", f"c-lat-{uuid.uuid4().hex[:6]}"):
        eng.process(ev, "test")
    lat = sorted(eng.latencies)
    p95 = lat[int(0.95 * (len(lat) - 1))]
    assert p95 < LATENCY_TARGET_MS, eng.stats()


def test_state_survives_restart_and_eviction_drops_text():
    conv = f"c-restart-{uuid.uuid4().hex[:6]}"
    evs = list(events("grooming", conv))
    clock = FakeClock()
    e1 = StreamEngine(persist=True, clock=clock)
    for ev in evs[:10]:
        e1.process(ev, "test")
    e2 = StreamEngine(persist=True, clock=clock)  # "restart": fresh memory, same DB
    last = None
    for ev in evs[10:]:
        last = e2.process(ev, "test")
    batch = analyze(scenario_messages("grooming"))
    assert last.point.index == len(evs) - 1
    assert abs(last.point.score - batch.score) <= 0.5

    clock.t += get_settings().conversation_ttl_seconds + 1
    assert e2.evict_idle() == [last.conversation_key]
    with session_scope() as s:
        row = s.get(ConversationStateRow, last.conversation_key)
        assert row.state["buffer"] == []
        assert "pic of you" not in str(row.state)


@pytest.mark.anyio
async def test_queue_worker_with_simulator_source():
    eng = StreamEngine(persist=False)
    await eng.start()
    sub = eng.broker.subscribe({"alert", "score"})
    try:
        src = SimulatorSource("c-sim")
        replay = asyncio.create_task(src.replay("grooming", speed=20, max_delay=0.01))
        n = await pump(src, eng)
        await replay
        await eng.drain()
        assert n == len(scenario_messages("grooming"))
        topics = []
        while not sub[0].empty():
            topics.append((await sub[0].get())[0])
        assert topics.count("score") == n and "alert" in topics
    finally:
        eng.broker.unsubscribe(sub)
        await eng.stop()


@pytest.mark.anyio
async def test_resent_event_ids_are_not_scored_twice():
    eng = StreamEngine(persist=False)
    ev = next(events("grooming", "c-dup")).model_copy(update={"event_id": "same-id"})
    k1 = eng.submit(ev, "test", "dev1")
    k2 = eng.submit(ev, "test", "dev1")
    assert k1 == k2 and eng.queue.qsize() == 1 and eng.duplicate_events == 1
    eng.submit(ev, "test", "dev2")  # same id from another device is a different event
    assert eng.queue.qsize() == 2
