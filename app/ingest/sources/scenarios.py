"""Built-in simulator scenarios (synthetic, non-explicit)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from app.models import ScoredMessage

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"


@lru_cache(maxsize=16)
def load_scenario(name: str) -> dict:
    if not name.replace("_", "").isalnum():
        raise KeyError(name)
    path = SCENARIO_DIR / f"{name}.json"
    if not path.exists():
        raise KeyError(name)
    return json.loads(path.read_text(encoding="utf-8"))


def list_scenarios() -> list[dict]:
    out = []
    for p in sorted(SCENARIO_DIR.glob("*.json")):
        d = load_scenario(p.stem)
        out.append({k: d[k] for k in ("name", "title", "platform", "description")})
    order = {"benign": 0, "borderline": 1, "grooming": 2}
    return sorted(out, key=lambda d: order.get(d["name"], 9))


def scenario_messages(name: str, start: datetime | None = None) -> list[ScoredMessage]:
    d = load_scenario(name)
    t = start or datetime(2025, 1, 1, 18, 0, 0)
    out = []
    for i, m in enumerate(d["messages"]):
        t += timedelta(seconds=float(m.get("delay", 2)) * 10)
        out.append(
            ScoredMessage(
                index=i,
                sender_hash="child" if m["as"] == "child" else "stranger",
                is_child=m["as"] == "child",
                text=m["text"],
                timestamp=t,
            )
        )
    return out
