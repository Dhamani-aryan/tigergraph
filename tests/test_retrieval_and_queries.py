from __future__ import annotations

import random

import pytest

from src.graph import vector_search
from src.graph.queries import parse_device_network_result
from src.graph.vector_search import balance_closed_cases, retrieve_knowledge


def _hit(vid, vtype, distance, **attrs):
    return {"v_id": vid, "v_type": vtype, "attributes": attrs, "_d": distance}


def _envelope(hits):
    return {"success": True, "data": {"result": [
        {"v": [{k: v for k, v in h.items() if k != "_d"} for h in hits]},
        {"distances": {h["v_id"]: h["_d"] for h in hits}},
    ]}}


class _FakeTg:
    """Records every vector search; a FraudCase index that keeps growing
    between calls stands in for earlier batch runs writing case memory."""

    def __init__(self):
        self.searched: list[str] = []
        self.fraud_case_hits = [_hit("CASE-HHG-001", "FraudCase", 0.01, pattern="out_of_region_use")]
        self.closed = [
            _hit(f"CC-{i:04d}", "ClosedCase", 0.10 + i / 100, outcome="confirmed_fraud" if i % 3 else "cleared",
                 pattern="card_testing" if i % 3 else "none")
            for i in range(12)
        ]

    async def search_top_k_similarity(self, vertex_type, attr, vector, top_k):
        self.searched.append(vertex_type)
        if vertex_type == "FraudCase":
            return _envelope(self.fraud_case_hits)
        if vertex_type == "ClosedCase":
            return _envelope(self.closed[:top_k])
        return _envelope([_hit("policy-r1", "KnowledgeDoc", 0.2, section="R1")])


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    monkeypatch.setattr(vector_search, "embed", lambda texts: [[0.0, 1.0] for _ in texts])


@pytest.mark.asyncio
async def test_fraudcase_vectors_are_excluded_from_benchmark_retrieval():
    tg = _FakeTg()
    result = await retrieve_knowledge(tg, "trigger risk_score; channel online")
    assert "FraudCase" not in tg.searched
    assert all(c["type"] == "ClosedCase" for c in result["similar_cases"])
    assert not any(c["id"].startswith("CASE-") for c in result["similar_cases"])


@pytest.mark.asyncio
async def test_batch_order_cannot_change_retrieval_evidence():
    tg = _FakeTg()
    first = await retrieve_knowledge(tg, "same query")
    # Earlier cases in a batch write more FraudCase memory...
    tg.fraud_case_hits.append(_hit("CASE-HHG-002", "FraudCase", 0.0, pattern="card_not_present_fraud"))
    second = await retrieve_knowledge(tg, "same query")
    assert first == second


def test_balanced_prior_cases_three_each_order_independent():
    hits = [
        {"id": f"CC-{i}", "type": "ClosedCase", "distance": 0.1 + i / 100,
         "outcome": "confirmed_fraud" if i < 8 else "cleared"}
        for i in range(12)
    ] + [{"id": "CASE-HHG-001", "type": "FraudCase", "distance": 0.0}]
    picked = balance_closed_cases(hits)
    outcomes = [h["outcome"] for h in picked]
    assert outcomes.count("confirmed_fraud") == 3 and outcomes.count("cleared") == 3
    shuffled = hits[:]
    random.Random(7).shuffle(shuffled)
    assert balance_closed_cases(shuffled) == picked


def test_parse_device_network_result_shape():
    raw = [
        {"device": [{"v_id": "Dabc", "v_type": "DeviceProfile", "attributes": {
            "DevStep.device_info": "SM-G", "DevStep.os": "Android 7.0", "DevStep.browser": "chrome", "DevStep.screen": "1080x1920"}}]},
        {"n_cards": 3},
        {"window_txns": [{"v_id": "3503878", "v_type": "Transaction", "attributes": {
            "WinSet.ts": "2016-12-01 21:28:53", "WinSet.TransactionAmt": 99.92, "WinSet.risk_score": 0.9,
            "WinSet.customer_id": "C07987", "WinSet.@card": ["C07987-K2"]}}]},
        {"fraud_txns": [{"v_id": "3400001", "v_type": "Transaction", "attributes": {
            "FraudSet.ts": "2016-11-20 10:00:00", "FraudSet.customer_id": "C00001",
            "FraudSet.@card": ["C00001-K1"], "FraudSet.@fraud_cases": ["CC-0001"]}}]},
    ]
    parsed = parse_device_network_result(raw)
    assert parsed["device_profile_id"] == "Dabc"
    assert parsed["device_profile_label"] == "SM-G | Android 7.0 | chrome | 1080x1920"
    assert parsed["total_distinct_cards"] == 3
    assert parsed["txns"][0] == {
        "txn_id": "3503878", "ts": "2016-12-01 21:28:53", "amount": 99.92, "risk_score": 0.9,
        "customer_id": "C07987", "card_id": "C07987-K2",
    }
    assert parsed["fraud_cases"] == [{
        "case_id": "CC-0001", "outcome": "confirmed_fraud", "txn_id": "3400001",
        "txn_ts": "2016-11-20 10:00:00", "card_id": "C00001-K1",
    }]


def test_parse_device_network_result_empty_for_no_device():
    assert parse_device_network_result([{"device": []}, {"n_cards": 0}]) == {}
