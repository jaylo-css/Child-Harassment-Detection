"""PII redaction: names, phone numbers, emails, @handles, usernames, URLs, addresses, school names
and "I live in ..." locations, where detectable.

Applied before any external API call and before rendering alerts. Redaction is best-effort:
regexes cannot catch everything, which is why alerts also carry at most 2 short excerpts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_.]{2,32}")
# 7+ digits with optional separators / country code (covers PH 09xx xxx xxxx, +63, US formats).
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){7,15}(?!\w)")
SOCIAL_ID = re.compile(
    r"(?i)\b(my|add me on|i'?m on|find me on)\s+(discord|snap(?:chat)?|insta(?:gram)?|ig|telegram|tiktok|kik|"
    r"username|user ?name|user|tag|gamertag|ign|roblox|epic|steam|whatsapp|signal)"
    r"(\s+(?:is|=|:|name is|id is|at|as)?\s*)([A-Za-z0-9_.#-]{3,32})"
)
ADDRESS = re.compile(
    r"(?i)\b\d{1,5}\s+(?:[A-Za-z0-9.'-]+\s+){0,4}"
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|highway|hwy|extension|ext)\b\.?"
)
BARANGAY = re.compile(r"(?i)\b(?:barangay|brgy\.?)\s+[A-Za-z0-9.'-]+(?:\s+[A-Z][A-Za-z0-9.'-]+){0,2}")
SCHOOL_CAP = re.compile(
    r"\b(?:[A-Z][\w.'-]*\s+){1,4}(?:National High School|Elementary School|Integrated School|High School|"
    r"Middle School|Elementary|Academy|College|University)\b"
)
SCHOOL_LOWER = re.compile(
    r"(?i)\b(go to|study at|attend|attends|from|at)\s+((?:[a-z0-9.'-]+\s+){1,4}"
    r"(?:elementary|middle school|high school|academy|school))\b"
)
LIVE_IN = re.compile(
    r"(?i)\b(i live (?:in|at|on|near)|i'?m from|we live (?:in|at|on|near))\s+([A-Za-z][\w .'-]{1,40}?)(?=[,.!?;]|$)"
)
NAME_INTRO = re.compile(r"\b((?:[Mm]y name is|[Cc]all me|[Ii]t'?s|[Ii]'?m|[Ii] am)\s+)([A-Z][a-z]{1,20})\b")
NAME_NOT = {
    "Ok",
    "Okay",
    "Here",
    "Home",
    "Back",
    "Fine",
    "Good",
    "Not",
    "So",
    "Just",
    "Sorry",
    "Done",
    "In",
    "On",
}


def redact(text: str, known_names: Iterable[str] = ()) -> str:
    t = EMAIL.sub("[EMAIL]", text)
    t = URL.sub("[URL]", t)
    t = SOCIAL_ID.sub(lambda m: f"{m.group(1)} {m.group(2)}{m.group(3)}[HANDLE]", t)
    t = HANDLE.sub("[HANDLE]", t)
    t = PHONE.sub(lambda m: "[PHONE]" if sum(c.isdigit() for c in m.group(0)) >= 7 else m.group(0), t)
    t = ADDRESS.sub("[ADDRESS]", t)
    t = BARANGAY.sub("[ADDRESS]", t)
    t = SCHOOL_CAP.sub("[SCHOOL]", t)
    t = SCHOOL_LOWER.sub(lambda m: f"{m.group(1)} [SCHOOL]", t)
    t = LIVE_IN.sub(lambda m: f"{m.group(1)} [LOCATION]", t)
    for name in sorted({n.strip() for n in known_names if n and len(n.strip()) >= 2}, key=len, reverse=True):
        t = re.sub(rf"(?i)(?<!\w){re.escape(name)}(?!\w)", "[NAME]", t)
    t = NAME_INTRO.sub(lambda m: m.group(0) if m.group(2) in NAME_NOT else f"{m.group(1)}[NAME]", t)
    return t


_PII_CHECKS = (EMAIL, URL, HANDLE)


def contains_pii(text: str) -> bool:
    """Conservative check used by tests and as a last guard before alerts are emitted."""
    if any(rx.search(text) for rx in _PII_CHECKS):
        return True
    return any(sum(c.isdigit() for c in m.group(0)) >= 7 for m in PHONE.finditer(text))


def excerpt(text: str, limit: int = 120, known_names: Iterable[str] = ()) -> str:
    """Redact, collapse whitespace and truncate to `limit` characters (including the ellipsis)."""
    t = " ".join(redact(text, known_names).split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"
