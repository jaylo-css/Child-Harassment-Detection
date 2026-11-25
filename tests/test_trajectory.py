from app.config import Thresholds, TrajectoryConfig
from app.detection.trajectory import (
    Observation,
    TrajectoryScorer,
    TrajectoryState,
    level_for,
    lis_length,
)

CFG = TrajectoryConfig()
TH = Thresholds()


def sig(stage, weight=0.8, name="x"):
    return {"signal": name, "stage": stage, "weight": weight}


def run(obs):
    sc = TrajectoryScorer(CFG, TH)
    st = TrajectoryState()
    for o in obs:
        sc.step(st, o)
    return sc.score(st), st


def obs_seq(stages, gap_msgs=1, t0=0.0, dt=60.0, weight=0.8):
    out, idx = [], 0
    for s in stages:
        for _ in range(gap_msgs - 1):
            out.append(Observation(idx, t0 + idx * dt, False))
            idx += 1
        out.append(Observation(idx, t0 + idx * dt, False, signals=[sig(s, weight)]))
        idx += 1
    return out


def test_lis():
    assert lis_length([]) == 0
    assert lis_length([1, 2, 3]) == 3
    assert lis_length([3, 2, 1]) == 1
    assert lis_length([1, 5, 2, 3, 4]) == 4


def test_levels():
    assert level_for(10, TH) == "LOW"
    assert level_for(40, TH) == "MEDIUM"
    assert level_for(70, TH) == "HIGH"
    assert level_for(85, TH) == "CRITICAL"


def test_ordered_progression_scores_higher_than_reversed():
    stages = ["FRIENDSHIP", "RISK_ASSESSMENT", "TRUST_EXCLUSIVITY", "ISOLATION"]
    fwd, _ = run(obs_seq(stages, weight=0.6))
    rev, _ = run(obs_seq(list(reversed(stages)), weight=0.6))
    assert fwd.components["lis_length"] == 4 and rev.components["lis_length"] == 1
    assert fwd.components["progression_bonus"] > rev.components["progression_bonus"] == 0
    assert fwd.score > rev.score


def test_velocity_fast_beats_slow():
    fast, _ = run(obs_seq(["FRIENDSHIP", "SECRECY"], gap_msgs=1, dt=30))
    slow, _ = run(obs_seq(["FRIENDSHIP", "SECRECY"], gap_msgs=45, dt=3 * 3600))
    assert fast.components["velocity_bonus"] > 0
    assert slow.components["velocity_bonus"] == 0
    assert fast.score > slow.score


def test_velocity_by_hours_even_with_many_messages():
    # late stage after 45 messages but within 1 hour -> hours-based velocity applies
    r, _ = run(obs_seq(["FRIENDSHIP", "SECRECY"], gap_msgs=45, dt=10))
    assert r.components["velocity_bonus"] > 0
    assert r.components["velocity"]["hours"] < 1


def test_hard_floor_escalation_with_secrecy():
    weak = [
        Observation(0, 0.0, False, signals=[sig("ESCALATION", 0.1)]),
        Observation(1, 60.0, False, signals=[sig("SECRECY", 0.1)]),
    ]
    r, _ = run(weak)
    assert r.components["floor"] is not None
    assert r.score >= TH.high and r.level in ("HIGH", "CRITICAL")


def test_hard_floor_escalation_with_platform_move():
    r, _ = run([Observation(0, 0.0, False, signals=[sig("ESCALATION", 0.1), sig("PLATFORM_MOVE", 0.1)])])
    assert r.score >= TH.high


def test_no_floor_for_escalation_alone_or_classifier_only():
    r, _ = run([Observation(0, 0.0, False, signals=[sig("ESCALATION", 0.1)])])
    assert r.components["floor"] is None and r.level == "LOW"
    # classifier-only escalation (no signal) never triggers the floor
    probs = {"ESCALATION": 0.3, "SECRECY": 0.9, "NONE": 0.1}
    r, _ = run([Observation(0, 0.0, False, probs=probs)])
    assert r.components["floor"] is None


def test_time_decay_half_life():
    r0, st0 = run([Observation(0, 0.0, False, signals=[sig("SECRECY", 0.8)])])
    hl = CFG.half_life_hours * 3600
    r1, st1 = run([Observation(0, 0.0, False, signals=[sig("SECRECY", 0.8)]), Observation(1, hl, True)])
    assert abs(st1.evidence["SECRECY"] - st0.evidence["SECRECY"] / 2) < 1e-9
    assert r1.score < r0.score


def test_classifier_none_tempers_signal():
    with_none, _ = run([Observation(0, 0.0, False, probs={"NONE": 1.0}, signals=[sig("PLATFORM_MOVE", 0.6)])])
    no_ctx, _ = run([Observation(0, 0.0, False, signals=[sig("PLATFORM_MOVE", 0.6)])])
    assert with_none.score < no_ctx.score


def test_max_of_window_probs():
    _, st = run(
        [
            Observation(0, 0.0, False, probs={"RISK_ASSESSMENT": 0.7}),
            Observation(1, 0.0, False, probs={"RISK_ASSESSMENT": 0.2}),
        ]
    )
    assert abs(st.evidence["RISK_ASSESSMENT"] - 0.7) < 1e-6
    assert st.first_seen["RISK_ASSESSMENT"] == 0
