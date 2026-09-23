## Task 4: Graph schema creation

**Files:**
- Create: `src/schema/columns.py`
- Create: `src/schema/build_schema.py`
- Create: `scripts/create_schema.py`

**Interfaces:**
- Consumes: `TigerGraphMCP` (Task 2), the real `transactions.csv`/`identity.csv` headers, local Ollama (`nomic-embed-text`) for the vector-dimension probe.
- Produces: `generate_transaction_attrs(csv_path) -> list[tuple[str, str]]` (attribute name, GSQL type pairs); `SCHEMA_GSQL: str` — the full `CREATE VERTEX`/`CREATE EDGE`/`CREATE GRAPH` statement block; `add_vector_attributes(tg)` — adds a `"embedding"`-named vector attribute to `KnowledgeDoc`/`ClosedCase`/`Case`. Task 7 (loading jobs), Task 9 (knowledge ingestion), Task 10 (query tools), and Task 12/13 (case write-back) all assume these vertex/edge type names — and, for the three GraphRAG vertex types, the `"embedding"` vector attribute name specifically — exist.

- [ ] **Step 1: Write `src/schema/columns.py`** — generates the `Transaction` attribute list programmatically instead of hand-typing 397 columns.

```python
from __future__ import annotations

import csv
from pathlib import Path

# Columns whose GSQL type should be STRING even though they look numeric or are
# sparsely populated categoricals, per the README's column-group descriptions.
_STRING_COLUMNS = {
    "TransactionID",
    "ProductCD",
    "card4",
    "card6",
    "addr1",
    "addr2",
    "P_emaildomain",
    "R_emaildomain",
    "customer_id",
    "channel",
    *(f"M{i}" for i in range(1, 10)),
    *(f"id_{i:02d}" for i in (12, 15, 16, 23, 27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38)),
    "DeviceType",
    "DeviceInfo",
}


def generate_attrs(csv_path: str | Path, primary_key: str) -> list[tuple[str, str]]:
    """Read just the header row and yield (attr_name, gsql_type) pairs.
    primary_key is excluded (it becomes the vertex's PRIMARY_ID, not a listed attr).
    """
    with open(csv_path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))

    attrs: list[tuple[str, str]] = []
    for col in header:
        if col == primary_key:
            continue
        gsql_type = "STRING" if col in _STRING_COLUMNS else "DOUBLE"
        attrs.append((col, gsql_type))
    return attrs


def to_gsql_attr_list(attrs: list[tuple[str, str]]) -> str:
    return ",\n    ".join(f"{name} {gsql_type}" for name, gsql_type in attrs)
```

- [ ] **Step 2: Write `src/schema/build_schema.py`**

```python
from __future__ import annotations

from src.schema.columns import generate_attrs, to_gsql_attr_list
from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


def build_schema_gsql(transactions_csv: str, identity_csv: str) -> str:
    txn_attrs = generate_attrs(transactions_csv, primary_key="TransactionID")
    txn_attr_gsql = to_gsql_attr_list(txn_attrs)

    return f"""
USE GRAPH {GRAPH_NAME}

CREATE VERTEX Customer (PRIMARY_ID customer_id STRING)
CREATE VERTEX Card (
    PRIMARY_ID card_id STRING, customer_id STRING,
    ring_cluster_id STRING, cluster_prior_fraud_rate DOUBLE
)
CREATE VERTEX Transaction (
    PRIMARY_ID transaction_id STRING,
    {txn_attr_gsql}
) WITH primary_id_as_attribute="true"
CREATE VERTEX DeviceProfile (
    PRIMARY_ID device_id STRING,
    device_info STRING, os STRING, browser STRING, screen STRING
)
CREATE VERTEX EmailDomain (PRIMARY_ID domain STRING)
CREATE VERTEX BillingRegion (PRIMARY_ID addr1 STRING)
CREATE VERTEX ClosedCase (
    PRIMARY_ID case_id STRING,
    customer_id STRING, card_id STRING, opened_at STRING, closed_at STRING,
    outcome STRING, pattern STRING, first_fraud_txn_id STRING,
    n_txns INT, exposure_usd DOUBLE, actions_taken STRING,
    report_filed STRING, analyst_notes STRING
)
CREATE VERTEX Case (
    PRIMARY_ID case_id STRING,
    customer_id STRING, card_id STRING, status STRING, verdict STRING,
    fraud_probability DOUBLE, pattern STRING, exposure_usd DOUBLE,
    summary STRING, written_at STRING
)
CREATE VERTEX KnowledgeDoc (
    PRIMARY_ID doc_id STRING,
    source STRING, section STRING, text STRING
)

CREATE DIRECTED EDGE OWNS (FROM Customer, TO Card)
CREATE DIRECTED EDGE MADE (FROM Card, TO Transaction)
CREATE DIRECTED EDGE FROM_DEVICE (FROM Transaction, TO DeviceProfile)
CREATE DIRECTED EDGE PURCHASER_EMAIL (FROM Transaction, TO EmailDomain)
CREATE DIRECTED EDGE BILLED_IN (FROM Transaction, TO BillingRegion)
CREATE DIRECTED EDGE NEXT (FROM Transaction, TO Transaction)
CREATE DIRECTED EDGE INVOLVES (FROM ClosedCase, TO Transaction)
CREATE DIRECTED EDGE ON_CARD (FROM ClosedCase, TO Card)
CREATE DIRECTED EDGE CONNECTED_TO (FROM ClosedCase, TO Card)
CREATE DIRECTED EDGE CASE_INVOLVES (FROM Case, TO Transaction)
CREATE DIRECTED EDGE CASE_ON_CARD (FROM Case, TO Card)
CREATE DIRECTED EDGE CASE_CONNECTED_TO (FROM Case, TO Card)
CREATE UNDIRECTED EDGE SHARES_ORIGIN (FROM Card, TO Card, origin_type STRING)

CREATE GRAPH {GRAPH_NAME} (
    Customer, Card, Transaction, DeviceProfile, EmailDomain, BillingRegion,
    ClosedCase, Case, KnowledgeDoc,
    OWNS, MADE, FROM_DEVICE, PURCHASER_EMAIL, BILLED_IN, NEXT,
    INVOLVES, ON_CARD, CONNECTED_TO, CASE_INVOLVES, CASE_ON_CARD, CASE_CONNECTED_TO,
    SHARES_ORIGIN
)
""".strip()


async def apply_schema(tg: TigerGraphMCP, transactions_csv: str, identity_csv: str) -> None:
    gsql = build_schema_gsql(transactions_csv, identity_csv)
    result = await tg.gsql(gsql)
    print(result)
```

