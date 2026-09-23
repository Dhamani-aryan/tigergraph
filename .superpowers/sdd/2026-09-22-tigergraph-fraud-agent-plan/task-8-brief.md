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
    # Task 7 confirmed live against this server: raw tg.gsql("INSERT INTO
    # VERTEX/EDGE ...") is rejected outright by the /gsql/v1/statements
    # endpoint (the parser's "expecting one of" list never includes "insert").
    # Use the same tigergraph__add_nodes/add_edges MCP tools Task 7 established
    # instead -- REST++ batch upsert, not raw GSQL INSERT.
    await tg.call(
        "tigergraph__add_nodes",
        {
            "vertex_type": "DeviceProfile",
            "vertex_id": "device_id",
            "vertices": [
                {
                    "device_id": device_id,
                    "device_info": device_info,
                    "os": os_,
                    "browser": browser,
                    "screen": screen,
                }
                for device_id, device_info, os_, browser, screen, _ in batch
            ],
        },
    )
    await tg.call(
        "tigergraph__add_edges",
        {
            "edge_type": "FROM_DEVICE",
            "edges": [
                {
                    "source_type": "Transaction",
                    "source_id": str(txn_id),
                    "target_type": "DeviceProfile",
                    "target_id": device_id,
                }
                for device_id, _, _, _, _, txn_id in batch
            ],
        },
    )


async def load_closed_case_multi_edges(tg: TigerGraphMCP, closed_cases_csv_path: str) -> None:
    with open(closed_cases_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        involves_batch: list[dict] = []
        connected_batch: list[dict] = []
        for row in reader:
            case_id = row["case_id"]
            for txn_id in (row.get("txn_ids") or "").split("|"):
                if txn_id:
                    involves_batch.append(
                        {
                            "source_type": "ClosedCase",
                            "source_id": case_id,
                            "target_type": "Transaction",
                            "target_id": txn_id,
                        }
                    )
            for card_id in (row.get("connected_card_ids") or "").split("|"):
                if card_id:
                    connected_batch.append(
                        {
                            "source_type": "ClosedCase",
                            "source_id": case_id,
                            "target_type": "Card",
                            "target_id": card_id,
                        }
                    )
            # add_edges requires every edge in one batch to share the same edge
            # type -- flush INVOLVES and CONNECTED_TO separately, never mixed.
            if len(involves_batch) >= 500:
                await tg.call("tigergraph__add_edges", {"edge_type": "INVOLVES", "edges": involves_batch})
                involves_batch = []
            if len(connected_batch) >= 500:
                await tg.call("tigergraph__add_edges", {"edge_type": "CONNECTED_TO", "edges": connected_batch})
                connected_batch = []
        if involves_batch:
            await tg.call("tigergraph__add_edges", {"edge_type": "INVOLVES", "edges": involves_batch})
        if connected_batch:
            await tg.call("tigergraph__add_edges", {"edge_type": "CONNECTED_TO", "edges": connected_batch})
```

**Note on `CONNECTED_TO` target cards:** these reference `Card` vertices for *other customers* (per Task 3's finding). Task 7's `load_cards_and_made_edges` already resolved every customer's correct `card_id` (default or override) before this task runs, so every `-K2`/`-K3` card referenced here already exists with the right ID — no separate patching step is needed at this point (an earlier draft of this plan had a `fix_card_ids` correction step here; it's now redundant and has been removed, since fixing it at the source in Task 7 is what actually keeps `MADE` edges pointed at the right card, which a post-hoc patch here could not do).

**Note on `add_nodes`/`add_edges` batch semantics:** confirmed live during Task 7 — `add_edges` requires every edge in one call to share the same source/target vertex types (an API limitation of the underlying REST++ batch upsert), which is why `INVOLVES` and `CONNECTED_TO` are flushed as two separate lists above rather than one combined batch, even though the source loop is interleaved per CSV row.

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

**Also worth knowing before debugging this live:** Task 7 found that this specific
Savanna server rejects a bare top-level `tg.gsql("INSERT INTO VERTEX/EDGE ...")`
outright (use `tigergraph__add_nodes`/`add_edges` instead for that case). The
`ACCUM INSERT INTO EDGE SHARES_ORIGIN VALUES(...)` used inside `build_shares_origin`
below is a different GSQL construct — an insert *inside a query body*, submitted via
`CREATE QUERY`/`INSTALL QUERY`/`RUN QUERY` rather than as a bare DML statement — so
it isn't necessarily subject to the same rejection. But given this server has already
proven pickier than documented GSQL behavior once, if this specific line errors,
that prior finding is the first thing to suspect, not just a syntax typo.

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

