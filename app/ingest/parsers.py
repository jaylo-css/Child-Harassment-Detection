"""Chat-log parsers: JSON, plain text ("name: message" lines) and CSV.

All parsers return a list of `ChatMessage`. They never log message content.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, datetime
from typing import Any

from app.models import ChatMessage, ScoredMessage
from app.privacy.hashing import hash_handle


class ParseError(ValueError):
    pass


_SENDER_KEYS = ("sender", "from", "author", "name", "user", "username", "speaker")
_TEXT_KEYS = ("text", "message", "content", "body", "msg")
_TIME_KEYS = ("timestamp", "time", "ts", "date", "datetime", "sent_at", "created_at")


def _pick(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k in lower and lower[k] not in (None, ""):
            return lower[k]
    return None


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, int | float):
        v = float(value)
        if v > 1e12:  # milliseconds
            v /= 1000.0
        return datetime.fromtimestamp(v, UTC).replace(tzinfo=None)
    s = str(value).strip()
    if re.fullmatch(r"\d{10,13}(\.\d+)?", s):
        return parse_timestamp(float(s))
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%d/%m/%Y %H:%M", "%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _records_to_messages(records: list[Any]) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    for rec in records:
        if not isinstance(rec, dict):
            raise ParseError("each message must be an object")
        sender = _pick(rec, _SENDER_KEYS)
        text = _pick(rec, _TEXT_KEYS)
        if sender is None or text is None:
            raise ParseError("each message needs a sender and a text field")
        out.append(
            ChatMessage(
                index=len(out),
                sender=str(sender).strip(),
                text=str(text),
                timestamp=parse_timestamp(_pick(rec, _TIME_KEYS)),
            )
        )
    return out


def parse_json(content: str) -> list[ChatMessage]:
    content = content.strip()
    if not content:
        raise ParseError("empty input")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # JSON Lines: one message object per line
        try:
            data = [json.loads(line) for line in content.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise ParseError(f"invalid JSON: {exc.msg}") from exc
    if isinstance(data, dict):
        data = data.get("messages")
        if not isinstance(data, list):
            raise ParseError('JSON object must contain a "messages" list')
    if not isinstance(data, list):
        raise ParseError("JSON must be a list of messages or an object with 'messages'")
    return _records_to_messages(data)


# "[2024-05-01 10:00] name: text", "10:00 name: text", or "name: text"
_LINE = re.compile(r"^\s*(?:\[(?P<ts>[^\]]{1,40})\]\s*)?(?P<sender>[^:\n]{1,48}?)\s*:\s(?P<text>.*)$")


def parse_text(content: str) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    for raw in content.splitlines():
        if not raw.strip():
            continue
        m = _LINE.match(raw)
        if m and m.group("sender").strip():
            out.append(
                ChatMessage(
                    index=len(out),
                    sender=m.group("sender").strip(),
                    text=m.group("text").strip(),
                    timestamp=parse_timestamp(m.group("ts")),
                )
            )
        elif out:  # continuation of the previous message
            prev = out[-1]
            out[-1] = prev.model_copy(update={"text": f"{prev.text}\n{raw.strip()}"})
        else:
            raise ParseError('text lines must look like "name: message"')
    if not out:
        raise ParseError("no messages found")
    return out


def parse_csv(content: str) -> list[ChatMessage]:
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    if not reader.fieldnames:
        raise ParseError("CSV needs a header row (sender,text[,timestamp])")
    rows = list(reader)
    if not rows:
        raise ParseError("no messages found")
    return _records_to_messages(rows)


def parse_auto(content: str, filename: str | None = None) -> list[ChatMessage]:
    name = (filename or "").lower()
    if name.endswith((".json", ".jsonl")):
        return parse_json(content)
    if name.endswith(".csv"):
        return parse_csv(content)
    if name.endswith(".txt"):
        return parse_text(content)
    stripped = content.lstrip()
    if stripped.startswith(("[{", "{", "[\n", "[ ")) or (stripped.startswith("[") and '"' in stripped[:50]):
        try:
            return parse_json(content)
        except ParseError:
            pass
    first = stripped.splitlines()[0].lower() if stripped else ""
    if "," in first and any(k in first for k in _TEXT_KEYS) and any(k in first for k in _SENDER_KEYS):
        return parse_csv(content)
    return parse_text(content)


def participants(messages: list[ChatMessage]) -> list[str]:
    seen: dict[str, None] = {}
    for m in messages:
        seen.setdefault(m.sender, None)
    return list(seen)


def to_scored(messages: list[ChatMessage], child: str) -> list[ScoredMessage]:
    """Mark the child participant and hash every handle."""
    names = participants(messages)
    if child not in names:
        matches = [n for n in names if n.lower() == child.strip().lower()]
        if not matches:
            raise ParseError(f"child participant not found; participants: {len(names)}")
        child = matches[0]
    return [
        ScoredMessage(
            index=m.index,
            sender_hash=hash_handle(m.sender),
            is_child=(m.sender == child),
            text=m.text,
            timestamp=m.timestamp,
        )
        for m in messages
    ]