**Note on `Case` vertex type name:** GSQL's own reserved words don't include "Case" but double-check the Task 1 dump / a dry run doesn't collide with anything; if `CREATE VERTEX Case` errors, rename to `FraudCase` consistently across this file, Task 12's `graph_flow.py`, and the spec's terminology (cosmetic rename only, no design change).

- [ ] **Step 3: Add vector attributes to the three GraphRAG vertex types** — `tigergraph__upsert_vectors`/`tigergraph__search_top_k_similarity` (confirmed against the real tool schema in `docs/tigergraph-mcp-tools.json` during Task 2) both require a named vector-typed attribute to already exist on the vertex (`ALTER VERTEX ... ADD VECTOR ATTRIBUTE`) — a plain `STRING`/`DOUBLE` column will not work for embeddings. This has to happen after the vertex types exist (Step 1's schema) but doesn't need any data loaded yet.

Add to `src/schema/build_schema.py`:

```python
import ollama


async def add_vector_attributes(tg: TigerGraphMCP) -> None:
    # Determine the real embedding dimension from the actual model rather than
    # hardcoding it (nomic-embed-text is commonly cited as 768-dim, but verifying
    # against a live call removes any risk of that being wrong or model-version-
    # dependent) -- a dimension mismatch later would make every vector search fail.
    probe = ollama.embeddings(model="nomic-embed-text", prompt="dimension probe")
    dimension = len(probe["embedding"])
    print(f"nomic-embed-text dimension: {dimension}")

    for vertex_type in ("KnowledgeDoc", "ClosedCase", "Case"):
        result = await tg.call(
            "tigergraph__add_vector_attribute",
            {
                "vertex_type": vertex_type,
                "vector_name": "embedding",
                "dimension": dimension,
                "metric": "COSINE",
            },
        )
        print(f"{vertex_type}: {result}")
```

If the `ollama.embeddings()` call fails with a connection error, the Ollama daemon isn't running — start it (the Ollama desktop app, or `ollama serve` in a separate terminal) and retry; Task 1's `ollama list` check only confirmed the models are pulled, not that the daemon is live right now.

- [ ] **Step 4: Write `scripts/create_schema.py`**

```python
import asyncio

from src.schema.build_schema import add_vector_attributes, apply_schema
from src.tg_client import TigerGraphMCP


async def main() -> None:
    async with TigerGraphMCP() as tg:
        await apply_schema(tg, "transactions.csv", "identity.csv")
        await add_vector_attributes(tg)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run it against Savanna**

```bash
.venv\Scripts\python scripts\create_schema.py
```

Expected: no GSQL errors. If a specific `CREATE VERTEX`/`CREATE EDGE` statement fails (e.g. a reserved word, or DOUBLE-typing a column that's actually non-numeric), fix that one statement and re-run — GSQL schema creation is idempotent-ish (re-running `CREATE VERTEX` on an existing type errors harmlessly; drop and recreate via `DROP VERTEX <name>` if you need a clean retry).

**This is also the real test of the privilege question Task 1 flagged** (the `tigergraph12` user's `READ_SCHEMA` permission-denied response, never confirmed to affect write-side schema operations). If `CREATE GRAPH`, `CREATE VERTEX`, or `ADD VECTOR ATTRIBUTE` fail with a permission/authorization error here, that confirms the account genuinely lacks schema-write privileges and needs a role grant in the Savanna Access Management UI before continuing — report this plainly as BLOCKED with the exact error text rather than working around it. If they succeed, the earlier `READ_SCHEMA` denial was specific to that read-enumeration operation (plausible on an empty workspace with nothing to enumerate) and isn't a blocker after all.

- [ ] **Step 6: Verify with a read-only check**

```bash
.venv\Scripts\python -c "
import asyncio
from src.tg_client import TigerGraphMCP
async def main():
    async with TigerGraphMCP() as tg:
        print(await tg.gsql('LS'))
asyncio.run(main())
"
```

Expected: output lists all 9 vertex types and 12 edge types created above.

- [ ] **Step 7: Commit**

```bash
git add src/schema/columns.py src/schema/build_schema.py scripts/create_schema.py
git commit -m "feat: TigerGraph schema creation from CSV headers, with vector attributes for GraphRAG"
```

---

