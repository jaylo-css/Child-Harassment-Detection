"""Sliding windows over a conversation.

* Scoring (batch and streaming) classifies the *newest* window - the last `size` messages - each
  time the other party sends a message. Batch replays use exactly the same windows, so streaming
  and batch scores agree.
* The stride grid (default size 6, stride 3) is used for the explanation panel and for
  window-level evaluation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from app.models import ScoredMessage

CHILD_MARK = "[child]"


def newest_window(messages: Sequence[ScoredMessage], size: int) -> list[ScoredMessage]:
    return list(messages[-size:])


def window_ending_at(messages: Sequence[ScoredMessage], end: int, size: int) -> list[ScoredMessage]:
    return list(messages[max(0, end - size + 1) : end + 1])


def grid_windows(n: int, size: int, stride: int) -> list[tuple[int, int]]:
    """Inclusive (start, end) index pairs; the final partial window is always included."""
    if n <= 0:
        return []
    if n <= size:
        return [(0, n - 1)]
    out = [(s, s + size - 1) for s in range(0, n - size + 1, stride)]
    if out[-1][1] != n - 1:
        out.append((n - size, n - 1))
    return out


def split_text(window: Sequence[ScoredMessage]) -> tuple[str, str]:
    """(other-party text, child context text) - classifier features keep them separate."""
    other = " \n ".join(m.text for m in window if not m.is_child)
    child = " \n ".join(m.text for m in window if m.is_child)
    return other, child


def has_other_party(window: Sequence[ScoredMessage]) -> bool:
    return any(not m.is_child for m in window)


def window_hash(window: Sequence[ScoredMessage]) -> str:
    h = hashlib.sha256()
    for m in window:
        h.update(b"C" if m.is_child else b"O")
        h.update(m.text.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()
