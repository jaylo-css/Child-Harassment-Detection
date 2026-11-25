"""Layer 3: conversation-level risk scorer (0-100).

score = min(100, base + progression_bonus + velocity_bonus), then hard floors.

* Per-stage evidence: running max of window probabilities, boosted by signal hits (noisy-OR),
  with exponential time decay (half-life `half_life_hours`, using message timestamps).
  A signal's boost is scaled down when the classifier is confident the window is NONE, so
  context (e.g. classmates swapping Discord names) can temper a keyword hit.
* base: noisy-OR over stages of severity[stage] * evidence[stage], scaled to 0-100.
* progression bonus: longest strictly-increasing subsequence (LIS) over the stage-order indices
  in first-seen order; rewards ordered advancement (FRIENDSHIP -> RISK_ASSESSMENT -> SECRECY ...).
* velocity bonus: reaching a late stage (SECRECY, PLATFORM_MOVE, ESCALATION) within few messages
  or few hours of the conversation start.
* hard floor: any ESCALATION signal together with a SECRECY or PLATFORM_MOVE signal/evidence
  -> at least HIGH.

The scorer is a pure fold over observations (no text), so batch scoring, streaming and
LLM refinement all produce the same numbers.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any

from app.config import Thresholds, TrajectoryConfig
from app.detection.stages import LATE_STAGES, RISK_STAGES, STAGE_DEFS, Stage

RISK_KEYS = [s.value for s in RISK_STAGES]
ORDER = {s.value: s.order for s in Stage}
LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def level_for(score: float, th: Thresholds) -> str:
    if score >= th.critical:
        return "CRITICAL"
    if score >= th.high:
        return "HIGH"
    if score >= th.medium:
        return "MEDIUM"
    return "LOW"


def level_rank(level: str) -> int:
    return LEVELS.index(level)


def lis_length(seq: list[int]) -> int:
    """Length of the longest strictly increasing subsequence (patience sorting, O(n log n))."""
    tails: list[int] = []
    for x in seq:
        i = bisect.bisect_left(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return len(tails)


@dataclass
class Observation:
    """Everything the scorer needs about one message - deliberately no text."""

    index: int
    ts: float | None
    is_child: bool
    probs: dict[str, float] | None = None
    signals: list[dict[str, Any]] = field(default_factory=list)  # {signal, stage, weight}

    def to_dict(self) -> dict[str, Any]:
        return {"i": self.index, "t": self.ts, "c": self.is_child, "p": self.probs, "s": self.signals}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Observation:
        return cls(d["i"], d.get("t"), d["c"], d.get("p"), d.get("s") or [])


@dataclass
class TrajectoryState:
    evidence: dict[str, float] = field(default_factory=lambda: dict.fromkeys(RISK_KEYS, 0.0))
    first_seen: dict[str, int] = field(default_factory=dict)
    first_seen_ts: dict[str, float | None] = field(default_factory=dict)
    signal_counts: dict[str, int] = field(default_factory=dict)
    start_ts: float | None = None
    last_ts: float | None = None
    n: int = 0


@dataclass
class ScoreResult:
    score: float
    level: str
    components: dict[str, Any]


class TrajectoryScorer:
    def __init__(self, cfg: TrajectoryConfig, thresholds: Thresholds) -> None:
        self.cfg = cfg
        self.th = thresholds

    # -- fold ------------------------------------------------------------------------------
    def step(self, st: TrajectoryState, ob: Observation) -> list[str]:
        """Apply one observation in place; return stages first seen at this message."""
        if ob.ts is not None:
            if st.start_ts is None:
                st.start_ts = ob.ts
            if st.last_ts is not None and ob.ts > st.last_ts:
                factor = 0.5 ** (((ob.ts - st.last_ts) / 3600.0) / self.cfg.half_life_hours)
                for k in st.evidence:
                    st.evidence[k] *= factor
            st.last_ts = ob.ts if st.last_ts is None else max(st.last_ts, ob.ts)
        st.n += 1

        p_none = None
        if ob.probs:
            p_none = ob.probs.get("NONE", 0.0)
            for k in RISK_KEYS:
                st.evidence[k] = max(st.evidence[k], ob.probs.get(k, 0.0))
        context = 1.0 if p_none is None else 0.5 + 0.5 * (1.0 - p_none)
        for hit in ob.signals:
            k = hit["stage"]
            if k not in st.evidence:
                continue
            eff = hit["weight"] * context
            st.evidence[k] = 1.0 - (1.0 - st.evidence[k]) * (1.0 - eff)
            st.signal_counts[k] = st.signal_counts.get(k, 0) + 1

        new = []
        for k in RISK_KEYS:
            if k not in st.first_seen and st.evidence[k] >= self.cfg.stage_seen_threshold:
                st.first_seen[k] = ob.index
                st.first_seen_ts[k] = ob.ts
                new.append(k)
        return new

    # -- score ------------------------------------------------------------------------------
    def score(self, st: TrajectoryState) -> ScoreResult:
        sev = self.cfg.severity
        contrib = {k: sev.get(k, 0.0) * st.evidence[k] for k in RISK_KEYS}
        remain = 1.0
        for v in contrib.values():
            remain *= 1.0 - v
        base = 100.0 * (1.0 - remain)

        seen = sorted(st.first_seen.items(), key=lambda kv: (kv[1], ORDER[kv[0]]))
        order_seq = [ORDER[k] for k, _ in seen]
        lis = lis_length(order_seq)
        presence = max((st.evidence[k] for k, _ in seen), default=0.0)
        progression = self.cfg.progression_max_bonus * max(0, lis - 1) / (len(RISK_KEYS) - 1) * presence

        velocity = 0.0
        vel_detail: dict[str, Any] = {}
        late = [k for k in st.first_seen if Stage(k) in LATE_STAGES]
        if late:
            first_late = min(late, key=lambda k: st.first_seen[k])
            idx = st.first_seen[first_late]
            v_msgs = max(0.0, 1.0 - idx / self.cfg.velocity_ref_messages)
            v_hrs = 0.0
            hrs = None
            ts = st.first_seen_ts.get(first_late)
            if ts is not None and st.start_ts is not None:
                hrs = (ts - st.start_ts) / 3600.0
                v_hrs = max(0.0, 1.0 - hrs / self.cfg.velocity_ref_hours)
            late_ev = max(st.evidence[k] for k in late)
            velocity = self.cfg.velocity_max_bonus * max(v_msgs, v_hrs) * late_ev
            vel_detail = {"stage": first_late, "message_index": idx, "hours": hrs}

        total = min(100.0, base + progression + velocity)
        floor = None
        esc_signal = st.signal_counts.get("ESCALATION", 0) > 0
        combo = any(
            st.signal_counts.get(k, 0) > 0 or k in st.first_seen for k in ("SECRECY", "PLATFORM_MOVE")
        )
        if esc_signal and combo and total < self.th.high:
            floor = "escalation+secrecy_or_platform_move"
            total = self.th.high
        level = level_for(total, self.th)
        return ScoreResult(
            score=round(total, 2),
            level=level,
            components={
                "base": round(base, 2),
                "progression_bonus": round(progression, 2),
                "velocity_bonus": round(velocity, 2),
                "lis_length": lis,
                "stage_order": [k for k, _ in seen],
                "stage_contribution": {k: round(100 * v, 1) for k, v in contrib.items() if v > 0.005},
                "velocity": vel_detail,
                "floor": floor,
            },
        )


def explain(result: ScoreResult, st: TrajectoryState, top_signal_names: list[str]) -> list[str]:
    c = result.components
    lines = [f"Risk {result.score:.0f}/100 ({result.level})."]
    for k in c["stage_order"]:
        lines.append(
            f"{STAGE_DEFS[Stage(k)].label} first detected at message {st.first_seen[k] + 1} "
            f"(current evidence {st.evidence[k]:.2f})."
        )
    if not c["stage_order"]:
        lines.append("No grooming stage has strong evidence yet.")
    if c["progression_bonus"] >= 0.5:
        lines.append(
            f"{c['lis_length']} stages appeared in the typical grooming order, adding "
            f"{c['progression_bonus']:.0f} points."
        )
    if c["velocity_bonus"] >= 0.5:
        v = c["velocity"]
        when = f"message {v['message_index'] + 1}"
        if v.get("hours") is not None:
            when += f" ({v['hours']:.1f} h after the first message)"
        lines.append(
            f"Late-stage behavior ({STAGE_DEFS[Stage(v['stage'])].label}) appeared quickly, at {when}, adding "
            f"{c['velocity_bonus']:.0f} points."
        )
    if c["floor"]:
        lines.append(
            "Safety floor applied: an escalation signal together with secrecy or a platform move sets the "
            "level to at least HIGH."
        )
    if top_signal_names:
        lines.append("Strongest signals: " + ", ".join(top_signal_names) + ".")
    return lines
