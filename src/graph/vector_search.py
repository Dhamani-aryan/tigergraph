from __future__ import annotations

import math
from typing import Any

from src.graph.queries import closed_case_features, knowledge_docs_by_source
from src.tg_client import TigerGraphMCP

# --------------------------------------------------------------------------
# Retrieval for a live investigation (Task 14 architecture change)
# --------------------------------------------------------------------------
# Live case path: TigerGraph structured candidate retrieval -> deterministic
# scoring against the corrected behavioral features -> balanced candidates
# per outcome -> GPT-5.5/Pi structured reranking (graph_flow._rerank_retrieval)
# -> assessment.
#
# No embedding is generated and no vector search runs on the live path: the
# only embedding model the stored ClosedCase/KnowledgeDoc vectors are
# compatible with is a local Ollama model, which investigations no longer
# require, and GPT-5.5 through Pi is not an embeddings endpoint. The stored
# vector attributes stay in TigerGraph untouched; `_unwrap_hits` remains for
# the ingestion scripts.
#
# FraudCase rows (this pipeline's own earlier outputs, including failed
# runs and the current case's previous write) are never retrieved, so one
# run's mistakes cannot become the next run's evidence and batch order
# cannot change what a case sees.

RETRIEVAL_METHOD = "tigergraph_structured_candidates+gpt_rerank"
CANDIDATES_PER_OUTCOME = 8
PER_OUTCOME_K = 3
MAX_DOC_CANDIDATES = 10
AMOUNT_SIMILAR_FACTOR = 2.0


def _unwrap_hits(search_result: Any) -> list[dict]:
    """Unwrap a `tg.search_top_k_similarity(...)` envelope (ingestion-time
    utility; the live case path does not run vector search)."""
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
    """Up to `per_outcome` best confirmed_fraud and cleared ClosedCases,
    ranked by `structured_score` (descending) then id -- order-independent.
    Only ClosedCase rows are ever kept."""
    closed = [h for h in hits if h.get("type") == "ClosedCase" and h.get("id")]
    closed.sort(key=lambda h: (-(h.get("structured_score") or 0), str(h["id"])))
    fraud = [h for h in closed if h.get("outcome") == "confirmed_fraud"][:per_outcome]
    cleared = [h for h in closed if h.get("outcome") == "cleared"][:per_outcome]
    return fraud + cleared


# --------------------------------------------------------------------------
# Case shape
# --------------------------------------------------------------------------
def build_case_shape(
    trigger_type: str, profile: Any, card_testing: Any, cnp: Any, region: Any, network: Any, recurrence: Any,
    episode_size: int = 1,
) -> dict[str, Any]:
    """The corrected behavioral features that drive candidate retrieval."""
    candidate_patterns: list[str] = []
    if card_testing.fired:
        candidate_patterns.append("card_testing")
    if profile.flagged_channel == "in_person":
        if region.fired or profile.region_class in ("new", "seen"):
            candidate_patterns.append("out_of_region_use")
        candidate_patterns.append("account_takeover")
    elif profile.flagged_channel == "online":
        candidate_patterns.append("card_not_present_new_device" if profile.is_new_device else "card_not_present_fraud")
        candidate_patterns.append("account_takeover")
    return {
        "trigger_type": trigger_type,
        "channel": profile.flagged_channel,
        "product": profile.flagged_product,
        "amount": profile.flagged_amount,
        "amount_class": profile.amount_class,
        "product_class": profile.product_class,
        "is_new_device": profile.is_new_device if profile.flagged_channel == "online" else None,
        "region_class": profile.region_class,
        "card_testing": card_testing.fired,
        "strict_out_of_region": region.fired,
        "cnp_documented_burst": bool(cnp.documented_burst),
        "network_corroborated": network.corroborated,
        "recurrence_tier": recurrence.tier,
        "episode_size": episode_size,
        "candidate_patterns": candidate_patterns,
    }


