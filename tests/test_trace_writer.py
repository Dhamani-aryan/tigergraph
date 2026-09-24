from __future__ import annotations

import math

from src.agent.schemas import (
    SAR,
    ActionEntry,
    AnswerFile,
    CaseRecord,
    Evidence,
    NextBestActionSet,
)
from src.run.trace_writer import build_trace

CASE_ROW = {
    "case_id": "HHG-017",
    "opened_at": "2016-11-12 00:46:24",
    "trigger_type": "risk_score",
    "trigger_text": "Real-time model scored transaction 3450629 ($100.09, online) at 0.57.",
    "flagged_txn_id": 3450629,
    "card_id": "C04570-K1",
    "customer_id": "C04570",
    "risk_score": 0.57,
}


def _answer(**overrides) -> AnswerFile:
    case_kwargs = dict(
        status="closed_fraud", verdict="fraud", fraud_probability=0.86,
        pattern="card_testing", pattern_description="",
        affected_txn_ids=["3450629"], first_suspicious_txn_id="3450629",
        connected_card_ids=[], connected_device_profiles=[], exposure_usd=100.09,
        evidence=[Evidence(claim="x", source="graph", ref="query:card_window", entity_ids=["3450629"])],
        similar_prior_cases=["CC-0141"], summary="Fraud found.", written_to_graph=True,
        graph_case_id="CASE-HHG-017",
    )
    case_kwargs.update(overrides.pop("case", {}))
    sar = overrides.pop("sar", SAR(file=False, reason="No filing criteria met.", narrative="", subjects=[], total_amount_usd=0.0, activity_dates=[]))
    return AnswerFile(
        case_id="HHG-017",
        case=CaseRecord(**case_kwargs),
        evidence_requests=overrides.pop("evidence_requests", []),
        next_best_actions=NextBestActionSet(
            initial=[ActionEntry(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R1: single signal")],
            final=[ActionEntry(action="BLOCK_CARD", route="L1", reason="R2: customer denied")],
            what_changed="Customer denied.",
        ),
        sar=sar,
        stop_reason="Customer response settled the verdict.",
        tool_calls=7, tokens=5000, latency_s=42.0,
        **overrides,
    )


def _final_state(**overrides) -> dict:
    state = {
        "cutoff_ts": "2016-11-12 00:46:24",
        "evidence": [
            {"type": "card_window", "data": [{"id": "3450629", "ts": "2016-11-11 23:46:24", "TransactionAmt": 100.09, "channel": "online", "addr1": "204.0"}]},
            {"type": "customer_cards", "data": []},
            {"type": "device_neighbors", "data": []},
            {"type": "device_profile_label", "data": "SAMSUNG SM-G892A | Android 7.0 | Chrome | 1920x1080"},
            {"type": "closed_cases", "data": [{"id": "CC-0141", "outcome": "confirmed_fraud", "pattern": "card_testing"}]},
            {"type": "ring_membership", "data": {"ring_cluster_id": None, "cluster_prior_fraud_rate": None}},
            {"type": "knowledge", "data": {
                "knowledge": [{"id": "policy-r5", "source": "fraud_policy.md", "section": "R5", "distance": 0.2}],
                "similar_cases": [{"id": "CC-0141", "outcome": "confirmed_fraud", "pattern": "card_testing", "distance": 0.1, "type": "ClosedCase"}],
            }},
        ],
        "episode": {"txn_ids": ["3450629"], "first_txn_id": "3450629", "exposure_usd": 100.09, "detected_pattern": "card_testing"},
        "is_new_device": True, "is_proxy": False, "out_of_region": False,
        "shared_device": False, "shared_region": False, "cluster_prior_fraud_rate": 0.0,
        "recurring_charge_detected": False,
        "connected_card_ids": [], "connected_device_profiles": [],
        "initial_assessment": {"pattern": "card_testing", "fraud_probability": 0.72, "evidence_claims": [], "similar_prior_case_ids": []},
        "assessment": {"pattern": "card_testing", "fraud_probability": 0.86, "evidence_claims": [], "similar_prior_case_ids": ["CC-0141"]},
        "initial_policy_result": {"actions": [{"action": "VERIFY_WITH_CUSTOMER", "route": "auto", "reason": "R1: single signal"}], "sar_file": False, "sar_reason": "x"},
        "final_policy_result": {"actions": [{"action": "BLOCK_CARD", "route": "L1", "reason": "R2: customer denied"}], "sar_file": False, "sar_reason": "x"},
        "evidence_requests": [{"type": "customer_validation", "asked_after_step": 3, "assumed_response": "Customer denies."}],
        "tool_calls": 7,
        "stop_reason": "Customer response settled the verdict.",
    }
    state.update(overrides)
    return state


def _context(final_state: dict, **overrides) -> dict:
    ctx = {
        "final_state": final_state,
        "written_at": "2026-09-24 10:00:00",
        "graph_case_id": "CASE-HHG-017",
        "episode": final_state.get("episode") or {},
        "connected_card_ids": final_state.get("connected_card_ids") or [],
    }
    ctx.update(overrides)
    return ctx


def test_build_trace_has_all_required_top_level_keys():
    fs = _final_state()
    trace = build_trace(CASE_ROW, _answer(), _context(fs))
    required = {
        "schema_version", "case_id", "trigger", "cutoff_ts", "steps",
        "probability_timeline", "signals", "rules_fired", "retrieval",
        "subgraph", "graph_write", "validation", "llm",
    }
    assert required.issubset(trace.keys())
    assert trace["case_id"] == "HHG-017"


def test_trigger_converts_nan_risk_score_to_none():
    row = {**CASE_ROW, "risk_score": float("nan")}
    trace = build_trace(row, _answer(), _context(_final_state()))
    assert trace["trigger"]["risk_score"] is None
    assert not (isinstance(trace["trigger"]["risk_score"], float) and math.isnan(trace["trigger"]["risk_score"]))


def test_trigger_keeps_real_risk_score():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    assert trace["trigger"]["risk_score"] == 0.57


def test_steps_include_request_evidence_and_reassess_when_present():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    nodes = [s["node"] for s in trace["steps"]]
    assert "request_evidence" in nodes
    assert "reassess" in nodes
    assert "final_policy" in nodes


def test_steps_omit_request_evidence_when_no_evidence_was_requested():
    fs = _final_state(evidence_requests=[])
    trace = build_trace(CASE_ROW, _answer(evidence_requests=[]), _context(fs))
    nodes = [s["node"] for s in trace["steps"]]
    assert "request_evidence" not in nodes
    assert "reassess" not in nodes


def test_steps_always_end_with_write_case_read_back_validate():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    nodes = [s["node"] for s in trace["steps"]]
    assert nodes[-3:] == ["write_case", "read_back", "validate"]


def test_write_sar_step_present_only_when_sar_filed():
    with_sar = _answer(sar=SAR(file=True, reason="R2", narrative="x" * 50, subjects=["C04570"], total_amount_usd=100.09, activity_dates=["2016-11-11", "2016-11-11"]))
    trace = build_trace(CASE_ROW, with_sar, _context(_final_state()))
    assert "write_sar" in [s["node"] for s in trace["steps"]]

    without_sar = _answer()
    trace2 = build_trace(CASE_ROW, without_sar, _context(_final_state()))
    assert "write_sar" not in [s["node"] for s in trace2["steps"]]


def test_probability_timeline_has_initial_and_reassessed_points():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    probs = [p["fraud_probability"] for p in trace["probability_timeline"]]
    assert probs == [0.72, 0.86]


def test_probability_timeline_single_point_without_reassess():
    # No reassess step -> the timeline shows the one (initial) assessment
    # that actually happened, not a phantom second point.
    fs = _final_state(evidence_requests=[])
    trace = build_trace(CASE_ROW, _answer(evidence_requests=[]), _context(fs))
    assert len(trace["probability_timeline"]) == 1
    assert trace["probability_timeline"][0]["fraud_probability"] == 0.72


def test_signals_reflect_final_state_booleans():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    by_name = {s["name"]: s for s in trace["signals"]}
    assert by_name["card_testing_sequence"]["fired"] is True
    assert by_name["card_testing_sequence"]["entity_ids"] == ["3450629"]
    assert by_name["new_device"]["fired"] is True
    assert by_name["out_of_region"]["fired"] is False
    assert by_name["shared_device"]["fired"] is False


def test_signals_never_expose_a_subthreshold_cluster_rate_as_coordinated():
    # Consistency with the graph_flow.py fix: shared_region is already
    # gated at the 0.95 threshold, so trusting it here (rather than the
    # raw rate) can't resurface the baseline-noise bug.
    fs = _final_state(shared_region=False, cluster_prior_fraud_rate=0.85)
    trace = build_trace(CASE_ROW, _answer(), _context(fs))
    by_name = {s["name"]: s for s in trace["signals"]}
    assert by_name["coordinated_ring"]["fired"] is False


def test_rules_fired_extracted_from_action_reasons_deduplicated():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    rules = {(r["rule"], r["phase"]) for r in trace["rules_fired"]}
    assert ("R1", "initial") in rules
    assert ("R2", "final") in rules


def test_retrieval_prior_case_marked_used_when_cited_in_answer():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    prior = trace["retrieval"]["prior_cases"][0]
    assert prior["case_id"] == "CC-0141"
    assert prior["used"] is True


def test_retrieval_prior_case_not_used_when_not_cited():
    answer = _answer(case={"similar_prior_cases": []})
    trace = build_trace(CASE_ROW, answer, _context(_final_state()))
    prior = trace["retrieval"]["prior_cases"][0]
    assert prior["used"] is False


def test_subgraph_includes_subject_and_flagged_transaction():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    node_ids = {n["id"] for n in trace["subgraph"]["nodes"]}
    assert "C04570" in node_ids
    assert "C04570-K1" in node_ids
    assert "3450629" in node_ids
    flagged = next(n for n in trace["subgraph"]["nodes"] if n["id"] == "3450629")
    assert flagged["role"] == "flagged"


def test_subgraph_includes_this_case_node_when_written():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    this_case = [n for n in trace["subgraph"]["nodes"] if n["role"] == "this_case"]
    assert len(this_case) == 1
    assert this_case[0]["id"] == "CASE-HHG-017"


def test_subgraph_omits_this_case_node_when_not_written():
    answer = _answer(case={"written_to_graph": False, "graph_case_id": ""})
    trace = build_trace(CASE_ROW, answer, _context(_final_state()))
    assert not [n for n in trace["subgraph"]["nodes"] if n["role"] == "this_case"]


def test_graph_write_reflects_written_to_graph_and_written_at():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state(), written_at="2026-09-24 10:00:00"))
    assert trace["graph_write"] == {
        "written": True, "graph_case_id": "CASE-HHG-017",
        "read_back_ok": True, "written_at": "2026-09-24 10:00:00",
    }


def test_validation_reflects_passed_violations():
    trace_ok = build_trace(CASE_ROW, _answer(), _context(_final_state()), validation_errors=[])
    assert trace_ok["validation"] == {"passed": True, "errors": [], "warnings": []}

    trace_bad = build_trace(CASE_ROW, _answer(), _context(_final_state()), validation_errors=["bad id"])
    assert trace_bad["validation"]["passed"] is False
    assert trace_bad["validation"]["errors"] == ["bad id"]


def test_llm_info_passed_through():
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()), llm_provider="ollama", llm_model="qwen3:4b-instruct")
    assert trace["llm"] == {"provider": "ollama", "model": "qwen3:4b-instruct", "tokens": 5000}


def test_build_trace_is_json_serializable():
    import json
    trace = build_trace(CASE_ROW, _answer(), _context(_final_state()))
    json.dumps(trace)  # must not raise
