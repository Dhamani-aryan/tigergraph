from __future__ import annotations

from typing import Any

from src.ingestion.embeddings import embed
from src.tg_client import TigerGraphMCP

# Balanced prior-case sample shown to the assessment (Task 14 fix): the
# closed-case history is 83.8% confirmed fraud, so an unbalanced top-k hands
# the model that base rate as if it were evidence about this case.
CLOSED_CASE_POOL_K = 30
PER_OUTCOME_K = 3


def _unwrap_hits(search_result: Any) -> list[dict]:
    """Unwrap `tg.search_top_k_similarity(...)`'s envelope down to a flat
    list of `{"id", "source"/"section"/etc..., "distance"}` rows.

    Confirmed live (this task): like `tg.gsql`/`tg.run_installed_query`,
    `tg.search_top_k_similarity` returns the `{"success", "data", ...}`
    envelope, not a bare list. The real shape, confirmed live against the
    populated `KnowledgeDoc` index (660 vectors, per Task 9): `data["result"]`
    is a two-element list -- `[{"v": [{"v_id", "v_type", "attributes": {...}},
    ...]}, {"distances": {v_id: float, ...}}]` -- i.e. the hit vertices and
    their distances arrive as two SEPARATE top-level entries, joined only by
    `v_id`, not already paired together per-hit. This function joins them
    itself so callers get one row per hit with its own distance attached.
    """
    if not isinstance(search_result, dict):
        return []
    rows = search_result.get("data", {}).get("result", []) or []
    vertices: list[dict] = []
    distances: dict[str, float] = {}
    for entry in rows:
        if "v" in entry:
            vertices.extend(entry["v"])
        if "distances" in entry:
            distances.update(entry["distances"])

    hits: list[dict] = []
    for v in vertices:
        row: dict[str, Any] = {"id": v.get("v_id"), "type": v.get("v_type")}
        row.update(v.get("attributes", {}))
        if v.get("v_id") in distances:
            row["distance"] = distances[v["v_id"]]
        hits.append(row)
    return hits


def balance_closed_cases(hits: list[dict], per_outcome: int = PER_OUTCOME_K) -> list[dict]:
    """Up to `per_outcome` most similar confirmed_fraud and up to
    `per_outcome` most similar cleared ClosedCases. Only ClosedCase hits
    are ever kept (FraudCase rows from earlier runs are not benchmark
    evidence). Ties broken by id so the selection is order-independent."""
    closed = [h for h in hits if h.get("type") == "ClosedCase" and h.get("id")]
    closed.sort(key=lambda h: (h.get("distance", float("inf")), str(h["id"])))
    fraud = [h for h in closed if h.get("outcome") == "confirmed_fraud"][:per_outcome]
    cleared = [h for h in closed if h.get("outcome") == "cleared"][:per_outcome]
    return sorted(fraud + cleared, key=lambda h: (h.get("distance", float("inf")), str(h["id"])))


async def retrieve_knowledge(
    tg: TigerGraphMCP, query_text: str, top_k: int = 5, case_pool_k: int = CLOSED_CASE_POOL_K,
) -> dict[str, list[dict]]:
    """Semantic search over policy/pattern/regulatory knowledge (`KnowledgeDoc`)
    and the immutable closed-case history (`ClosedCase`).

    Task 14 fix: `FraudCase` vectors are deliberately NOT searched. They are
    this pipeline's own earlier outputs (including failed all-fraud runs and
    the current case's own previous write), so retrieving them as evidence
    made one run's mistakes the next run's "prior cases" and made results
    depend on batch order. FraudCase memory is still written to the graph
    by run_case.py; it just never feeds a benchmark assessment.
    """
    query_vector = embed([query_text])[0]
    knowledge_raw = await tg.search_top_k_similarity("KnowledgeDoc", "embedding", query_vector, top_k)
    closed_case_raw = await tg.search_top_k_similarity("ClosedCase", "embedding", query_vector, case_pool_k)

    pool = _unwrap_hits(closed_case_raw)
    return {
        "knowledge": _unwrap_hits(knowledge_raw),
        "similar_cases": balance_closed_cases(pool),
        "closed_case_pool_size": len([h for h in pool if h.get("type") == "ClosedCase"]),
    }


def build_similarity_query(
    trigger_type: str, profile: Any, card_testing: Any, cnp: Any, region: Any, network: Any, recurrence: Any,
) -> str:
    """Retrieval text built from the case's measured shape rather than the
    generic trigger prose (which put every ClosedCase at ~0.18 distance)."""
    parts = [f"trigger {trigger_type}", f"channel {profile.flagged_channel or 'unknown'}"]
    if profile.amount_ratio is not None:
        parts.append(f"amount {profile.amount_class} {profile.amount_ratio:.1f}x median percentile {profile.amount_percentile:.2f}")
    parts.append(f"product {profile.flagged_product or 'unknown'} {profile.product_class}")
    if profile.flagged_channel == "online":
        parts.append("device new" if profile.is_new_device else "device found" if profile.is_new_device is False else "device unknown")
        if profile.is_proxy:
            parts.append(f"proxy {profile.proxy_type}")
    if card_testing.fired:
        parts.append("card testing small authorizations then larger purchase")
    elif cnp.documented_burst:
        parts.append(f"card not present burst {cnp.online_count} online transactions 48 hours")
    else:
        parts.append("single transaction no burst")
    parts.append("out of region card present new region home activity" if region.fired else f"region {profile.region_class}")
    parts.append("shared device other cards corroborated" if network.corroborated else "no shared device ring")
    if recurrence.tier != "none":
        parts.append(f"recurring monthly charge {recurrence.tier}")
    return "; ".join(parts)
