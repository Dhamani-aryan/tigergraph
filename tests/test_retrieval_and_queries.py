from __future__ import annotations

import random

import pytest

from src.graph import queries
from src.graph.queries import (
    parse_closed_case_features,
    parse_device_network_result,
)
from src.graph.vector_search import (
    balance_closed_cases,
    closed_case_candidates,
    knowledge_candidates,
    matched_prior_case,
    retrieve_knowledge,
    score_closed_case,
)


def _cc(cid, outcome, pattern, channel="online", product="C", amount=100.0, new=0, n=1, notes=""):
    return {"id": cid, "type": "ClosedCase", "outcome": outcome, "pattern": pattern, "channels": [channel],
            "products": [product], "max_amount": amount, "n_new_device": new, "n_txns": n, "n_involved": n,
            "analyst_notes": notes, "exposure_usd": amount}


CATALOG = [
    _cc("CC-0001", "confirmed_fraud", "card_not_present_new_device", amount=95.0, new=1),
    _cc("CC-0002", "confirmed_fraud", "card_not_present_fraud", amount=900.0),
    _cc("CC-0003", "confirmed_fraud", "out_of_region_use", channel="in_person", product="W"),
    _cc("CC-0004", "confirmed_fraud", "card_not_present_new_device", amount=110.0, new=1, n=3),
    _cc("CC-0005", "cleared", "none", amount=105.0, new=1, notes="Cardholder confirmed the purchase from a new phone."),
    _cc("CC-0006", "cleared", "none", channel="in_person", product="W",
        notes="Cardholder confirmed travel to the billing region in question."),
    _cc("CC-0007", "cleared", "none", amount=20.0, notes="Cardholder confirmed the purchase."),
]
SHAPE = {"trigger_type": "risk_score", "channel": "online", "product": "C", "amount": 99.92, "amount_class": "normal",
         "is_new_device": True, "episode_size": 1, "card_testing": False, "network_corroborated": False,
         "candidate_patterns": ["card_not_present_new_device", "account_takeover"]}


