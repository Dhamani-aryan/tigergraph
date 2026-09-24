from __future__ import annotations

import inspect
from pathlib import Path

from src.agent import simulator
from src.agent.decision import is_decisive, resolve_decision, resolve_status
from src.agent.features import (
    compute_behavior_profile,
    compute_evidence_families,
    detect_card_testing,
    detect_cnp_burst,
    detect_out_of_region,
    evaluate_device_network,
)
from src.agent.simulator import SIMULATED_PREFIX, simulate_customer_validation
from src.policy.engine import apply_policy
from src.policy.models import Findings
from tests.test_features import CUTOFF, history, txn


def _simulate(window, flagged="F"):
    profile = compute_behavior_profile(window, flagged, CUTOFF)
    ct = detect_card_testing(window, flagged, CUTOFF)
    cnp = detect_cnp_burst(window, flagged, CUTOFF, profile.baseline_online_per_48h)
    region = detect_out_of_region(window, flagged, CUTOFF, profile)
    net = evaluate_device_network({}, "C1-K1", profile.flagged_ts, CUTOFF)
    fams = compute_evidence_families(profile, ct, cnp, region, net)
    return simulate_customer_validation(profile, fams, ct, cnp, region, net), profile, fams


# -- 16. The LLM's probability cannot influence the simulator ----------------
def test_simulator_has_no_llm_inputs():
    params = set(inspect.signature(simulate_customer_validation).parameters)
    assert params == {"profile", "families", "card_testing", "cnp", "region", "network"}
    for forbidden in ("fraud_probability", "verdict", "pattern", "case_id"):
        assert forbidden not in params


def test_simulator_is_deterministic_for_the_same_profile():
    window = history(30) + [txn("F", 0, 41.0, channel="in_person", product="W", addr1="100.0")]
    assert _simulate(window)[0] == _simulate(window)[0]


