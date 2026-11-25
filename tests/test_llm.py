"""LLM engine tests. No network: the Anthropic client is always mocked."""

import asyncio
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.config import get_settings
from app.detection import engines
from app.detection.classifiers.llm_rubric import LLMRubricClassifier, render_window
from app.detection.pipeline import analyze
from app.detection.stream import StreamEngine
from app.ingest.sources.scenarios import scenario_messages
from app.models import MessageEvent, ScoredMessage


def resp(payload=None, stop="end_turn", text=None):
    body = text if text is not None else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=body)])


GOOD = {
    "stage_probs": {"NONE": 0.05, "SECRECY": 0.9, "ESCALATION": 0.7},
    "rationale": "Asks the child to keep contact secret and requests a private photo.",
    "evidence_message_ids": [0, 1, 99],
}


class FakeMessages:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.responder(kw)


class FakeClient:
    def __init__(self, responder):
        self.messages = FakeMessages(responder)
        self.beta = SimpleNamespace(messages=self.messages)


def make(responder, model="claude-sonnet-5-5"):
    s = get_settings()
    return LLMRubricClassifier(
        model=model, api_key="test", fallback=engines.baseline(s), client=FakeClient(responder)
    )


WINDOW = [
    ScoredMessage(
        index=0, sender_hash="o", is_child=False, text="dont tell anyone, text me at 0917 555 1234"
    ),
    ScoredMessage(index=1, sender_hash="c", is_child=True, text="ok"),
]


def test_no_api_key_runs_offline_as_baseline():
    s = get_settings().model_copy(update={"engine": "llm", "anthropic_api_key": None})
    assert engines.llm(s) is None
    assert engines.effective_engine(s) == "baseline"
    a = analyze(scenario_messages("grooming"), settings=s)
    assert a.engine == "baseline" and a.level in ("HIGH", "CRITICAL")


def test_request_is_redacted_strict_and_safety_framed():
    clf = make(lambda kw: resp(GOOD))
    res = clf.classify_detailed(WINDOW)
    assert res is not None and res.stage_probs["SECRECY"] == 0.9 and res.stage_probs["ISOLATION"] == 0.0
    assert res.evidence_offsets == [0, 1]  # out-of-range id dropped
    (call,) = clf.client.messages.calls
    sent = call["messages"][0]["content"]
    assert "0917" not in sent and "[PHONE]" in sent
    assert call["model"] == "claude-sonnet-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert call["fallbacks"] == "default"
    assert "child-safety classifier" in call["system"] and "Never reproduce" in call["system"]


def test_cache_by_window_hash():
    clf = make(lambda kw: resp(GOOD))
    clf.classify_detailed(WINDOW)
    clf.classify_detailed(list(WINDOW))
    assert len(clf.client.messages.calls) == 1


@pytest.mark.parametrize(
    "responder",
    [
        lambda kw: (_ for _ in ()).throw(
            anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))
        ),
        lambda kw: (_ for _ in ()).throw(RuntimeError("boom")),
        lambda kw: resp(GOOD, stop="refusal"),
        lambda kw: resp(GOOD, stop="max_tokens"),
        lambda kw: resp(text="not json"),
        lambda kw: resp({"rationale": "missing probs"}),
    ],
)
def test_failures_fall_back_to_baseline(responder):
    clf = make(responder)
    assert clf.classify_detailed(WINDOW) is None
    assert clf.classify(WINDOW) == clf.fallback.classify(WINDOW)


def test_circuit_breaker_stops_calling_after_failures():
    clf = make(lambda kw: (_ for _ in ()).throw(RuntimeError("down")))
    for i in range(6):
        w = [ScoredMessage(index=0, sender_hash="o", is_child=False, text=f"msg {i}")]
        clf.classify_detailed(w)
    assert len(clf.client.messages.calls) == 3


def test_older_model_uses_plain_create_without_beta():
    clf = make(lambda kw: resp(GOOD), model="claude-sonnet-4-6")
    clf.classify_detailed(WINDOW)
    (call,) = clf.client.messages.calls
    assert "fallbacks" not in call and "effort" not in call["output_config"]


def test_render_window_marks_roles():
    out = render_window(WINDOW)
    assert "[0] OTHER:" in out and "[1] CHILD:" in out


def test_ensemble_pipeline_with_mocked_llm(monkeypatch):
    s = get_settings().model_copy(update={"engine": "ensemble", "anthropic_api_key": "test"})
    clf = make(lambda kw: resp(GOOD))
    monkeypatch.setattr(engines, "llm", lambda settings: clf if settings.engine != "baseline" else None)
    a = analyze(scenario_messages("benign"), settings=s)
    assert a.engine == "ensemble"
    assert a.windows and all(w.engine == "ensemble" for w in a.windows)
    assert any(w.rationale for w in a.windows)
    # averaged: baseline says ~0 SECRECY on benign chat, LLM (mock) says 0.9 -> about 0.45
    assert all(0.3 < w.stage_probs["SECRECY"] < 0.6 for w in a.windows)


@pytest.mark.anyio
async def test_stream_refines_asynchronously_once_medium(monkeypatch):
    s = get_settings().model_copy(update={"engine": "llm", "anthropic_api_key": "test"})
    clf = make(lambda kw: resp(GOOD))
    monkeypatch.setattr(engines, "llm", lambda settings: clf)
    eng = StreamEngine(settings_fn=lambda: s, persist=False)
    sub = eng.broker.subscribe({"score"})
    for m in scenario_messages("grooming"):
        eng.process(
            MessageEvent(
                platform="Roblox",
                conversation_id="llm-refine",
                sender_hash=m.sender_hash,
                is_child=m.is_child,
                text=m.text,
                timestamp=m.timestamp,
            ),
            "test",
        )
        await asyncio.sleep(0)
    for _ in range(50):
        if not eng._bg:
            break
        await asyncio.sleep(0.02)
    refined = []
    while not sub[0].empty():
        _, data = await sub[0].get()
        refined.append(data["refined"])
    assert any(refined), "expected at least one LLM-refined score update"
    assert clf.client.messages.calls, "LLM should be called once risk reached MEDIUM"
    first_calls = len(clf.client.messages.calls)
    assert first_calls < len(scenario_messages("grooming"))  # not called for every message
