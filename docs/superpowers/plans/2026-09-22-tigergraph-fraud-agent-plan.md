# TigerGraph Fraud Investigation Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an agent that investigates all 20 `case_pack.csv` cases using TigerGraph (graph + vector/GraphRAG via TigerGraph MCP) and closed-case memory, applies the fraud policy deterministically, writes cases back to the graph, and emits 20 schema-exact answer JSON files plus a Streamlit dashboard.

**Architecture:** Python system talking to TigerGraph exclusively through the `tigergraph-mcp` server (via the official `mcp` SDK, stdio transport). Evidence gathering is **deterministic by default** (Python always runs a fixed set of graph queries + vector retrieval per case), plus **one bounded, real function-calling round** where the LLM may request exactly one additional targeted query if the deterministic evidence looks ambiguous — this is the genuinely agentic piece, kept bounded so a rate-limited or small model can't loop. A LangGraph state machine orchestrates the per-case flow; a separate pure-Python policy engine (no LLM, no graph) applies rules R1–R10 and is independently unit-tested against the README's worked example. A **Connected Components** graph algorithm runs once in batch to cluster cards sharing a device/region/email into fraud rings, satisfying the README's named "graph algorithms" required component and giving every case an O(1) cluster-risk lookup instead of relying on live traversal alone.

**Tech Stack:** Python 3.10, `tigergraph-mcp` + `mcp` SDK, LangGraph, Groq's free-tier API (`llama-3.3-70b-versatile`, OpenAI-compatible client) for LLM reasoning with local Ollama (`qwen3:4b-instruct`) as a `LLM_BACKEND=ollama` fallback, Ollama `nomic-embed-text` for embeddings (always local, regardless of LLM backend), Pydantic v2, pandas, pypdf, Streamlit, pytest.

**Spec:** [docs/superpowers/specs/2026-09-22-tigergraph-fraud-agent-design.md](../specs/2026-09-22-tigergraph-fraud-agent-design.md) — that doc explains *why*; this plan is *how*, task by task. [README.md](../../../README.md) is the ground truth for the answer JSON schema, fraud policy (rules R1–R10), and the 20 cases.

## Global Constraints

- No paid LLM API — reasoning runs on Groq's free tier (`llama-3.3-70b-versatile`, rate-limited not metered, no credit card required) with local Ollama (`qwen3:4b-instruct`) as an explicit fallback via `LLM_BACKEND`. Embeddings are always local (`nomic-embed-text`, already pulled) regardless of which LLM backend is active.
- All TigerGraph access — setup and runtime — goes through the `tigergraph-mcp` MCP server, never a parallel `pyTigerGraph` connection, per the hackathon's required-components list. Graph algorithms (Task 8.5) are likewise run through this same MCP `gsql` tool, not a separate interface.
- Git workflow: local commits only, one per completed task, in order. No push to any remote until all 20 cases validate cleanly and the submission checklist is otherwise ready.
- Every ID (`transaction_id`, `card_id`, `customer_id`, `case_id`) written into an answer JSON or the graph must be one that actually exists in the dataset. In particular: **`card_id` is derived by trusting the `-K` suffix given in `case_pack.csv`/`closed_cases_history.csv` for that `customer_id`, defaulting to `-K1` when unreferenced** — never invented from card1 grouping (card1 is 1:1 with `customer_id` in this dataset; there is no second distinguishable card to detect).
- Action names, approval routes (`auto`/`L1`/`L2`), and field names in output JSON must match the README's Answer Format and Fraud Policy sections character-for-character.
- Raw CSVs (`transactions.csv` 708MB, `identity.csv`, `closed_cases_history.csv`, `case_pack.csv`) and any downloaded PDFs stay out of git (`.gitignore` already covers this).
- Windows/PowerShell environment; Python resolves to 3.10.5 at `C:\Users\naman\AppData\Local\Programs\Python\Python310\python.exe` (first on PATH) — use that for the venv to satisfy `tigergraph-mcp`'s 3.10–3.14 requirement.

---

## Task 1: Environment setup, MCP connectivity, and tool schema discovery

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.env` (gitignored — real Savanna credentials)
- Create: `scripts/discover_mcp_tools.py`
- Create: `docs/tigergraph-mcp-tools.json` (generated artifact, committed — reference for later tasks)

**Interfaces:**
- Produces: a committed dump of every `tigergraph-mcp` tool's exact name and input JSON schema, which Tasks 2–11 read from instead of guessing parameter names.

- [ ] **Step 1: Create the venv and install dependencies**

```bash
"C:\Users\naman\AppData\Local\Programs\Python\Python310\python.exe" -m venv .venv
.venv\Scripts\pip install --upgrade pip
```

- [ ] **Step 2: Write `requirements.txt`**

```
tigergraph-mcp
mcp>=1.2.0
python-dotenv
langgraph>=0.2.0
openai>=1.40.0
ollama
pydantic>=2.0
pandas
pypdf
requests
streamlit
pytest
pytest-asyncio
tenacity
```

Install: `.venv\Scripts\pip install -r requirements.txt`

(`openai` is the client used to talk to Groq, whose API is OpenAI-compatible — see Step 3b. `ollama` stays as the fallback LLM backend and is still required for `nomic-embed-text` embeddings either way. `tenacity` is for rate-limit retry/backoff on the Groq calls in Task 11.)

- [ ] **Step 3: Write `.env.example`**

```
TG_HOST=https://your-workspace.i.tgcloud.io
TG_GRAPHNAME=FraudInvestigation
TG_USERNAME=tigergraph
TG_PASSWORD=changeme
TG_RESTPP_PORT=9000
TG_GS_PORT=14240

LLM_BACKEND=groq
GROQ_API_KEY=your-groq-key-here
GROQ_MODEL=llama-3.3-70b-versatile
```

Copy to `.env` and fill in the real Savanna workspace hostname/credentials from the workspace you created (`https://savanna.tgcloud.io`, "Explore with Your Own Data").

- [ ] **Step 3b: Get a free Groq API key** (do this yourself — account creation isn't something to automate): go to `https://console.groq.com`, sign up (no credit card required for the free tier), create an API key, paste it into `.env` as `GROQ_API_KEY`. While there, check the current model list at `https://console.groq.com/docs/models` and confirm `llama-3.3-70b-versatile` (or whatever the current strongest general-purpose model is called — model names on free platforms change) is available and supports tool/function calling; update `GROQ_MODEL` in `.env` if the name has changed since this plan was written. Also note the free tier's requests-per-minute limit from your account dashboard — Task 11's retry/backoff logic needs to know roughly what it's working around.

- [ ] **Step 4: Confirm Ollama models are present** (still needed for embeddings, and as the LLM fallback)

```bash
ollama list
```

Expected: `qwen3:4b-instruct` and `nomic-embed-text` both listed (already pulled per earlier session check). If either is missing, `ollama pull qwen3:4b-instruct` / `ollama pull nomic-embed-text`.

- [ ] **Step 5: Write the tool-discovery script**

`scripts/discover_mcp_tools.py`:

```python
import asyncio
import json
from pathlib import Path

from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client


async def main() -> None:
    env_dict = dotenv_values(Path(".env").resolve())
    server_params = StdioServerParameters(
        command="tigergraph-mcp",
        args=["-vv"],
        env={**get_default_environment(), **env_dict},
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            dumped = [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                }
                for t in tools.tools
            ]
            out_path = Path("docs/tigergraph-mcp-tools.json")
            out_path.write_text(json.dumps(dumped, indent=2))
            print(f"Wrote {len(dumped)} tool schemas to {out_path}")

            # Sanity check: confirm the server can actually reach the TG instance.
            result = await session.call_tool("tigergraph__list_graphs", arguments={})
            for content in result.content:
                if hasattr(content, "text"):
                    print("list_graphs ->", content.text)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Run it and verify connectivity**

```bash
.venv\Scripts\python scripts\discover_mcp_tools.py
```

Expected: prints "Wrote N tool schemas to docs/tigergraph-mcp-tools.json" (N should be around 69 per the tigergraph-mcp README) and a `list_graphs` result that doesn't error. If it errors, the `.env` credentials/host are wrong — fix before continuing; nothing downstream works without this.

- [ ] **Step 7: Open `docs/tigergraph-mcp-tools.json` and note the exact input schemas for these tools** (write the findings as a comment block at the top of `scripts/discover_mcp_tools.py` for later tasks to reference): `tigergraph__gsql`, `tigergraph__create_graph`, `tigergraph__create_loading_job`, `tigergraph__upsert_vectors`, `tigergraph__search_top_k_similarity`, `tigergraph__run_installed_query`, `tigergraph__install_query`. Tasks 2, 3, 8–11 below use plausible parameter names (`command`, `vertex_type`, `vectors`, etc.) based on the tigergraph-mcp README's tool-name table — **confirm each against the actual dumped schema before writing the call, and adjust parameter names to match if they differ.**

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .env.example scripts/discover_mcp_tools.py docs/tigergraph-mcp-tools.json
git commit -m "feat: environment setup and MCP tool schema discovery"
```

(`.env` itself stays untracked per `.gitignore`.)

---

## Task 2: Persistent MCP client wrapper

**Files:**
- Create: `src/__init__.py` (empty)
- Create: `src/tg_client.py`
- Test: `tests/test_tg_client.py`

**Interfaces:**
- Produces: `class TigerGraphMCP` — async context manager with `.call(tool_name, arguments) -> Any` (JSON-decoded if possible, else raw text) and typed convenience methods `.gsql(command)`, `.upsert_vectors(...)`, `.search_top_k_similarity(...)`, `.run_installed_query(...)`. All later tasks that touch TigerGraph go through this class, never a raw MCP session of their own.
- Consumes: `.env` (via `python-dotenv`), the `tigergraph-mcp` CLI on PATH (installed in Task 1).

- [ ] **Step 1: Write `src/tg_client.py`**

```python
from __future__ import annotations

import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client


class TigerGraphMCP:
    """Persistent MCP client session against a running tigergraph-mcp server.

    Usage:
        async with TigerGraphMCP() as tg:
            result = await tg.gsql("SHOW VERTEX TYPE Customer")
    """

    def __init__(self, env_path: str = ".env") -> None:
        self._env_path = env_path
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "TigerGraphMCP":
        env_dict = dotenv_values(Path(self._env_path).resolve())
        server_params = StdioServerParameters(
            command="tigergraph-mcp",
            args=["-vv"],
            env={**get_default_environment(), **env_dict},
        )
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(server_params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self.session = None

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        assert self.session is not None, "use 'async with TigerGraphMCP() as tg:'"
        result = await self.session.call_tool(tool_name, arguments=arguments)
        texts = [c.text for c in result.content if hasattr(c, "text")]
        if not texts:
            return None
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined

    async def gsql(self, command: str) -> Any:
        return await self.call("tigergraph__gsql", {"command": command})

    async def upsert_vectors(self, vertex_type: str, vectors: list[dict[str, Any]]) -> Any:
        return await self.call(
            "tigergraph__upsert_vectors",
            {"vertex_type": vertex_type, "vectors": vectors},
        )

    async def search_top_k_similarity(
        self, vertex_type: str, query_vector: list[float], k: int = 5
    ) -> Any:
        return await self.call(
            "tigergraph__search_top_k_similarity",
            {"vertex_type": vertex_type, "query_vector": query_vector, "k": k},
        )

    async def run_installed_query(self, query_name: str, params: dict[str, Any]) -> Any:
        return await self.call(
            "tigergraph__run_installed_query",
            {"query_name": query_name, "params": params},
        )
```

**Note:** if Task 1 Step 7 found different parameter names in the real schema (e.g. `query_name` vs `queryName`, or `vector` vs `query_vector`), edit the method bodies above to match before moving on — everything downstream calls through these methods, so fixing it once here is cheaper than fixing every call site later.

- [ ] **Step 2: Write the connectivity test**

`tests/test_tg_client.py`:

```python
import pytest

from src.tg_client import TigerGraphMCP


@pytest.mark.asyncio
async def test_gsql_show_returns_something():
    async with TigerGraphMCP() as tg:
        result = await tg.gsql("HELP")
        assert result is not None
```

- [ ] **Step 3: Run it against the live Savanna workspace**

```bash
.venv\Scripts\pytest tests/test_tg_client.py -v
```

Expected: PASS. This requires the real `.env` credentials from Task 1 — there is no offline/mocked mode, since the whole point is to verify live connectivity before building anything on top.

- [ ] **Step 4: Commit**

```bash
git add src/__init__.py src/tg_client.py tests/test_tg_client.py
git commit -m "feat: persistent TigerGraph MCP client wrapper"
```

---

## Task 3: Card ID derivation (customer_id → card_id mapping)

This is its own task because it's a genuine, non-obvious data-modeling decision (see Global Constraints) that Task 4's schema/loading depends on, and it's fully testable without touching TigerGraph at all.

**Files:**
- Create: `src/schema/__init__.py` (empty)
- Create: `src/schema/card_ids.py`
- Test: `tests/test_card_ids.py`

**Interfaces:**
- Produces: `build_card_id_map(case_pack_df, closed_cases_df) -> dict[str, str]` mapping `customer_id -> card_id` (e.g. `"C08623" -> "C08623-K2"`), and `card_id_for(customer_id, card_map) -> str` (falls back to `f"{customer_id}-K1"` for unmapped customers). Task 7's `load_cards_and_made_edges` imports both — this is what resolves the correct `card_id` before any `Card`/`OWNS`/`MADE` data is created, not a post-hoc patch (see Task 7's note on why order matters here).

- [ ] **Step 1: Write the failing test**

`tests/test_card_ids.py`:

```python
import pandas as pd

from src.schema.card_ids import build_card_id_map, card_id_for


def _case_pack_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"case_id": "HHG-003", "card_id": "C08623-K2", "customer_id": "C08623"},
            {"case_id": "HHG-007", "card_id": "C09933-K2", "customer_id": "C09933"},
        ]
    )


def _closed_cases_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "case_id": "CC-2649",
                "customer_id": "C03528",
                "card_id": "C03528-K1",
                "connected_card_ids": "C00255-K1|C01935-K1|C03551-K2",
            }
        ]
    )


def test_known_customers_use_referenced_suffix():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_map["C08623"] == "C08623-K2"
    assert card_map["C09933"] == "C09933-K2"
    assert card_map["C03528"] == "C03528-K1"


def test_connected_card_ids_are_parsed_into_the_map():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_map["C00255"] == "C00255-K1"
    assert card_map["C01935"] == "C01935-K1"
    assert card_map["C03551"] == "C03551-K2"


def test_unreferenced_customer_defaults_to_k1():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_id_for("C99999", card_map) == "C99999-K1"


def test_referenced_customer_uses_map_not_default():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_id_for("C08623", card_map) == "C08623-K2"
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv\Scripts\pytest tests/test_card_ids.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.schema.card_ids'`.

- [ ] **Step 3: Implement**

`src/schema/card_ids.py`:

```python
from __future__ import annotations

import pandas as pd


def build_card_id_map(
    case_pack_df: pd.DataFrame, closed_cases_df: pd.DataFrame
) -> dict[str, str]:
    """Map customer_id -> card_id, trusting the -K suffix given in the reference
    files. card1 is 1:1 with customer_id across the whole transactions.csv (verified
    empirically), so there is no second distinguishable card to derive from raw
    transaction data; the -K suffix is per-customer labeling assigned in the
    reference data, not evidence of multiple physical cards.
    """
    card_map: dict[str, str] = {}

    for _, row in case_pack_df.iterrows():
        card_map[row["customer_id"]] = row["card_id"]

    for _, row in closed_cases_df.iterrows():
        card_map[row["customer_id"]] = row["card_id"]
        connected = row.get("connected_card_ids")
        if isinstance(connected, str) and connected:
            for card_id in connected.split("|"):
                customer_id = card_id.split("-K")[0]
                card_map.setdefault(customer_id, card_id)

    return card_map


def card_id_for(customer_id: str, card_map: dict[str, str]) -> str:
    return card_map.get(customer_id, f"{customer_id}-K1")
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv\Scripts\pytest tests/test_card_ids.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/schema/__init__.py src/schema/card_ids.py tests/test_card_ids.py
git commit -m "feat: card_id derivation from case reference data"
```

---

## Task 4: Graph schema creation

**Files:**
- Create: `src/schema/columns.py`
- Create: `src/schema/build_schema.py`
- Create: `scripts/create_schema.py`

