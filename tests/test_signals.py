"""Positive and negative cases for every Layer-1 rule. All examples are non-explicit signals."""

from pathlib import Path

import pytest

from app.config import APP_DIR
from app.detection.signals import RuleError, SignalDetector, load_rules
from app.detection.stages import STAGE_DEFS, Stage
from app.models import ScoredMessage

RULES = APP_DIR / "detection" / "rules" / "en.yaml"
DET = SignalDetector.from_file(RULES)

CASES: dict[str, tuple[list[str], list[str]]] = {
    "age_probe": (
        ["how old r u", "hey asl?", "what grade are you in", "are you like 12?"],
        ["how old is that skin", "coach: how old are you for the league registration form", "my age is 30"],
    ),
    "location_probe": (
        ["where do u live", "what school do you go to?", "what city are you in", "whats your address"],
        ["where do i find the sword", "what school of magic is best in this game"],
    ),
    "parent_monitoring": (
        ["do your parents check your phone?", "who else uses your tablet", "is this your own phone?"],
        ["my parents check my grades lol", "who else uses this strategy"],
    ),
    "parents_around": (
        ["are your parents home right now?", "when do your parents go to bed"],
        ["my parents are home", "are your pets around"],
    ),
    "home_alone": (
        ["are you home alone this weekend?", "is anyone else home?", "are you alone right now"],
        ["i'll solo queue, i'm alone in the lobby", "home alone is a great movie"],
    ),
    "secrecy": (
        ["dont tell anyone ok", "this is our little secret", "keep this between us", "no one needs to know"],
        [
            "don't tell anyone about the surprise party for mom",
            "secret level unlocked",
            "tell me a secret code",
        ],
    ),
    "delete_chat": (
        ["delete this chat after", "pls delete our messages", "turn on disappearing messages"],
        ["delete this item from your inventory", "i deleted the game"],
    ),
    "platform_move": (
        ["add me on snap", "lets talk on telegram instead", "whats your discord", "somewhere more private"],
        [
            "add me on discord, it's the class server",
            "join our team discord for the tournament",
            "my snap broke",
        ],
    ),
    "phone_number_request": (
        ["whats your number?", "can i get your number", "text me at this one"],
        ["what's your number in the leaderboard? lol no", "what number is the level"],
    ),
    "gift_offer": (
        ["i'll give you 800 robux", "free robux if you want", "i can buy you a skin"],
        ["robux are expensive", "i bought a skin yesterday"],
    ),
    "money_for_favor": (
        ["i'll send a gift card if you send a selfie", "500 robux for you if you keep this secret"],
        ["i got a gift card for my birthday", "you can buy robux with gift cards"],
    ),
    "photo_request": (
        ["send me a pic of you, just for me", "can i see what you look like", "send a selfie pls"],
        ["send me a screenshot of your base", "never send pics to people online", "send me the map seed"],
    ),
    "video_call": (
        ["can we video call tonight", "turn your camera on", "facetime me"],
        ["video call with grandma later", "class video call at 9"],
    ),
    "meetup": (
        ["lets meet at the mall, don't tell your mom", "i can pick you up after school", "where can we meet"],
        ["meet at spawn", "let's meet in the lobby", "meet at the practice field for tryouts"],
    ),
    "exclusivity": (
        ["you're so mature for your age", "only i understand you", "you can tell me anything"],
        ["you're mature for a bot lol", "i understand the quest"],
    ),
    "isolation": (
        ["your parents don't understand you", "your friends are fake", "talk after everyone goes to sleep"],
        ["your parents are nice", "my friends are fake fans of that team lol"],
    ),
    "compliment_appearance": (
        ["you're so cute", "you look older"],
        ["your skin is cute", "you look like you're lagging"],
    ),
}


def test_every_rule_has_cases():
    ids = {r.id for r in load_rules(RULES)}
    assert ids == set(CASES), f"missing cases for {ids - set(CASES)} / unknown {set(CASES) - ids}"


@pytest.mark.parametrize("rule_id", sorted(CASES))
def test_rule_positive_and_negative(rule_id):
    positives, negatives = CASES[rule_id]
    for text in positives:
        hits = {h.signal for h in DET.detect_text(text, 0)}
        assert rule_id in hits, f"{rule_id} should fire on {text!r}"
    for text in negatives:
        hits = {h.signal for h in DET.detect_text(text, 0)}
        assert rule_id not in hits, f"{rule_id} should NOT fire on {text!r}"


def test_hit_shape_and_child_messages_ignored():
    m = ScoredMessage(index=5, sender_hash="x", is_child=False, text="dont tell anyone")
    (hit,) = [h for h in DET.detect(m) if h.signal == "secrecy"]
    assert (
        hit.stage == "SECRECY" and hit.message_id == 5 and hit.span[1] > hit.span[0] and 0 < hit.weight <= 1
    )
    child = m.model_copy(update={"is_child": True})
    assert DET.detect(child) == []


def test_rule_validation(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("rules:\n  - id: x\n    stage: NOT_A_STAGE\n    patterns: ['a']\n")
    with pytest.raises(RuleError):
        load_rules(bad)
    bad.write_text("rules:\n  - id: x\n    stage: SECRECY\n    weight: 2\n    patterns: ['a']\n")
    with pytest.raises(RuleError):
        load_rules(bad)


def test_stage_definitions_complete():
    assert set(STAGE_DEFS) == set(Stage)
    assert Stage.ESCALATION.order > Stage.SECRECY.order > Stage.FRIENDSHIP.order > Stage.NONE.order
