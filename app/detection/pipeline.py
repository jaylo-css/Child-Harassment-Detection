"""Batch analysis: runs layers 1-3 over a whole conversation and returns an Assessment.

Scoring replays the conversation through the same `ConversationScorer` the real-time engine
uses (newest window classified at each other-party message), with classification batched for
speed. The stride grid windows (default 6/3) are classified as well for the explanation panel
and window-level evaluation; with the llm/ensemble engine the LLM refines those grid windows.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.config import Settings, get_settings
from app.detection import engines
from app.detection.scorer import ConversationScorer
from app.detection.windows import grid_windows, has_other_party, window_ending_at
from app.models import Assessment, ScoredMessage, WindowResult


def analyze(
    messages: Sequence[ScoredMessage],
    conversation_id: str = "upload",
    platform: str | None = None,
    settings: Settings | None = None,
    engine: str | None = None,
) -> Assessment:
    s = settings or get_settings()
    if engine is not None and engine != s.engine:
        s = s.model_copy(update={"engine": engine})
    msgs = [m if m.index == i else m.model_copy(update={"index": i}) for i, m in enumerate(messages)]
    n, size = len(msgs), s.window_size
    base = engines.baseline(s)
    llm = engines.llm(s)
    eff_engine = s.engine if llm is not None else "baseline"

    ends = [m.index for m in msgs if not m.is_child]
    probs_by_end = dict(
        zip(ends, base.classify_batch([window_ending_at(msgs, e, size) for e in ends]), strict=True)
    )

    grid = [(a, b) for a, b in grid_windows(n, size, s.window_stride) if has_other_party(msgs[a : b + 1])]
    grid_base = base.classify_batch([msgs[a : b + 1] for a, b in grid])
    grid_results: list[WindowResult] = []
    for (a, b), bp in zip(grid, grid_base, strict=True):
        wr = WindowResult(start=a, end=b, stage_probs=bp, engine=base.name)
        if llm is not None:
            res = llm.classify_detailed(msgs[a : b + 1])
            if res is not None:
                combined = engines.combine(eff_engine, bp, res.stage_probs)
                wr = WindowResult(
                    start=a,
                    end=b,
                    stage_probs=combined,
                    engine=eff_engine,
                    rationale=res.rationale,
                    evidence_message_ids=[a + i for i in res.evidence_offsets],
                )
                prev = probs_by_end.get(b)
                probs_by_end[b] = engines.combine(eff_engine, prev, res.stage_probs) if prev else combined
        grid_results.append(wr)

    scorer = ConversationScorer(s, base, engines.detector(s), conversation_id, platform)
    for m in msgs:
        scorer.process(m, probs=probs_by_end.get(m.index))
    a = scorer.assessment(engine=eff_engine)
    a.windows = grid_results
    if grid_results and eff_engine != "baseline":
        a.top_windows = sorted(
            grid_results,
            key=lambda w: max((v for k, v in w.stage_probs.items() if k != "NONE"), default=0),
            reverse=True,
        )[:3]
    return a
