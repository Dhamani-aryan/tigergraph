"""End-to-end run of the real LangGraph flow + run_case assembly with the
graph and the LLM stubbed out: proves that verdict, status, pattern, actions,
SAR and affected transactions come from one resolved decision and can never
contradict each other, whatever probability the model returns."""
from __future__ import annotations

import pytest

from src.agent import graph_flow, sar_writer
from src.agent.llm import ToolCallResult
from src.run import run_case
from src.run.dataset_index import DatasetIndex
from src.run.trace_writer import build_trace
from src.run.validate_outputs import validate_semantics
from tests.test_features import CUTOFF, _ts, history, txn

CARD = "C00001-K1"


class _FakeTg:
    def __init__(self):
        self.written: dict = {}

    async def call(self, tool, args):
        if tool == "tigergraph__add_nodes":
            v = args["vertices"][0]
            self.written = v
            return {"success": True}
        if tool == "tigergraph__get_node":
            return {"data": {"v_id": self.written.get("case_id"), "attributes": {"verdict": self.written.get("verdict")}}}
        raise AssertionError(tool)

    async def upsert_vectors(self, *a, **k):
        return {"success": True}


def _install(monkeypatch, window, *, device=None, probability=0.5, pattern="none",
             pattern_if_fraud="card_not_present_fraud", prompts=None):
    async def _card_window(tg, card_id, hours, reference_txn_id=None, cutoff_ts=None):
        return window

    async def _empty_list(*a, **k):
        return []

    async def _empty_dict(*a, **k):
        return {}

    async def _device(*a, **k):
        return dict(device or {})

    async def _label(*a, **k):
        return "X | Android | Chrome | 1080x1920" if device else ""

    async def _knowledge(tg, query_text, top_k=5):
        return {"knowledge": [], "similar_cases": [], "closed_case_pool_size": 0}

    async def _no_tool(prompt, tools, max_tool_calls=1):
        return ToolCallResult(tool_name=None, final_text="sufficient")

    async def _structured(prompt, schema, max_retries=2):
        if prompts is not None:
            prompts.append(prompt)
        if schema is graph_flow.AssessmentOutput:
            return schema(
                pattern=pattern, pattern_if_fraud=pattern_if_fraud, recommended_verdict="uncertain",
                fraud_probability=probability, probability_rationale="scripted", supporting_evidence_families=[],
                independent_evidence_count=0, evidence_claims=["scripted claim"],
            )
        return schema(narrative="Scripted SAR narrative.")

    for name, fn in (("card_window", _card_window), ("customer_cards", _empty_list), ("device_network", _device),
                     ("device_profile_label", _label), ("closed_case_lookup", _empty_list),
                     ("ring_membership", _empty_dict), ("ring_context", _empty_dict),
                     ("retrieve_knowledge", _knowledge), ("generate_with_tools", _no_tool),
                     ("generate_structured", _structured)):
        monkeypatch.setattr(graph_flow, name, fn)
    monkeypatch.setattr(sar_writer, "generate_structured", _structured)
    import src.ingestion.embeddings as embeddings
    monkeypatch.setattr(embeddings, "embed", lambda texts: [[0.0] for _ in texts])


def _row(trigger="risk_score", text="Real-time model scored transaction F ($41.00) at 0.9."):
    return {"case_id": "HHG-900", "opened_at": CUTOFF, "trigger_type": trigger, "trigger_text": text,
            "flagged_txn_id": "F", "card_id": CARD, "customer_id": "C00001", "risk_score": 0.9}


def _index(window):
    return DatasetIndex(
        transaction_ids=frozenset(t["id"] for t in window), card_ids=frozenset({CARD, "C00002-K1", "C00003-K1"}),
        customer_ids=frozenset({"C00001"}), closed_case_ids=frozenset(),
        txn_channel={t["id"]: t["channel"] for t in window if t.get("channel")},
        case_flagged_txn={"HHG-900": "F"},
    )


async def _run(window, row):
    tg = _FakeTg()
    answer, ctx = await run_case.run_single_case_with_context(tg, row)
    trace = build_trace(row, answer, ctx)
    return answer, ctx, trace, validate_semantics(answer, _index(window), trace)


