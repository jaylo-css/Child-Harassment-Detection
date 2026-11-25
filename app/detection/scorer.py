"""Incremental per-conversation scorer shared by batch analysis (pipeline.py) and the real-time
engine (stream.py).

Holds: the rolling window buffer (the ONLY place raw text lives, at most `window_size`
messages), text-free observations, trajectory state, timeline, signal hits, top windows and at
most 2 redacted excerpt candidates for alerts.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any

from app.config import Settings
from app.detection.classifiers.base import StageClassifier
from app.detection.signals import SignalDetector
from app.detection.stages import STAGE_DEFS, Stage
from app.detection.trajectory import (
    Observation,
    ScoreResult,
    TrajectoryScorer,
    TrajectoryState,
    explain,
    level_rank,
)
from app.models import MAX_EXCERPTS, Assessment, ScoredMessage, SignalHit, TimelinePoint, WindowResult
from app.privacy.redact import excerpt

_AUTO = object()
MAX_WINDOW_LOG = 50


def _ts(dt: datetime | None) -> float | None:
    return dt.timestamp() if dt is not None else None


class ConversationScorer:
    def __init__(
        self,
        settings: Settings,
        classifier: StageClassifier,
        detector: SignalDetector,
        conversation_id: str,
        platform: str | None = None,
    ) -> None:
        self.settings = settings
        self.classifier = classifier
        self.detector = detector
        self.conversation_id = conversation_id
        self.platform = platform
        self.traj = TrajectoryScorer(settings.trajectory, settings.thresholds)
        self.buffer: deque[ScoredMessage] = deque(maxlen=settings.window_size)
        self.observations: list[Observation] = []
        self.state = TrajectoryState()
        self.timeline: list[TimelinePoint] = []
        self.signals: list[SignalHit] = []
        self.windows: list[WindowResult] = []
        self.excerpts: list[dict[str, Any]] = []
        self.result = ScoreResult(0.0, "LOW", {})
        self.next_index = 0

    # ------------------------------------------------------------------------------------
    @property
    def score(self) -> float:
        return self.result.score

    @property
    def level(self) -> str:
        return self.result.level

    def process(self, msg: ScoredMessage, probs: Any = _AUTO) -> TimelinePoint:
        """Score one new message. `probs` may be precomputed (batch); default classifies the
        newest window when the other party speaks."""
        if msg.index != self.next_index:
            msg = msg.model_copy(update={"index": self.next_index})
        self.next_index += 1
        self.buffer.append(msg)

        hits = self.detector.detect(msg)
        if probs is _AUTO:
            probs = None if msg.is_child else self.classifier.classify(list(self.buffer))
        if probs:
            self._log_window(self.buffer[0].index, msg.index, probs, self.classifier.name)

        ob = Observation(
            index=msg.index,
            ts=_ts(msg.timestamp),
            is_child=msg.is_child,
            probs=dict(probs) if probs else None,
            signals=[{"signal": h.signal, "stage": h.stage, "weight": h.weight} for h in hits],
        )
        self.observations.append(ob)
        self.signals.extend(hits)
        new = self.traj.step(self.state, ob)
        self.result = self.traj.score(self.state)
        point = TimelinePoint(
            index=msg.index, score=self.result.score, level=self.result.level, new_stages=new
        )
        self.timeline.append(point)
        if hits:
            self._offer_excerpt(msg, hits)
        return point

    def refine(self, index: int, probs: dict[str, float], engine: str) -> TimelinePoint | None:
        """Replace the window probabilities observed at `index` (e.g. with an async LLM result) and
        recompute the trajectory by folding the stored observations (no re-classification)."""
        for ob in self.observations:
            if ob.index == index:
                ob.probs = dict(probs)
                break
        else:
            return None
        for w in self.windows:
            if w.end == index:
                w.stage_probs = dict(probs)
                w.engine = engine
        self._refold()
        return self.timeline[-1] if self.timeline else None

    def _refold(self) -> None:
        self.state = TrajectoryState()
        self.timeline = []
        for ob in self.observations:
            new = self.traj.step(self.state, ob)
            r = self.traj.score(self.state)
            self.timeline.append(TimelinePoint(index=ob.index, score=r.score, level=r.level, new_stages=new))
        self.result = self.traj.score(self.state)

    def _log_window(self, start: int, end: int, probs: dict[str, float], engine: str) -> None:
        self.windows.append(WindowResult(start=start, end=end, stage_probs=dict(probs), engine=engine))
        if len(self.windows) > MAX_WINDOW_LOG:
            # keep the strongest windows plus the most recent ones
            keep = sorted(self.windows, key=_window_strength, reverse=True)[:10]
            recent = self.windows[-(MAX_WINDOW_LOG - 10) :]
            ids = {id(w) for w in keep}
            self.windows = keep + [w for w in recent if id(w) not in ids]
            self.windows.sort(key=lambda w: w.end)

    def _offer_excerpt(self, msg: ScoredMessage, hits: list[SignalHit]) -> None:
        best = max(hits, key=lambda h: (h.weight, Stage(h.stage).order))
        cand = {
            "index": msg.index,
            "stage": best.stage,
            "weight": best.weight,
            "text": excerpt(msg.text),  # redacted + truncated immediately
        }
        pool = self.excerpts + [cand]
        pool.sort(key=lambda c: (c["weight"], Stage(c["stage"]).order), reverse=True)
        chosen: list[dict[str, Any]] = []
        for c in pool:  # prefer distinct stages
            if all(c["stage"] != x["stage"] for x in chosen):
                chosen.append(c)
        for c in pool:
            if len(chosen) >= MAX_EXCERPTS:
                break
            if c not in chosen:
                chosen.append(c)
        self.excerpts = chosen[:MAX_EXCERPTS]

    # ------------------------------------------------------------------------------------
    def top_signals(self, k: int = 5) -> list[SignalHit]:
        best: dict[str, SignalHit] = {}
        for h in self.signals:
            if h.signal not in best or h.weight > best[h.signal].weight:
                best[h.signal] = h
        return sorted(best.values(), key=lambda h: (h.weight, Stage(h.stage).order), reverse=True)[:k]

    def top_windows(self, k: int = 3) -> list[WindowResult]:
        return sorted(self.windows, key=_window_strength, reverse=True)[:k]

    def stages_detected(self) -> list[str]:
        return list(self.result.components.get("stage_order", []))

    def first_alert_index(self) -> int | None:
        for p in self.timeline:
            if level_rank(p.level) >= 1:
                return p.index
        return None

    def assessment(self, engine: str | None = None) -> Assessment:
        tops = self.top_signals()
        return Assessment(
            conversation_id=self.conversation_id,
            platform=self.platform,
            engine=engine or self.classifier.name,
            score=self.result.score,
            level=self.result.level,
            stage_evidence={k: round(v, 3) for k, v in self.state.evidence.items()},
            stages_detected=self.stages_detected(),
            stage_first_seen=dict(self.state.first_seen),
            timeline=list(self.timeline),
            signals=list(self.signals),
            top_signals=tops,
            top_windows=self.top_windows(),
            components=self.result.components,
            explanation=explain(
                self.result,
                self.state,
                [f"{h.signal} ({STAGE_DEFS[Stage(h.stage)].label}, msg {h.message_id + 1})" for h in tops],
            ),
            first_alert_index=self.first_alert_index(),
            n_messages=self.next_index,
        )

    # -- persistence --------------------------------------------------------------------
    def to_state(self, include_buffer: bool = True) -> dict[str, Any]:
        return {
            "v": 1,
            "conversation_id": self.conversation_id,
            "platform": self.platform,
            "next_index": self.next_index,
            # Raw text: at most window_size messages; dropped entirely on eviction.
            "buffer": [m.model_dump(mode="json") for m in self.buffer] if include_buffer else [],
            "observations": [o.to_dict() for o in self.observations],
            "signals": [h.model_dump(mode="json") for h in self.signals],
            "windows": [w.model_dump(mode="json") for w in self.windows],
            "excerpts": self.excerpts,
        }

    @classmethod
    def from_state(
        cls, data: dict[str, Any], settings: Settings, classifier: StageClassifier, detector: SignalDetector
    ) -> ConversationScorer:
        sc = cls(settings, classifier, detector, data["conversation_id"], data.get("platform"))
        sc.next_index = int(data.get("next_index", 0))
        for m in data.get("buffer", []):
            sc.buffer.append(ScoredMessage.model_validate(m))
        sc.observations = [Observation.from_dict(o) for o in data.get("observations", [])]
        sc.signals = [SignalHit.model_validate(h) for h in data.get("signals", [])]
        sc.windows = [WindowResult.model_validate(w) for w in data.get("windows", [])]
        sc.excerpts = list(data.get("excerpts", []))[:MAX_EXCERPTS]
        sc._refold()
        return sc


def _window_strength(w: WindowResult) -> float:
    return max((v for k, v in w.stage_probs.items() if k != "NONE"), default=0.0)
