"""Optional LLM rubric classifier (Claude via the official `anthropic` SDK).

* PII is redacted from the window BEFORE anything is sent.
* Strict JSON comes back through structured outputs (`output_config.format` json_schema):
  {stage_probs, rationale (<= 2 sentences), evidence_message_ids}.
* The system prompt frames the task as child-safety classification and forbids reproducing or
  extending explicit content; the rationale is further redacted and truncated locally.
* Results are cached by window hash. Any API error, refusal, truncation or invalid output
  returns None, and callers fall back to the baseline classifier. After repeated failures the
  classifier backs off for a minute (circuit breaker) instead of hammering the API.
* Refusals: on current-generation models the server-side `fallbacks: "default"` option is
  enabled, so a safety decline is retried on a suitable model inside the same call. If the
  final response is still a refusal, we fall back to the baseline.

Model: GUARDIAN_LLM_MODEL (default claude-sonnet-5-5). Key: ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.detection.classifiers.base import StageClassifier, StageProbs, normalize_probs
from app.detection.stages import ALL_STAGES, STAGE_DEFS
from app.detection.windows import window_hash
from app.models import ScoredMessage
from app.privacy.redact import redact

log = logging.getLogger(__name__)

# Models that accept `output_config.effort` and the server-side `fallbacks: "default"` form.
_CURRENT_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are a child-safety classifier inside SafeChat Guardian, a parental-protection tool. \
You read a short window of an online chat between a child and another person and estimate, for each \
grooming stage in the rubric, the probability that the OTHER person's messages show that stage. \
The child's messages are context only.

Rules:
- This is detection only. Never write messages to anyone, never suggest replies, never role-play.
- Never reproduce, continue, paraphrase in detail, or elaborate on sexual or explicit content. If the chat \
contains such content, classify it (it is an ESCALATION signal) and describe it only in neutral, \
non-graphic terms such as "requests a private photo".
- Do not quote the chat in the rationale. Keep the rationale to at most two short, neutral sentences.
- Personal data has been replaced with placeholders like [NAME] or [PHONE]; do not try to infer it.
- Stages are independent: several can be present, and NONE means ordinary chat. Be calibrated: ordinary \
gaming chat, classmates sharing usernames, coaches or teachers with legitimate logistics, and parents \
checking in are NOT grooming.

Rubric:
{rubric}
"""


def _rubric() -> str:
    lines = []
    for st in ALL_STAGES:
        d = STAGE_DEFS[st]
        lines.append(f"- {st.value}: {d.description} Example signals: {'; '.join(d.example_signals)}.")
    return "\n".join(lines)


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "stage_probs": {
                "type": "object",
                "properties": {s.value: {"type": "number"} for s in ALL_STAGES},
                "required": [s.value for s in ALL_STAGES],
                "additionalProperties": False,
            },
            "rationale": {"type": "string"},
            "evidence_message_ids": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["stage_probs", "rationale", "evidence_message_ids"],
        "additionalProperties": False,
    }


@dataclass
class LLMResult:
    stage_probs: StageProbs
    rationale: str
    evidence_offsets: list[int] = field(default_factory=list)  # 0-based offsets within the window


def render_window(window: Sequence[ScoredMessage]) -> str:
    rows = []
    for i, m in enumerate(window):
        who = "CHILD" if m.is_child else "OTHER"
        rows.append(f"[{i}] {who}: {redact(m.text)}")
    return "Chat window (ids in brackets):\n" + "\n".join(rows)


class LLMRubricClassifier:
    name = "llm"

    def __init__(
        self,
        model: str,
        api_key: str | None,
        fallback: StageClassifier,
        client: Any | None = None,
        timeout: float = 20.0,
        max_retries: int = 2,
        cache_size: int = 4096,
        breaker_threshold: int = 3,
        breaker_seconds: float = 60.0,
    ) -> None:
        if client is None:
            import anthropic  # optional dependency

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.client = client
        self.model = model
        self.fallback = fallback
        self.system = SYSTEM_PROMPT.format(rubric=_rubric())
        self.schema = _schema()
        self._cache: OrderedDict[str, LLMResult] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0
        self._breaker_threshold = breaker_threshold
        self._breaker_seconds = breaker_seconds
        self.calls = 0

    # -- StageClassifier protocol (falls back to baseline) ------------------------------
    def classify(self, window: Sequence[ScoredMessage]) -> StageProbs:
        res = self.classify_detailed(window)
        return res.stage_probs if res is not None else self.fallback.classify(window)

    def classify_batch(self, windows: Sequence[Sequence[ScoredMessage]]) -> list[StageProbs]:
        return [self.classify(w) for w in windows]

    # -- core -------------------------------------------------------------------------------
    def classify_detailed(self, window: Sequence[ScoredMessage]) -> LLMResult | None:
        if not window:
            return None
        key = window_hash(window)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            if time.monotonic() < self._open_until:
                return None
        res = self._call(window)
        with self._lock:
            if res is None:
                self._failures += 1
                if self._failures >= self._breaker_threshold:
                    self._open_until = time.monotonic() + self._breaker_seconds
                    self._failures = 0
                    log.warning("LLM engine paused for %.0fs after repeated failures", self._breaker_seconds)
                return None
            self._failures = 0
            self._cache[key] = res
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return res

    def _request(self, window: Sequence[ScoredMessage]):
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": self.schema}}
        common = {
            "model": self.model,
            "max_tokens": 1024,
            "system": self.system,
            "messages": [{"role": "user", "content": render_window(window)}],
        }
        self.calls += 1
        if self.model in _CURRENT_MODELS:
            output_config["effort"] = "low"  # classification: low effort is enough
            return self.client.beta.messages.create(
                **common, output_config=output_config, betas=[_FALLBACK_BETA], fallbacks="default"
            )
        return self.client.messages.create(**common, output_config=output_config)

    def _call(self, window: Sequence[ScoredMessage]) -> LLMResult | None:
        try:
            import anthropic
        except ImportError:  # pragma: no cover
            anthropic = None  # type: ignore[assignment]
        try:
            resp = self._request(window)
        except Exception as exc:  # noqa: BLE001 - every failure means "use the baseline"
            if anthropic is not None and isinstance(exc, anthropic.RateLimitError):
                log.warning("LLM rate limited; using baseline")
            elif anthropic is not None and isinstance(exc, anthropic.APIStatusError):
                log.warning("LLM API error %s; using baseline", exc.status_code)
            elif anthropic is not None and isinstance(exc, anthropic.APIConnectionError):
                log.warning("LLM connection/timeout error; using baseline")
            else:
                log.warning("LLM call failed (%s); using baseline", type(exc).__name__)
            return None

        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            log.warning("LLM declined to classify a window; using baseline")
            return None
        if stop == "max_tokens":
            log.warning("LLM output truncated; using baseline")
            return None
        text = next(
            (b.text for b in getattr(resp, "content", []) if getattr(b, "type", None) == "text"), None
        )
        if not text:
            return None
        try:
            data = json.loads(text)
            probs = normalize_probs(data["stage_probs"])
            rationale = str(data.get("rationale", ""))
            ids = [int(i) for i in data.get("evidence_message_ids", []) if 0 <= int(i) < len(window)]
        except (ValueError, KeyError, TypeError):
            log.warning("LLM returned invalid JSON; using baseline")
            return None
        rationale = " ".join(redact(rationale).split())[:300]
        return LLMResult(stage_probs=probs, rationale=rationale, evidence_offsets=ids)
