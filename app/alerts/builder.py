"""Build the guardian alert from scorer state / an Assessment.

Hard limits (safety rule 4): risk level, detected stages, and at most 2 redacted excerpts of at
most 120 characters each. Never a transcript. Excerpts are re-redacted here as defense in depth,
and anything still looking like PII is dropped.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.alerts.escalation import suggested_actions
from app.detection.stages import STAGE_DEFS, Stage
from app.models import MAX_EXCERPT_CHARS, MAX_EXCERPTS, AlertPayload
from app.privacy.redact import contains_pii, excerpt


def safe_excerpts(candidates: Iterable[Any], known_names: Iterable[str] = ()) -> list[str]:
    out: list[str] = []
    for c in candidates:
        text = c["text"] if isinstance(c, dict) else str(c)
        e = excerpt(text, MAX_EXCERPT_CHARS, known_names)
        if e and not contains_pii(e) and e not in out:
            out.append(e)
        if len(out) >= MAX_EXCERPTS:
            break
    return out


def summarize(level: str, stages: list[str], platform: str | None) -> str:
    where = f" on {platform}" if platform else ""
    if not stages:
        return f"{level.title()} risk detected in a conversation{where}."
    latest = STAGE_DEFS[Stage(stages[-1])].guardian_text
    return f"{level.title()} risk in a conversation{where}. {latest}"


def build_alert(
    conversation_id: str,
    platform: str | None,
    level: str,
    score: float,
    stages: list[str],
    excerpt_candidates: Iterable[Any] = (),
    known_names: Iterable[str] = (),
) -> AlertPayload:
    stages = [s for s in stages if s in Stage.__members__ and s != "NONE"]
    return AlertPayload(
        conversation_id=conversation_id,
        platform=platform,
        level=level,
        score=round(float(score), 1),
        stages=stages,
        stage_labels=[STAGE_DEFS[Stage(s)].label for s in stages],
        excerpts=safe_excerpts(excerpt_candidates, known_names),
        suggested_actions=suggested_actions(level),
        summary=summarize(level, stages, platform),
    )
