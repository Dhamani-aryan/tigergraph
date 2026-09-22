from __future__ import annotations

from typing import Any

from src.ingestion.embeddings import embed
from src.tg_client import TigerGraphMCP


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


async def retrieve_knowledge(tg: TigerGraphMCP, query_text: str, top_k: int = 5) -> dict[str, list[dict]]:
    """Semantic search over policy/pattern/regulatory knowledge (Task 9's
    `KnowledgeDoc` index, 660 vectors) and prior investigations (`ClosedCase`,
    5,565 vectors, and this run's own `FraudCase` writes).

    Confirmed live: both `KnowledgeDoc` and `ClosedCase` searches return
    real, relevant hits (e.g. querying "card testing small authorizations"
    surfaces `pattern-card-testing`/`policy-r5`/`policy-r10` from
    `KnowledgeDoc` with low, sensible distances). `FraudCase`'s vector index
    exists (Task 4's schema) but is empty until Task 12/13 actually writes
    an embedding when closing a case -- an empty `own_case_hits` list here
    is the expected, correct result until that write path exists, not a bug
    in this function.
    """
    query_vector = embed([query_text])[0]
    knowledge_raw = await tg.search_top_k_similarity("KnowledgeDoc", "embedding", query_vector, top_k)
    closed_case_raw = await tg.search_top_k_similarity("ClosedCase", "embedding", query_vector, top_k)
    own_case_raw = await tg.search_top_k_similarity("FraudCase", "embedding", query_vector, top_k)

    knowledge_hits = _unwrap_hits(knowledge_raw)
    closed_case_hits = _unwrap_hits(closed_case_raw)
    own_case_hits = _unwrap_hits(own_case_raw)

    return {
        "knowledge": knowledge_hits,
        "similar_cases": closed_case_hits + own_case_hits,
    }