def _note_flags(notes: str) -> dict[str, bool]:
    n = (notes or "").lower()
    return {
        "region": "billing region" in n or "travel" in n,
        "device": "new phone" in n or "device" in n,
        "amount": "amount unusual" in n,
    }


def score_closed_case(shape: dict[str, Any], case: dict[str, Any]) -> tuple[int, int, list[str]]:
    """(score, applicable features, matched feature names). Deterministic,
    computed only from structured ClosedCase features and the analyst-note
    template phrases -- never from an LLM."""
    matched: list[str] = []
    applicable = 0
    notes = _note_flags(case.get("analyst_notes", ""))

    applicable += 1  # channel is a hard filter; counting it keeps "full match" meaningful
    matched.append("channel")

    if shape.get("product"):
        applicable += 1
        if shape["product"] in (case.get("products") or []):
            matched.append("product")

    amount, case_amount = shape.get("amount"), case.get("max_amount")
    if amount and case_amount:
        applicable += 1
        ratio = max(amount, case_amount) / max(min(amount, case_amount), 0.01)
        if ratio <= AMOUNT_SIMILAR_FACTOR:
            matched.append("amount")

    if shape.get("is_new_device") is not None:
        applicable += 1
        case_new = (case.get("n_new_device") or 0) > 0 or notes["device"]
        if case_new == bool(shape["is_new_device"]):
            matched.append("device")

    applicable += 1
    case_multi = (case.get("n_txns") or case.get("n_involved") or 1) > 1
    if case_multi == (shape.get("episode_size", 1) > 1):
        matched.append("episode_size")

    applicable += 1
    if case.get("outcome") == "confirmed_fraud":
        if case.get("pattern") in shape.get("candidate_patterns", []):
            matched.append("pattern_shape")
    else:
        # Cleared cases record WHY the alert was false: match the reason to
        # the case's own open question.
        reason_fits = (
            (notes["region"] and shape.get("channel") == "in_person")
            or (notes["device"] and bool(shape.get("is_new_device")))
            or (notes["amount"] and shape.get("amount_class") in ("extreme", "elevated"))
        )
        if reason_fits:
            matched.append("pattern_shape")
    return len(matched), applicable, matched


def shared_anomalies(shape: dict[str, Any], case: dict[str, Any]) -> list[str]:
    """Case-specific anomalies this case and the prior case BOTH exhibit.
    Coarse matches (channel, product, similar amount) describe most of the
    history; only a shared anomaly makes a prior case materially similar."""
    notes = _note_flags(case.get("analyst_notes", ""))
    fraud = case.get("outcome") == "confirmed_fraud"
    pattern = case.get("pattern")
    out: list[str] = []
    if shape.get("is_new_device") and ((case.get("n_new_device") or 0) > 0 or notes["device"]):
        out.append("new_device")
    if shape.get("card_testing") and fraud and pattern == "card_testing":
        out.append("card_testing")
    if shape.get("strict_out_of_region") and (pattern == "out_of_region_use" or notes["region"]):
        out.append("out_of_region")
    if shape.get("cnp_documented_burst") and shape.get("episode_size", 1) > 1 and fraud \
            and (case.get("n_txns") or 1) > 1 and pattern in ("card_not_present_fraud", "card_not_present_new_device"):
        out.append("cnp_burst")
    if shape.get("amount_class") == "extreme" and not fraud and notes["amount"]:
        out.append("unusual_amount")
    return out


