"""Regression tests for the independent review of the evidence-classification fix."""
from __future__ import annotations

from src.agent import graph_flow
from src.agent.features import (
    compute_behavior_profile,
    compute_evidence_families,
    detect_card_testing,
    detect_cnp_burst,
    detect_out_of_region,
    evaluate_device_network,
)
from src.agent.simulator import simulate_customer_validation
from src.policy.engine import apply_policy
from src.policy.models import Findings
from tests.test_features import CUTOFF, _device, _dtx, _ts, history, txn


def _families_for(window):
    profile = compute_behavior_profile(window, "F", CUTOFF)
    ct = detect_card_testing(window, "F", CUTOFF)
    cnp = detect_cnp_burst(window, "F", CUTOFF, profile.baseline_online_per_48h)
    region = detect_out_of_region(window, "F", CUTOFF, profile)
    net = evaluate_device_network({}, "C1-K1", profile.flagged_ts, CUTOFF)
    return profile, ct, cnp, region, net, compute_evidence_families(profile, ct, cnp, region, net)


def test_uncertain_verdict_never_files_a_report_on_shared_origin():
    for response in (None, "no_reply"):
        result = apply_policy(Findings(pattern="undocumented", fraud_probability=0.75, single_signal=False,
                                       verdict="uncertain", shared_device=True, exposure_usd=200.0,
                                       undocumented_coordinated=True, customer_response=response))
        names = [a.action for a in result.actions]
        assert "FILE_REPORT" not in names and result.sar_file is False
        assert "ESCALATE_TO_ANALYST" in names


def test_anonymizing_proxy_blocks_simulated_confirmation():
    window = history(30, channel="online", product="C", addr1=None, step_hours=48) + [
        txn("F", 0, 41.0, id_15="New", id_23="IP_PROXY:ANONYMOUS")
    ]
    profile, ct, cnp, region, net, fams = _families_for(window)
    assert simulate_customer_validation(profile, fams, ct, cnp, region, net).response == "no_reply"


def test_missing_proxy_field_is_unavailable_not_benign():
    window = history(30, channel="online", product="C", addr1=None, step_hours=48) + [
        txn("F", 0, 41.0, id_15="Found", id_23=None)
    ]
    profile, *_, fams = _families_for(window)
    assert profile.is_proxy is None
    assert "identity_consistency" not in fams.benign
    present = history(30, channel="online", product="C", addr1=None, step_hours=48) + [
        txn("F", 0, 41.0, id_15="Found", id_23="")
    ]
    # an empty id_23 string on the identity record is also "not recorded"
    assert compute_behavior_profile(present, "F", CUTOFF).is_proxy is None


def test_cnp_burst_is_one_48h_window_not_plus_minus_48h():
    window = [txn("A", -47, 50.0), txn("F", 0, 50.0), txn("B", 2.5, 50.0)]
    cutoff = _ts(3)
    cnp = detect_cnp_burst(window, "F", cutoff)
    assert cnp.documented_burst is True
    assert len(cnp.online_txn_ids) == 2  # A..F or F..B, never all three across 49.5h
    spread = [txn("A", -47, 50.0), txn("F", 0, 50.0), txn("B", 47, 50.0)]
    wide = detect_cnp_burst(spread, "F", _ts(48))
    assert wide.online_count == 2


def test_confirmed_fraud_on_other_card_counts_without_48h_activity():
    fraud = [{"case_id": "CC-9", "outcome": "confirmed_fraud", "txn_id": "T9", "txn_ts": _ts(-24 * 10),
              "closed_at": _ts(-24 * 5), "card_id": "C2-K1"}]
    res = evaluate_device_network(_device(2, [], fraud), "C1-K1", _ts(0), CUTOFF)
    assert res.corroborated and res.corroboration_basis == "confirmed_fraud_on_other_card"


def test_confirmed_fraud_closed_after_cutoff_is_ignored():
    fraud = [{"case_id": "CC-9", "outcome": "confirmed_fraud", "txn_id": "T9", "txn_ts": _ts(-24 * 10),
              "closed_at": _ts(24 * 5), "card_id": "C2-K1"}]
    res = evaluate_device_network(_device(2, [], fraud), "C1-K1", _ts(0), CUTOFF)
    assert not res.corroborated


def test_prior_case_family_is_chosen_from_retrieval_not_llm_citation():
    knowledge = {"similar_cases": [
        {"id": "CC-2", "type": "ClosedCase", "outcome": "cleared", "distance": 0.05},
        {"id": "CC-1", "type": "ClosedCase", "outcome": "confirmed_fraud", "distance": 0.08},
        {"id": "CC-3", "type": "ClosedCase", "outcome": "confirmed_fraud", "distance": 0.30},
    ]}
    assert graph_flow._closest_prior_case(knowledge)["id"] == "CC-2"
    assert graph_flow._closest_prior_case({"similar_cases": [{"id": "CC-3", "type": "ClosedCase", "distance": 0.3}]}) is None
    assert not hasattr(graph_flow, "_with_prior_case")
