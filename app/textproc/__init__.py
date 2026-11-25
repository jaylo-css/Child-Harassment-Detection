"""Language-specific text processing, isolated behind a small interface.

To add a language (e.g. Taglish): implement `LanguagePack`, register it in `_PACKS`, and add a
rules file `app/detection/rules/<code>.yaml`. Nothing else in the pipeline is language-aware.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol


class LanguagePack(Protocol):
    code: str

    def normalize(self, text: str) -> str:
        """Lowercase/normalize text for rule matching and classification."""
        ...

    def tokenize(self, text: str) -> list[str]: ...


# Common chat spellings mapped to canonical forms so rules stay short.
_CHAT_SPELLINGS = {
    r"\bu\b": "you",
    r"\bur\b": "your",
    r"\br\b": "are",
    r"\bpls\b": "please",
    r"\bplz\b": "please",
    r"\bcuz\b": "because",
    r"\bwat\b": "what",
    r"\bwut\b": "what",
    r"\bim\b": "i'm",
    r"\bdont\b": "don't",
    r"\bcant\b": "can't",
    r"\bwont\b": "won't",
    r"\blets\b": "let's",
    r"\bthats\b": "that's",
    r"\bwhats\b": "what's",
    r"\byoure\b": "you're",
    r"\bpix\b": "pics",
    r"\bppl\b": "people",
}
_COMPILED = [(re.compile(p), r) for p, r in _CHAT_SPELLINGS.items()]
_REPEAT = re.compile(r"(.)\1{2,}")
_SPACES = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9']+")


class EnglishPack:
    code = "en"

    def normalize(self, text: str) -> str:
        t = unicodedata.normalize("NFKC", text).lower()
        t = t.replace("’", "'").replace("‘", "'")
        t = _REPEAT.sub(r"\1\1", t)  # "sooooo" -> "soo"
        for rx, rep in _COMPILED:
            t = rx.sub(rep, t)
        return _SPACES.sub(" ", t).strip()

    def tokenize(self, text: str) -> list[str]:
        return _TOKEN.findall(self.normalize(text))


_PACKS: dict[str, LanguagePack] = {"en": EnglishPack()}


def get_language_pack(code: str = "en") -> LanguagePack:
    try:
        return _PACKS[code]
    except KeyError as exc:
        raise ValueError(f"unsupported language {code!r}; available: {sorted(_PACKS)}") from exc
