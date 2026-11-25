"""Server-rendered inline SVG: risk gauge and score timeline (no JS charting library)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from html import escape

from markupsafe import Markup

from app.config import Thresholds
from app.detection.stages import STAGE_DEFS, Stage
from app.models import TimelinePoint

STAGE_ABBR = {
    "FRIENDSHIP": "F",
    "RISK_ASSESSMENT": "R",
    "TRUST_EXCLUSIVITY": "T",
    "ISOLATION": "I",
    "SECRECY": "S",
    "PLATFORM_MOVE": "P",
    "ESCALATION": "E",
}


def _polar(cx: float, cy: float, r: float, frac: float) -> tuple[float, float]:
    ang = math.pi * (1 - frac)
    return cx + r * math.cos(ang), cy - r * math.sin(ang)


def gauge_svg(score: float, level: str, size: int = 220) -> Markup:
    cx, cy, r = size / 2, size * 0.55, size * 0.4
    frac = max(0.0, min(1.0, score / 100))
    x0, y0 = _polar(cx, cy, r, 0)
    x1, y1 = _polar(cx, cy, r, 1)
    xs, ys = _polar(cx, cy, r, frac)
    large = 0
    svg = f"""<svg class="gauge" viewBox="0 0 {size} {size * 0.7:.0f}" role="img"
  aria-label="Risk {score:.0f} of 100, level {escape(level)}">
  <path d="M{x0:.1f},{y0:.1f} A{r:.1f},{r:.1f} 0 0 1 {x1:.1f},{y1:.1f}" class="gauge-track"/>
  <path d="M{x0:.1f},{y0:.1f} A{r:.1f},{r:.1f} 0 {large} 1 {xs:.1f},{ys:.1f}" class="gauge-fill lvl-{escape(level)}"/>
  <text x="{cx:.0f}" y="{cy - 6:.0f}" class="gauge-score">{score:.0f}</text>
  <text x="{cx:.0f}" y="{cy + 18:.0f}" class="gauge-level">{escape(level)}</text>
</svg>"""
    return Markup(svg)


def timeline_svg(
    points: Sequence[TimelinePoint],
    thresholds: Thresholds,
    width: int = 640,
    height: int = 200,
) -> Markup:
    pad_l, pad_r, pad_t, pad_b = 34, 12, 14, 26
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    n = max(1, len(points) - 1)

    def x(i: int) -> float:
        return pad_l + w * (i / n if len(points) > 1 else 0.5)

    def y(v: float) -> float:
        return pad_t + h * (1 - v / 100)

    parts = [
        f'<svg class="timeline" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Risk score over {len(points)} messages">'
    ]
    for label, v in (
        ("MEDIUM", thresholds.medium),
        ("HIGH", thresholds.high),
        ("CRITICAL", thresholds.critical),
    ):
        parts.append(
            f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="th th-{label}"/>'
            f'<text x="{pad_l - 4}" y="{y(v) + 4:.1f}" class="axis" text-anchor="end">{v:.0f}</text>'
        )
    parts.append(
        f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="axis-line"/>'
    )
    if points:
        path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(p.score):.1f}" for i, p in enumerate(points))
        parts.append(f'<path d="{path}" class="tl-line"/>')
        for i, p in enumerate(points):
            for st in p.new_stages:
                label = escape(STAGE_DEFS[Stage(st)].label)
                parts.append(
                    f'<g class="marker"><line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{pad_t}" y2="{y(0):.1f}" '
                    f'class="stage-line"/><circle cx="{x(i):.1f}" cy="{y(p.score):.1f}" r="5" '
                    f'class="stage-dot"><title>{label} (message {p.index + 1})</title></circle>'
                    f'<text x="{x(i):.1f}" y="{pad_t - 2}" class="stage-abbr" text-anchor="middle">'
                    f"{STAGE_ABBR.get(st, '?')}</text></g>"
                )
        last = points[-1]
        parts.append(
            f'<circle cx="{x(len(points) - 1):.1f}" cy="{y(last.score):.1f}" r="3.5" class="tl-last lvl-{last.level}"/>'
        )
    parts.append(
        f'<text x="{pad_l}" y="{height - 6}" class="axis">msg 1</text>'
        f'<text x="{width - pad_r}" y="{height - 6}" class="axis" text-anchor="end">msg {len(points)}</text>'
    )
    parts.append("</svg>")
    return Markup("".join(parts))


def stage_legend() -> list[tuple[str, str]]:
    return [(STAGE_ABBR[s.value], s.label) for s in Stage if s is not Stage.NONE]
