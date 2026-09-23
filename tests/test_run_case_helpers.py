from __future__ import annotations

from src.run.run_case import _grounded_similar_cases, _what_changed
from src.agent.schemas import ActionEntry


def test_grounded_similar_cases_keeps_ids_from_graph_traversal_evidence():
    final_state = {
        "evidence": [
            {"type": "closed_cases", "data": [{"id": "CC-1383", "outcome": "cleared"}]},
        ],
    }
    assert _grounded_similar_cases(final_state, ["CC-1383", "CC-9999"]) == ["CC-1383"]


def test_grounded_similar_cases_keeps_ids_from_vector_search_evidence():
    # Regression test: assess_node's prompt points the LLM at retrieve_knowledge's
    # vector hits ("knowledge.similar_cases"), not the graph-traversal
    # "closed_cases" evidence -- a filter checking only the latter would zero
    # out every legitimate vector-retrieved match (the bug this locks in).
    final_state = {
        "evidence": [
            {"type": "closed_cases", "data": []},
            {"type": "knowledge", "data": {
                "knowledge": [], "similar_cases": [{"id": "CC-0141", "type": "ClosedCase"}],
            }},
        ],
    }
    assert _grounded_similar_cases(final_state, ["CC-0141", "CC-9999"]) == ["CC-0141"]


def test_grounded_similar_cases_rejects_self_cited_fraudcase_id():
    # Regression test: caught live on HHG-001's actual batch output --
    # retrieve_knowledge's "similar_cases" mixes ClosedCase AND FraudCase
    # vector hits together, and an earlier version of the grounding filter
    # accepted either. "CASE-HHG-001" is a FraudCase id (this pipeline's own
    # write), not a closed_cases_history.csv id, and must never be accepted
    # here even though it's a real graph vertex the vector search actually
    # returned.
    final_state = {
        "evidence": [
            {"type": "closed_cases", "data": []},
            {"type": "knowledge", "data": {
                "knowledge": [],
                "similar_cases": [
                    {"id": "CASE-HHG-001", "type": "FraudCase"},
                    {"id": "CC-1066", "type": "ClosedCase"},
                ],
            }},
        ],
    }
    assert _grounded_similar_cases(final_state, ["CASE-HHG-001", "CC-1066"]) == ["CC-1066"]


def test_grounded_similar_cases_drops_ids_not_seen_in_any_evidence():
    final_state = {"evidence": []}
    assert _grounded_similar_cases(final_state, ["CC-INVENTED"]) == []


def test_grounded_similar_cases_handles_missing_evidence_key():
    assert _grounded_similar_cases({}, ["CC-0141"]) == []


def _action(name: str) -> list[ActionEntry]:
    return [ActionEntry(action=name, route="auto", reason="x")]


def test_what_changed_nothing_when_actions_identical():
    assert _what_changed({"trigger_type": "risk_score"}, {}, _action("CLOSE_NO_FRAUD"), _action("CLOSE_NO_FRAUD")) == "nothing"


def test_what_changed_customer_report_denies_path():
    result = _what_changed(
        {"trigger_type": "customer_report"}, {"recurring_charge_detected": False},
        _action("VERIFY_WITH_CUSTOMER"), _action("BLOCK_CARD"),
    )
    assert "already on file" in result


def test_what_changed_customer_report_recurring_path():
    result = _what_changed(
        {"trigger_type": "customer_report"}, {"recurring_charge_detected": True},
        _action("VERIFY_WITH_CUSTOMER"), _action("CLOSE_NO_FRAUD"),
    )
    assert "recurring" in result.lower()


def test_what_changed_simulated_evidence_path():
    result = _what_changed(
        {"trigger_type": "risk_score"}, {"evidence_requests": [{"type": "customer_validation"}]},
        _action("VERIFY_WITH_CUSTOMER"), _action("BLOCK_CARD"),
    )
    assert "Simulated" in result
