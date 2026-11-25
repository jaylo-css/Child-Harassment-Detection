"""Load the labeled synthetic corpus produced by scripts/generate_synthetic.py."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.config import ROOT
from app.ingest.parsers import parse_timestamp
from app.models import ScoredMessage
from app.privacy.hashing import hash_handle

SYNTH_DIR = ROOT / "data" / "synthetic"


@dataclass
class LabeledConversation:
    id: str
    platform: str
    label: str  # benign | hard_negative | grooming
    category: str
    max_stage: str
    speed: str
    messages: list[ScoredMessage]
    stage_labels: list[str]  # per message (other party); child messages are NONE

    @property
    def is_grooming(self) -> bool:
        return self.label == "grooming"


def iter_split(split: str, root: Path = SYNTH_DIR) -> Iterator[LabeledConversation]:
    path = root / f"{split}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - run `make seed` first")
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield from_record(json.loads(line))


def load_split(split: str, root: Path = SYNTH_DIR) -> list[LabeledConversation]:
    return list(iter_split(split, root))


def from_record(rec: dict) -> LabeledConversation:
    msgs = [
        ScoredMessage(
            index=m["idx"],
            sender_hash=hash_handle(m["sender"]),
            is_child=bool(m["is_child"]),
            text=m["text"],
            timestamp=parse_timestamp(m.get("timestamp")),
        )
        for m in rec["messages"]
    ]
    return LabeledConversation(
        id=rec["id"],
        platform=rec.get("platform", "unknown"),
        label=rec["label"],
        category=rec.get("category", ""),
        max_stage=rec.get("max_stage", "NONE"),
        speed=rec.get("speed", "n/a"),
        messages=msgs,
        stage_labels=[m.get("stage", "NONE") for m in rec["messages"]],
    )


def window_labels(conv: LabeledConversation, start: int, end: int) -> set[str]:
    stages = {
        conv.stage_labels[i]
        for i in range(start, end + 1)
        if not conv.messages[i].is_child and conv.stage_labels[i] != "NONE"
    }
    return stages or {"NONE"}
