"""Grooming stages.

Loosely follows published grooming-stage research: O'Connell's (2003) cybersexploitation stages
(friendship forming, relationship forming, risk assessment, exclusivity, ...), and the Lanning /
Black et al. (2015) models of online grooming. Stage boundaries in practice are fuzzy and stages
can overlap or be skipped; the trajectory scorer therefore rewards *ordered* progression without
requiring every stage.

Example phrases are detection signals only and are deliberately non-explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Stage(StrEnum):
    NONE = "NONE"
    FRIENDSHIP = "FRIENDSHIP"
    RISK_ASSESSMENT = "RISK_ASSESSMENT"
    TRUST_EXCLUSIVITY = "TRUST_EXCLUSIVITY"
    ISOLATION = "ISOLATION"
    SECRECY = "SECRECY"
    PLATFORM_MOVE = "PLATFORM_MOVE"
    ESCALATION = "ESCALATION"

    @property
    def order(self) -> int:
        return STAGE_ORDER[self]

    @property
    def label(self) -> str:
        return STAGE_DEFS[self].label


# Progression order used by the trajectory scorer (NONE = 0).
STAGE_ORDER: dict[Stage, int] = {s: i for i, s in enumerate(Stage)}
RISK_STAGES: list[Stage] = [s for s in Stage if s is not Stage.NONE]
ALL_STAGES: list[Stage] = list(Stage)
LATE_STAGES = {Stage.SECRECY, Stage.PLATFORM_MOVE, Stage.ESCALATION}


@dataclass(frozen=True)
class StageDefinition:
    stage: Stage
    label: str
    description: str
    example_signals: tuple[str, ...]
    guardian_text: str  # plain-language description used in alerts


STAGE_DEFS: dict[Stage, StageDefinition] = {
    Stage.NONE: StageDefinition(
        Stage.NONE,
        "Normal chat",
        "Ordinary conversation with no grooming indicators.",
        ("gg", "want to queue again?", "nice build"),
        "Normal conversation.",
    ),
    Stage.FRIENDSHIP: StageDefinition(
        Stage.FRIENDSHIP,
        "Friendship forming",
        "Building rapport: compliments, shared interests, gaming help, gifts of in-game items or currency.",
        ("you're really good at this", "i can carry you to the next rank", "i'll give you 800 robux"),
        "Someone is building a friendship quickly, for example with compliments or free in-game items.",
    ),
    Stage.RISK_ASSESSMENT: StageDefinition(
        Stage.RISK_ASSESSMENT,
        "Risk assessment",
        "Probing age, location, school, whether parents check the phone, who else uses the device.",
        ("how old are you?", "what school do you go to?", "do your parents check your phone?"),
        "Someone asked personal questions such as age, school, location or whether parents check the phone.",
    ),
    Stage.TRUST_EXCLUSIVITY: StageDefinition(
        Stage.TRUST_EXCLUSIVITY,
        "Trust and exclusivity",
        "Creating a special bond and emotional dependence.",
        ("you're so mature for your age", "only i understand you", "you can tell me anything"),
        "Someone is presenting themselves as the only person who truly understands your child.",
    ),
    Stage.ISOLATION: StageDefinition(
        Stage.ISOLATION,
        "Isolation",
        "Undermining parents or friends; pushing to talk alone or late at night.",
        ("your parents don't get you", "your friends are fake", "talk after everyone's asleep"),
        "Someone is undermining parents or friends, or pushing to talk alone or late at night.",
    ),
    Stage.SECRECY: StageDefinition(
        Stage.SECRECY,
        "Secrecy",
        "Asking the child to keep the relationship or messages secret.",
        ("don't tell anyone", "delete this chat", "this is our secret"),
        "Someone asked your child to keep things secret or delete messages.",
    ),
    Stage.PLATFORM_MOVE: StageDefinition(
        Stage.PLATFORM_MOVE,
        "Platform move",
        "Moving the conversation to a more private app or asking for a phone number.",
        ("add me on snap", "what's your number?", "let's talk on telegram instead"),
        "Someone is trying to move the conversation to a more private app or get a phone number.",
    ),
    Stage.ESCALATION: StageDefinition(
        Stage.ESCALATION,
        "Escalation",
        "Requests for photos or video calls, asking about being home alone, proposing in-person meetings, "
        "or offering money/gift cards for favors.",
        ("send me a pic of you, just for me", "are you home alone this weekend?", "let's meet at the mall"),
        "Someone asked for photos or a video call, asked whether your child is home alone, "
        "proposed meeting in person, or offered money or gifts in exchange for something.",
    ),
}


def parse_stage(value: str) -> Stage:
    return Stage(value.strip().upper())
