from __future__ import annotations

import pytest

from src.agent import graph_flow

FLAGGED_TS = "2016-11-11 23:46:24"


def _patch_evidence_functions(
    monkeypatch,
    *,
    device_network_result: dict,
    ring_result: dict,
    window: list[dict] | None = None,
    closed_cases: list[dict] | None = None,
    ring_ctx: dict | None = None,
):
    """Stub every graph/vector-search call `gather_evidence_node` makes, so its
    shared-origin / single_signal logic can be exercised without TigerGraph."""

    async def _fake_card_window(tg, card_id, hours, reference_txn_id=None, cutoff_ts=None):
        return window if window is not None else [
            {"id": reference_txn_id, "addr1": "204.0", "ts": FLAGGED_TS, "channel": "online",
             "TransactionAmt": 100.09, "ProductCD": "R", "id_15": "Found", "id_23": ""},
        ]

    async def _fake_customer_cards(tg, customer_id):
        return []

    async def _fake_device_network(tg, transaction_id, flagged_ts, cutoff_ts):
        return dict(device_network_result)

    async def _fake_device_profile_label(tg, transaction_id):
        return "FAKE_DEVICE | Android | Chrome | 1080x1920"

    async def _fake_closed_case_lookup(tg, card_id=None, device_id=None, addr1=None):
        return closed_cases or []

    async def _fake_ring_membership(tg, card_id):
        return ring_result

    async def _fake_ring_context(tg, cluster_id):
        return ring_ctx or {"ring_cluster_id": cluster_id, "n_cards": 1, "n_closed_cases": 1, "n_confirmed": 1}

    async def _fake_retrieve_knowledge(tg, query_text, top_k=5):
        return {"knowledge": [], "similar_cases": [], "closed_case_pool_size": 0}

    monkeypatch.setattr(graph_flow, "card_window", _fake_card_window)
    monkeypatch.setattr(graph_flow, "customer_cards", _fake_customer_cards)
    monkeypatch.setattr(graph_flow, "device_network", _fake_device_network)
    monkeypatch.setattr(graph_flow, "device_profile_label", _fake_device_profile_label)
    monkeypatch.setattr(graph_flow, "closed_case_lookup", _fake_closed_case_lookup)
    monkeypatch.setattr(graph_flow, "ring_membership", _fake_ring_membership)
    monkeypatch.setattr(graph_flow, "ring_context", _fake_ring_context)
    monkeypatch.setattr(graph_flow, "retrieve_knowledge", _fake_retrieve_knowledge)


_CASE_ROW = {
    "card_id": "C04570-K1",
    "customer_id": "C04570",
    "flagged_txn_id": "3450629",
    "trigger_type": "risk_score",
    "trigger_text": "Real-time model scored transaction 3450629 ($100.09, online) at 0.57.",
    "opened_at": "2016-11-12 00:46:24",
}


def _device(total, txns):
    return {"device_profile_id": "Dabc", "device_profile_label": "X | Android | Chrome | 1080x1920",
            "total_distinct_cards": total, "txns": txns, "fraud_cases": []}


def _dtx(txn_id, card, ts, amount, risk):
    return {"txn_id": txn_id, "card_id": card, "customer_id": card.split("-")[0], "ts": ts, "amount": amount, "risk_score": risk}


@pytest.mark.asyncio
async def test_generic_device_collision_is_not_shared_origin(monkeypatch):
    """HHG-017's real profile is shared by ~300 cards: a browser/OS
    collision, not a ring. It must not set shared_device or connected cards."""
    txns = [_dtx(f"T{i}", f"C{i:05d}-K1", "2016-11-11 20:00:00", 100.0, 0.9) for i in range(40)]
    _patch_evidence_functions(
        monkeypatch, device_network_result=_device(299, txns),
        ring_result={"ring_cluster_id": "RING-BIG", "cluster_prior_fraud_rate": 0.8469},
    )
    state = await graph_flow.gather_evidence_node(tg=None, state={"case_row": _CASE_ROW})
    assert state["shared_device"] is False
    assert state["shared_region"] is False
    assert state["connected_card_ids"] == []
    assert state["signals"]["device_network"]["generic_profile"] is True


@pytest.mark.asyncio
async def test_small_coordinated_device_is_corroborated_shared_origin(monkeypatch):
    txns = [
        _dtx("T2", "C00002-K1", "2016-11-10 10:00:00", 100.06, 0.71),
        _dtx("T3", "C00003-K1", "2016-11-11 20:00:00", 100.00, 0.91),
    ]
    _patch_evidence_functions(
        monkeypatch, device_network_result=_device(3, txns),
        ring_result={"ring_cluster_id": None, "cluster_prior_fraud_rate": None},
    )
    state = await graph_flow.gather_evidence_node(tg=None, state={"case_row": _CASE_ROW})
    assert state["shared_device"] is True
    assert state["connected_card_ids"] == ["C00002-K1", "C00003-K1"]
    assert state["connected_device_profiles"]
    assert "direct_network_corroboration" in state["families"]["suspicious"]