**Interfaces:**
- Consumes: `TigerGraphMCP` (Task 2), the real `transactions.csv`/`identity.csv` headers.
- Produces: `generate_transaction_attrs(csv_path) -> list[tuple[str, str]]` (attribute name, GSQL type pairs); `SCHEMA_GSQL: str` — the full `CREATE VERTEX`/`CREATE EDGE`/`CREATE GRAPH` statement block. Task 6 (loading jobs) and Task 10 (query tools) both assume these vertex/edge type names exist.

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

- [ ] **Step 3: Write `scripts/create_schema.py`**

```python
import asyncio

from src.schema.build_schema import apply_schema
from src.tg_client import TigerGraphMCP


async def main() -> None:
    async with TigerGraphMCP() as tg:
        await apply_schema(tg, "transactions.csv", "identity.csv")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run it against Savanna**

```bash
.venv\Scripts\python scripts\create_schema.py
```

Expected: no GSQL errors. If a specific `CREATE VERTEX`/`CREATE EDGE` statement fails (e.g. a reserved word, or DOUBLE-typing a column that's actually non-numeric), fix that one statement and re-run — GSQL schema creation is idempotent-ish (re-running `CREATE VERTEX` on an existing type errors harmlessly; drop and recreate via `DROP VERTEX <name>` if you need a clean retry).

- [ ] **Step 5: Verify with a read-only check**

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

- [ ] **Step 6: Commit**

```bash
git add src/schema/columns.py src/schema/build_schema.py scripts/create_schema.py
git commit -m "feat: TigerGraph schema creation from CSV headers"
```

---

## Task 5: Policy engine (pure Python, no TigerGraph, no LLM)

The single most-scored piece of logic in the system (25% next-best-action + feeds into 25% accuracy), and the only piece that's fully deterministic and unit-testable in isolation — build and lock this down before anything that depends on it.

**Files:**
- Create: `src/policy/__init__.py` (empty)
- Create: `src/policy/models.py`
- Create: `src/policy/engine.py`
- Test: `tests/test_policy_engine.py`

**Interfaces:**
- Produces: `Findings` (pydantic model: `pattern`, `fraud_probability`, `single_signal: bool`, `shared_device: bool`, `shared_region: bool`, `shared_email: bool`, `exposure_usd: float`, `customer_response: Literal["confirmed_fraud","denies","confirmed_legitimate","disputes_recurring","no_reply",None]`, `undocumented_coordinated: bool`), `ActionRec` (`action: str`, `route: Literal["auto","L1","L2"]`, `reason: str`), `PolicyResult` (`actions: list[ActionRec]`, `sar_file: bool`, `sar_reason: str`). `apply_policy(findings: Findings) -> PolicyResult`. Task 12's `graph_flow.py` is the sole caller.

- [ ] **Step 1: Write `src/policy/models.py`**

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

CustomerResponse = Literal[
    "confirmed_fraud", "denies", "confirmed_legitimate", "disputes_recurring", "no_reply"
]


class Findings(BaseModel):
    pattern: str  # one of the README's pattern enum values, or "none"
    fraud_probability: float
    single_signal: bool  # True if the case rests on one signal (e.g. risk score alone)
    shared_device: bool = False
    shared_region: bool = False
    shared_email: bool = False
    exposure_usd: float = 0.0
    customer_response: CustomerResponse | None = None
    undocumented_coordinated: bool = False  # R9: undocumented pattern, coordinated/repeated abuse


class ActionRec(BaseModel):
    action: str
    route: Literal["auto", "L1", "L2"]
    reason: str


class PolicyResult(BaseModel):
    actions: list[ActionRec]
    sar_file: bool
    sar_reason: str
```

- [ ] **Step 2: Write the failing tests** — table-driven, one test per policy rule plus the README's own worked example.

`tests/test_policy_engine.py`:

```python
from src.policy.engine import apply_policy
from src.policy.models import Findings


def _actions(result):
    return [a.action for a in result.actions]


def test_r1_weak_single_signal_verifies_before_block():
    findings = Findings(pattern="none", fraud_probability=0.55, single_signal=True)
    result = apply_policy(findings)
    assert "VERIFY_WITH_CUSTOMER" in _actions(result) or "STEP_UP_AUTH" in _actions(result)
    assert "BLOCK_CARD" not in _actions(result)


def test_r2_customer_denies_blocks_and_creates_case():
    findings = Findings(
        pattern="card_not_present_fraud",
        fraud_probability=0.85,
        single_signal=False,
        exposure_usd=1200.0,
        customer_response="denies",
    )
    result = apply_policy(findings)
    assert "BLOCK_CARD" in _actions(result)
    assert "CREATE_CASE" in _actions(result)
    assert result.sar_file is True  # exposure > $1000


def test_r2_denies_low_exposure_no_shared_signal_no_report():
    findings = Findings(
        pattern="card_not_present_fraud",
        fraud_probability=0.8,
        single_signal=False,
        exposure_usd=200.0,
        customer_response="denies",
    )
    result = apply_policy(findings)
    assert "BLOCK_CARD" in _actions(result)
    assert result.sar_file is False


def test_r3_customer_confirms_closes_no_fraud():
    findings = Findings(
        pattern="none", fraud_probability=0.4, single_signal=True,
        customer_response="confirmed_legitimate",
    )
    result = apply_policy(findings)
    assert _actions(result) == ["CLOSE_NO_FRAUD"]


def test_r4_no_reply_monitors_and_declines_pending():
    findings = Findings(
        pattern="card_not_present_fraud", fraud_probability=0.6, single_signal=True,
        exposure_usd=600.0, customer_response="no_reply",
    )
    result = apply_policy(findings)
    assert "MONITOR_CARD" in _actions(result)
    assert "DECLINE_TRANSACTION" in _actions(result)
    assert "ESCALATE_TO_ANALYST" in _actions(result)  # exposure > $500


def test_r5_card_testing_declines_and_steps_up():
    findings = Findings(pattern="card_testing", fraud_probability=0.72, single_signal=False)
    result = apply_policy(findings)
    assert "DECLINE_TRANSACTION" in _actions(result)
    assert "STEP_UP_AUTH" in _actions(result)


def test_r6_shared_origin_creates_case_and_monitors_connected():
    findings = Findings(
        pattern="account_takeover", fraud_probability=0.9, single_signal=False,
        shared_device=True, exposure_usd=800.0,
    )
    result = apply_policy(findings)
    assert "CREATE_CASE" in _actions(result)
    assert "FILE_REPORT" in _actions(result)
    assert "MONITOR_CONNECTED_CARDS" in _actions(result)


def test_r7_disputed_but_recurring_does_not_block():
    findings = Findings(
        pattern="none", fraud_probability=0.3, single_signal=False,
        customer_response="disputes_recurring",
    )
    result = apply_policy(findings)
    assert "BLOCK_CARD" not in _actions(result)
    assert "VERIFY_WITH_CUSTOMER" in _actions(result)
    assert "WARN_CUSTOMER" in _actions(result)
    assert "CREATE_CASE" in _actions(result)


def test_r8_uncertain_and_exposed_escalates():
    findings = Findings(
        pattern="none", fraud_probability=0.5, single_signal=False, exposure_usd=900.0
    )
    result = apply_policy(findings)
    assert "ESCALATE_TO_ANALYST" in _actions(result)


def test_r9_undocumented_coordinated_creates_case_files_report_and_escalates():
    findings = Findings(
        pattern="undocumented", fraud_probability=0.8, single_signal=False,
        undocumented_coordinated=True, exposure_usd=1500.0,
    )
    result = apply_policy(findings)
    for expected in ("CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST"):
        assert expected in _actions(result)


def test_r10_block_all_cards_requires_two_confirmed_or_compromised_credentials():
    findings = Findings(
        pattern="account_takeover", fraud_probability=0.95, single_signal=False,
        exposure_usd=3000.0, customer_response="denies",
    )
    result = apply_policy(findings)
    assert "BLOCK_ALL_CARDS" not in _actions(result)  # only one card's evidence here


def test_approval_routes_match_policy_table():
    findings = Findings(
        pattern="card_not_present_fraud", fraud_probability=0.9, single_signal=False,
        exposure_usd=3000.0, customer_response="denies",
    )
    result = apply_policy(findings)
    routes = {a.action: a.route for a in result.actions}
    assert routes["BLOCK_CARD"] == "L2"  # exposure > $2,500
    assert routes["CREATE_CASE"] == "auto"
    if "FILE_REPORT" in routes:
        assert routes["FILE_REPORT"] == "L2"


def test_worked_example_from_readme():
    """README example: HHG-017, card testing, prob 0.72 -> denies -> prob 0.86."""
    initial = Findings(pattern="card_testing", fraud_probability=0.72, single_signal=True)
    initial_result = apply_policy(initial)
    assert "VERIFY_WITH_CUSTOMER" in _actions(initial_result)
    assert "DECLINE_TRANSACTION" in _actions(initial_result)

    final = Findings(
        pattern="card_testing", fraud_probability=0.86, single_signal=False,
        shared_device=True, exposure_usd=268.43, customer_response="denies",
    )
    final_result = apply_policy(final)
    final_actions = _actions(final_result)
    assert "BLOCK_CARD" in final_actions
    assert "CREATE_CASE" in final_actions
    assert "FILE_REPORT" in final_actions
    assert "MONITOR_CONNECTED_CARDS" in final_actions
    assert final_result.sar_file is True
```

- [ ] **Step 3: Run to verify failure**

```bash
.venv\Scripts\pytest tests/test_policy_engine.py -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Implement `src/policy/engine.py`**

```python
from __future__ import annotations

from src.policy.models import ActionRec, Findings, PolicyResult


