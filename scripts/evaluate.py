"""Evaluate SafeChat Guardian on the synthetic test split (and PAN-2012 if present).

Per engine (baseline / llm / ensemble; llm-based engines are skipped without ANTHROPIC_API_KEY):
  * conversation-level precision / recall / F1 at the MEDIUM and HIGH thresholds
    (a conversation is "flagged" if its score ever reached the level, i.e. an alert would fire)
  * false-positive rate on benign and hard-negative conversations, separately
  * early detection: median message index of the first MEDIUM+ alert as % of conversation length
  * latency-weighted F1 (Sadeque et al. 2018, as used in early-risk / PAN-style evaluation)
  * window-level stage confusion matrix on the stride grid
  * five worst false positives / false negatives as IDs and stage labels only (never text)

PAN-2012 (optional, user-provided in data/pan2012/): metrics only, no text is ever printed.

Usage: python scripts/evaluate.py [--split test] [--engines baseline,llm,ensemble] [--pan-limit N]
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.detection.pipeline import analyze  # noqa: E402
from app.detection.stages import ALL_STAGES, STAGE_ORDER, Stage  # noqa: E402
from app.detection.trajectory import level_rank  # noqa: E402
from app.ingest import pan2012  # noqa: E402
from app.ingest.synthetic import load_split, window_labels  # noqa: E402

STAGES = [s.value for s in ALL_STAGES]
ABBR = {
    "NONE": "NONE",
    "FRIENDSHIP": "FRND",
    "RISK_ASSESSMENT": "RISK",
    "TRUST_EXCLUSIVITY": "TRST",
    "ISOLATION": "ISOL",
    "SECRECY": "SECR",
    "PLATFORM_MOVE": "PLAT",
    "ESCALATION": "ESCL",
}


@dataclass
class ConvResult:
    id: str
    label: str
    category: str
    max_stage: str
    n: int
    max_score: float
    max_level: str
    first_medium: int | None
    stages_detected: list[str]


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def latency_penalty(k: int, p: float) -> float:
    """Sadeque et al. (2018): 0 at the first message, rising towards 1 as k grows."""
    return -1.0 + 2.0 / (1.0 + math.exp(-p * (k - 1)))


def latency_f1(results: list[ConvResult], threshold: str = "MEDIUM") -> float:
    pos = [r for r in results if r.label == "grooming"]
    neg = [r for r in results if r.label != "grooming"]
    tp_rs = [r for r in pos if level_rank(r.max_level) >= level_rank(threshold)]
    fp = sum(level_rank(r.max_level) >= level_rank(threshold) for r in neg)
    _, _, f1 = prf(len(tp_rs), fp, len(pos) - len(tp_rs))
    if not tp_rs:
        return 0.0
    med_len = statistics.median(r.n for r in results)
    p = math.log(3) / max(1.0, med_len - 1)  # penalty 0.5 at the median conversation length
    speed = statistics.median(1 - latency_penalty((r.first_medium or 0) + 1, p) for r in tp_rs)
    return f1 * speed


def run_engine(convs, engine: str):
    s = get_settings()
    results: list[ConvResult] = []
    confusion: Counter = Counter()
    t0 = time.time()
    for c in convs:
        a = analyze(c.messages, c.id, c.platform, engine=engine)
        peak = max(a.timeline, key=lambda p: p.score) if a.timeline else None
        results.append(
            ConvResult(
                id=c.id,
                label=c.label,
                category=c.category,
                max_stage=c.max_stage,
                n=len(c.messages),
                max_score=peak.score if peak else 0.0,
                max_level=peak.level if peak else "LOW",
                first_medium=a.first_alert_index,
                stages_detected=a.stages_detected,
            )
        )
        for w in a.windows:
            gold = max(window_labels(c, w.start, w.end), key=lambda st: STAGE_ORDER[Stage(st)])
            pred = max(w.stage_probs, key=lambda k: w.stage_probs[k])
            confusion[(gold, pred)] += 1
    secs = time.time() - t0
    return results, confusion, secs, s


def metrics(results: list[ConvResult]) -> dict:
    out: dict = {}
    pos = [r for r in results if r.label == "grooming"]
    for th in ("MEDIUM", "HIGH"):
        flagged = lambda r, th=th: level_rank(r.max_level) >= level_rank(th)  # noqa: E731
        tp = sum(flagged(r) for r in pos)
        fp = sum(flagged(r) for r in results if r.label != "grooming")
        fn = len(pos) - tp
        p, r_, f = prf(tp, fp, fn)
        out[th] = {"precision": p, "recall": r_, "f1": f, "tp": tp, "fp": fp, "fn": fn}
        for lab in ("benign", "hard_negative"):
            group = [r for r in results if r.label == lab]
            out[th][f"fpr_{lab}"] = sum(flagged(r) for r in group) / len(group) if group else 0.0
    early = [r.first_medium / r.n for r in pos if r.first_medium is not None]
    out["early_median_pct"] = 100 * statistics.median(early) if early else None
    out["early_n"] = len(early)
    out["latency_f1"] = latency_f1(results)
    depth: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in pos:
        depth[r.max_stage][1] += 1
        depth[r.max_stage][0] += level_rank(r.max_level) >= level_rank("MEDIUM")
    out["recall_by_depth"] = dict(depth)
    return out


def confusion_md(conf: Counter) -> str:
    head = "| gold \\ pred | " + " | ".join(ABBR[s] for s in STAGES) + " |"
    sep = "|---|" + "---|" * len(STAGES)
    rows = [head, sep]
    for g in STAGES:
        rows.append(f"| **{ABBR[g]}** | " + " | ".join(str(conf.get((g, p), 0)) for p in STAGES) + " |")
    total = sum(conf.values())
    correct = sum(conf.get((s, s), 0) for s in STAGES)
    rows.append("")
    rows.append(
        f"Window accuracy (argmax vs. highest-order gold stage): {correct}/{total} = {correct / max(1, total):.3f}"
    )
    return "\n".join(rows)


def worst_md(results: list[ConvResult]) -> str:
    fps = sorted((r for r in results if r.label != "grooming"), key=lambda r: -r.max_score)[:5]
    fns = sorted((r for r in results if r.label == "grooming"), key=lambda r: r.max_score)[:5]
    lines = ["**Worst false positives** (highest-scoring non-grooming conversations)", ""]
    lines += [
        "| id | label | category | peak score | peak level | stages detected |",
        "|---|---|---|---|---|---|",
    ]
    for r in fps:
        lines.append(
            f"| {r.id} | {r.label} | {r.category} | {r.max_score:.1f} | {r.max_level} | {', '.join(r.stages_detected) or '-'} |"
        )
    lines += ["", "**Worst false negatives** (lowest-scoring grooming conversations)", ""]
    lines += [
        "| id | true max stage | category | peak score | peak level | stages detected |",
        "|---|---|---|---|---|---|",
    ]
    for r in fns:
        lines.append(
            f"| {r.id} | {r.max_stage} | {r.category} | {r.max_score:.1f} | {r.max_level} | {', '.join(r.stages_detected) or '-'} |"
        )
    return "\n".join(lines)


def pan_eval(engine: str, limit: int | None) -> str | None:
    """PAN-2012: conversation flagged if any author, scored as the suspect, reaches the level."""
    if not pan2012.available():
        return None
    results: list[ConvResult] = []
    for conv in pan2012.iter_conversations(limit=limit):
        best = None
        for author in conv.authors[:4]:
            a = analyze(conv.as_scored(author), conv.id, engine=engine)
            peak = max(a.timeline, key=lambda p: p.score)
            if best is None or peak.score > best[0]:
                best = (peak.score, peak.level, a.first_alert_index)
        results.append(
            ConvResult(
                conv.id,
                "grooming" if conv.has_predator else "benign",
                "pan",
                "?",
                len(conv.raw),
                best[0],
                best[1],
                best[2],
                [],
            )
        )
    m = metrics(results)
    lines = [
        f"### PAN-2012 ({len(results)} conversations, {sum(r.label == 'grooming' for r in results)} with a predator)",
        "",
    ]
    lines += ["| threshold | precision | recall | F1 | FPR |", "|---|---|---|---|---|"]
    for th in ("MEDIUM", "HIGH"):
        x = m[th]
        lines.append(
            f"| {th} | {x['precision']:.3f} | {x['recall']:.3f} | {x['f1']:.3f} | {x['fpr_benign']:.3f} |"
        )
    lines.append("")
    lines.append(f"Latency-weighted F1 (MEDIUM): {m['latency_f1']:.3f}")
    lines.append("")
    lines.append("PAN results are reported as metrics only; no conversation text is printed or stored.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--engines", default="baseline,llm,ensemble")
    ap.add_argument("--pan-limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "reports")
    args = ap.parse_args()
    s = get_settings()
    convs = load_split(args.split)
    labels = Counter(c.label for c in convs)
    sections = [
        "# SafeChat Guardian evaluation",
        "",
        f"Generated {time.strftime('%Y-%m-%d %H:%M')} on the synthetic **{args.split}** split: "
        f"{len(convs)} conversations ({labels['benign']} benign, {labels['hard_negative']} hard negatives, "
        f"{labels['grooming']} grooming). Thresholds: MEDIUM {s.thresholds.medium:.0f}, HIGH {s.thresholds.high:.0f}, "
        f"CRITICAL {s.thresholds.critical:.0f}.",
        "",
        "A conversation counts as flagged at a level if its score reached that level at any message "
        "(i.e. an alert would have fired). About 25% of templates are held out of training and appear "
        "only in the test split. The test set is small, so treat differences of a few points as noise.",
        "",
    ]
    summary_rows = []
    for engine in [e.strip() for e in args.engines.split(",") if e.strip()]:
        if engine != "baseline" and not s.llm_available:
            sections += [
                f"## Engine: {engine}",
                "",
                "Skipped: no ANTHROPIC_API_KEY set (the app runs fully offline).",
                "",
            ]
            summary_rows.append(f"| {engine} | skipped | | | | | | | |")
            continue
        results, conf, secs, _ = run_engine(convs, engine)
        m = metrics(results)
        md, hi = m["MEDIUM"], m["HIGH"]
        early = f"{m['early_median_pct']:.0f}%" if m["early_median_pct"] is not None else "n/a"
        summary_rows.append(
            f"| {engine} | {md['precision']:.3f} | {md['recall']:.3f} | {md['f1']:.3f} | {hi['precision']:.3f} | "
            f"{hi['recall']:.3f} | {hi['f1']:.3f} | {early} | {m['latency_f1']:.3f} |"
        )
        sections += [
            f"## Engine: {engine}",
            "",
            f"Scored {len(convs)} conversations in {secs:.1f} s.",
            "",
            "| threshold | precision | recall | F1 | TP | FP | FN | FPR benign | FPR hard negatives |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for th in ("MEDIUM", "HIGH"):
            x = m[th]
            sections.append(
                f"| {th} | {x['precision']:.3f} | {x['recall']:.3f} | {x['f1']:.3f} | {x['tp']} | {x['fp']} | {x['fn']} | "
                f"{x['fpr_benign']:.3f} | {x['fpr_hard_negative']:.3f} |"
            )
        sections += [
            "",
            f"**Early detection:** median first MEDIUM+ alert at **{early}** of the conversation length "
            f"(over {m['early_n']} detected grooming conversations). Latency-weighted F1 (MEDIUM): "
            f"**{m['latency_f1']:.3f}**.",
            "",
            "**Recall at MEDIUM by how far the grooming trajectory went** (early-stopping conversations are "
            "deliberately hard):",
            "",
            "| true max stage | detected / total |",
            "|---|---|",
        ]
        for st in STAGES[1:]:
            if st in m["recall_by_depth"]:
                d, t = m["recall_by_depth"][st]
                sections.append(f"| {st} | {d} / {t} |")
        sections += [
            "",
            "### Window-level stage confusion matrix",
            "",
            confusion_md(conf),
            "",
            worst_md(results),
            "",
        ]
        pan = pan_eval(engine, args.pan_limit)
        sections += [pan if pan else "_PAN-2012 not present in data/pan2012/; skipped._", ""]

    table = [
        "## Summary",
        "",
        "| engine | P@MED | R@MED | F1@MED | P@HIGH | R@HIGH | F1@HIGH | median first alert | latency F1 |",
        "|---|---|---|---|---|---|---|---|---|",
        *summary_rows,
        "",
    ]
    report = "\n".join(sections[:6] + table + sections[6:])
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"eval_{args.split}_{time.strftime('%Y%m%d')}.md"
    path.write_text(report, encoding="utf-8")
    (args.out / "latest.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
