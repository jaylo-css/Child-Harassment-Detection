"""Stage classifier protocol shared by the baseline, LLM-rubric and ensemble engines."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.detection.stages import ALL_STAGES, Stage
from app.models import ScoredMessage

StageProbs = dict[str, float]


@runtime_checkable
class StageClassifier(Protocol):
    name: str

    def classify(self, window: Sequence[ScoredMessage]) -> StageProbs:
        """Return a probability per stage (keys: every Stage value, including NONE)."""
        ...

    def classify_batch(self, windows: Sequence[Sequence[ScoredMessage]]) -> list[StageProbs]: ...


def none_probs() -> StageProbs:
    return {s.value: (1.0 if s is Stage.NONE else 0.0) for s in ALL_STAGES}


def normalize_probs(raw: dict[str, float]) -> StageProbs:
    """Fill missing stages with 0, clip to [0, 1]. Stages are independent (multi-label)."""
    out = {s.value: 0.0 for s in ALL_STAGES}
    for k, v in raw.items():
        key = str(k).upper()
        if key in out:
            try:
                out[key] = min(1.0, max(0.0, float(v)))
            except (TypeError, ValueError):
                continue
    return out


def average_probs(a: StageProbs, b: StageProbs) -> StageProbs:
    return {k: (a.get(k, 0.0) + b.get(k, 0.0)) / 2.0 for k in a.keys() | b.keys()}


class NullClassifier:
    """Used when no trained model exists: scoring falls back to Layer-1 signals only.

    Returns an empty dict ("no opinion"), which the trajectory scorer treats as absent evidence
    (signals then keep their full weight)."""

    name = "signals-only"

    def classify(self, window: Sequence[ScoredMessage]) -> StageProbs:
        return {}

    def classify_batch(self, windows: Sequence[Sequence[ScoredMessage]]) -> list[StageProbs]:
        return [{} for _ in windows]