# -- 17. No hardcoded $100 median ---------------------------------------------
def test_no_hardcoded_hundred_dollar_median():
    root = Path(__file__).resolve().parents[1] / "src" / "agent"
    for name in ("simulator.py", "graph_flow.py", "features.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "customer_median_amount=100" not in text
        assert "median_amount=100" not in text
    window = history(30, amount=10.0) + [txn("F", 0, 11.0, channel="in_person", product="W", addr1="100.0")]
    result, profile, _ = _simulate(window)
    assert profile.amount_median == 12.0
    assert "$12.0" in result.text


# -- 18. Strong benign profile simulates confirmation -------------------------
def test_strong_benign_profile_simulates_confirmation_even_with_new_device():
    window = history(30, channel="online", product="C", addr1=None, step_hours=48) + [
        txn("F", 0, 41.0, id_15="New")
    ]
    result, _, _ = _simulate(window)
    assert result.response == "confirmed_legitimate"
    assert result.text.startswith(SIMULATED_PREFIX)
    assert "does not block confirmation" in result.text


def test_in_person_confirmation_requires_established_region():
    window = history(30) + [txn("F", 0, 41.0, channel="in_person", product="W", addr1="100.0")]
    assert _simulate(window)[0].response == "confirmed_legitimate"
    unseen = history(30) + [txn("F", 0, 41.0, channel="in_person", product="W", addr1="777.0")]
    assert _simulate(unseen)[0].response == "no_reply"


# -- 19. Ambiguous profile produces no_reply ----------------------------------
def test_ambiguous_profile_produces_no_reply():
    window = history(30, channel="online", product="C", addr1=None, step_hours=48) + [
        txn("F", 0, 70.0, product="R", id_15="New")  # unseen ProductCD + new device, no strong signal
    ]
    result, _, fams = _simulate(window)
    assert len(fams.suspicious) == 2 and not fams.strong_suspicious
    assert result.response == "no_reply"
    assert result.text.startswith(SIMULATED_PREFIX)


def test_short_history_is_no_reply_not_denial():
    result, _, _ = _simulate([txn("H1", -100, 10.0), txn("F", 0, 900.0, id_15="New")])
    assert result.response == "no_reply"


# -- 20. Strong multi-family anomaly simulates denial -------------------------
def test_strong_multi_family_anomaly_simulates_denial():
    window = history(30, channel="online", product="C", addr1=None, step_hours=48, amount=20.0) + [
        txn("F", 0, 900.0, id_15="New")
    ]
    result, _, fams = _simulate(window)
    assert "extreme_amount_with_identity_anomaly" in fams.strong_suspicious
    assert result.response == "denies"
    assert result.text.startswith(SIMULATED_PREFIX)
    assert "behavioral_anomaly" in result.text


def test_simulator_module_has_no_probability_threshold():
    assert "fraud_probability" not in inspect.getsource(simulator)


# -- 25-29. Central decision resolver ----------------------------------------
TWO = ["behavioral_anomaly", "identity_anomaly"]


def test_probability_between_070_and_085_is_not_fraud():
    d = resolve_decision(probability=0.78, suspicious_families=TWO + ["temporal_pattern"], benign_families=[])
    assert d.verdict == "uncertain" and not d.decisive


def test_high_probability_with_one_family_remains_uncertain():
    d = resolve_decision(probability=0.93, suspicious_families=["identity_anomaly"], benign_families=[])
    assert d.verdict == "uncertain"
    assert "only 1" in d.reason


def test_high_probability_with_two_families_is_fraud():
    d = resolve_decision(probability=0.9, suspicious_families=TWO, benign_families=[], llm_pattern="card_not_present_new_device")
    assert d.verdict == "fraud" and d.settled_by == "probability_and_evidence"
    assert d.pattern == "card_not_present_new_device"


def test_low_probability_needs_two_benign_families():
    one = resolve_decision(probability=0.05, suspicious_families=[], benign_families=["behavioral_anomaly"])
    assert one.verdict == "uncertain"
    two = resolve_decision(probability=0.05, suspicious_families=[], benign_families=["behavioral_anomaly", "geographic_anomaly"])
    assert two.verdict == "legitimate" and two.pattern == "none"


def test_confirmation_settles_legitimate_with_capped_probability():
    d = resolve_decision(probability=0.6, suspicious_families=TWO, benign_families=[], response="confirmed_legitimate",
                         llm_pattern="card_not_present_fraud", deterministic_pattern="card_testing")
    assert d.verdict == "legitimate" and d.fraud_probability == 0.15 and d.pattern == "none"


def test_denial_settles_fraud_with_floored_probability():
    d = resolve_decision(probability=0.4, suspicious_families=[], benign_families=[], response="denies",
                         llm_pattern="none", llm_pattern_if_fraud="card_not_present_fraud")
    assert d.verdict == "fraud" and d.fraud_probability == 0.85 and d.pattern == "card_not_present_fraud"


def test_no_reply_is_not_a_denial():
    d = resolve_decision(probability=0.6, suspicious_families=TWO, benign_families=[], response="no_reply")
    assert d.verdict == "uncertain" and d.fraud_probability == 0.6


def test_r7_cannot_produce_closed_fraud():
    d = resolve_decision(probability=0.9, suspicious_families=TWO, benign_families=[], response="denies",
                         recurrence_strong=True, llm_pattern="card_not_present_fraud")
    assert d.verdict == "legitimate" and d.response == "disputes_recurring" and d.pattern == "none"
    policy = apply_policy(Findings(pattern=d.pattern, fraud_probability=d.fraud_probability, single_signal=False,
                                   verdict=d.verdict, customer_response=d.response, exposure_usd=0.0))
    names = [a.action for a in policy.actions]
    assert "BLOCK_CARD" not in names and "FILE_REPORT" not in names
    assert {"CREATE_CASE", "VERIFY_WITH_CUSTOMER", "WARN_CUSTOMER"} <= set(names)
    assert resolve_status(d.verdict, names) == "closed_legitimate"


def test_card_testing_names_pattern_but_does_not_force_fraud():
    d = resolve_decision(probability=0.7, suspicious_families=["temporal_pattern"], benign_families=[],
                         deterministic_pattern="card_testing", llm_pattern="none")
    assert d.verdict == "uncertain" and d.pattern == "card_testing"


def test_legitimate_decision_forces_pattern_none():
    d = resolve_decision(probability=0.05, suspicious_families=[], benign_families=["a", "b"],
                         llm_pattern="card_not_present_fraud", deterministic_pattern="out_of_region_use")
    assert d.pattern == "none" and d.pattern_description == ""


def test_uncertain_status_is_open_or_escalated():
    assert resolve_status("uncertain", ["VERIFY_WITH_CUSTOMER", "CREATE_CASE"]) == "open"
    assert resolve_status("uncertain", ["MONITOR_CARD", "ESCALATE_TO_ANALYST"]) == "escalated"
    assert resolve_status("fraud", ["BLOCK_CARD"]) == "closed_fraud"
    assert resolve_status("legitimate", ["CLOSE_NO_FRAUD"]) == "closed_legitimate"


def test_is_decisive_matches_resolver():
    assert is_decisive(0.9, TWO, []) is True
    assert is_decisive(0.9, ["x"], []) is False
    assert is_decisive(0.1, [], ["a", "b"]) is True
    assert is_decisive(0.5, TWO, ["a", "b"]) is False