BENIGN_IN_PERSON = history(30) + [txn("F", 0, 41.0, channel="in_person", product="W", addr1="100.0", risk=0.9)]


@pytest.mark.asyncio
async def test_strong_benign_profile_resolves_legitimate_with_clean_shape(monkeypatch):
    _install(monkeypatch, BENIGN_IN_PERSON, probability=0.45, pattern="card_not_present_fraud")
    answer, ctx, trace, violations = await _run(BENIGN_IN_PERSON, _row())
    case = answer.case
    assert answer.evidence_requests[0].assumed_response.startswith("Simulated assumption: customer confirms")
    assert (case.verdict, case.status, case.pattern) == ("legitimate", "closed_legitimate", "none")
    assert case.affected_txn_ids == [] and case.exposure_usd == 0 and case.first_suspicious_txn_id == ""
    assert answer.sar.file is False
    assert [a.action for a in answer.next_best_actions.final] == ["CLOSE_NO_FRAUD"]
    assert case.fraud_probability <= 0.15
    assert violations == []
    assert trace["behavior_profile"]["amount_class"] == "normal"


AMBIGUOUS_ONLINE = history(30, channel="online", product="C", addr1=None, step_hours=48) + [
    txn("F", 0, 70.0, product="R", id_15="New", risk=0.9)
]


@pytest.mark.asyncio
@pytest.mark.parametrize("probability", [0.2, 0.6, 0.84])
async def test_ambiguous_profile_stays_uncertain_below_the_decisive_bar(monkeypatch, probability):
    _install(monkeypatch, AMBIGUOUS_ONLINE, probability=probability, pattern="card_not_present_new_device")
    answer, ctx, trace, violations = await _run(AMBIGUOUS_ONLINE, _row())
    case = answer.case
    # the simulated response is identical for every model probability
    assert "did not reply" in answer.evidence_requests[0].assumed_response
    assert case.verdict == "uncertain"
    assert case.status in ("open", "escalated")
    final = [a.action for a in answer.next_best_actions.final]
    assert "BLOCK_CARD" not in final and "FILE_REPORT" not in final
    assert violations == []


@pytest.mark.asyncio
async def test_two_families_at_095_is_decisive_without_asking(monkeypatch):
    # Sec 6 met on the evidence alone (unseen ProductCD + new device): the
    # model's call decides it, not a simulated denial.
    _install(monkeypatch, AMBIGUOUS_ONLINE, probability=0.95, pattern="card_not_present_new_device")
    answer, ctx, trace, violations = await _run(AMBIGUOUS_ONLINE, _row())
    assert answer.evidence_requests == []
    assert answer.case.verdict == "fraud"
    assert trace["decision"]["final"]["settled_by"] == "probability_and_evidence"
    assert set(trace["decision"]["final"]["supporting_families"]) == {"behavioral_anomaly", "identity_anomaly"}
    assert violations == []


@pytest.mark.asyncio
async def test_high_probability_on_one_family_is_not_decisive(monkeypatch):
    window = history(30, channel="online", product="C", addr1=None, step_hours=48) + [txn("F", 0, 41.0, id_15="New")]
    _install(monkeypatch, window, probability=0.95)
    answer, ctx, *_ = await _run(window, _row())
    assert answer.evidence_requests, "0.95 on a single family must not stop the investigation"
    assert ctx["final_state"]["initial_decision"]["verdict"] == "uncertain"


def _recurring_window():
    rows = [txn(f"R{i}", -24 * (120 - i * 2), 20.0 + i * 3.1) for i in range(40)]
    return rows + [txn("PRIOR", -30 * 24, 12.99), txn("F", 0, 12.99)]


@pytest.mark.asyncio
async def test_customer_dispute_on_strong_recurrence_is_r7_never_closed_fraud(monkeypatch):
    window = _recurring_window()
    _install(monkeypatch, window, probability=0.9, pattern="card_not_present_fraud")
    row = _row("customer_report", "Customer message: 'I never made this $12.99 purchase.'")
    answer, ctx, trace, violations = await _run(window, row)
    case = answer.case
    assert (case.verdict, case.status, case.pattern) == ("legitimate", "closed_legitimate", "none")
    final = [a.action for a in answer.next_best_actions.final]
    assert final == ["CREATE_CASE", "VERIFY_WITH_CUSTOMER", "WARN_CUSTOMER"]
    assert answer.evidence_requests == []  # the customer already answered
    assert violations == []


