"""Layer 1: rule-based red-flag detectors loaded from YAML (see app/detection/rules/en.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.detection.stages import Stage, parse_stage
from app.models import ScoredMessage, SignalHit
from app.textproc import LanguagePack, get_language_pack


@dataclass
class Rule:
    id: str
    stage: Stage
    weight: float
    patterns: list[re.Pattern]
    exclude: list[re.Pattern] = field(default_factory=list)
    description: str = ""


class RuleError(ValueError):
    pass


def load_rules(path: str | Path) -> list[Rule]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list):
        raise RuleError(f"{path}: expected a top-level 'rules' list")
    rules: list[Rule] = []
    seen: set[str] = set()
    for raw in doc["rules"]:
        rid = raw.get("id")
        if not rid or rid in seen:
            raise RuleError(f"{path}: missing or duplicate rule id {rid!r}")
        seen.add(rid)
        weight = float(raw.get("weight", 0.5))
        if not 0.0 < weight <= 1.0:
            raise RuleError(f"{path}: rule {rid}: weight must be in (0, 1]")
        try:
            stage = parse_stage(raw["stage"])
            pats = [re.compile(p) for p in raw.get("patterns", [])]
            excl = [re.compile(p) for p in raw.get("exclude", []) or []]
        except (KeyError, ValueError, re.error) as exc:
            raise RuleError(f"{path}: rule {rid}: {exc}") from exc
        if not pats:
            raise RuleError(f"{path}: rule {rid}: needs at least one pattern")
        rules.append(Rule(rid, stage, weight, pats, excl, raw.get("description", "")))
    return rules


class SignalDetector:
    def __init__(self, rules: list[Rule], lang: LanguagePack | None = None) -> None:
        self.rules = rules
        self.lang = lang or get_language_pack("en")

    @classmethod
    def from_file(cls, path: str | Path, language: str = "en") -> SignalDetector:
        return cls(load_rules(path), get_language_pack(language))

    def detect_text(self, text: str, message_id: int) -> list[SignalHit]:
        norm = self.lang.normalize(text)
        hits: list[SignalHit] = []
        for rule in self.rules:
            if any(x.search(norm) for x in rule.exclude):
                continue
            for pat in rule.patterns:
                m = pat.search(norm)
                if m:
                    hits.append(
                        SignalHit(
                            signal=rule.id,
                            stage=rule.stage.value,
                            weight=rule.weight,
                            message_id=message_id,
                            span=(m.start(), m.end()),
                        )
                    )
                    break  # one hit per rule per message
        return hits

    def detect(self, message: ScoredMessage) -> list[SignalHit]:
        """Rules apply to the other party only - never to the child's own messages."""
        if message.is_child:
            return []
        return self.detect_text(message.text, message.index)

    def detect_all(self, messages: list[ScoredMessage]) -> list[SignalHit]:
        out: list[SignalHit] = []
        for m in messages:
            out.extend(self.detect(m))
        return out


@lru_cache(maxsize=4)
def get_detector(path: str, language: str = "en") -> SignalDetector:
    return SignalDetector.from_file(path, language)
