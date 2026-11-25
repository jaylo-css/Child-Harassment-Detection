from app.detection.pipeline import analyze
from app.detection.trajectory import level_rank
from app.ingest.sources.scenarios import scenario_messages
from app.ingest.synthetic import load_split


def test_benign_scenario_stays_low():
    a = analyze(scenario_messages("benign"))
    assert a.level == "LOW", a.explanation
    assert all(p.level == "LOW" for p in a.timeline)


def test_grooming_scenario_reaches_high():
    msgs = scenario_messages("grooming")
    a = analyze(msgs)
    assert level_rank(a.level) >= level_rank("HIGH"), a.explanation
    assert "ESCALATION" in a.stages_detected and "SECRECY" in a.stages_detected
    assert a.first_alert_index is not None and a.first_alert_index < len(msgs) - 1
    assert len(a.timeline) == len(msgs)
    assert a.top_signals and a.explanation and a.top_windows


def test_borderline_scenario_below_high():
    a = analyze(scenario_messages("borderline"))
    assert level_rank(a.level) < level_rank("HIGH"), a.explanation


def test_hard_negatives_stay_below_high():
    hns = [c for c in load_split("val") if c.label == "hard_negative"]
    assert hns
    for c in hns:
        a = analyze(c.messages, c.id)
        assert level_rank(a.level) < level_rank("HIGH"), (c.id, c.category, a.score)


def test_assessment_explainable_and_text_free():
    a = analyze(scenario_messages("grooming"))
    dumped = a.model_dump_json()
    assert "pic of you" not in dumped  # assessments carry ids/spans, not message text
    assert a.components["stage_order"][0] in ("FRIENDSHIP", "RISK_ASSESSMENT")
    assert any("Strongest signals" in line for line in a.explanation)
    assert a.windows and all(w.end - w.start < 6 for w in a.windows)
