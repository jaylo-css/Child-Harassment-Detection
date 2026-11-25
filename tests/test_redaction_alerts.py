import pytest
from pydantic import ValidationError

from app.alerts.builder import build_alert
from app.alerts.escalation import load_resources
from app.config import get_settings
from app.detection import engines
from app.detection.pipeline import analyze
from app.detection.scorer import ConversationScorer
from app.ingest.sources.scenarios import scenario_messages
from app.models import MAX_EXCERPT_CHARS, AlertPayload, ScoredMessage
from app.privacy.redact import contains_pii, excerpt, redact


@pytest.mark.parametrize(
    "text,leak,tag",
    [
        ("text me at 0917 123 4567 ok", "0917", "[PHONE]"),
        ("call +63 917-123-4567", "917", "[PHONE]"),
        ("my number is (555) 123-4567", "555", "[PHONE]"),
        ("email me kid.name+x@example.com", "example.com", "[EMAIL]"),
        ("add @shadowfox_99 on insta", "shadowfox", "[HANDLE]"),
        ("my discord is pixelninja#1234", "pixelninja", "[HANDLE]"),
        ("my snap is coolkid2012", "coolkid2012", "[HANDLE]"),
        ("go to https://example.org/x?id=1", "example.org", "[URL]"),
        ("i live at 221 Baker Street", "Baker", "[ADDRESS]"),
        ("i go to Riverside Elementary", "Riverside", "[SCHOOL]"),
        ("i go to sunrise national high school", "sunrise", "[SCHOOL]"),
        ("i live in Quezon City", "Quezon", "[LOCATION]"),
        ("my name is Maria", "Maria", "[NAME]"),
    ],
)
def test_redaction(text, leak, tag):
    out = redact(text)
    assert leak.lower() not in out.lower(), out
    assert tag in out


def test_known_names_and_safe_text():
    assert redact("hi Jamie, ok?", known_names=["Jamie"]) == "hi [NAME], ok?"
    assert redact("gg nice game, level 12") == "gg nice game, level 12"
    assert redact("i'm 12") == "i'm 12"


def test_contains_pii():
    assert contains_pii("call 09171234567")
    assert contains_pii("a@b.co")
    assert contains_pii("hi @someone")
    assert not contains_pii("gg 12 kills")


def test_excerpt_limit():
    e = excerpt("x " * 200)
    assert len(e) <= MAX_EXCERPT_CHARS


def test_alert_payload_enforces_limits():
    with pytest.raises(ValidationError):
        AlertPayload(
            conversation_id="c",
            platform=None,
            level="HIGH",
            score=80,
            stages=[],
            stage_labels=[],
            excerpts=["a", "b", "c"],
        )
    with pytest.raises(ValidationError):
        AlertPayload(
            conversation_id="c",
            platform=None,
            level="HIGH",
            score=80,
            stages=[],
            stage_labels=[],
            excerpts=["x" * 121],
        )


def test_build_alert_redacts_and_limits():
    cands = [
        {"text": "text me at 0917 123 4567, " + "long " * 50},
        {"text": "my snap is coolkid2012 add me @coolkid"},
        {"text": "third excerpt should be dropped"},
    ]
    a = build_alert("c1", "Roblox", "HIGH", 81.2, ["SECRECY", "PLATFORM_MOVE"], cands)
    assert len(a.excerpts) <= 2
    for e in a.excerpts:
        assert len(e) <= MAX_EXCERPT_CHARS and not contains_pii(e)
        assert "0917" not in e and "coolkid" not in e
    assert a.stage_labels == ["Secrecy", "Platform move"]
    assert a.suggested_actions and "Do not contact" in " ".join(a.suggested_actions)


def test_alert_from_scorer_never_contains_transcript_or_pii():
    s = get_settings()
    sc = ConversationScorer(s, engines.baseline(s), engines.detector(s), "conv")
    msgs = scenario_messages("grooming")
    msgs[12] = msgs[12].model_copy(update={"text": "add me on snap @groomer_acc or text 0917 555 1234"})
    for m in msgs:
        sc.process(m)
    a = build_alert("conv", "Roblox", sc.level, sc.score, sc.stages_detected(), sc.excerpts)
    body = a.model_dump_json()
    assert len(a.excerpts) <= 2
    assert "0917" not in body and "groomer_acc" not in body
    full = " ".join(m.text for m in msgs)
    assert full not in body
    assert sum(len(e) for e in a.excerpts) <= 2 * MAX_EXCERPT_CHARS


def test_escalation_resources_load():
    data = load_resources(get_settings().escalation_path)
    assert data["last_verified"] and "Verify numbers" in data["note"]
    names = " ".join(r["name"] for reg in data["regions"] for r in reg["resources"])
    for needle in ("CICC", "Bantay Bata", "Women and Children Protection", "NBI", "NCMEC"):
        assert needle in names
    assert any("never submits" in c for c in data["report_checklist"])


def test_analyze_with_pii_in_text_assessment_has_no_text():
    msgs = [ScoredMessage(index=0, sender_hash="a", is_child=False, text="my number 0917 123 4567")]
    assert "0917" not in analyze(msgs).model_dump_json()
