"""Static escalation resources and "report" guidance.

Human-in-the-loop only: this module never contacts anyone, files reports, or messages the other
party. It just loads the guidance shown to guardians.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@lru_cache(maxsize=2)
def load_resources(path: str) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    for region in data.get("regions", []):
        for r in region.get("resources", []):
            r.setdefault("status", "unverified")
    return data


SUGGESTED_ACTIONS = {
    "MEDIUM": [
        "Check in with your child calmly about who they are chatting with.",
        "Review privacy settings and who can message your child on this platform.",
    ],
    "HIGH": [
        "Talk with your child today, calmly and without blame.",
        "Preserve evidence (screenshots with usernames and dates) before anything is deleted.",
        "Do not contact or confront the other person.",
        "Block and report the account on the platform after saving evidence.",
    ],
    "CRITICAL": [
        "Talk with your child now, calmly and without blame.",
        "Preserve evidence (screenshots with usernames and dates) before anything is deleted.",
        "If a meeting may happen or your child may be in danger, contact the police immediately.",
        "Do not contact or confront the other person.",
        "Review the report checklist and consider reporting to the CICC (1326) or the platform.",
    ],
}


def suggested_actions(level: str) -> list[str]:
    return list(SUGGESTED_ACTIONS.get(level, []))
