from __future__ import annotations

import pytest

from src.agent import graph_flow


def _patch_evidence_functions(
    monkeypatch,
    *,
    device_neighbors_result: list[dict],
    ring_result: dict,
):
    """Stub every graph/vector-search call `gather_evidence_node` makes, so its
    shared_device/shared_region/single_signal logic can be exercised synthetically,
    without a live TigerGraph connection or LLM call."""

    async def _fake_card_window(tg, card_id, hours, reference_txn_id=None, cutoff_ts=None):
        # The flagged transaction itself must be present so gather_evidence_node's
        # (indirect, via later nodes) reference resolution has something real to
        # find; the specific fields don't matter for the shared_device assertions.
        return [{"id": reference_txn_id, "addr1": "204.0", "ts": "2016-11-11 23:46:24"}]

    async def _fake_customer_cards(tg, customer_id):
        return []

    async def _fake_device_neighbors(tg, transaction_id, cutoff_ts=None):
        return device_neighbors_result

    async def _fake_closed_case_lookup(tg, card_id=None, device_id=None, addr1=None):
        return []

    async def _fake_ring_membership(tg, card_id):
        return ring_result

    async def _fake_retrieve_knowledge(tg, query_text, top_k=5):
        return {"knowledge": [], "similar_cases": []}

    monkeypatch.setattr(graph_flow, "card_window", _fake_card_window)
    monkeypatch.setattr(graph_flow, "customer_cards", _fake_customer_cards)
    monkeypatch.setattr(graph_flow, "device_neighbors", _fake_device_neighbors)
    monkeypatch.setattr(graph_flow, "closed_case_lookup", _fake_closed_case_lookup)
    monkeypatch.setattr(graph_flow, "ring_membership", _fake_ring_membership)
    monkeypatch.setattr(graph_flow, "retrieve_knowledge", _fake_retrieve_knowledge)


_CASE_ROW = {
    "card_id": "C04570-K1",
    "customer_id": "C04570",
    "flagged_txn_id": "3450629",
    "trigger_type": "risk_score",
    "trigger_text": "Real-time model scored transaction 3450629 ($100.09, online) at 0.57.",
    # HHG-017's real case-open time (1h after the flagged txn's own ts,
    # 2016-11-11 23:46:24) -- gather_evidence_node now requires opened_at to
    # compute cutoff_ts for the temporal leakage fix.
    "opened_at": "2016-11-12 00:46:24",
}


@pytest.mark.asyncio
async def test_shared_device_false_positive_gated_by_cardinality(monkeypatch):
    """Task 12 review: shared_device used to be set from bare `bool(neighbors)`, so a
    generic-device-fingerprint collision shared by hundreds of unrelated cards (HHG-017's
    real live device_neighbors result: 299 cards, all in the same low-rate cluster --
    exactly the collision Task 8's manual checkpoint already identified as noise, not a
    real ring) counted as a genuine shared-device signal. Reproduces that shape
    synthetically and asserts the new cardinality gate excludes it."""
    large_collision = [
        {"id": f"C{i:05d}-K1", "ring_cluster_id": "RING-BIG", "cluster_prior_fraud_rate": 0.8469}
        for i in range(299)
    ]
    _patch_evidence_functions(
        monkeypatch,
        device_neighbors_result=large_collision,
        ring_result={"ring_cluster_id": "RING-BIG", "cluster_prior_fraud_rate": 0.8469},
    )

    state = await graph_flow.gather_evidence_node(tg=None, state={"case_row": _CASE_ROW})

    assert state["shared_device"] is False
    # 0.8469 is below the 0.95 coordinated threshold too, so shared_region should stay
    # False here as well -- confirms this isn't accidentally passing via the sibling
    # signal instead of the cardinality gate actually doing the filtering.
    assert state["shared_region"] is False


@pytest.mark.asyncio
async def test_shared_device_true_for_small_genuine_collision(monkeypatch):
    """A small number of cards genuinely sharing a device (below the collision cap)
    should still count as a real shared-device signal -- confirms the cardinality gate
    doesn't over-correct into never flagging device sharing at all."""
    small_collision = [
        {"id": "C00001-K1", "ring_cluster_id": None, "cluster_prior_fraud_rate": None},
        {"id": "C00002-K1", "ring_cluster_id": None, "cluster_prior_fraud_rate": None},
    ]
    _patch_evidence_functions(
        monkeypatch,
        device_neighbors_result=small_collision,
        ring_result={"ring_cluster_id": None, "cluster_prior_fraud_rate": None},
    )

    state = await graph_flow.gather_evidence_node(tg=None, state={"case_row": _CASE_ROW})

    assert state["shared_device"] is True


@pytest.mark.asyncio
async def test_shared_device_true_when_cluster_genuinely_coordinated_even_if_collision_large(monkeypatch):
    """A large device collision that ALSO clears the coordinated cluster-fraud-rate
    threshold should still count as shared -- the cardinality gate only removes the
    ungated bool(neighbors) path, it must not suppress the independent, already-gated
    `coordinated` signal."""
    large_collision = [
        {"id": f"C{i:05d}-K1", "ring_cluster_id": "RING-REAL", "cluster_prior_fraud_rate": 0.97}
        for i in range(50)
    ]
    _patch_evidence_functions(
        monkeypatch,
        device_neighbors_result=large_collision,
        ring_result={"ring_cluster_id": "RING-REAL", "cluster_prior_fraud_rate": 0.97},
    )

    state = await graph_flow.gather_evidence_node(tg=None, state={"case_row": _CASE_ROW})

    assert state["shared_device"] is True
    assert state["shared_region"] is True
