## Task 10: Graph evidence-gathering functions

**Files:**
- Create: `src/graph/__init__.py` (empty)
- Create: `src/graph/queries.py`
- Create: `src/graph/vector_search.py`
- Test: `tests/test_graph_queries.py` (live, requires the loaded graph from Tasks 7-9)

**Interfaces:**
- Produces: `async card_window(tg, card_id, hours) -> list[dict]`, `async customer_cards(tg, customer_id) -> list[dict]`, `async device_neighbors(tg, transaction_id) -> list[dict]`, `async region_neighbors(tg, addr1, txn_ts, window_days) -> list[dict]`, `async closed_case_lookup(tg, card_id=None, device_id=None, addr1=None) -> list[dict]`, `async ring_membership(tg, card_id) -> dict` (reads Task 8.5's graph-algorithm output), `async retrieve_knowledge(tg, query_text, top_k=5) -> list[dict]`, plus `FOLLOWUP_TOOL_SCHEMAS` and `async dispatch_followup_tool(tg, name, arguments)` for the bounded agentic round. Task 12's `graph_flow.py` calls the deterministic functions directly every time, and calls `dispatch_followup_tool` at most once per case, only when the LLM's function-calling round (step 2a) requests it — see plan Architecture note on why evidence-gathering is deterministic-by-default with one bounded exception.

**Known live-confirmed risk to check before trusting any query below (found during Task 8's manual checkpoint):** `Card` and `DeviceProfile` don't have `primary_id_as_attribute` set (only `Transaction` got that flag in Task 4's schema) — a live probe confirmed this breaks `WHERE c.card_id == "..."`-style filtering. `Customer`/`BillingRegion`/`EmailDomain`/`ClosedCase`/`FraudCase` were never explicitly tested for the same gap but were declared with the identical plain `PRIMARY_ID` syntax, so assume they have it too until proven otherwise. Every query below filters by exactly this kind of primary-key attribute comparison, so **before trusting any of them, run one as a live probe first.** If it fails the way Task 8 predicts, the idiomatic GSQL fix is a typed query **parameter** instead of a WHERE-clause filter — e.g. `CREATE QUERY card_window(VERTEX<Card> input_card, FLOAT hours) FOR GRAPH {GRAPH_NAME} {{ Start = {{input_card}}; ... }}`, then pass the primary-id string as the parameter value when running the query (GSQL resolves a `VERTEX<Type>` parameter from its primary-id string automatically — no attribute access needed). Rewrite each function's GSQL using this pattern if the WHERE-clause version fails; this is a schema-shape limitation already loaded live, not something to fix by altering Task 4's already-populated schema.

- [ ] **Step 1: Write `src/graph/queries.py`** — implemented as parameterized GSQL run through `tg.gsql` (interpreted queries), since installing formal GSQL query objects is extra ceremony this timeline doesn't need; interpreted GSQL is fine for read-only evidence gathering at this data scale.

```python
from __future__ import annotations

from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


async def card_window(tg: TigerGraphMCP, card_id: str, hours: float = 2.0) -> list[dict]:
    gsql = f'''
    USE GRAPH {GRAPH_NAME}
    INTERPRET QUERY () FOR GRAPH {GRAPH_NAME} {{
        SetAccum<VERTEX<Transaction>> @@txns;
        Start = {{Card.*}};
        Start = SELECT c FROM Start:c WHERE c.card_id == "{card_id}";
        Txns = SELECT t FROM Start-(MADE)->Transaction:t
               ACCUM @@txns += t;
        PRINT @@txns;
    }}
    '''
    return await tg.gsql(gsql)


async def customer_cards(tg: TigerGraphMCP, customer_id: str) -> list[dict]:
    gsql = f'''
    USE GRAPH {GRAPH_NAME}
    SELECT c FROM Customer-(OWNS)->Card:c WHERE Customer.customer_id == "{customer_id}"
    '''
    return await tg.gsql(gsql)


async def device_neighbors(tg: TigerGraphMCP, transaction_id: str) -> list[dict]:
    gsql = f'''
    USE GRAPH {GRAPH_NAME}
    SELECT c FROM Transaction:t -(FROM_DEVICE)-> DeviceProfile:d
             <-(FROM_DEVICE)- Transaction <-(MADE)- Card:c
    WHERE t.transaction_id == "{transaction_id}"
    '''
    return await tg.gsql(gsql)


async def region_neighbors(tg: TigerGraphMCP, addr1: str) -> list[dict]:
    gsql = f'''
    USE GRAPH {GRAPH_NAME}
    SELECT t FROM BillingRegion:b <-(BILLED_IN)- Transaction:t
    WHERE b.addr1 == "{addr1}"
    LIMIT 200
    '''
    return await tg.gsql(gsql)


async def closed_case_lookup(
    tg: TigerGraphMCP, card_id: str | None = None, addr1: str | None = None
) -> list[dict]:
    if card_id:
        gsql = f'''
        USE GRAPH {GRAPH_NAME}
        SELECT cc FROM ClosedCase:cc -(ON_CARD|CONNECTED_TO)-> Card:c
        WHERE c.card_id == "{card_id}"
        '''
        return await tg.gsql(gsql)
    if addr1:
        gsql = f'''
        USE GRAPH {GRAPH_NAME}
        SELECT cc FROM ClosedCase:cc -(INVOLVES)-> Transaction:t -(BILLED_IN)-> BillingRegion:b
        WHERE b.addr1 == "{addr1}"
        '''
        return await tg.gsql(gsql)
    return []


async def ring_membership(tg: TigerGraphMCP, card_id: str) -> dict:
    """O(1) lookup against the ring_cluster_id/cluster_prior_fraud_rate attributes
    written by Task 8.5's connected-components pass -- this is the graph-algorithm
    output, not a live traversal."""
    gsql = f'''
    USE GRAPH {GRAPH_NAME}
    SELECT card_id, ring_cluster_id, cluster_prior_fraud_rate FROM Card
    WHERE card_id == "{card_id}"
    '''
    result = await tg.gsql(gsql)
    # Task 2's tg_client.py wraps successful tigergraph__gsql responses in an
    # envelope ({"success": ..., "operation": ..., "data": ...}), not a bare list --
    # confirmed live during Task 2. `data` is presumed to hold the actual query rows,
    # but its exact nested shape for a SELECT statement hasn't been observed live yet.
    # Before trusting this function's output, run this query by hand once
    # (`python -c "..."` against a real card_id) and print the raw result to confirm
    # whether `rows` below is right, or needs another level of unwrapping (e.g.
    # `data["results"]` or similar) -- adjust the two lines below to match what's
    # actually observed rather than assuming this guess is correct.
    rows = result.get("data") if isinstance(result, dict) else result
    return rows[0] if isinstance(rows, list) and rows else {}


# --- Bounded agentic follow-up round (Task 12 step 2a) -------------------------
# A small, fixed menu of the SAME query functions above, re-parameterized, exposed
# to the LLM as real function-calling tools. The LLM may call at most one of these
# after seeing the deterministic evidence pass; this is genuine tool selection, not
# prose-parsing, but capped to one call so a rate-limited/small model can't loop.

FOLLOWUP_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "wider_region_check",
            "description": "Check for other transactions in the same billing region over a wider window than the default pass.",
            "parameters": {
                "type": "object",
                "properties": {"addr1": {"type": "string"}},
                "required": ["addr1"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "closed_case_lookup_by_region",
            "description": "Look up closed cases connected to a billing region rather than a specific card.",
            "parameters": {
                "type": "object",
                "properties": {"addr1": {"type": "string"}},
                "required": ["addr1"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wider_card_window",
            "description": "Re-run the card transaction window with a longer lookback (e.g. 7 days instead of 48 hours) when the default window looks incomplete.",
            "parameters": {
                "type": "object",
                "properties": {"card_id": {"type": "string"}, "hours": {"type": "number"}},
                "required": ["card_id", "hours"],
            },
        },
    },
]


async def dispatch_followup_tool(tg: TigerGraphMCP, name: str, arguments: dict) -> list[dict] | dict:
    if name == "wider_region_check":
        return await region_neighbors(tg, arguments["addr1"])
    if name == "closed_case_lookup_by_region":
        return await closed_case_lookup(tg, addr1=arguments["addr1"])
    if name == "wider_card_window":
        return await card_window(tg, arguments["card_id"], hours=arguments.get("hours", 168))
    raise ValueError(f"Unknown follow-up tool: {name}")
```

**Note:** the `card_window` and `device_neighbors` GSQL above use interpreted-query syntax that TigerGraph's GSQL dialect is picky about (multi-hop patterns, `reverse_` edge aliases). Treat these as a first draft: run each one standalone against the live graph in Step 3 below, and fix syntax errors against the actual GSQL error message and TigerGraph's interpreted-query documentation — this is normal GSQL iteration, not a sign the design is wrong.

- [ ] **Step 2: Write `src/graph/vector_search.py`**

```python
from __future__ import annotations

from src.ingestion.embeddings import embed
from src.tg_client import TigerGraphMCP


async def retrieve_knowledge(tg: TigerGraphMCP, query_text: str, top_k: int = 5) -> list[dict]:
    query_vector = embed([query_text])[0]
    knowledge_hits = await tg.search_top_k_similarity("KnowledgeDoc", "embedding", query_vector, top_k)
    closed_case_hits = await tg.search_top_k_similarity("ClosedCase", "embedding", query_vector, top_k)
    # Search `FraudCase` (this run's own cases) too -- without this, a later case-pack
    # case in the same batch can never retrieve an earlier one this agent already wrote,
    # which defeats the point of "case memory" within the run itself (see spec §6 step 8
    # and the README's "add your own cases to the graph as you close them"). This only
    # returns results once Task 12/13 actually upserts an embedding when writing a
    # FraudCase -- empty results here are expected until that write path exists.
    own_case_hits = await tg.search_top_k_similarity("FraudCase", "embedding", query_vector, top_k)
    return {
        "knowledge": knowledge_hits,
        "similar_cases": closed_case_hits + own_case_hits,
    }
```

- [ ] **Step 3: Write and run `tests/test_graph_queries.py`** — live integration tests against the graph loaded in Tasks 7-9, using the `HHG-017` card (`C04570-K1`) from the manual checkpoint in Task 8 as a known-good fixture.

```python
import pytest

from src.graph.queries import card_window, closed_case_lookup, customer_cards
from src.graph.vector_search import retrieve_knowledge
from src.tg_client import TigerGraphMCP


@pytest.mark.asyncio
async def test_card_window_returns_transactions_for_known_card():
    async with TigerGraphMCP() as tg:
        result = await card_window(tg, "C04570-K1", hours=24)
        assert result is not None


@pytest.mark.asyncio
async def test_customer_cards_returns_at_least_one_card():
    async with TigerGraphMCP() as tg:
        result = await customer_cards(tg, "C04570")
        assert result is not None


@pytest.mark.asyncio
async def test_retrieve_knowledge_returns_policy_and_case_hits():
    async with TigerGraphMCP() as tg:
        result = await retrieve_knowledge(tg, "card testing small authorizations", top_k=3)
        assert "knowledge" in result and "similar_cases" in result


@pytest.mark.asyncio
async def test_ring_membership_returns_cluster_from_known_ring():
    from src.graph.queries import ring_membership
    async with TigerGraphMCP() as tg:
        result = await ring_membership(tg, "C03528-K1")  # known 22-member ring from Task 8.5
        assert result.get("ring_cluster_id")


@pytest.mark.asyncio
async def test_dispatch_followup_tool_routes_to_correct_function():
    from src.graph.queries import dispatch_followup_tool
    async with TigerGraphMCP() as tg:
        result = await dispatch_followup_tool(tg, "wider_card_window", {"card_id": "C04570-K1", "hours": 168})
        assert result is not None
```

```bash
.venv\Scripts\pytest tests/test_graph_queries.py -v
```

Expected: passes once the GSQL in Step 1 is fixed against real syntax errors (iterate here before moving on — Task 12 depends on these functions actually returning usable data, not just not-crashing).

- [ ] **Step 4: Commit**

```bash
git add src/graph tests/test_graph_queries.py
git commit -m "feat: deterministic graph evidence-gathering queries and vector retrieval"
```

---