def closed_case_candidates(
    catalog: list[dict[str, Any]], shape: dict[str, Any], per_outcome: int = CANDIDATES_PER_OUTCOME,
) -> dict[str, list[dict[str, Any]]]:
    channel = shape.get("channel")
    out: dict[str, list[dict[str, Any]]] = {"confirmed_fraud": [], "cleared": []}
    for case in catalog:
        if case.get("outcome") not in out:
            continue
        if channel and channel not in (case.get("channels") or []):
            continue
        score, applicable, matched = score_closed_case(shape, case)
        out[case["outcome"]].append({
            **{k: case.get(k) for k in ("id", "type", "outcome", "pattern", "exposure_usd", "n_txns",
                                        "channels", "products", "n_new_device", "max_amount", "analyst_notes")},
            "structured_score": score, "applicable_features": applicable, "matched_features": matched,
            "full_match": score == applicable, "shared_anomalies": shared_anomalies(shape, case),
        })
    for outcome in out:
        out[outcome].sort(key=lambda c: (-c["structured_score"], abs(math.log(
            max(c.get("max_amount") or 1, 0.01) / max(shape.get("amount") or 1, 0.01))), str(c["id"])))
        out[outcome] = out[outcome][:per_outcome]
    return out


# --------------------------------------------------------------------------
# Knowledge documents by known source / section / keyword
# --------------------------------------------------------------------------
_PATTERN_DOC = {
    "card_testing": "pattern-card-testing",
    "card_not_present_fraud": "pattern-cnp-fraud",
    "card_not_present_new_device": "pattern-cnp-new-device",
    "out_of_region_use": "pattern-out-of-region",
    "account_takeover": "pattern-account-takeover",
}


def knowledge_candidates(docs: list[dict[str, Any]], shape: dict[str, Any]) -> list[dict[str, Any]]:
    wanted: list[str] = ["policy-r1", "policy-case-vs-report", "policy-r8"]
    wanted += [_PATTERN_DOC[p] for p in shape.get("candidate_patterns", []) if p in _PATTERN_DOC]
    if shape.get("card_testing"):
        wanted.append("policy-r5")
    if shape.get("trigger_type") == "customer_report":
        wanted += ["policy-r2", "policy-r7"]
    else:
        wanted += ["policy-r2", "policy-r3", "policy-r4"]
    if shape.get("network_corroborated") or shape.get("trigger_type") == "analyst_request":
        wanted += ["policy-r6", "policy-r9"]
    by_id = {d["id"]: d for d in docs}
    picked = [by_id[w] for w in dict.fromkeys(wanted) if w in by_id]
    return picked[:MAX_DOC_CANDIDATES]


async def retrieve_knowledge(tg: TigerGraphMCP, shape: dict[str, Any]) -> dict[str, Any]:
    """Bounded, balanced candidate sets for GPT-5.5 reranking. Makes only
    TigerGraph installed-query calls: no embedding, no vector search, no
    FraudCase."""
    catalog = await closed_case_features(tg)
    docs = [*(await knowledge_docs_by_source(tg, "policy")), *(await knowledge_docs_by_source(tg, "pattern"))]
    candidates = closed_case_candidates(catalog, shape)
    return {
        "retrieval_method": RETRIEVAL_METHOD,
        "vector_search_used": False,
        "case_shape": shape,
        "closed_case_catalog_size": len(catalog),
        "closed_case_candidates": candidates,
        "knowledge_candidates": knowledge_candidates(docs, shape),
        # Filled by the rerank step; until then the deterministic top picks.
        "similar_cases": balance_closed_cases(candidates["confirmed_fraud"] + candidates["cleared"]),
        "knowledge": knowledge_candidates(docs, shape)[:5],
    }


def matched_prior_case(candidates: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """A prior case counts as an evidence family only when a candidate matches
    every applicable structured feature, shares at least one case-specific
    anomaly with this case, and no candidate of the opposite outcome does
    the same (otherwise history does not discriminate). Deterministic --
    never chosen by the LLM."""
    full = {o: [c for c in cs if c.get("full_match") and c.get("shared_anomalies")] for o, cs in candidates.items()}
    if bool(full.get("confirmed_fraud")) == bool(full.get("cleared")):
        return None
    outcome = "confirmed_fraud" if full.get("confirmed_fraud") else "cleared"
    best = full[outcome][0]
    return {"id": best["id"], "outcome": outcome, "pattern": best.get("pattern"),
            "matched_features": best.get("matched_features"), "shared_anomalies": best.get("shared_anomalies")}