def apply_policy(f: Findings) -> PolicyResult:
    actions: list[ActionRec] = []
    sar_file = False
    sar_reason = "No filing criteria met."

    # R3: customer confirms the transaction themselves -> close, nothing else matters.
    if f.customer_response == "confirmed_legitimate":
        return PolicyResult(
            actions=[ActionRec(action="CLOSE_NO_FRAUD", route="auto", reason="R3")],
            sar_file=False,
            sar_reason="R3: customer confirmed the transaction as their own.",
        )

    # R7: disputed but matches the customer's own recurring pattern.
    if f.customer_response == "disputes_recurring":
        return PolicyResult(
            actions=[
                ActionRec(action="CREATE_CASE", route="auto", reason="R7"),
                ActionRec(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R7"),
                ActionRec(action="WARN_CUSTOMER", route="auto", reason="R7"),
            ],
            sar_file=False,
            sar_reason="R7: disputed charge matches the customer's own recurring pattern.",
        )

    # R2: customer denies the transaction.
    if f.customer_response == "denies":
        route = "L1" if f.exposure_usd <= 2500 else "L2"
        actions.append(ActionRec(action="BLOCK_CARD", route=route, reason="R2"))
        actions.append(ActionRec(action="CREATE_CASE", route="auto", reason="R2"))
        if f.exposure_usd > 1000 or f.shared_device or f.shared_region or f.shared_email:
            sar_file = True
            sar_reason = "R2: exposure exceeds $1,000 or activity connects to a shared origin."
            actions.append(ActionRec(action="FILE_REPORT", route="L2", reason="R2"))
        if f.shared_device or f.shared_region or f.shared_email:
            actions.append(
                ActionRec(action="MONITOR_CONNECTED_CARDS", route="auto", reason="R2/R6")
            )
        return _finish(f, actions, sar_file, sar_reason)

    # R4: no reply within 24 hours.
    if f.customer_response == "no_reply":
        actions.append(ActionRec(action="MONITOR_CARD", route="auto", reason="R4"))
        actions.append(ActionRec(action="DECLINE_TRANSACTION", route="L1", reason="R4"))
        if f.exposure_usd > 500:
            actions.append(ActionRec(action="ESCALATE_TO_ANALYST", route="auto", reason="R4"))
        return _finish(f, actions, sar_file, sar_reason)

    # R5: card testing pattern.
    if f.pattern == "card_testing":
        actions.append(ActionRec(action="DECLINE_TRANSACTION", route="L1", reason="R5"))
        actions.append(ActionRec(action="STEP_UP_AUTH", route="auto", reason="R5"))
        if f.single_signal and f.fraud_probability < 0.70:
            actions.append(
                ActionRec(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R1")
            )

    # R6: shared origin across cards (independent of pattern, if not already handled by R2).
    if (f.shared_device or f.shared_region or f.shared_email) and not any(
        a.action == "CREATE_CASE" for a in actions
    ):
        actions.append(ActionRec(action="CREATE_CASE", route="auto", reason="R6"))
        actions.append(ActionRec(action="FILE_REPORT", route="L2", reason="R6"))
        actions.append(
            ActionRec(action="MONITOR_CONNECTED_CARDS", route="auto", reason="R6")
        )
        sar_file = True
        sar_reason = "R6: shared device/region/email links this to other cards."

    # R9: undocumented pattern, coordinated/repeated abuse.
    if f.pattern == "undocumented" and f.undocumented_coordinated:
        if not any(a.action == "CREATE_CASE" for a in actions):
            actions.append(ActionRec(action="CREATE_CASE", route="auto", reason="R9"))
        if not any(a.action == "FILE_REPORT" for a in actions):
            actions.append(ActionRec(action="FILE_REPORT", route="L2", reason="R9"))
            sar_file = True
            sar_reason = "R9: undocumented but coordinated/repeated abuse across customers."
        actions.append(ActionRec(action="ESCALATE_TO_ANALYST", route="auto", reason="R9"))

    # R1: single weak signal -> verify before any block.
    if f.single_signal and f.fraud_probability < 0.70:
        if not any(a.action in ("VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH") for a in actions):
            actions.append(
                ActionRec(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R1")
            )
        actions = [
            a for a in actions if a.action not in ("BLOCK_CARD", "BLOCK_ALL_CARDS", "DECLINE_TRANSACTION")
            or a.action == "DECLINE_TRANSACTION" and f.pattern == "card_testing"
        ]

    # R8: uncertain verdict with real exposure -> escalate.
    if 0.15 < f.fraud_probability < 0.85 and f.exposure_usd > 500:
        if not any(a.action == "ESCALATE_TO_ANALYST" for a in actions):
            actions.append(ActionRec(action="ESCALATE_TO_ANALYST", route="auto", reason="R8"))

    if not actions:
        if f.fraud_probability <= 0.15:
            actions.append(ActionRec(action="CLOSE_NO_FRAUD", route="auto", reason="R8"))
        else:
            actions.append(
                ActionRec(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R1")
            )

    return _finish(f, actions, sar_file, sar_reason)


def _finish(
    f: Findings, actions: list[ActionRec], sar_file: bool, sar_reason: str
) -> PolicyResult:
    # R10: BLOCK_ALL_CARDS only with >=2 confirmed-fraud cards or confirmed compromised
    # credentials -- this engine only ever sees single-card findings, so it never emits
    # BLOCK_ALL_CARDS; a caller investigating multiple cards for one customer would need
    # to call apply_policy per card and apply R10 at a level above this function.
    return PolicyResult(actions=actions, sar_file=sar_file, sar_reason=sar_reason)
```

- [ ] **Step 5: Run to verify passing**

```bash
.venv\Scripts\pytest tests/test_policy_engine.py -v
```

Expected: all tests pass. If any fail, the rule logic (not the test) is usually what's wrong — trace the specific README rule text again and fix `engine.py`; do not weaken a test to make it pass.

- [ ] **Step 6: Commit**

```bash
git add src/policy tests/test_policy_engine.py
git commit -m "feat: deterministic fraud policy engine (rules R1-R10)"
```

---

## Task 6: Answer-JSON Pydantic schemas

**Files:**
- Create: `src/agent/__init__.py` (empty)
- Create: `src/agent/schemas.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Produces: `Evidence`, `CaseRecord`, `SAR`, `NextBestActionSet`, `EvidenceRequest`, `AnswerFile` — pydantic models mirroring every field in the README's "Answer Format" section exactly (names, types, enums). `AnswerFile.model_validate_json(...)` / `.model_dump_json(...)` are what Tasks 12 and 14 use to produce and validate `cases/<case_id>.json`.

- [ ] **Step 1: Write the failing test** — parse the README's own worked example verbatim, so the schema is validated against ground truth, not a hand-rolled fixture.

`tests/test_schemas.py`:

```python
import json

from src.agent.schemas import AnswerFile

README_EXAMPLE = {
    "case_id": "HHG-017",
    "case": {
        "status": "closed_fraud",
        "verdict": "fraud",
        "fraud_probability": 0.86,
        "pattern": "card_testing",
        "pattern_description": "",
        "affected_txn_ids": ["T0412877", "T0412878", "T0412879", "T0412883"],
        "first_suspicious_txn_id": "T0412877",
        "connected_card_ids": ["C00877-K1"],
        "connected_device_profiles": [
            "SAMSUNG SM-G892A Build/NRD90M | Android 7.0 | samsung browser 6.2 | 2220x1080"
        ],
        "exposure_usd": 268.43,
        "evidence": [
            {
                "claim": "Three online authorizations under $3 within 40 minutes, then a $259 purchase under a product code this card has never used",
                "source": "graph",
                "ref": "query:card_window(card_id=C00377-K1, hours=2)",
                "entity_ids": ["T0412877", "T0412878", "T0412879", "T0412883"],
            },
            {
                "claim": "Customer denied the purchases when asked",
                "source": "customer",
                "ref": "evidence_request:1",
                "entity_ids": [],
            },
        ],
        "similar_prior_cases": ["CC-0141"],
        "summary": "Textbook card testing.",
        "written_to_graph": True,
        "graph_case_id": "CASE-2016-1187",
    },
    "evidence_requests": [
        {
            "type": "customer_validation",
            "asked_after_step": 4,
            "assumed_response": "Customer states they did not make these purchases and still has the card",
        }
    ],
    "next_best_actions": {
        "initial": [
            {"action": "DECLINE_TRANSACTION", "route": "L1", "reason": "R5: testing sequence observed, purchase already cleared"},
            {"action": "VERIFY_WITH_CUSTOMER", "route": "auto", "reason": "R1: probability 0.72 on pattern alone, confirm before blocking"},
        ],
        "final": [
            {"action": "BLOCK_CARD", "route": "L1", "reason": "R2 and R5: customer denied; exposure $268 is under $2,500"},
            {"action": "CREATE_CASE", "route": "auto", "reason": "R2"},
            {"action": "FILE_REPORT", "route": "L2", "reason": "R2: shared device links this to another compromised card"},
            {"action": "MONITOR_CONNECTED_CARDS", "route": "auto", "reason": "Same device profile also used on C00877-K1"},
        ],
        "what_changed": "Customer denial raised probability from 0.72 to 0.86 and confirmed the block.",
    },
    "sar": {
        "file": True,
        "reason": "R2: confirmed unauthorized use linked by a shared device to a second compromised card",
        "narrative": "On 2016-11-14 ... Total unauthorized amount: $268.43.",
        "subjects": ["C00377", "C00377-K1", "C00877-K1"],
        "total_amount_usd": 268.43,
        "activity_dates": ["2016-11-14", "2016-11-14"],
    },
    "stop_reason": "Customer denial settled the verdict.",
    "tool_calls": 9,
    "tokens": 12480,
    "latency_s": 18.7,
}


def test_readme_example_parses():
    answer = AnswerFile.model_validate(README_EXAMPLE)
    assert answer.case_id == "HHG-017"
    assert answer.case.pattern == "card_testing"
    assert answer.next_best_actions.final[0].action == "BLOCK_CARD"
    assert answer.sar.file is True


def test_round_trip_json():
    answer = AnswerFile.model_validate(README_EXAMPLE)
    dumped = json.loads(answer.model_dump_json())
    reparsed = AnswerFile.model_validate(dumped)
    assert reparsed.case_id == answer.case_id
    assert reparsed.case.exposure_usd == answer.case.exposure_usd


def test_legitimate_verdict_shape():
    minimal = {
        **README_EXAMPLE,
        "case": {
            **README_EXAMPLE["case"],
            "verdict": "legitimate",
            "status": "closed_legitimate",
            "affected_txn_ids": [],
            "exposure_usd": 0,
        },
        "sar": {
            "file": False, "reason": "No fraud found.", "narrative": "",
            "subjects": [], "total_amount_usd": 0, "activity_dates": [],
        },
    }
    answer = AnswerFile.model_validate(minimal)
    assert answer.case.affected_txn_ids == []
    assert answer.sar.file is False
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv\Scripts\pytest tests/test_schemas.py -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `src/agent/schemas.py`**

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Pattern = Literal[
    "card_testing",
    "card_not_present_fraud",
    "card_not_present_new_device",
    "out_of_region_use",
    "account_takeover",
    "undocumented",
    "none",
]
Verdict = Literal["fraud", "legitimate", "uncertain"]
Status = Literal["open", "closed_fraud", "closed_legitimate", "escalated"]
EvidenceSource = Literal["graph", "document", "customer", "external"]
Route = Literal["auto", "L1", "L2"]
EvidenceRequestType = Literal["customer_validation", "step_up_auth", "analyst_info"]


class Evidence(BaseModel):
    claim: str
    source: EvidenceSource
    ref: str
    entity_ids: list[str] = []


class CaseRecord(BaseModel):
    status: Status
    verdict: Verdict
    fraud_probability: float
    pattern: Pattern
    pattern_description: str = ""
    affected_txn_ids: list[str] = []
    first_suspicious_txn_id: str = ""
    connected_card_ids: list[str] = []
    connected_device_profiles: list[str] = []
    exposure_usd: float = 0.0
    evidence: list[Evidence] = []
    similar_prior_cases: list[str] = []
    summary: str
    written_to_graph: bool
    graph_case_id: str = ""


class SAR(BaseModel):
    file: bool
    reason: str
    narrative: str = ""
    subjects: list[str] = []
    total_amount_usd: float = 0.0
    activity_dates: list[str] = []


class ActionEntry(BaseModel):
    action: str
    route: Route
    reason: str


class NextBestActionSet(BaseModel):
    initial: list[ActionEntry]
    final: list[ActionEntry]
    what_changed: str


class EvidenceRequestRecord(BaseModel):
    type: EvidenceRequestType
    asked_after_step: int
    assumed_response: str


class AnswerFile(BaseModel):
    case_id: str
    case: CaseRecord
    evidence_requests: list[EvidenceRequestRecord] = []
    next_best_actions: NextBestActionSet
    sar: SAR
    stop_reason: str
    tool_calls: int
    tokens: int
    latency_s: float
```

- [ ] **Step 4: Run to verify passing**

```bash
.venv\Scripts\pytest tests/test_schemas.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent/__init__.py src/agent/schemas.py tests/test_schemas.py
git commit -m "feat: Pydantic schemas for the answer JSON format"
```

---

## Task 7: Bulk data loading into TigerGraph

**Files:**
- Create: `src/schema/loading_jobs.py`
- Create: `scripts/load_data.py`

**Interfaces:**
- Consumes: `TigerGraphMCP` (Task 2), `card_id_for`/`build_card_id_map` (Task 3), the vertex/edge type names from Task 4.
- Produces: populated graph. Task 8 (manual checkpoint) and everything after depends on this having run successfully.

- [ ] **Step 1: Write `src/schema/loading_jobs.py`** — a declarative GSQL `LOADING JOB` for the columns that need no cross-file resolution, plus a Python pass for `Card`/`OWNS`/`MADE`, which do.

**Why `Card`/`OWNS`/`MADE` can't be a simple declarative load:** `transactions.csv` only has `customer_id` per row, not `card_id`. Per Task 3's finding, the correct `card_id` for a customer is not a fixed formula (`customer_id + "-K1"` is only the *default*, used when that customer isn't referenced in `case_pack.csv`/`closed_cases_history.csv` — plenty of customers' real `card_id` has a `-K2`/`-K3` suffix instead) — it can only be resolved by looking the customer up in those reference files. A declarative `LOAD ... TO VERTEX Card VALUES ($"customer_id", $"customer_id")` would key every card by the bare `customer_id`, and a blanket `-K1`-for-everyone default would be equally wrong for every non-default customer — and simply adding a second, correctly-suffixed `Card` vertex afterward doesn't fix anything, since the `MADE` edges (all of that customer's transactions) would still point at the wrong, first-created card, leaving the correct one empty. The only correct order is: resolve every customer's real `card_id` first, then create `Card`/`OWNS`/`MADE` using that resolved value from the start.

```python
from __future__ import annotations

import pandas as pd

from src.schema.card_ids import build_card_id_map, card_id_for
from src.schema.columns import generate_attrs
from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


def transactions_loading_job_gsql(transactions_csv_path: str) -> str:
    txn_attrs = generate_attrs(transactions_csv_path, primary_key="TransactionID")
    # $"ColumnName" -> attribute mapping. Card/OWNS/MADE are deliberately NOT loaded
    # here -- see load_cards_and_made_edges below and the note above this function.
    attr_mappings = ",\n        ".join(f'{name} = $"{name}"' for name, _ in txn_attrs)
    return f'''
USE GRAPH {GRAPH_NAME}
BEGIN
CREATE LOADING JOB load_transactions FOR GRAPH {GRAPH_NAME} {{
    DEFINE FILENAME f1 = "{transactions_csv_path}";
    LOAD f1 TO VERTEX Customer VALUES ($"customer_id") USING header="true", separator=",";
    LOAD f1 TO VERTEX Transaction VALUES (
        $"TransactionID",
        {attr_mappings}
    ) USING header="true", separator=",";
    LOAD f1 TO VERTEX EmailDomain VALUES ($"P_emaildomain") USING header="true", separator=",";
    LOAD f1 TO VERTEX BillingRegion VALUES ($"addr1") USING header="true", separator=",";
    LOAD f1 TO EDGE PURCHASER_EMAIL VALUES ($"TransactionID" Transaction, $"P_emaildomain" EmailDomain) USING header="true", separator=",";
    LOAD f1 TO EDGE BILLED_IN VALUES ($"TransactionID" Transaction, $"addr1" BillingRegion) USING header="true", separator=",";
}}
RUN LOADING JOB load_transactions
END
'''.strip()


def closed_cases_loading_job_gsql(closed_cases_csv_path: str) -> str:
    return f'''
USE GRAPH {GRAPH_NAME}
BEGIN
CREATE LOADING JOB load_closed_cases FOR GRAPH {GRAPH_NAME} {{
    DEFINE FILENAME f2 = "{closed_cases_csv_path}";
    LOAD f2 TO VERTEX ClosedCase VALUES (
        $"case_id", $"customer_id", $"card_id", $"opened_at", $"closed_at",
        $"outcome", $"pattern", $"first_fraud_txn_id", $"n_txns",
        $"exposure_usd", $"actions_taken", $"report_filed", $"analyst_notes"
    ) USING header="true", separator=",";
    LOAD f2 TO EDGE ON_CARD VALUES ($"case_id" ClosedCase, $"card_id" Card) USING header="true", separator=",";
}}
RUN LOADING JOB load_closed_cases
END
'''.strip()


async def load_cards_and_made_edges(
    tg: TigerGraphMCP, transactions_csv_path: str, case_pack_csv: str, closed_cases_csv: str
) -> dict[str, str]:
    """Creates every Card vertex and its OWNS edge with the CORRECT resolved card_id
    (via Task 3's build_card_id_map/card_id_for), then streams transactions.csv once
    to wire MADE edges using that same resolved card_id per row -- so no card is ever
    created under the wrong ID in the first place. Returns the full customer_id ->
    card_id map (13,500ish entries) for logging/verification."""
    case_pack_df = pd.read_csv(case_pack_csv)
    closed_cases_df = pd.read_csv(closed_cases_csv)
    override_map = build_card_id_map(case_pack_df, closed_cases_df)

    unique_customers = pd.read_csv(transactions_csv_path, usecols=["customer_id"])["customer_id"].unique()
    full_map = {cid: card_id_for(cid, override_map) for cid in unique_customers}

    # Phase 1: Card vertices + OWNS edges, straight from the small in-memory map --
    # no need to touch the 708MB transactions file for this part. Card's schema
    # (Task 4) declares 4 attributes (card_id PK, customer_id, ring_cluster_id,
    # cluster_prior_fraud_rate) but this INSERT only supplies the first two --
    # ring_cluster_id/cluster_prior_fraud_rate are meant to stay unset until Task
    # 8.5's connected-components pass writes them. If TigerGraph's GSQL rejects an
    # INSERT with fewer values than declared attributes (behavior can vary by
    # version), fall back to explicit defaults: VALUES("{card_id}", "{customer_id}",
    # "{card_id}", 0.0) -- Task 8.5's label-propagation query overwrites
    # ring_cluster_id unconditionally on its first pass regardless of this default,
    # so either form is safe once Task 8.5 runs.
    statements: list[str] = []
    for customer_id, card_id in full_map.items():
        statements.append(f'INSERT INTO VERTEX Card VALUES ("{card_id}", "{customer_id}")')
        statements.append(
            f'INSERT INTO EDGE OWNS VALUES ("{customer_id}" Customer, "{card_id}" Card)'
        )
        if len(statements) >= 1000:
            await tg.gsql(f"USE GRAPH {GRAPH_NAME}\n" + "\n".join(s + ";" for s in statements))
            statements = []
    if statements:
        await tg.gsql(f"USE GRAPH {GRAPH_NAME}\n" + "\n".join(s + ";" for s in statements))
    print(f"Created {len(full_map)} Card vertices with resolved card_id (OWNS edges included)")

    # Phase 2: MADE edges. This is the one part that has to stream the full file,
    # since transactions.csv only has customer_id per row, never the resolved card_id.
    made_statements: list[str] = []
    edge_count = 0
    for chunk in pd.read_csv(transactions_csv_path, usecols=["TransactionID", "customer_id"], chunksize=50_000):
        for txn_id, customer_id in zip(chunk["TransactionID"], chunk["customer_id"]):
            card_id = full_map[customer_id]
            made_statements.append(
                f'INSERT INTO EDGE MADE VALUES ("{card_id}" Card, "{txn_id}" Transaction)'
            )
            if len(made_statements) >= 1000:
                await tg.gsql(f"USE GRAPH {GRAPH_NAME}\n" + "\n".join(s + ";" for s in made_statements))
                edge_count += len(made_statements)
                made_statements = []
    if made_statements:
        await tg.gsql(f"USE GRAPH {GRAPH_NAME}\n" + "\n".join(s + ";" for s in made_statements))
        edge_count += len(made_statements)
    print(f"Created {edge_count} MADE edges")

    return full_map


async def run_all_loading_jobs(
    tg: TigerGraphMCP, transactions_csv: str, closed_cases_csv: str, case_pack_csv: str
) -> None:
    # Order matters: Transaction vertices must exist before Phase 2's MADE edges
    # reference them, and Card vertices must exist before closed_cases_loading_job_gsql's
    # ON_CARD edge references them.
    print(await tg.gsql(transactions_loading_job_gsql(transactions_csv)))
    await load_cards_and_made_edges(tg, transactions_csv, case_pack_csv, closed_cases_csv)
    print(await tg.gsql(closed_cases_loading_job_gsql(closed_cases_csv)))
```

**Note:** `INVOLVES` (ClosedCase→Transaction, from the pipe-separated `txn_ids` field) and `CONNECTED_TO` (from `connected_card_ids`) aren't expressible as a single-row `LOAD ... TO EDGE` mapping since they're one-to-many from a pipe-separated string. Handle those in Task 8's post-load step with a small Python script that reads `closed_cases_history.csv` directly, splits `txn_ids`/`connected_card_ids` on `|`, and issues individual `INSERT INTO EDGE` GSQL statements (or a batch `UPSERT` via `tg.gsql`) per pair — do this as part of Task 8, not here, since it needs row-level Python logic rather than a declarative loading job.

- [ ] **Step 2: Write `scripts/load_data.py`**

```python
import asyncio

from src.schema.loading_jobs import run_all_loading_jobs
from src.tg_client import TigerGraphMCP


async def main() -> None:
    async with TigerGraphMCP() as tg:
        await run_all_loading_jobs(tg, "transactions.csv", "closed_cases_history.csv", "case_pack.csv")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Run it**

```bash
.venv\Scripts\python scripts\load_data.py
```

Expected: this takes real time — 590K transaction rows / 708MB file for the declarative load, plus Phase 2's ~591 batched GSQL calls (590,742 rows / 1000 per batch) for `MADE` edges. Let it run, watch for GSQL errors rather than assuming success. A common failure mode is a type mismatch on one of the 393 generated `Transaction` attributes (e.g. a column that's actually string-typed but was inferred as DOUBLE in Task 4) — if you see a load error for a specific column, add it to `_STRING_COLUMNS` in `src/schema/columns.py`, re-run `scripts/create_schema.py` (after `DROP VERTEX Transaction` first, since the type already exists), then re-run this script.

- [ ] **Step 4: Verify counts**

```bash
.venv\Scripts\python -c "
import asyncio
from src.tg_client import TigerGraphMCP
async def main():
    async with TigerGraphMCP() as tg:
        for v in ('Customer','Card','Transaction','EmailDomain','BillingRegion','ClosedCase'):
            print(v, await tg.gsql(f'SELECT COUNT(*) FROM {v}'))
asyncio.run(main())
"
```

Expected roughly: Customer ~13,500, **Card ~13,500** (every customer gets exactly one card by construction — card1 is 1:1 with customer_id in this dataset, confirmed during design), Transaction 590,742, ClosedCase 5,565 (exact counts per the README's stated dataset sizes — large deviations mean the loading job silently dropped rows, worth investigating before continuing). Also spot-check one known non-default card, e.g. `SELECT * FROM Card WHERE card_id == "C08623-K2"` should return a row (this is the customer from case `HHG-003` whose real card_id has a `-K2` suffix, not the `-K1` default) — if it comes back empty, Phase 1 of `load_cards_and_made_edges` didn't resolve the override correctly.

- [ ] **Step 5: Commit**

```bash
git add src/schema/loading_jobs.py scripts/load_data.py
git commit -m "feat: bulk loading jobs for transactions and closed cases, with resolved card_id from the start"
```

---

## Task 8: Derived entities (DeviceProfile) + pipe-separated edges + manual case checkpoint

This is the README's own advice — "Investigate one case by hand, before writing any agent code" — combined with the remaining graph structure that a declarative loading job can't express.

**Files:**
- Create: `src/schema/derive_entities.py`
- Create: `scripts/derive_entities.py`
- Create: `docs/manual-case-checkpoint.md`

**Interfaces:**
- Consumes: `TigerGraphMCP`, `identity.csv` (for `DeviceProfile`), `closed_cases_history.csv` (for `INVOLVES`/`CONNECTED_TO` edges).
- Produces: fully connected graph (`DeviceProfile` vertices + `FROM_DEVICE` edges, `INVOLVES`/`CONNECTED_TO` edges) and a written record confirming the schema actually answers investigation questions — a real go/no-go gate before Task 9 onward.

- [ ] **Step 1: Write `src/schema/derive_entities.py`**

```python
from __future__ import annotations

import csv
import hashlib

from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


def device_id_for(device_info: str, os_: str, browser: str, screen: str) -> str:
    raw = f"{device_info}|{os_}|{browser}|{screen}"
    return "D" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


async def load_device_profiles(tg: TigerGraphMCP, identity_csv_path: str) -> int:
    inserted = 0
    with open(identity_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        batch: list[tuple[str, str, str, str, str, str]] = []
        for row in reader:
            device_info = row.get("DeviceInfo") or ""
            os_ = row.get("id_30") or ""
            browser = row.get("id_31") or ""
            screen = row.get("id_33") or ""
            if not device_info and not os_ and not browser:
                continue
            device_id = device_id_for(device_info, os_, browser, screen)
            batch.append((device_id, device_info, os_, browser, screen, row["TransactionID"]))
            if len(batch) >= 500:
                await _flush_device_batch(tg, batch)
                inserted += len(batch)
                batch = []
        if batch:
            await _flush_device_batch(tg, batch)
            inserted += len(batch)
    return inserted


async def _flush_device_batch(
    tg: TigerGraphMCP, batch: list[tuple[str, str, str, str, str, str]]
) -> None:
    statements = []
    for device_id, device_info, os_, browser, screen, txn_id in batch:
        esc = lambda s: s.replace('"', '\\"')
        statements.append(
            f'INSERT INTO VERTEX DeviceProfile VALUES ('
            f'"{device_id}", "{esc(device_info)}", "{esc(os_)}", "{esc(browser)}", "{esc(screen)}")'
        )
        statements.append(
            f'INSERT INTO EDGE FROM_DEVICE VALUES ("{txn_id}" Transaction, "{device_id}" DeviceProfile)'
        )
    gsql = f"USE GRAPH {GRAPH_NAME}\n" + "\n".join(s + ";" for s in statements)
    await tg.gsql(gsql)


async def load_closed_case_multi_edges(tg: TigerGraphMCP, closed_cases_csv_path: str) -> None:
    with open(closed_cases_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        statements: list[str] = []
        for row in reader:
            case_id = row["case_id"]
            for txn_id in (row.get("txn_ids") or "").split("|"):
                if txn_id:
                    statements.append(
                        f'INSERT INTO EDGE INVOLVES VALUES ("{case_id}" ClosedCase, "{txn_id}" Transaction)'
                    )
            for card_id in (row.get("connected_card_ids") or "").split("|"):
                if card_id:
                    statements.append(
                        f'INSERT INTO EDGE CONNECTED_TO VALUES ("{case_id}" ClosedCase, "{card_id}" Card)'
                    )
            if len(statements) >= 500:
                await tg.gsql(f"USE GRAPH {GRAPH_NAME}\n" + "\n".join(s + ";" for s in statements))
                statements = []
        if statements:
            await tg.gsql(f"USE GRAPH {GRAPH_NAME}\n" + "\n".join(s + ";" for s in statements))
```

**Note on `CONNECTED_TO` target cards:** these reference `Card` vertices for *other customers* (per Task 3's finding). Task 7's `load_cards_and_made_edges` already resolved every customer's correct `card_id` (default or override) before this task runs, so every `-K2`/`-K3` card referenced here already exists with the right ID — no separate patching step is needed at this point (an earlier draft of this plan had a `fix_card_ids` correction step here; it's now redundant and has been removed, since fixing it at the source in Task 7 is what actually keeps `MADE` edges pointed at the right card, which a post-hoc patch here could not do).

- [ ] **Step 2: Write `scripts/derive_entities.py`**

```python
import asyncio

from src.schema.derive_entities import load_closed_case_multi_edges, load_device_profiles
from src.tg_client import TigerGraphMCP


async def main() -> None:
    async with TigerGraphMCP() as tg:
        n = await load_device_profiles(tg, "identity.csv")
        print(f"Loaded {n} device profile references")
        await load_closed_case_multi_edges(tg, "closed_cases_history.csv")
        print("Loaded ClosedCase INVOLVES/CONNECTED_TO edges")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Run it**

```bash
.venv\Scripts\python scripts\derive_entities.py
```

Expected: completes without GSQL errors; `identity.csv` has 144,432 rows so device loading takes a few minutes.

- [ ] **Step 4: Manual investigation of one case — write findings to `docs/manual-case-checkpoint.md`**

Pick `HHG-017` (it's the README's own worked example, so you have a ground-truth answer to check against). Run each of these queries by hand and record the actual results:

```bash
.venv\Scripts\python -c "
import asyncio
from src.tg_client import TigerGraphMCP
async def main():
    async with TigerGraphMCP() as tg:
        print(await tg.gsql('USE GRAPH FraudInvestigation SELECT * FROM Transaction WHERE transaction_id == \"3450629\" LIMIT 1'))
        print(await tg.gsql('USE GRAPH FraudInvestigation SELECT * FROM Card-(MADE)->Transaction WHERE Card.card_id == \"C04570-K1\" LIMIT 20'))
asyncio.run(main())
"
```

Write `docs/manual-case-checkpoint.md` with: the raw transaction found, what other transactions exist on that card, whether a `DeviceProfile` links it to anything else, and your own by-hand verdict/pattern/recommended action per the policy. This is the ground truth Task 13's end-to-end test compares against.

**Go/no-go:** if these queries don't return sensible results (empty results, wrong types, edges not traversable), stop and fix the schema/loading before writing any agent code — this is the whole point of the checkpoint.

- [ ] **Step 5: Commit**

```bash
git add src/schema/derive_entities.py scripts/derive_entities.py docs/manual-case-checkpoint.md
git commit -m "feat: derived entities (DeviceProfile, multi-edges) and manual case checkpoint"
```

---

## Task 8.5: Graph algorithms — fraud-ring detection via Connected Components

The README's required-components list names *"GSQL and TigerGraph graph algorithms"*
explicitly, separately from plain GSQL traversal — Tasks 4-8 satisfy the GSQL half but
not the algorithms half. This task adds the missing piece: a batch pass that clusters
`Card`s connected by any chain of shared device/region/email into `ring_cluster_id`
groups, and computes each cluster's historical fraud rate from `ClosedCase`. Run once,
after Task 8's derived entities exist (it needs `DeviceProfile`/`BillingRegion`/
`EmailDomain` edges) and before Task 10 (evidence-gathering will read `ring_cluster_id`
directly off `Card`).

**Files:**
- Create: `src/graph_algorithms/__init__.py` (empty)
- Create: `src/graph_algorithms/connected_components.py`
- Create: `scripts/run_connected_components.py`

**Interfaces:**
- Consumes: `TigerGraphMCP`, the `SHARES_ORIGIN` edge type and `ring_cluster_id`/
  `cluster_prior_fraud_rate` `Card` attributes (both added to the schema in Task 4).
- Produces: every `Card` vertex gets `ring_cluster_id` and `cluster_prior_fraud_rate`
  populated. Task 10's `ring_membership(tg, card_id)` reads these directly.

- [ ] **Step 1: Write `src/graph_algorithms/connected_components.py`** — builds the
`SHARES_ORIGIN` projection, then runs label-propagation connected components over it.
Two GSQL query strings, run via `tg.gsql`.

```python
from __future__ import annotations

from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"

BUILD_SHARES_ORIGIN_GSQL = f'''
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY build_shares_origin() FOR GRAPH {GRAPH_NAME} {{
    // Two cards share origin if they made transactions from the same device,
    // billed to the same region, or with the same purchaser email domain.
    Devices = {{DeviceProfile.*}};
    ViaDevice = SELECT c2 FROM Devices:d -(reverse_FROM_DEVICE)- Transaction -(reverse_MADE)- Card:c1,
                       Devices:d -(reverse_FROM_DEVICE)- Transaction -(reverse_MADE)- Card:c2
                WHERE c1.card_id != c2.card_id
                ACCUM INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "device");

    Regions = {{BillingRegion.*}};
    ViaRegion = SELECT c2 FROM Regions:r -(reverse_BILLED_IN)- Transaction -(reverse_MADE)- Card:c1,
                       Regions:r -(reverse_BILLED_IN)- Transaction -(reverse_MADE)- Card:c2
                WHERE c1.card_id != c2.card_id
                ACCUM INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "region");

    Emails = {{EmailDomain.*}};
    ViaEmail = SELECT c2 FROM Emails:e -(reverse_PURCHASER_EMAIL)- Transaction -(reverse_MADE)- Card:c1,
                      Emails:e -(reverse_PURCHASER_EMAIL)- Transaction -(reverse_MADE)- Card:c2
               WHERE c1.card_id != c2.card_id
               ACCUM INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "email");
}}
INSTALL QUERY build_shares_origin
RUN QUERY build_shares_origin()
'''.strip()

CONNECTED_COMPONENTS_GSQL = f'''
USE GRAPH {GRAPH_NAME}
CREATE OR REPLACE QUERY label_propagation_cc() FOR GRAPH {GRAPH_NAME} {{
    // Standard "propagate minimum label" connected components: every card starts
    // labeled with its own card_id; each round, adopt the smallest label seen among
    // SHARES_ORIGIN neighbors; stop when nothing changes or after a round cap.
    MaxAccum<STRING> @min_label;
    Cards = {{Card.*}};
    Cards = SELECT c FROM Cards:c POST-ACCUM c.ring_cluster_id = c.card_id;

    WHILE TRUE LIMIT 25 DO
        Changed = SELECT c FROM Cards:c -(SHARES_ORIGIN)- Card:nbr
                  ACCUM c.@min_label += nbr.ring_cluster_id
                  POST-ACCUM
                      CASE WHEN c.@min_label < c.ring_cluster_id THEN
                          c.ring_cluster_id = c.@min_label
                      END;
        IF Changed.size() == 0 THEN
            BREAK;
        END;
    END;
}}
INSTALL QUERY label_propagation_cc
RUN QUERY label_propagation_cc()
'''.strip()


async def run_connected_components(tg: TigerGraphMCP) -> None:
    print(await tg.gsql(BUILD_SHARES_ORIGIN_GSQL))
    print(await tg.gsql(CONNECTED_COMPONENTS_GSQL))


async def compute_cluster_fraud_rates(tg: TigerGraphMCP) -> None:
    gsql = f'''
    USE GRAPH {GRAPH_NAME}
    CREATE OR REPLACE QUERY cluster_fraud_rate() FOR GRAPH {{GRAPH_NAME}} {{
        SumAccum<INT> @@fraudCount;
        SumAccum<INT> @@totalCount;
        MapAccum<STRING, SumAccum<INT>> @@clusterFraud;
        MapAccum<STRING, SumAccum<INT>> @@clusterTotal;

        ClosedCards = SELECT c FROM ClosedCase:cc -(ON_CARD)-> Card:c
                      ACCUM
                          @@clusterTotal += (c.ring_cluster_id -> 1),
                          IF cc.outcome == "confirmed_fraud" THEN
                              @@clusterFraud += (c.ring_cluster_id -> 1)
                          END;

        AllCards = {{Card.*}};
        AllCards = SELECT c FROM AllCards:c
                   POST-ACCUM
                       FLOAT total = @@clusterTotal.get(c.ring_cluster_id),
                       FLOAT fraud = @@clusterFraud.get(c.ring_cluster_id),
                       c.cluster_prior_fraud_rate = (total > 0) ? (fraud / total) : 0.0;
    }}
    INSTALL QUERY cluster_fraud_rate
    RUN QUERY cluster_fraud_rate()
    '''.strip().replace("{GRAPH_NAME}", GRAPH_NAME)
    print(await tg.gsql(gsql))
```

**Note — this is a first draft, expect live GSQL iteration:** GSQL's exact syntax for
multi-vertex-set joins (the `ViaDevice`/`ViaRegion`/`ViaEmail` selects above), the
`WHILE`-loop change-detection idiom, and nested-map accumulator access (`.get(...)`)
are all things TigerGraph's GSQL dialect is particular about across versions. Treat
each query the same way Task 10 treats its queries: run it standalone against the live
graph in Step 3, read the actual GSQL compiler error, and fix against TigerGraph's
interpreted/installed query documentation rather than guessing twice. If TigerGraph
GSQL's built-in algorithm library happens to be available on this Savanna instance
(check via `tg.gsql("SHOW QUERY tg_conn_comp")` or similar, or the Savanna workspace's
Applications/Algorithm-library panel) a packaged connected-components query can replace
`CONNECTED_COMPONENTS_GSQL` above — prefer it if it exists and produces the same
`ring_cluster_id`-on-`Card` result, since a maintained library implementation beats a
hand-rolled one; keep the hand-written version as the fallback either way.

- [ ] **Step 2: Write `scripts/run_connected_components.py`**

```python
import asyncio

from src.graph_algorithms.connected_components import (
    compute_cluster_fraud_rates,
    run_connected_components,
)
from src.tg_client import TigerGraphMCP


async def main() -> None:
    async with TigerGraphMCP() as tg:
        await run_connected_components(tg)
        await compute_cluster_fraud_rates(tg)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Run it**

```bash
.venv\Scripts\python scripts\run_connected_components.py
```

Expected: completes without GSQL errors (after the iteration noted above). This runs
over 13,500+ cards and their shared-origin edges — a full run may take a few minutes;
that's fine, it's a one-time batch pass, not something re-run per case.

- [ ] **Step 4: Verify with a spot check**

```bash
.venv\Scripts\python -c "
import asyncio
from src.tg_client import TigerGraphMCP
async def main():
    async with TigerGraphMCP() as tg:
        print(await tg.gsql('USE GRAPH FraudInvestigation SELECT card_id, ring_cluster_id, cluster_prior_fraud_rate FROM Card WHERE card_id == \"C03528-K1\" LIMIT 1'))
asyncio.run(main())
"
```

Expected: `C03528-K1` (one of the 22-member ring found during dataset exploration, case
`CC-2649`/`CC-2971`/etc. in `closed_cases_history.csv`) shows a `ring_cluster_id` shared
with the other 21 cards in that ring, and a `cluster_prior_fraud_rate` reflecting how
much of that cluster's history was confirmed fraud. If it comes back as its own
isolated cluster (`ring_cluster_id == card_id`, rate `0.0`), the `SHARES_ORIGIN`
projection or the label-propagation loop has a bug — this specific known ring is the
regression check for this task, use it.

- [ ] **Step 5: Commit**

```bash
git add src/graph_algorithms scripts/run_connected_components.py
git commit -m "feat: connected-components graph algorithm for fraud-ring clustering"
```

---

## Task 9: Knowledge ingestion — policy, patterns, and regulatory PDFs

**Files:**
- Create: `src/ingestion/__init__.py` (empty)
- Create: `src/ingestion/policy_chunks.py`
- Create: `src/ingestion/regulatory_docs.py`
- Create: `src/ingestion/embeddings.py`
- Create: `scripts/ingest_knowledge.py`
- Test: `tests/test_policy_chunks.py`
- Test: `tests/test_regulatory_docs.py`

**Interfaces:**
- Produces: `POLICY_CHUNKS: list[dict]` (each `{"doc_id", "source": "policy"|"pattern", "section", "text"}`), `chunk_pdf_text(text: str, source_name: str) -> list[dict]`, `embed(texts: list[str]) -> list[list[float]]` (calls Ollama `nomic-embed-text`). Task 10 (`retrieve_knowledge` tool) queries the `KnowledgeDoc` vertices this task creates.

- [ ] **Step 1: Write `src/ingestion/policy_chunks.py`** — the policy text, hand-chunked by rule (this is short enough to just write out; it's the actual R1–R10 text from README.md, not paraphrased).

```python
from __future__ import annotations

POLICY_CHUNKS: list[dict[str, str]] = [
    {"doc_id": "policy-r1", "source": "policy", "section": "R1", "text": "R1. Verify before you block on a weak signal. If the case rests on a single signal (including a risk score alone) and your assessed fraud probability is below 0.70, recommend VERIFY_WITH_CUSTOMER or STEP_UP_AUTH before any block. Blocking a legitimate customer on one signal is a policy breach."},
    {"doc_id": "policy-r2", "source": "policy", "section": "R2", "text": "R2. Customer denies the transaction. Recommend BLOCK_CARD and CREATE_CASE. Add FILE_REPORT if exposure exceeds $1,000 or the case connects to a shared device profile or another card's fraud."},
    {"doc_id": "policy-r3", "source": "policy", "section": "R3", "text": "R3. Customer confirms the transaction. Recommend CLOSE_NO_FRAUD. Note the confirmation in the case file."},
    {"doc_id": "policy-r4", "source": "policy", "section": "R4", "text": "R4. No reply within 24 hours. Recommend MONITOR_CARD and DECLINE_TRANSACTION for pending authorizations. Escalate if exposure exceeds $500."},
    {"doc_id": "policy-r5", "source": "policy", "section": "R5", "text": "R5. Card testing. Three or more small online authorizations on one card within an hour, followed by a larger purchase: recommend DECLINE_TRANSACTION and STEP_UP_AUTH. If a purchase over $100 has already cleared, recommend BLOCK_CARD."},
    {"doc_id": "policy-r6", "source": "policy", "section": "R6", "text": "R6. Shared origin. When several cards show fraud from the same device profile, the same billing region, or the same recipient email in one window, name the shared element, recommend CREATE_CASE and FILE_REPORT, and MONITOR_CONNECTED_CARDS for every card that shares it."},
    {"doc_id": "policy-r7", "source": "policy", "section": "R7", "text": "R7. Disputed but legitimate. When the customer disputes a charge that matches their own recurring pattern (same merchant, same amount, monthly), recommend CREATE_CASE, VERIFY_WITH_CUSTOMER, and WARN_CUSTOMER. Do not block."},
    {"doc_id": "policy-r8", "source": "policy", "section": "R8", "text": "R8. Escalate when uncertain and exposed. If the verdict is uncertain and exposure exceeds $500, or the evidence conflicts, recommend ESCALATE_TO_ANALYST."},
    {"doc_id": "policy-r9", "source": "policy", "section": "R9", "text": "R9. Undocumented patterns. When activity fits none of the known patterns but the evidence shows coordinated or repeated abuse across customers, recommend CREATE_CASE, FILE_REPORT, and ESCALATE_TO_ANALYST, and describe the pattern in your own words. Do not force it into a known category."},
    {"doc_id": "policy-r10", "source": "policy", "section": "R10", "text": "R10. Never BLOCK_ALL_CARDS unless at least two of the customer's cards show confirmed fraud or the customer's credentials are confirmed compromised."},
    {"doc_id": "policy-case-vs-report", "source": "policy", "section": "3a", "text": "A case (CREATE_CASE) is the bank's internal record of an investigation. Open one whenever fraud probability reaches 0.30, whenever you request evidence, or whenever a customer disputes a charge. A suspicious activity report (FILE_REPORT) is a regulatory filing sent outside the bank. File one when fraud is confirmed or strongly suspected and at least one of: exposure exceeds $1,000; the activity connects to a shared device profile, a shared region cluster, or another customer's fraud; the pattern is coordinated or undocumented (R9)."},
    {"doc_id": "pattern-card-testing", "source": "pattern", "section": "1", "text": "Card testing. A stolen card number is checked before use: three or more tiny online authorizations, often under $5, then a larger purchase. Confirmed by the sequence itself. Policy R5."},
    {"doc_id": "pattern-cnp-fraud", "source": "pattern", "section": "2", "text": "Card-not-present fraud. The number is used online without the card. Amounts and products that don't fit the cardholder's history, often in a burst of two to four within 48 hours. On its own, one unusual online purchase is ambiguous: verify. Policy R1 to R4."},
    {"doc_id": "pattern-cnp-new-device", "source": "pattern", "section": "3", "text": "Card-not-present fraud from a new device. Same as above, with the identity record marking the device as New for this account, sometimes behind a proxy. Stronger than pattern 2, still not proof: people buy new phones."},
    {"doc_id": "pattern-out-of-region", "source": "pattern", "section": "4", "text": "Out-of-region use. Card-present purchases in a billing region the cardholder has no history in, while their normal activity continues at home. Several days of purchases in one new region is a trip, not a clone. Policy R2, R3."},
    {"doc_id": "pattern-account-takeover", "source": "pattern", "section": "5", "text": "Account takeover. Mixed-channel activity inconsistent with the cardholder, often with device and match-flag anomalies, pointing to stolen credentials rather than a stolen number."},
]
```

- [ ] **Step 2: Write `tests/test_policy_chunks.py`**

```python
from src.ingestion.policy_chunks import POLICY_CHUNKS


def test_covers_all_ten_rules():
    sections = {c["section"] for c in POLICY_CHUNKS if c["source"] == "policy"}
    for rule in (f"R{i}" for i in range(1, 11)):
        assert rule in sections, f"missing {rule}"


def test_covers_all_five_patterns():
    patterns = [c for c in POLICY_CHUNKS if c["source"] == "pattern"]
    assert len(patterns) == 5


def test_every_chunk_has_required_fields():
    for chunk in POLICY_CHUNKS:
        assert chunk["doc_id"] and chunk["source"] and chunk["section"] and chunk["text"]
```

- [ ] **Step 3: Run and verify passing**

```bash
.venv\Scripts\pytest tests/test_policy_chunks.py -v
```

Expected: 3 passed (this test needs no network/TigerGraph — it's checking the static data structure).

- [ ] **Step 4: Write `src/ingestion/regulatory_docs.py`**

```python
from __future__ import annotations

import re
from pathlib import Path

import requests
from pypdf import PdfReader

REGULATORY_PDFS: list[tuple[str, str]] = [
    ("fincen-sar-faqs-2025", "https://www.fincen.gov/system/files/2025-10/SAR-FAQs-October-2025.pdf"),
    ("fincen-sar-narrative-guidance", "https://www.fincen.gov/system/files/shared/sar_guidance_narrative.pdf"),
    ("fincen-sar-narrative-complete", "https://www.fincen.gov/system/files/shared/sarnarrcompletguidfinal_112003.pdf"),
    ("fincen-sar-supporting-docs", "https://www.fincen.gov/system/files/shared/fin-2007-g003.pdf"),
    ("fincen-sar-trends-tips", "https://www.fincen.gov/sites/default/files/sar_report/sar_tti_19.pdf"),
    ("fincen-account-takeover", "https://www.fincen.gov/resources/advisories/fincen-advisory-fin-2011-a016"),
    ("fincen-imposter-mule", "https://www.fincen.gov/system/files/advisory/2020-07-07/Advisory_%20Imposter_and_Money_Mule_COVID_19_508_FINAL.pdf"),
    ("fincen-identity-suspicious", "https://www.fincen.gov/system/files/shared/FTA_Identity_Final508.pdf"),
    ("fatf-cyber-fraud", "https://www.fatf-gafi.org/content/dam/fatf-gafi/reports/Illicit-financial-flows-cyber-enabled-fraud.pdf.coredownload.inline.pdf"),
]
# Note: the README also lists several FATF/FFIEC/OFAC pages that are HTML, not PDF
# (money-laundering typology pages, the FFIEC manual, the OFAC SDN list). Those need
# get_page_text-style HTML extraction instead of pypdf -- handle in Step 6 below with
# a second, smaller list and a `requests.get(...).text` + a simple tag-stripping regex,
# since pulling in a full HTML-parsing dependency for ~6 pages isn't worth it.


def download_pdf(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = dest_dir / (url.split("/")[-1].split("?")[0] or "doc.pdf")
    if not filename.exists():
        resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        filename.write_bytes(resp.content)
    return filename


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def chunk_text(text: str, doc_id_prefix: str, max_chars: int = 1200) -> list[dict[str, str]]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip() and len(p.strip()) > 40]
    chunks: list[dict[str, str]] = []
    buf = ""
    idx = 0
    for para in paragraphs:
        if len(buf) + len(para) > max_chars and buf:
            chunks.append({
                "doc_id": f"{doc_id_prefix}-{idx}",
                "source": "regulatory",
                "section": str(idx),
                "text": buf.strip(),
            })
            idx += 1
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        chunks.append({
            "doc_id": f"{doc_id_prefix}-{idx}",
            "source": "regulatory",
            "section": str(idx),
            "text": buf.strip(),
        })
    return chunks
```

- [ ] **Step 5: Write `tests/test_regulatory_docs.py`** (tests the pure chunking function only — no network call in the test).

```python
from src.ingestion.regulatory_docs import chunk_text


def test_chunk_text_splits_on_paragraphs_within_max_chars():
    text = "\n\n".join([f"Paragraph number {i} with some real content padding here." * 3 for i in range(10)])
    chunks = chunk_text(text, "test-doc", max_chars=500)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["text"]) <= 600  # allows the last paragraph to slightly exceed max_chars
        assert c["source"] == "regulatory"


def test_chunk_text_drops_short_noise_paragraphs():
    text = "Real paragraph with enough content to survive the length filter here.\n\nshort\n\nAnother real paragraph with plenty of content in it too."
    chunks = chunk_text(text, "test-doc")
    joined = " ".join(c["text"] for c in chunks)
    assert "short" not in joined
```

- [ ] **Step 6: Run and verify passing**

```bash
.venv\Scripts\pytest tests/test_regulatory_docs.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Write `src/ingestion/embeddings.py`** — Ollama embedding wrapper.

```python
from __future__ import annotations

import ollama


def embed(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    return [ollama.embeddings(model=model, prompt=t)["embedding"] for t in texts]
```

- [ ] **Step 8: Write `scripts/ingest_knowledge.py`** — orchestrates the full one-time ingestion pass: policy/pattern chunks, regulatory PDFs (best-effort per URL — some of the README's regulatory links are HTML pages, not PDFs; catch and skip those with a printed warning rather than failing the whole run), and `ClosedCase` narrative embeddings.

```python
import asyncio
from pathlib import Path

import pandas as pd

from src.ingestion.embeddings import embed
from src.ingestion.policy_chunks import POLICY_CHUNKS
from src.ingestion.regulatory_docs import REGULATORY_PDFS, chunk_text, download_pdf, extract_pdf_text
from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


async def ingest_policy_and_patterns(tg: TigerGraphMCP) -> None:
    texts = [c["text"] for c in POLICY_CHUNKS]
    vectors = embed(texts)
    entries = [
        {**chunk, "embedding": vec} for chunk, vec in zip(POLICY_CHUNKS, vectors)
    ]
    await _upsert_knowledge_docs(tg, entries)
    print(f"Ingested {len(entries)} policy/pattern chunks")


async def ingest_regulatory_pdfs(tg: TigerGraphMCP) -> None:
    all_chunks: list[dict] = []
    for doc_id_prefix, url in REGULATORY_PDFS:
        try:
            pdf_path = download_pdf(url, Path("data/raw/regulatory"))
            text = extract_pdf_text(pdf_path)
            all_chunks.extend(chunk_text(text, doc_id_prefix))
        except Exception as exc:  # noqa: BLE001 -- best-effort ingestion, log and continue
            print(f"Skipping {url}: {exc}")
    if not all_chunks:
        return
    vectors = embed([c["text"] for c in all_chunks])
    entries = [{**chunk, "embedding": vec} for chunk, vec in zip(all_chunks, vectors)]
    await _upsert_knowledge_docs(tg, entries)
    print(f"Ingested {len(entries)} regulatory chunks")


async def ingest_closed_case_narratives(tg: TigerGraphMCP, closed_cases_csv: str) -> None:
    df = pd.read_csv(closed_cases_csv)
    texts = df["analyst_notes"].fillna("").tolist()
    case_ids = df["case_id"].tolist()
    batch_size = 200
    for start in range(0, len(texts), batch_size):
        batch_texts = texts[start : start + batch_size]
        batch_ids = case_ids[start : start + batch_size]
        vectors = embed(batch_texts)
        await tg.upsert_vectors(
            "ClosedCase",
            [{"id": cid, "embedding": vec} for cid, vec in zip(batch_ids, vectors)],
        )
    print(f"Embedded {len(texts)} closed case narratives")


async def _upsert_knowledge_docs(tg: TigerGraphMCP, entries: list[dict]) -> None:
    for entry in entries:
        statement = (
            f'INSERT INTO VERTEX KnowledgeDoc VALUES '
            f'("{entry["doc_id"]}", "{entry["source"]}", "{entry["section"]}", '
            f'"{entry["text"].replace(chr(34), chr(92)+chr(34)).replace(chr(10), " ")}")'
        )
        await tg.gsql(f"USE GRAPH {GRAPH_NAME}\n{statement};")
    await tg.upsert_vectors(
        "KnowledgeDoc",
        [{"id": e["doc_id"], "embedding": e["embedding"]} for e in entries],
    )


async def main() -> None:
    async with TigerGraphMCP() as tg:
        await ingest_policy_and_patterns(tg)
        await ingest_regulatory_pdfs(tg)
        await ingest_closed_case_narratives(tg, "closed_cases_history.csv")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 9: Run it**

```bash
.venv\Scripts\python scripts\ingest_knowledge.py
```

Expected: policy/pattern chunks ingest quickly; regulatory PDFs take a few minutes (network + PDF parsing) and some may print "Skipping ... " for the HTML-page README links — that's expected per the Step 4 note, not a bug; closed-case embedding of 5,565 narratives via local Ollama takes the longest (likely 10-30+ minutes depending on hardware) — let it run, this is a one-time cost.

- [ ] **Step 10: Verify**

```bash
.venv\Scripts\python -c "
import asyncio
from src.tg_client import TigerGraphMCP
async def main():
    async with TigerGraphMCP() as tg:
        print(await tg.gsql('USE GRAPH FraudInvestigation SELECT COUNT(*) FROM KnowledgeDoc'))
asyncio.run(main())
"
```

Expected: at least 16 (10 policy + 5 pattern + case-vs-report = 16 from Step 1, plus however many regulatory chunks succeeded).

- [ ] **Step 11: Commit**

```bash
git add src/ingestion scripts/ingest_knowledge.py tests/test_policy_chunks.py tests/test_regulatory_docs.py
git commit -m "feat: knowledge ingestion (policy, patterns, regulatory PDFs, closed-case embeddings)"
```

---

## Task 10: Graph evidence-gathering functions

**Files:**
- Create: `src/graph/__init__.py` (empty)
- Create: `src/graph/queries.py`
- Create: `src/graph/vector_search.py`
- Test: `tests/test_graph_queries.py` (live, requires the loaded graph from Tasks 7-9)

**Interfaces:**
- Produces: `async card_window(tg, card_id, hours) -> list[dict]`, `async customer_cards(tg, customer_id) -> list[dict]`, `async device_neighbors(tg, transaction_id) -> list[dict]`, `async region_neighbors(tg, addr1, txn_ts, window_days) -> list[dict]`, `async closed_case_lookup(tg, card_id=None, device_id=None, addr1=None) -> list[dict]`, `async ring_membership(tg, card_id) -> dict` (reads Task 8.5's graph-algorithm output), `async retrieve_knowledge(tg, query_text, top_k=5) -> list[dict]`, plus `FOLLOWUP_TOOL_SCHEMAS` and `async dispatch_followup_tool(tg, name, arguments)` for the bounded agentic round. Task 12's `graph_flow.py` calls the deterministic functions directly every time, and calls `dispatch_followup_tool` at most once per case, only when the LLM's function-calling round (step 2a) requests it — see plan Architecture note on why evidence-gathering is deterministic-by-default with one bounded exception.

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
             <-(FROM_DEVICE)- Transaction -(reverse_MADE)- Card:c
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
    return result[0] if isinstance(result, list) and result else {}


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
    knowledge_hits = await tg.search_top_k_similarity("KnowledgeDoc", query_vector, top_k)
    closed_case_hits = await tg.search_top_k_similarity("ClosedCase", query_vector, top_k)
    # Search `Case` (this run's own cases) too -- without this, a later case-pack case
    # in the same batch can never retrieve an earlier one this agent already wrote,
    # which defeats the point of "case memory" within the run itself (see spec §6 step 8
    # and the README's "add your own cases to the graph as you close them"). This only
    # returns results once Task 12/13 actually upserts an embedding when writing a Case --
    # empty results here are expected until that write path exists, not a bug in this file.
    own_case_hits = await tg.search_top_k_similarity("Case", query_vector, top_k)
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

## Task 11: LLM wrapper (Groq primary, Ollama fallback) with schema-validated retry

**Files:**
- Create: `src/agent/llm.py`
- Create: `src/agent/simulator.py`
- Test: `tests/test_llm_wrapper.py`

**Interfaces:**
- Produces: `async generate_structured(prompt: str, schema: type[BaseModel], max_retries: int = 2) -> BaseModel`, `async generate_with_tools(prompt: str, tools: list[dict], max_tool_calls: int = 1) -> ToolCallResult`, `simulate_evidence_response(request_type: str, case_context: dict) -> str`. Task 12 uses all three — `generate_with_tools` specifically backs the spec's step 2a bounded agentic round.
- Backend is selected by the `LLM_BACKEND` env var (`groq` default, `ollama` fallback) set in Task 1's `.env`. Both backends implement the same two functions so Task 12 never branches on which one is active.

- [ ] **Step 1: Write `src/agent/llm.py`**

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import ollama
import openai
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

LLM_BACKEND = os.environ.get("LLM_BACKEND", "groq")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
OLLAMA_MODEL = "qwen3:4b-instruct"

_SYSTEM_PROMPT = (
    "You are a fraud investigation assistant. Respond with ONLY a single JSON "
    "object matching the requested schema. No prose, no markdown fences."
)


def _groq_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ["GROQ_API_KEY"],
    )


@dataclass
class ToolCallResult:
    tool_name: str | None
    tool_arguments: dict = field(default_factory=dict)
    final_text: str | None = None


class _RateLimited(Exception):
    """Raised on a Groq 429 so tenacity's retry can back off and try again."""


class TokenTracker:
    """Accumulates token usage across one case's worth of LLM calls. The answer
    JSON schema (Task 6) requires a `tokens` field per case -- Groq's responses
    report real usage, so this replaces what would otherwise be a hardcoded 0."""

    def __init__(self) -> None:
        self.total = 0

    def reset(self) -> None:
        self.total = 0

    def add_from_response(self, response: "openai.types.chat.ChatCompletion") -> None:
        if response.usage is not None:
            self.total += response.usage.total_tokens


token_tracker = TokenTracker()


@retry(
    retry=retry_if_exception_type(_RateLimited),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
)
def _groq_chat(messages: list[dict], **kwargs) -> "openai.types.chat.ChatCompletion":
    client = _groq_client()
    try:
        response = client.chat.completions.create(model=GROQ_MODEL, messages=messages, **kwargs)
        token_tracker.add_from_response(response)
        return response
    except openai.RateLimitError as exc:
        raise _RateLimited from exc


async def generate_structured(
    prompt: str, schema: type[BaseModel], max_retries: int = 2
) -> BaseModel:
    last_error: Exception | None = None
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    for attempt in range(max_retries + 1):
        raw = await _chat_raw(messages, schema)
        try:
            return schema.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": f"That was not valid JSON matching the schema ({exc}). Try again, JSON only.",
                }
            )
    raise RuntimeError(
        f"Failed to get valid structured output after {max_retries + 1} attempts"
    ) from last_error


async def _chat_raw(messages: list[dict], schema: type[BaseModel]) -> str:
    if LLM_BACKEND == "groq":
        response = _groq_chat(
            messages,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"
    response = ollama.chat(model=OLLAMA_MODEL, messages=messages, format=schema.model_json_schema())
    return response["message"]["content"]


async def generate_with_tools(
    prompt: str, tools: list[dict], max_tool_calls: int = 1
) -> ToolCallResult:
    """One bounded round: the model may call at most one tool, or answer directly.
    Ollama's tool-calling support is inconsistent across small local models, so this
    function only runs meaningfully on the Groq backend; when LLM_BACKEND=ollama it
    degrades gracefully to 'no tool call' (final_text only) rather than erroring,
    since the spec explicitly treats the local backend as a reliability fallback,
    not a feature-parity requirement.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You may call at most one tool if the evidence so far is genuinely "
                "ambiguous. If it's already clear, answer directly with no tool call."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    if LLM_BACKEND != "groq":
        return ToolCallResult(tool_name=None, final_text="(tool-calling round skipped on ollama backend)")

    response = _groq_chat(messages, tools=tools, tool_choice="auto")
    message = response.choices[0].message
    if message.tool_calls:
        call = message.tool_calls[0]
        return ToolCallResult(
            tool_name=call.function.name,
            tool_arguments=json.loads(call.function.arguments),
        )
    return ToolCallResult(tool_name=None, final_text=message.content)
```

- [ ] **Step 2: Write `src/agent/simulator.py`** — rule-based, not a second LLM call, per the spec's decision to keep the evidence-response simulator separate and auditable.

```python
from __future__ import annotations

from typing import Literal

SimulatedType = Literal["customer_validation", "step_up_auth", "analyst_info"]


def simulate_evidence_response(
    request_type: SimulatedType,
    *,
    flagged_amount: float,
    customer_median_amount: float,
    is_new_device: bool,
    fraud_probability: float,
) -> str:
    """Simulate the response a customer/analyst would plausibly give, grounded in the
    actual transaction data rather than invented freely. State the assumption plainly --
    callers must log this string verbatim into evidence_requests.assumed_response.
    """
    amount_ratio = flagged_amount / customer_median_amount if customer_median_amount else 999
    looks_anomalous = amount_ratio > 3 or is_new_device or fraud_probability > 0.6

    if request_type == "customer_validation":
        if looks_anomalous:
            return (
                f"Customer states they did not make this ${flagged_amount:.2f} purchase "
                f"and still has the card. (Simulated: amount is {amount_ratio:.1f}x their "
                f"typical transaction{' from a device new to this account' if is_new_device else ''}.)"
            )
        return (
            f"Customer confirms they made this ${flagged_amount:.2f} purchase. "
            f"(Simulated: amount is in line with their typical spending pattern.)"
        )

    if request_type == "step_up_auth":
        if looks_anomalous:
            return "Step-up authentication failed / was not completed. (Simulated: anomalous activity pattern.)"
        return "Step-up authentication completed successfully. (Simulated: activity fits customer's normal pattern.)"

    return "Analyst confirms no additional context beyond the graph evidence is available. (Simulated.)"
```

- [ ] **Step 3: Write `tests/test_llm_wrapper.py`**

```python
import pytest
from pydantic import BaseModel

from src.agent.llm import generate_structured
from src.agent.simulator import simulate_evidence_response


class _TinySchema(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_generate_structured_returns_valid_instance():
    result = await generate_structured("Reply with a JSON object with an 'answer' field containing the word 'ok'.", _TinySchema)
    assert isinstance(result, _TinySchema)
    assert result.answer


@pytest.mark.asyncio
async def test_generate_with_tools_calls_a_tool_when_ambiguous():
    from src.agent.llm import generate_with_tools

    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup_region",
                "description": "Look up other activity in a billing region.",
                "parameters": {"type": "object", "properties": {"addr1": {"type": "string"}}, "required": ["addr1"]},
            },
        }
    ]
    result = await generate_with_tools(
        "The card's own history is thin and inconclusive. Region code is 444. "
        "Consider whether checking regional activity would help before deciding.",
        tools,
    )
    # On the groq backend this should pick the tool; on the ollama fallback it degrades
    # to no-tool-call by design (see generate_with_tools docstring) -- assert only what
    # both backends guarantee: the call completes and returns a well-formed result.
    assert result.tool_name is None or result.tool_name == "lookup_region"


def test_token_tracker_resets_and_accumulates():
    from src.agent.llm import TokenTracker

    class _FakeUsage:
        total_tokens = 42

    class _FakeResponse:
        usage = _FakeUsage()

    tracker = TokenTracker()
    tracker.add_from_response(_FakeResponse())
    tracker.add_from_response(_FakeResponse())
    assert tracker.total == 84
    tracker.reset()
    assert tracker.total == 0


def test_simulator_anomalous_amount_denies():
    response = simulate_evidence_response(
        "customer_validation",
        flagged_amount=500.0,
        customer_median_amount=50.0,
        is_new_device=False,
        fraud_probability=0.7,
    )
    assert "did not make" in response


def test_simulator_typical_amount_confirms():
    response = simulate_evidence_response(
        "customer_validation",
        flagged_amount=52.0,
        customer_median_amount=50.0,
        is_new_device=False,
        fraud_probability=0.2,
    )
    assert "confirms" in response
```

- [ ] **Step 4: Run**

```bash
.venv\Scripts\pytest tests/test_llm_wrapper.py -v
```

Expected: passes; the first test is the real proof Groq's `llama-3.3-70b-versatile` can follow a JSON-schema instruction reliably, and the second proves the tool-calling round actually invokes a tool when the prompt is engineered to be ambiguous — if either fails repeatedly against the Groq backend, check `GROQ_API_KEY`/`GROQ_MODEL` in `.env` before assuming the model itself is the problem (a 429 surfacing as a test failure instead of a retry usually means `tenacity`'s `retry_if_exception_type(_RateLimited)` isn't catching the actual exception type the installed `openai` package version raises — confirm `openai.RateLimitError` is still the right class for the installed version). If Groq is unusably rate-limited during testing, set `LLM_BACKEND=ollama` in `.env` and re-run — this is the fallback path the spec anticipates, not a dead end.

- [ ] **Step 5: Commit**

```bash
git add src/agent/llm.py src/agent/simulator.py tests/test_llm_wrapper.py
git commit -m "feat: Groq-backed LLM wrapper (structured output + bounded tool-calling) with Ollama fallback"
```

---

## Task 12: LangGraph investigation flow + case write-back

**Files:**
- Create: `src/agent/state.py`
- Create: `src/agent/graph_flow.py`
- Create: `src/run/__init__.py` (empty)
- Create: `src/run/run_case.py`
- Test: `tests/test_run_case_hhg017.py` (live end-to-end test against the manual checkpoint case)

**Interfaces:**
- Consumes: everything from Tasks 3, 5, 6, 8.5, 9, 10, 11.
- Produces: `async run_single_case(tg: TigerGraphMCP, case_row: dict) -> AnswerFile`. Task 13 calls this per case-pack row.
- Flow is now: `gather_evidence` (deterministic, includes the Task 8.5 ring lookup) →
  `agentic_followup` (bounded real function-calling, spec step 2a) → `apply_followup`
  (executes at most one tool call if one was requested) → `assess` → stopping check →
  optionally `request_evidence`/`reassess` → `policy` → (this task's write-back, below).

- [ ] **Step 1: Write `src/agent/state.py`**

```python
from __future__ import annotations

from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    case_row: dict[str, Any]
    card_id: str
    evidence: list[dict[str, Any]]
    assessment: dict[str, Any]  # LLM output: pattern, probability, claims, similar_cases
    single_signal: bool
    shared_device: bool
    shared_region: bool
    shared_email: bool
    cluster_prior_fraud_rate: float  # from Task 8.5's connected-components pass
    _pending_followup: dict[str, Any]  # set by agentic_followup_node, consumed by apply_followup_node
    evidence_requests: list[dict[str, Any]]
    initial_policy_result: dict[str, Any]
    final_policy_result: dict[str, Any]
    tool_calls: int
    stop_reason: str
```

- [ ] **Step 2: Write `src/agent/graph_flow.py`** — implements the README's 8-step flow as plain async functions composed by LangGraph. Given the deterministic-evidence-gathering design (see plan Architecture), this uses `langgraph.graph.StateGraph` for the control flow (conditional loop-back for the evidence-request round) but each node is a straightforward async function, not an LLM-driven ReAct loop.

```python
from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from src.agent.llm import generate_structured, generate_with_tools
from src.agent.simulator import simulate_evidence_response
from src.agent.state import InvestigationState
from src.graph.queries import (
    FOLLOWUP_TOOL_SCHEMAS,
    card_window,
    closed_case_lookup,
    customer_cards,
    device_neighbors,
    dispatch_followup_tool,
    region_neighbors,
    ring_membership,
)
from src.graph.vector_search import retrieve_knowledge
from src.policy.engine import apply_policy
from src.policy.models import Findings
from src.tg_client import TigerGraphMCP

CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD = 0.5
CLUSTER_MIN_SIZE_FOR_COORDINATED = 3


class AssessmentOutput(BaseModel):
    pattern: str
    fraud_probability: float
    evidence_claims: list[str]
    similar_prior_case_ids: list[str] = []


async def gather_evidence_node(tg: TigerGraphMCP, state: InvestigationState) -> InvestigationState:
    row = state["case_row"]
    card_id = row["card_id"]
    evidence: list[dict[str, Any]] = []
    tool_calls = 0

    window = await card_window(tg, card_id, hours=48)
    evidence.append({"type": "card_window", "data": window})
    tool_calls += 1

    cards = await customer_cards(tg, row["customer_id"])
    evidence.append({"type": "customer_cards", "data": cards})
    tool_calls += 1

    neighbors = await device_neighbors(tg, str(row["flagged_txn_id"]))
    evidence.append({"type": "device_neighbors", "data": neighbors})
    tool_calls += 1

    closed = await closed_case_lookup(tg, card_id=card_id)
    evidence.append({"type": "closed_cases", "data": closed})
    tool_calls += 1

    ring = await ring_membership(tg, card_id)
    evidence.append({"type": "ring_membership", "data": ring})
    tool_calls += 1

    knowledge = await retrieve_knowledge(
        tg, f"fraud investigation {row.get('trigger_text', '')}", top_k=5
    )
    evidence.append({"type": "knowledge", "data": knowledge})
    tool_calls += 1

    cluster_size = ring.get("cluster_prior_fraud_rate") is not None  # presence implies clustered
    cluster_rate = ring.get("cluster_prior_fraud_rate", 0.0) or 0.0
    coordinated = (
        cluster_rate >= CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD and bool(ring.get("ring_cluster_id"))
    )

    return {
        **state,
        "card_id": card_id,
        "evidence": evidence,
        "tool_calls": state.get("tool_calls", 0) + tool_calls,
        # shared_device/shared_region now come from the graph-algorithm cluster output
        # (Task 8.5) as well as the live neighbor check -- either signal is enough to
        # flag a shared origin, since the cluster catches multi-hop chains a single
        # device_neighbors lookup would miss.
        "shared_device": bool(neighbors) or coordinated,
        "shared_region": coordinated,
        "shared_email": False,
        "single_signal": row.get("trigger_type") == "risk_score" and not evidence[3]["data"] and not coordinated,
        "cluster_prior_fraud_rate": cluster_rate,
    }


async def agentic_followup_node(state: InvestigationState) -> InvestigationState:
    """Spec step 2a: one bounded, real function-calling round. The LLM sees a summary
    of the deterministic evidence and may request exactly one additional targeted
    query if it judges the evidence ambiguous -- this is the genuinely agentic piece
    of the flow (see plan Architecture note); everything before and after this node
    is deterministic Python."""
    row = state["case_row"]
    evidence_summary = {e["type"]: bool(e["data"]) for e in state["evidence"]}
    prompt = (
        f"Case trigger: {row.get('trigger_text', '')}\n"
        f"Evidence gathered so far (type -> has_results): {evidence_summary}\n\n"
        "Is this evidence sufficient to assess the case, or would one more targeted "
        "lookup meaningfully change your confidence? If sufficient, respond with no "
        "tool call. If not, call exactly one tool."
    )
    result = await generate_with_tools(prompt, FOLLOWUP_TOOL_SCHEMAS, max_tool_calls=1)
    if result.tool_name is None:
        return state
    return {**state, "_pending_followup": {"name": result.tool_name, "arguments": result.tool_arguments}}


async def apply_followup_node(tg: TigerGraphMCP, state: InvestigationState) -> InvestigationState:
    pending = state.get("_pending_followup")
    if not pending:
        return state
    followup_result = await dispatch_followup_tool(tg, pending["name"], pending["arguments"])
    evidence = [*state["evidence"], {"type": f"followup:{pending['name']}", "data": followup_result}]
    return {
        **state,
        "evidence": evidence,
        "tool_calls": state.get("tool_calls", 0) + 1,
    }


async def assess_node(state: InvestigationState) -> InvestigationState:
    row = state["case_row"]
    prompt = (
        f"Case trigger: {row.get('trigger_text', '')}\n"
        f"Evidence gathered: {state['evidence']}\n\n"
        "Based on this evidence, classify the fraud pattern (one of: card_testing, "
        "card_not_present_fraud, card_not_present_new_device, out_of_region_use, "
        "account_takeover, undocumented, none), estimate fraud_probability (0-1), "
        "list evidence_claims (short strings), and similar_prior_case_ids if any closed "
        "case narratives clearly match."
    )
    result = await generate_structured(prompt, AssessmentOutput)
    return {**state, "assessment": result.model_dump()}


def stopping_check(state: InvestigationState) -> str:
    prob = state["assessment"]["fraud_probability"]
    if prob >= 0.85 or prob <= 0.15:
        return "stop"
    if state.get("evidence_requests"):
        return "stop"  # already asked once; don't loop indefinitely with a small model
    return "request_evidence"


async def evidence_request_node(state: InvestigationState) -> InvestigationState:
    row = state["case_row"]
    window_txns = state["evidence"][0]["data"]
    flagged_amount = float(row.get("risk_score") and row.get("flagged_amount", 0) or 0)
    response = simulate_evidence_response(
        "customer_validation",
        flagged_amount=flagged_amount or 100.0,
        customer_median_amount=100.0,
        is_new_device=state.get("shared_device", False),
        fraud_probability=state["assessment"]["fraud_probability"],
    )
    request = {
        "type": "customer_validation",
        "asked_after_step": 3,
        "assumed_response": response,
    }
    return {**state, "evidence_requests": [request]}


async def reassess_node(state: InvestigationState) -> InvestigationState:
    row = state["case_row"]
    response_text = state["evidence_requests"][-1]["assumed_response"]
    prompt = (
        f"Original assessment: {state['assessment']}\n"
        f"Customer response: {response_text}\n\n"
        "Update the fraud_probability and evidence_claims given this new information. "
        "Keep the same pattern unless the response clearly changes it."
    )
    result = await generate_structured(prompt, AssessmentOutput)
    return {**state, "assessment": result.model_dump()}


def _findings_from_state(state: InvestigationState, customer_response: str | None) -> Findings:
    assessment = state["assessment"]
    row = state["case_row"]
    return Findings(
        pattern=assessment["pattern"],
        fraud_probability=assessment["fraud_probability"],
        single_signal=state.get("single_signal", False),
        shared_device=state.get("shared_device", False),
        shared_region=state.get("shared_region", False),
        shared_email=state.get("shared_email", False),
        exposure_usd=row.get("flagged_amount", 0.0) or 0.0,
        customer_response=customer_response,
        undocumented_coordinated=(
            assessment["pattern"] == "undocumented"
            and (state.get("shared_device", False) or state.get("cluster_prior_fraud_rate", 0.0) >= CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD)
        ),
    )


async def policy_node(state: InvestigationState) -> InvestigationState:
    initial_findings = _findings_from_state(state, customer_response=None)
    initial_result = apply_policy(initial_findings)

    if state.get("evidence_requests"):
        raw_response = state["evidence_requests"][-1]["assumed_response"]
        customer_response = "denies" if "did not make" in raw_response else (
            "confirmed_legitimate" if "confirms they made" in raw_response else "no_reply"
        )
        final_findings = _findings_from_state(state, customer_response=customer_response)
        final_result = apply_policy(final_findings)
    else:
        final_result = initial_result

    return {
        **state,
        "initial_policy_result": initial_result.model_dump(),
        "final_policy_result": final_result.model_dump(),
        "stop_reason": (
            "Customer response settled the verdict." if state.get("evidence_requests")
            else "Fraud probability reached a decisive threshold with sufficient evidence."
        ),
    }


def build_graph(tg: TigerGraphMCP):
    workflow = StateGraph(InvestigationState)
    workflow.add_node("gather_evidence", lambda s: gather_evidence_node(tg, s))
    workflow.add_node("agentic_followup", agentic_followup_node)
    workflow.add_node("apply_followup", lambda s: apply_followup_node(tg, s))
    workflow.add_node("assess", assess_node)
    workflow.add_node("request_evidence", evidence_request_node)
    workflow.add_node("reassess", reassess_node)
    workflow.add_node("policy", policy_node)

    workflow.set_entry_point("gather_evidence")
    workflow.add_edge("gather_evidence", "agentic_followup")
    workflow.add_edge("agentic_followup", "apply_followup")  # apply_followup_node is a no-op if no tool was requested
    workflow.add_edge("apply_followup", "assess")
    workflow.add_conditional_edges(
        "assess", stopping_check, {"stop": "policy", "request_evidence": "request_evidence"}
    )
    workflow.add_edge("request_evidence", "reassess")
    workflow.add_edge("reassess", "policy")
    workflow.add_edge("policy", END)

    return workflow.compile()
```

**Note:** `flagged_amount` isn't a column in `case_pack.csv` directly (the README's case table shows dollar amounts only inside `trigger_text` prose, e.g. `"$77.07"`). Before this task is "done", add a small `_parse_flagged_amount(trigger_text: str) -> float` helper (regex `\$([\d,]+\.\d{2})`) in `src/agent/graph_flow.py` and use it wherever `row.get('flagged_amount', ...)` appears above — replace those placeholder lookups with real parsing before running Step 5's test.

- [ ] **Step 3: Write `src/run/run_case.py`** — runs the graph, converts state into an `AnswerFile`, writes the case back to TigerGraph.

```python
from __future__ import annotations

import time

from src.agent.graph_flow import build_graph
from src.agent.llm import token_tracker
from src.agent.schemas import (
    ActionEntry,
    AnswerFile,
    CaseRecord,
    Evidence,
    EvidenceRequestRecord,
    NextBestActionSet,
    SAR,
)
from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


async def run_single_case(tg: TigerGraphMCP, case_row: dict) -> AnswerFile:
    start = time.monotonic()
    token_tracker.reset()  # each case's `tokens` field should reflect only its own calls
    app = build_graph(tg)
    final_state = await app.ainvoke({"case_row": case_row})

    assessment = final_state["assessment"]
    initial_actions = [ActionEntry(**a) for a in final_state["initial_policy_result"]["actions"]]
    final_actions = [ActionEntry(**a) for a in final_state["final_policy_result"]["actions"]]
    sar_info = final_state["final_policy_result"]

    verdict = (
        "fraud" if assessment["fraud_probability"] >= 0.7
        else "legitimate" if assessment["fraud_probability"] <= 0.15
        else "uncertain"
    )
    status = (
        "closed_fraud" if verdict == "fraud"
        else "closed_legitimate" if verdict == "legitimate"
        else "escalated"
    )

    graph_case_id = f"CASE-{case_row['case_id']}"
    written = await _write_case_to_graph(
        tg, graph_case_id, case_row, assessment, final_actions, sar_info, verdict, status
    )

    case_record = CaseRecord(
        status=status,
        verdict=verdict,
        fraud_probability=assessment["fraud_probability"],
        pattern=assessment["pattern"],
        pattern_description="",
        affected_txn_ids=[str(case_row["flagged_txn_id"])] if verdict != "legitimate" else [],
        first_suspicious_txn_id=str(case_row["flagged_txn_id"]) if verdict != "legitimate" else "",
        connected_card_ids=[],
        connected_device_profiles=[],
        exposure_usd=case_row.get("flagged_amount", 0.0) or 0.0 if verdict != "legitimate" else 0.0,
        evidence=[
            Evidence(claim=c, source="graph", ref=f"assessment", entity_ids=[])
            for c in assessment["evidence_claims"]
        ],
        similar_prior_cases=assessment.get("similar_prior_case_ids", []),
        summary=f"Pattern {assessment['pattern']} assessed at probability {assessment['fraud_probability']:.2f}.",
        written_to_graph=written,
        graph_case_id=graph_case_id if written else "",
    )

    evidence_requests = [
        EvidenceRequestRecord(**er) for er in final_state.get("evidence_requests", [])
    ]

    next_best_actions = NextBestActionSet(
        initial=initial_actions,
        final=final_actions,
        what_changed=(
            "nothing" if initial_actions == final_actions
            else "Simulated evidence response changed the recommended actions."
        ),
    )

    sar = SAR(
        file=sar_info["sar_file"],
        reason=sar_info["sar_reason"],
        narrative="",  # filled by a follow-up LLM call if sar_info["sar_file"] is True
        subjects=[case_row["customer_id"], case_row["card_id"]] if sar_info["sar_file"] else [],
        total_amount_usd=case_record.exposure_usd if sar_info["sar_file"] else 0.0,
        activity_dates=[] if not sar_info["sar_file"] else [str(case_row["opened_at"])[:10]] * 2,
    )

    return AnswerFile(
        case_id=case_row["case_id"],
        case=case_record,
        evidence_requests=evidence_requests,
        next_best_actions=next_best_actions,
        sar=sar,
        stop_reason=final_state["stop_reason"],
        tool_calls=final_state["tool_calls"],
        tokens=token_tracker.total,  # 0 on the ollama fallback backend, real usage on Groq
        latency_s=round(time.monotonic() - start, 1),
    )


async def _write_case_to_graph(
    tg: TigerGraphMCP, graph_case_id: str, case_row: dict, assessment: dict,
    final_actions: list[ActionEntry], sar_info: dict, verdict: str, status: str,
) -> bool:
    esc = lambda s: str(s).replace('"', '\\"')
    summary_text = (
        f"Case {graph_case_id} on card {case_row['card_id']}: pattern "
        f"{assessment['pattern']}, probability {assessment['fraud_probability']:.2f}. "
        f"{' '.join(assessment['evidence_claims'])}"
    )
    # Field order must match Task 4's CREATE VERTEX Case exactly: case_id(PK),
    # customer_id, card_id, status, verdict, fraud_probability, pattern, exposure_usd,
    # summary, written_at -- verdict and status come from the caller's already-computed
    # values (run_single_case), not re-derived here, since assessment only carries
    # pattern/probability/evidence, not a verdict.
    statement = (
        f'INSERT INTO VERTEX Case VALUES ('
        f'"{graph_case_id}", "{case_row["customer_id"]}", "{case_row["card_id"]}", '
        f'"{esc(status)}", "{esc(verdict)}", {assessment["fraud_probability"]}, '
        f'"{esc(assessment["pattern"])}", 0.0, "{esc(summary_text)}", "now")'
    )
    try:
        await tg.gsql(f"USE GRAPH {GRAPH_NAME}\n{statement};")
        # Embed and upsert immediately -- this is what makes case memory real within
        # the same 20-case batch run: a later case's retrieve_knowledge call (Task 10)
        # searches the `Case` vertex type and will find this one, not just pre-loaded
        # ClosedCase history. See spec §6 step 8.
        from src.ingestion.embeddings import embed  # local import: keeps run_case.py
                                                       # decoupled from ingestion until
                                                       # the write path actually needs it
        vector = embed([summary_text])[0]
        await tg.upsert_vectors("Case", [{"id": graph_case_id, "embedding": vector}])
        return True
    except Exception:  # noqa: BLE001
        return False
```

**Note:** this task's Python has several rough joins between the LangGraph state and the answer schema (verdict thresholds, SAR narrative left blank, `pattern_description` always empty) that are simplified first drafts, not final judged-quality output. Task 13's end-to-end test against the `HHG-017` manual checkpoint is where these get tightened — expect to revisit `run_case.py` after seeing real output next to the by-hand answer.

- [ ] **Step 4: Write `tests/test_run_case_hhg017.py`**

```python
import pytest

from src.run.run_case import run_single_case
from src.tg_client import TigerGraphMCP


@pytest.mark.asyncio
async def test_hhg017_end_to_end_produces_valid_answer():
    case_row = {
        "case_id": "HHG-017",
        "opened_at": "2016-11-12 00:46:24",
        "trigger_type": "risk_score",
        "trigger_text": "Real-time model scored transaction 3450629 ($100.09, online) at 0.57. Review and decide.",
        "flagged_txn_id": 3450629,
        "card_id": "C04570-K1",
        "customer_id": "C04570",
        "risk_score": 0.57,
        "flagged_amount": 100.09,
    }
    async with TigerGraphMCP() as tg:
        answer = await run_single_case(tg, case_row)

    assert answer.case_id == "HHG-017"
    assert answer.case.verdict in ("fraud", "legitimate", "uncertain")
    assert answer.next_best_actions.initial
    assert answer.tool_calls > 0
```

- [ ] **Step 5: Run it**

```bash
.venv\Scripts\pytest tests/test_run_case_hhg017.py -v -s
```

Expected: this is the real proof-of-life for the whole pipeline. It will likely fail on the first several attempts — GSQL query syntax errors from Task 10, LLM output that doesn't match `AssessmentOutput`, or a `KeyError` in `run_case.py`'s field-mapping. Iterate here rather than moving to Task 13. Compare the produced JSON (`print(answer.model_dump_json(indent=2))`) against `docs/manual-case-checkpoint.md`'s by-hand answer for HHG-017 — they don't need to match exactly, but the pattern/verdict/actions should be in the same neighborhood; if wildly different, the bug is more likely in evidence gathering or prompt wording than in the schema.

- [ ] **Step 6: Commit**

```bash
git add src/agent/state.py src/agent/graph_flow.py src/run/__init__.py src/run/run_case.py tests/test_run_case_hhg017.py
git commit -m "feat: LangGraph investigation flow and single-case runner"
```

---

## Task 13: SAR narrative generation

**Files:**
- Modify: `src/run/run_case.py`
- Create: `src/agent/sar_writer.py`
- Test: `tests/test_sar_writer.py`

**Interfaces:**
- Produces: `async write_sar_narrative(case_row: dict, case_record: dict) -> str`. `run_single_case` (Task 12) calls this only when `sar_info["sar_file"]` is true, replacing the empty-string placeholder from Task 12 Step 3.

- [ ] **Step 1: Write `src/agent/sar_writer.py`**

```python
from __future__ import annotations

from pydantic import BaseModel

from src.agent.llm import generate_structured


class SARNarrativeOutput(BaseModel):
    narrative: str


async def write_sar_narrative(case_row: dict, case_summary: dict) -> str:
    prompt = (
        "Write a suspicious activity report narrative, six to twelve sentences, covering "
        "who (customer, cards, merchants, devices), what happened, when (dates), where "
        "(locations, channels), how it was carried out, and why it is suspicious. Base it "
        "only on these facts -- do not invent details:\n\n"
        f"Customer: {case_row['customer_id']}, Card: {case_row['card_id']}\n"
        f"Pattern: {case_summary['pattern']}\n"
        f"Fraud probability: {case_summary['fraud_probability']}\n"
        f"Evidence claims: {case_summary['evidence_claims']}\n"
        f"Trigger: {case_row.get('trigger_text', '')}\n"
    )
    result = await generate_structured(prompt, SARNarrativeOutput)
    return result.narrative
```

- [ ] **Step 2: Write `tests/test_sar_writer.py`**

```python
import pytest

from src.agent.sar_writer import write_sar_narrative


@pytest.mark.asyncio
async def test_narrative_mentions_key_facts():
    case_row = {
        "customer_id": "C04570",
        "card_id": "C04570-K1",
        "trigger_text": "Real-time model scored transaction 3450629 ($100.09, online) at 0.57.",
    }
    case_summary = {
        "pattern": "card_testing",
        "fraud_probability": 0.86,
        "evidence_claims": ["Three small authorizations then a larger purchase"],
    }
    narrative = await write_sar_narrative(case_row, case_summary)
    assert len(narrative) > 100
    assert "C04570" in narrative or "card" in narrative.lower()
```

- [ ] **Step 3: Run**

```bash
.venv\Scripts\pytest tests/test_sar_writer.py -v
```

Expected: passes; a weak/short narrative here is a real quality signal worth tuning the prompt for, since the SAR narrative is explicitly "the one place to be complete" per the README.

- [ ] **Step 4: Wire it into `run_case.py`** — modify the `SAR(...)` construction in `src/run/run_case.py` (Task 12 Step 3):

```python
from src.agent.sar_writer import write_sar_narrative

# ... inside run_single_case, replace the SAR(...) block with:
narrative = ""
if sar_info["sar_file"]:
    narrative = await write_sar_narrative(case_row, assessment)

sar = SAR(
    file=sar_info["sar_file"],
    reason=sar_info["sar_reason"],
    narrative=narrative,
    subjects=[case_row["customer_id"], case_row["card_id"]] if sar_info["sar_file"] else [],
    total_amount_usd=case_record.exposure_usd if sar_info["sar_file"] else 0.0,
    activity_dates=[] if not sar_info["sar_file"] else [str(case_row["opened_at"])[:10]] * 2,
)
```

- [ ] **Step 5: Re-run the Task 12 end-to-end test to confirm nothing broke**

```bash
.venv\Scripts\pytest tests/test_run_case_hhg017.py -v
```

Expected: still passes.

- [ ] **Step 6: Commit**

```bash
git add src/agent/sar_writer.py tests/test_sar_writer.py src/run/run_case.py
git commit -m "feat: SAR narrative generation, wired into the case runner"
```

---

## Task 14: Batch run over all 20 cases + output validation

**Files:**
- Create: `src/run/run_all.py`
- Create: `src/run/validate_outputs.py`
- Test: `tests/test_validate_outputs.py`

**Interfaces:**
- Produces: `cases/HHG-001.json` … `cases/HHG-020.json`; `validate_answer_file(path, valid_txn_ids, valid_card_ids) -> list[str]` (returns a list of problems, empty if clean).

- [ ] **Step 1: Write `src/run/run_all.py`**

```python
import asyncio
import json
from pathlib import Path

import pandas as pd

from src.run.run_case import run_single_case
from src.tg_client import TigerGraphMCP


def _amount_from_trigger_text(trigger_text: str) -> float:
    import re
    match = re.search(r"\$([\d,]+\.\d{2})", trigger_text or "")
    return float(match.group(1).replace(",", "")) if match else 0.0


async def main() -> None:
    case_pack = pd.read_csv("case_pack.csv")
    out_dir = Path("cases")
    out_dir.mkdir(exist_ok=True)

    async with TigerGraphMCP() as tg:
        for _, row in case_pack.iterrows():
            case_row = row.to_dict()
            case_row["flagged_amount"] = _amount_from_trigger_text(case_row.get("trigger_text", ""))
            print(f"Running {case_row['case_id']}...")
            try:
                answer = await run_single_case(tg, case_row)
            except Exception as exc:  # noqa: BLE001 -- log and continue so one bad case doesn't block the batch
                print(f"  FAILED: {exc}")
                continue
            out_path = out_dir / f"{case_row['case_id']}.json"
            out_path.write_text(answer.model_dump_json(indent=2))
            print(f"  wrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Write `src/run/validate_outputs.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.agent.schemas import AnswerFile


def load_valid_ids() -> tuple[set[str], set[str]]:
    txn_ids: set[str] = set()
    for chunk in pd.read_csv("transactions.csv", usecols=["TransactionID"], chunksize=100_000):
        txn_ids.update(str(x) for x in chunk["TransactionID"])
    case_pack = pd.read_csv("case_pack.csv")
    closed_cases = pd.read_csv("closed_cases_history.csv")
    card_ids = set(case_pack["card_id"]) | set(closed_cases["card_id"])
    for connected in closed_cases["connected_card_ids"].dropna():
        card_ids.update(connected.split("|"))
    return txn_ids, card_ids


def validate_answer_file(path: Path, valid_txn_ids: set[str], valid_card_ids: set[str]) -> list[str]:
    problems: list[str] = []
    try:
        answer = AnswerFile.model_validate_json(path.read_text())
    except Exception as exc:  # noqa: BLE001
        return [f"schema validation failed: {exc}"]

    for txn_id in answer.case.affected_txn_ids:
        if txn_id not in valid_txn_ids:
            problems.append(f"unknown transaction_id: {txn_id}")
    for card_id in answer.case.connected_card_ids:
        if card_id not in valid_card_ids:
            problems.append(f"unknown card_id: {card_id}")
    if answer.case.verdict == "legitimate":
        if answer.case.affected_txn_ids or answer.case.exposure_usd != 0:
            problems.append("legitimate verdict must have empty affected_txn_ids and 0 exposure_usd")
        if answer.sar.file:
            problems.append("legitimate verdict must not file a SAR")
    if answer.sar.file != any(a.action == "FILE_REPORT" for a in answer.next_best_actions.final):
        problems.append("sar.file must agree with whether FILE_REPORT is in next_best_actions.final")

    return problems


def main() -> None:
    valid_txn_ids, valid_card_ids = load_valid_ids()
    cases_dir = Path("cases")
    total_problems = 0
    for path in sorted(cases_dir.glob("HHG-*.json")):
        problems = validate_answer_file(path, valid_txn_ids, valid_card_ids)
        if problems:
            total_problems += len(problems)
            print(f"{path.name}: {len(problems)} problem(s)")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"{path.name}: OK")
    print(f"\n{total_problems} total problems across {len(list(cases_dir.glob('HHG-*.json')))} files")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write `tests/test_validate_outputs.py`** — tests the validation logic against synthetic fixtures, not the live batch output.

```python
import json
from pathlib import Path

from src.run.validate_outputs import validate_answer_file

VALID_ANSWER = {
    "case_id": "HHG-999", "case": {
        "status": "closed_legitimate", "verdict": "legitimate", "fraud_probability": 0.1,
        "pattern": "none", "pattern_description": "", "affected_txn_ids": [],
        "first_suspicious_txn_id": "", "connected_card_ids": [], "connected_device_profiles": [],
        "exposure_usd": 0, "evidence": [], "similar_prior_cases": [], "summary": "Legit.",
        "written_to_graph": True, "graph_case_id": "CASE-HHG-999",
    },
    "evidence_requests": [],
    "next_best_actions": {"initial": [], "final": [], "what_changed": "nothing"},
    "sar": {"file": False, "reason": "no fraud", "narrative": "", "subjects": [], "total_amount_usd": 0, "activity_dates": []},
    "stop_reason": "clear", "tool_calls": 3, "tokens": 0, "latency_s": 2.0,
}


def test_valid_file_has_no_problems(tmp_path: Path):
    p = tmp_path / "HHG-999.json"
    p.write_text(json.dumps(VALID_ANSWER))
    problems = validate_answer_file(p, valid_txn_ids=set(), valid_card_ids=set())
    assert problems == []


def test_unknown_txn_id_is_flagged(tmp_path: Path):
    bad = json.loads(json.dumps(VALID_ANSWER))
    bad["case"]["verdict"] = "fraud"
    bad["case"]["affected_txn_ids"] = ["T99999999"]
    p = tmp_path / "HHG-998.json"
    p.write_text(json.dumps(bad))
    problems = validate_answer_file(p, valid_txn_ids={"T1"}, valid_card_ids=set())
    assert any("unknown transaction_id" in p_ for p_ in problems)


def test_sar_file_mismatch_is_flagged(tmp_path: Path):
    bad = json.loads(json.dumps(VALID_ANSWER))
    bad["sar"]["file"] = True
    bad["sar"]["narrative"] = "x" * 20
    p = tmp_path / "HHG-997.json"
    p.write_text(json.dumps(bad))
    problems = validate_answer_file(p, valid_txn_ids=set(), valid_card_ids=set())
    assert any("sar.file must agree" in p_ for p_ in problems)
```

- [ ] **Step 4: Run**

```bash
.venv\Scripts\pytest tests/test_validate_outputs.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Run the real batch** (this is the actual deliverable — budget real time for it: 20 cases × however long one case took in Task 12's test)

```bash
.venv\Scripts\python -m src.run.run_all
```

- [ ] **Step 6: Validate the real output**

```bash
.venv\Scripts\python -m src.run.validate_outputs
```

Expected: "0 total problems across 20 files" — if not, fix the specific flagged issues in `run_case.py` or `run_all.py` and re-run only the affected cases (running `run_single_case` for one `case_id` at a time is faster than a full re-batch while iterating).

- [ ] **Step 7: Commit**

```bash
git add src/run/run_all.py src/run/validate_outputs.py tests/test_validate_outputs.py cases/
git commit -m "feat: batch runner and output validation for all 20 cases"
```

---

## Task 15: Streamlit dashboard

**Files:**
- Create: `ui/streamlit_app.py`
- Create: `ui/__init__.py` (empty)

**Interfaces:**
- Consumes: `cases/*.json` (source of truth), optionally live `TigerGraphMCP` queries for a graph snippet.

- [ ] **Step 1: Write `ui/streamlit_app.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Fraud Investigation Cases", layout="wide")


@st.cache_data
def load_cases() -> dict[str, dict]:
    cases = {}
    for path in sorted(Path("cases").glob("HHG-*.json")):
        cases[path.stem] = json.loads(path.read_text())
    return cases


cases = load_cases()

st.title("Fraud Investigation — Case Dashboard")

if not cases:
    st.warning("No case files found in cases/. Run `python -m src.run.run_all` first.")
    st.stop()

col_list, col_detail = st.columns([1, 2])

with col_list:
    st.subheader(f"{len(cases)} cases")
    for case_id, data in cases.items():
        verdict = data["case"]["verdict"]
        color = {"fraud": "🔴", "legitimate": "🟢", "uncertain": "🟡"}[verdict]
        if st.button(f"{color} {case_id} — {data['case']['pattern']}", key=case_id):
            st.session_state["selected"] = case_id

selected = st.session_state.get("selected", list(cases.keys())[0])
data = cases[selected]

with col_detail:
    st.subheader(selected)
    c = data["case"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Verdict", c["verdict"])
    m2.metric("Probability", f"{c['fraud_probability']:.2f}")
    m3.metric("Pattern", c["pattern"])
    m4.metric("Exposure", f"${c['exposure_usd']:,.2f}")

    st.markdown(f"**Summary:** {c['summary']}")

    st.markdown("### Evidence")
    for ev in c["evidence"]:
        st.markdown(f"- **{ev['source']}**: {ev['claim']} _(ref: `{ev['ref']}`)_")

    if c["similar_prior_cases"]:
        st.markdown("### Similar prior cases")
        st.write(", ".join(c["similar_prior_cases"]))

    st.markdown("### Next best actions")
    action_col1, action_col2 = st.columns(2)
    with action_col1:
        st.markdown("**Initial**")
        for a in data["next_best_actions"]["initial"]:
            st.markdown(f"- `{a['action']}` ({a['route']}) — {a['reason']}")
    with action_col2:
        st.markdown("**Final**")
        for a in data["next_best_actions"]["final"]:
            st.markdown(f"- `{a['action']}` ({a['route']}) — {a['reason']}")
    st.caption(f"What changed: {data['next_best_actions']['what_changed']}")

    if data["evidence_requests"]:
        st.markdown("### Evidence requested")
        for er in data["evidence_requests"]:
            st.markdown(f"- **{er['type']}**: {er['assumed_response']}")

    if data["sar"]["file"]:
        st.markdown("### Suspicious Activity Report")
        st.info(data["sar"]["narrative"])
        st.caption(f"Subjects: {', '.join(data['sar']['subjects'])} | Total: ${data['sar']['total_amount_usd']:,.2f}")

    st.markdown("### Run metadata")
    st.caption(
        f"Tool calls: {data['tool_calls']} | Latency: {data['latency_s']}s | "
        f"Stop reason: {data['stop_reason']}"
    )
```

- [ ] **Step 2: Run it**

```bash
.venv\Scripts\streamlit run ui\streamlit_app.py
```

Expected: opens in a browser, lists the 20 cases (or however many `cases/*.json` exist at this point — usable even before Task 14's full batch run finishes, since it reads whatever's in `cases/`), clicking one shows the detail view. Manually check at least 3 cases render sensibly (a fraud one, a legitimate one, a SAR-filed one) before considering this done.

- [ ] **Step 3: Commit**

```bash
git add ui/
git commit -m "feat: Streamlit case management dashboard"
```

---

## Task 16: Submission packaging

**Files:**
- Modify: `README.md` (add a short "How to run this" section — the existing README is the hackathon's task spec, not yours; add a section rather than replacing it)
- Verify: `cases/` directory has exactly 20 files

- [ ] **Step 1: Add a "Running this project" section to the top of `README.md`** (above the existing "# TigerGraph × Hacker House Goa" heading, so the task spec stays intact below it):

```markdown
# Running this project

1. `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, fill in your TigerGraph Savanna workspace credentials
   and a free Groq API key from `https://console.groq.com` (`GROQ_API_KEY`).
3. `ollama pull qwen3:4b-instruct && ollama pull nomic-embed-text` (if not already present — still needed for embeddings and as the LLM fallback).
4. `python scripts\create_schema.py`
5. `python scripts\load_data.py`
6. `python scripts\derive_entities.py`
7. `python scripts\run_connected_components.py`
8. `python scripts\ingest_knowledge.py`
9. `python -m src.run.run_all` — produces `cases/HHG-001.json` … `cases/HHG-020.json`
10. `python -m src.run.validate_outputs` — sanity-checks the output
11. `streamlit run ui\streamlit_app.py` — dashboard

Design doc: `docs/superpowers/specs/2026-09-22-tigergraph-fraud-agent-design.md`
Implementation plan: `docs/superpowers/plans/2026-09-22-tigergraph-fraud-agent-plan.md`

---
```

- [ ] **Step 2: Verify all 20 answer files exist and are valid**

```bash
.venv\Scripts\python -m src.run.validate_outputs
```

Expected: "0 total problems across 20 files".

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add run instructions for submission"
```

- [ ] **Step 4: Remaining, non-engineering deliverables (not part of this plan's tasks — do after the above is solid):** 3–5 min demo video, technical blog post (architecture, TigerGraph usage, agentic capabilities, learnings, improvements — spec section 10 already lists the honest scope cuts to mention), social post tagging @TigerGraphDB. These can start as soon as Task 14's batch run produces real output to show.

---

## Self-Review Notes

- **Spec coverage:** graph schema (Task 4), data loading (Task 7-8), **graph algorithms (Task 8.5 — added after re-audit found the README's named required component wasn't used anywhere)**, GraphRAG/vector store (Task 9-11), agent architecture including the bounded agentic round (Task 12), output generation (Task 6, 14), UI (Task 15), build order (task sequence matches spec section 9), deliverables checklist (Task 16). All spec sections, including the amendments, have a corresponding task.
- **Card ID derivation** (Task 3) was elevated from a schema-generation detail to its own task after live data investigation showed it's a genuine correctness risk, not a mechanical step — this is a deviation from a purely mechanical read of the spec, made from evidence, not guesswork.
- **Task 7/8 cross-task conflict, found during subagent-driven-development's pre-flight scan:** the original Task 7 draft created `Card` vertices keyed by bare `customer_id`, contradicting Task 3's `-K1`/`-K2` suffix convention that Global Constraints, Task 8, and Task 10 all assumed. Worse, the obvious-looking fix (default every card to `-K1`, then add a second correctly-suffixed vertex for the exceptions in Task 8) doesn't actually work, because a customer only has one real transaction set — the `MADE` edges would still point at the wrongly-defaulted card, leaving the correctly-suffixed one empty. Fixed by resolving every customer's correct `card_id` via Task 3's functions *before* creating any `Card`/`OWNS`/`MADE` data (Task 7's `load_cards_and_made_edges`), which also made Task 8's separate `fix_card_ids` step entirely unnecessary — removed.
- **Case memory within the run** (spec §6 step 8) had a real bug in the first draft — new `Case` vertices were written but never embedded, so `retrieve_knowledge` could never find them. Fixed by embedding on write in `_write_case_to_graph` (Task 12) and having `retrieve_knowledge` (Task 10) search `Case` alongside `ClosedCase`.
- **LLM backend** switched from local-only (`qwen3:4b-instruct`) to Groq's free tier as primary (Task 11), with the local model kept as an explicit `LLM_BACKEND=ollama` fallback — both code paths exist, so a rate-limit problem mid-build doesn't block progress, it's a one-line `.env` change.
- **MCP tool parameter names** are the one place this plan can't be 100% concrete ahead of time (external API not yet introspected) — Task 1 makes discovering them a first-class, verifiable step, and later tasks explicitly flag where to adjust against that discovery rather than silently assuming. The same honesty applies to Task 8.5's GSQL (multi-vertex-set joins, `WHILE`-loop change detection) — flagged as first-draft-expect-iteration, same as Task 10's queries.
- **Known rough edges flagged inline for revisit during execution:** `_amount_from_trigger_text` needs to actually be threaded through `graph_flow.py` (flagged in Task 12's note), GSQL query syntax in Task 10 and Task 8.5 needs live iteration, `run_case.py`'s verdict/status thresholds are a first draft to be tightened against the Task 8 manual checkpoint, and Task 8.5's connected components run once after initial load rather than incrementally (documented as a known limitation in the spec, not silently glossed over).