@pytest.mark.asyncio
async def test_customer_denial_without_recurrence_is_fraud_with_episode(monkeypatch):
    window = history(30, channel="online", product="C", addr1=None, step_hours=48) + [txn("F", 0, 41.0)]
    _install(monkeypatch, window, probability=0.4, pattern="none", pattern_if_fraud="card_not_present_fraud")
    row = _row("customer_report", "Customer message: 'I never made this $41.00 purchase.'")
    answer, ctx, trace, violations = await _run(window, row)
    case = answer.case
    assert (case.verdict, case.status) == ("fraud", "closed_fraud")
    assert case.pattern == "card_not_present_fraud"
    assert case.fraud_probability >= 0.85
    assert case.affected_txn_ids == ["F"] and case.exposure_usd == 41.0
    assert "BLOCK_CARD" in [a.action for a in answer.next_best_actions.final]
    assert violations == []


@pytest.mark.asyncio
async def test_card_testing_with_multi_family_denial_blocks_and_names_pattern(monkeypatch):
    window = history(30, channel="online", product="C", addr1=None, step_hours=48, amount=20.0) + [
        txn("T1", -1.0, 1.10), txn("T2", -0.7, 2.40), txn("T3", -0.4, 0.95), txn("F", 0, 259.98, id_15="New"),
    ]
    _install(monkeypatch, window, probability=0.7, pattern="card_not_present_new_device")
    answer, ctx, trace, violations = await _run(window, _row())
    case = answer.case
    assert "Simulated assumption: customer states they did not make" in answer.evidence_requests[0].assumed_response
    assert (case.verdict, case.pattern) == ("fraud", "card_testing")
    assert case.affected_txn_ids == ["T1", "T2", "T3", "F"]
    assert {"DECLINE_TRANSACTION", "BLOCK_CARD", "CREATE_CASE"} <= {a.action for a in answer.next_best_actions.final}
    assert {"DECLINE_TRANSACTION", "STEP_UP_AUTH"} <= {a.action for a in answer.next_best_actions.initial}
    assert violations == []


@pytest.mark.asyncio
async def test_prompt_carries_structured_observations_and_rules(monkeypatch):
    prompts: list[str] = []
    _install(monkeypatch, BENIGN_IN_PERSON, probability=0.45, prompts=prompts)
    await _run(BENIGN_IN_PERSON, _row())
    first = prompts[0]
    assert "risk_score is the alert trigger, not a verdict" in first
    assert "behavior_profile" in first and "evidence_families" in first
    assert "graph_context_only" in first
    assert "trust these" not in first.lower()


@pytest.mark.asyncio
async def test_corroborated_device_populates_only_corroborated_cards(monkeypatch):
    window = [txn("F", 0, 99.92, id_15="New", risk=0.9)]
    device = {
        "device_profile_id": "Dabc", "device_profile_label": "X | Android | Chrome | 1080x1920",
        "total_distinct_cards": 4, "fraud_cases": [],
        "txns": [
            {"txn_id": "T2", "card_id": "C00002-K1", "ts": _ts(-40), "amount": 100.06, "risk_score": 0.71},
            {"txn_id": "T3", "card_id": "C00003-K1", "ts": _ts(-3), "amount": 100.00, "risk_score": 0.91},
            {"txn_id": "T4", "card_id": "C00004-K1", "ts": _ts(-2), "amount": 7.00, "risk_score": 0.05},
        ],
    }
    _install(monkeypatch, window, device=device, probability=0.9, pattern="card_not_present_new_device")
    answer, ctx, trace, violations = await _run(window, _row())
    fs = ctx["final_state"]
    assert fs["connected_card_ids"] == ["C00002-K1", "C00003-K1"]
    if answer.case.verdict == "fraud":
        assert answer.case.connected_card_ids == ["C00002-K1", "C00003-K1"]
        assert "FILE_REPORT" in [a.action for a in answer.next_best_actions.final]
    assert violations == []