class _FakeTg:
    """TigerGraph stand-in: answers the two structured-retrieval installed
    queries, records every call, and fails the test on any vector search,
    vector upsert, or FraudCase access."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def gsql(self, text):
        name = text.split("CREATE OR REPLACE QUERY ")[1].split("(")[0]
        return {"data": {"result": f"Successfully created queries: [{name}]. Query installation finished."}}

    async def run_installed_query(self, name, params):
        self.calls.append((name, params))
        if name == "closed_case_features":
            return {"data": {"result": [{"cases": [
                {"v_id": c["id"], "v_type": "ClosedCase", "attributes": {
                    "Cases.outcome": c["outcome"], "Cases.pattern": c["pattern"], "Cases.exposure_usd": c["exposure_usd"],
                    "Cases.n_txns": c["n_txns"], "Cases.analyst_notes": c["analyst_notes"],
                    "Cases.@channels": c["channels"], "Cases.@products": c["products"],
                    "Cases.@n_new_device": c["n_new_device"], "Cases.@n_involved": c["n_involved"],
                    "Cases.@max_amount": c["max_amount"], "Cases.@min_amount": c["max_amount"]}}
                for c in CATALOG]}]}}
        if name == "knowledge_docs_by_source":
            src = params["doc_source"]
            ids = ["policy-r1", "policy-r2", "policy-r3", "policy-r4", "policy-r8", "policy-case-vs-report"] \
                if src == "policy" else ["pattern-cnp-new-device", "pattern-cnp-fraud", "pattern-account-takeover"]
            return {"data": {"result": [{"docs": [
                {"v_id": i, "v_type": "KnowledgeDoc", "attributes": {"Docs.source": src, "Docs.section": i, "Docs.text": i}}
                for i in ids]}]}}
        raise AssertionError(f"unexpected installed query {name}")

    async def search_top_k_similarity(self, *a, **k):
        raise AssertionError("vector search must not run on the live path")

    async def upsert_vectors(self, *a, **k):
        raise AssertionError("no vector upsert on the live path")


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    monkeypatch.setattr(queries, "_CLOSED_CASE_CACHE", None)
    monkeypatch.setattr(queries, "_KNOWLEDGE_CACHE", {})
    monkeypatch.setattr(queries, "_INSTALLED_QUERIES", set())


@pytest.mark.asyncio
async def test_structured_retrieval_uses_only_installed_queries_and_no_fraudcase():
    tg = _FakeTg()
    result = await retrieve_knowledge(tg, SHAPE)
    assert {name for name, _ in tg.calls} == {"closed_case_features", "knowledge_docs_by_source"}
    assert result["vector_search_used"] is False
    assert result["retrieval_method"] == "tigergraph_structured_candidates+gpt_rerank"
    ids = [c["id"] for cs in result["closed_case_candidates"].values() for c in cs]
    assert all(i.startswith("CC-") for i in ids)
    # channel filter: in-person cases never become candidates for an online case
    assert "CC-0003" not in ids and "CC-0006" not in ids


@pytest.mark.asyncio
async def test_batch_order_cannot_change_retrieval_evidence():
    first = await retrieve_knowledge(_FakeTg(), SHAPE)
    second = await retrieve_knowledge(_FakeTg(), SHAPE)
    assert first == second


def test_candidates_are_balanced_and_scored_by_behavioral_features():
    cands = closed_case_candidates(CATALOG, SHAPE)
    assert [c["id"] for c in cands["confirmed_fraud"]][0] == "CC-0001"  # same device status, amount, episode size
    assert [c["id"] for c in cands["cleared"]][0] == "CC-0005"  # cleared because of a new phone
    top = cands["confirmed_fraud"][0]
    assert top["full_match"] is True and top["shared_anomalies"] == ["new_device"]
    assert set(top["matched_features"]) >= {"channel", "product", "amount", "device", "pattern_shape"}
    picked = balance_closed_cases(cands["confirmed_fraud"] + cands["cleared"])
    assert [c["outcome"] for c in picked].count("confirmed_fraud") <= 3
    assert [c["outcome"] for c in picked].count("cleared") <= 3
    shuffled = CATALOG[:]
    random.Random(3).shuffle(shuffled)
    assert closed_case_candidates(shuffled, SHAPE) == cands


def test_score_ignores_device_when_unavailable():
    score, applicable, matched = score_closed_case({**SHAPE, "is_new_device": None}, CATALOG[0])
    assert "device" not in matched and applicable == 5


def test_prior_case_family_requires_one_sided_full_match():
    cands = closed_case_candidates(CATALOG, SHAPE)
    # both a fraud and a cleared case fully match -> history does not discriminate
    assert matched_prior_case(cands) is None


def test_knowledge_candidates_selected_by_known_source_and_section():
    docs = [{"id": i} for i in ("policy-r1", "policy-r5", "policy-r7", "policy-case-vs-report",
                                "pattern-card-testing", "pattern-cnp-new-device")]
    ids = [d["id"] for d in knowledge_candidates(docs, {**SHAPE, "card_testing": True,
                                                        "candidate_patterns": ["card_testing"]})]
    assert "policy-r5" in ids and "pattern-card-testing" in ids and "policy-r7" not in ids
    report = [d["id"] for d in knowledge_candidates(docs, {**SHAPE, "trigger_type": "customer_report"})]
    assert "policy-r7" in report


def test_parse_closed_case_features_shape():
    tg_rows = [{"cases": [{"v_id": "CC-9", "v_type": "ClosedCase", "attributes": {
        "Cases.outcome": "cleared", "Cases.pattern": "none", "Cases.analyst_notes": "x",
        "Cases.@channels": ["online"], "Cases.@products": ["C"], "Cases.@n_new_device": 1,
        "Cases.@n_involved": 1, "Cases.@max_amount": 10.0, "Cases.@min_amount": 10.0}}]}]
    row = parse_closed_case_features(tg_rows)[0]
    assert row["id"] == "CC-9" and row["channels"] == ["online"] and row["n_new_device"] == 1


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


@pytest.mark.asyncio
async def test_disabled_endpoint_is_retried_with_backoff(monkeypatch):
    sleeps = []

    async def _sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(queries.asyncio, "sleep", _sleep)

    class _Flaky:
        n = 0

        async def run_installed_query(self, name, params):
            self.n += 1
            if self.n < 4:
                raise RuntimeError("Query endpoint is disabled (REST-1005)")
            return {"ok": True}

    tg = _Flaky()
    assert await queries._run_installed_query(tg, "ring_membership", {}) == {"ok": True}
    assert sleeps == [5.0, 10.0, 20.0]