@pytest.mark.asyncio
async def test_high_cluster_rate_alone_never_sets_shared_origin(monkeypatch):
    """A 100% cluster rate built from one closed case (or a giant transitive
    component) is context, not R6 evidence."""
    _patch_evidence_functions(
        monkeypatch, device_network_result=_device(1, []),
        ring_result={"ring_cluster_id": "RING-X", "cluster_prior_fraud_rate": 1.0},
        ring_ctx={"ring_cluster_id": "RING-X", "n_cards": 2, "n_closed_cases": 1, "n_confirmed": 1},
    )
    state = await graph_flow.gather_evidence_node(tg=None, state={"case_row": _CASE_ROW})
    assert state["shared_device"] is False
    assert state["shared_region"] is False
    ring_ctx = next(e["data"] for e in state["evidence"] if e["type"] == "ring_context")
    assert ring_ctx["n_closed_cases"] == 1 and ring_ctx["cluster_prior_fraud_rate"] == 1.0


@pytest.mark.asyncio
async def test_generic_prior_closed_case_does_not_disable_single_signal(monkeypatch):
    """Test 22: any historical ClosedCase on the card used to make
    single_signal false. Existence alone is not evidence."""
    _patch_evidence_functions(
        monkeypatch, device_network_result=_device(1, []),
        ring_result={"ring_cluster_id": None, "cluster_prior_fraud_rate": None},
        closed_cases=[{"id": "CC-1383", "outcome": "confirmed_fraud", "pattern": "card_testing"}],
    )
    state = await graph_flow.gather_evidence_node(tg=None, state={"case_row": _CASE_ROW})
    assert state["single_signal"] is True
    assert state["independent_evidence_count"] == 0


@pytest.mark.asyncio
async def test_in_person_flagged_transaction_skips_device_query(monkeypatch):
    called = []
    window = [{"id": "3450629", "ts": FLAGGED_TS, "channel": "in_person", "TransactionAmt": 50.0,
               "ProductCD": "W", "addr1": "204.0"}]
    _patch_evidence_functions(monkeypatch, device_network_result={}, ring_result={}, window=window)

    async def _spy(*a, **k):
        called.append(a)
        return {}

    monkeypatch.setattr(graph_flow, "device_network", _spy)
    state = await graph_flow.gather_evidence_node(tg=None, state={"case_row": _CASE_ROW})
    assert called == []
    assert state["signals"]["cnp_burst"]["flagged_online"] is False


@pytest.mark.asyncio
async def test_customer_report_statement_is_denial_unless_strong_recurrence(monkeypatch):
    _patch_evidence_functions(monkeypatch, device_network_result=_device(1, []), ring_result={})
    row = {**_CASE_ROW, "trigger_type": "customer_report", "trigger_text": "I never made this $100.09 purchase."}
    state = await graph_flow.gather_evidence_node(tg=None, state={"case_row": row})
    assert state["customer_statement"] == "denies"
    assert "customer_statement" in state["families"]["suspicious"]
    assert "customer_statement" not in state["families_initial"]["suspicious"]


def test_deterministic_override_only_for_card_testing_and_strict_region():
    base = {"signals": {"card_testing": {"fired": False}, "out_of_region": {"fired": False}}}
    assert graph_flow._deterministic_pattern_override(base) is None
    cnp = {**base, "episode": {"detected_pattern": "cnp_burst"}, "is_new_device": True}
    assert graph_flow._deterministic_pattern_override(cnp) is None
    ct = {"signals": {"card_testing": {"fired": True}, "out_of_region": {"fired": True}}}
    assert graph_flow._deterministic_pattern_override(ct) == "card_testing"
    oor = {"signals": {"card_testing": {"fired": False}, "out_of_region": {"fired": True}}}
    assert graph_flow._deterministic_pattern_override(oor) == "out_of_region_use"


def test_assessment_prompt_states_the_required_rules():
    for phrase in (
        "risk_score is the alert trigger, not a verdict",
        "scores above 0.7 are often legitimate",
        "A device marked New alone is not fraud",
        "merely nearby in time is not part of the same episode",
        "Historical fraud on this card does not prove",
        "positive benign evidence",
        "increases uncertainty, not suspicion",
        "cleared prior cases",
        "Do not target any particular verdict distribution",
        "Pattern and verdict are separate decisions",
        "Never list a signal that did not fire",
    ):
        assert phrase in graph_flow.ASSESSMENT_RULES
    assert "trust these" not in graph_flow.ASSESSMENT_RULES.lower()


def test_assessment_output_constrains_probability():
    with pytest.raises(Exception):
        graph_flow.AssessmentOutput(
            pattern="none", pattern_if_fraud="card_not_present_fraud", recommended_verdict="uncertain",
            fraud_probability=1.4, probability_rationale="x", supporting_evidence_families=[],
            independent_evidence_count=0, evidence_claims=[],
        )
