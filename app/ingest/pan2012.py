"""Loader for the PAN-2012 Sexual Predator Identification corpus (user-provided, optional).

The corpus is NOT bundled or downloaded by this project. Place the files you obtained from the
PAN organizers under data/pan2012/ (that directory is .gitignored).

SAFETY: the corpus contains explicit text. This module must never print, log or render message
text. Public functions return objects for in-memory scoring and `summary()` returns counts only.
Do not add debugging prints of message content here or in any caller.

Expected files (names as distributed by PAN; matched by glob):
  *.xml                                  conversations
  *predators*.txt / *groundtruth-problem1*.txt   one predator author id per line
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.config import ROOT
from app.models import ScoredMessage
from app.privacy.hashing import hash_handle

PAN_DIR = ROOT / "data" / "pan2012"
log = logging.getLogger(__name__)


@dataclass
class PanConversation:
    id: str
    authors: list[str]
    predator_authors: set[str]
    raw: list[tuple[str, str]] = field(repr=False)  # (author, text) - never print

    @property
    def has_predator(self) -> bool:
        return bool(self.predator_authors)

    def __repr__(self) -> str:  # never expose text through repr
        return f"PanConversation(id={self.id!r}, n={len(self.raw)}, predator={self.has_predator})"

    def as_scored(self, suspect: str) -> list[ScoredMessage]:
        """View with `suspect` as the other party and every other author as the child side."""
        return [
            ScoredMessage(index=i, sender_hash=hash_handle(a), is_child=(a != suspect), text=t)
            for i, (a, t) in enumerate(self.raw)
        ]


def available(root: Path = PAN_DIR) -> bool:
    return root.exists() and any(root.rglob("*.xml"))


def _predators(root: Path) -> set[str]:
    ids: set[str] = set()
    for pattern in ("*predators*.txt", "*groundtruth-problem1*.txt"):
        for p in root.rglob(pattern):
            ids.update(
                line.strip()
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip()
            )
    return ids


def _check_no_dtd(path: Path) -> None:
    head = path.open("rb").read(4096)
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ValueError(f"{path.name}: DTD/entities not allowed")


def iter_conversations(root: Path = PAN_DIR, limit: int | None = None) -> Iterator[PanConversation]:
    predators = _predators(root)
    n = 0
    for xml_path in sorted(root.rglob("*.xml")):
        _check_no_dtd(xml_path)
        try:
            for _, elem in ET.iterparse(xml_path, events=("end",)):
                if elem.tag != "conversation":
                    continue
                raw: list[tuple[str, str]] = []
                for msg in elem.findall("message"):
                    author = (msg.findtext("author") or "").strip()
                    text = msg.findtext("text") or ""
                    if author:
                        raw.append((author, text))
                elem.clear()
                if not raw:
                    continue
                authors = list(dict.fromkeys(a for a, _ in raw))
                yield PanConversation(
                    id=elem.get("id", f"pan-{n}"),
                    authors=authors,
                    predator_authors={a for a in authors if a in predators},
                    raw=raw,
                )
                n += 1
                if limit is not None and n >= limit:
                    return
        except ET.ParseError as exc:  # report position only, never content
            log.warning("PAN XML parse error in %s at %s", xml_path.name, getattr(exc, "position", "?"))


def summary(root: Path = PAN_DIR) -> dict[str, int]:
    convs = pred = msgs = 0
    for c in iter_conversations(root):
        convs += 1
        pred += c.has_predator
        msgs += len(c.raw)
    return {"conversations": convs, "with_predator": pred, "messages": msgs}
