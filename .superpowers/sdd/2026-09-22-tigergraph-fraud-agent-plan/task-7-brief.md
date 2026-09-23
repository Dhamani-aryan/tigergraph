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

