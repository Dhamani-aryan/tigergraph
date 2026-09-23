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
    //
    // Uses GSQL's native backward-traversal syntax (<-(EdgeName)-) rather than
    // a "reverse_X" named reverse edge -- Task 8 confirmed live that no edge
    // in this schema has a declared REVERSE_EDGE (Task 4 never added one), so
    // a pattern referencing "reverse_FROM_DEVICE" etc. as if it were a
    // declared edge type name fails with a semantic error. <-(FROM_DEVICE)-
    // needs no schema change and no REVERSE_EDGE declaration -- it just means
    // "traverse this directed edge backwards," which is native GSQL.
    //
    // Device-derived SHARES_ORIGIN carries a real risk worth checking for
    // before trusting it: Task 8's manual checkpoint found one common device
    // fingerprint (generic Windows/Chrome/1920x1080) shared by 621
    // transactions across 299 unrelated customers -- a fingerprint collision
    // from a coarse DeviceProfile derivation, not a real fraud ring. If a
    // first run of this query produces implausibly large clusters, add a
    // degree cap: skip ACCUM-ing SHARES_ORIGIN edges through any DeviceProfile
    // whose connected-card count exceeds some threshold (e.g. 15-20) before
    // trusting it as a meaningful shared-origin signal, rather than treating
    // every shared device fingerprint as ring evidence. Confirm the actual
    // collision rate live before deciding whether this cap is needed or what
    // threshold is right -- 621/299 was one observed fingerprint, not
    // necessarily representative of all of them.
    Devices = {{DeviceProfile.*}};
    ViaDevice = SELECT c2 FROM Devices:d <-(FROM_DEVICE)- Transaction <-(MADE)- Card:c1,
                       Devices:d <-(FROM_DEVICE)- Transaction <-(MADE)- Card:c2
                WHERE c1.card_id != c2.card_id
                ACCUM INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "device");

    Regions = {{BillingRegion.*}};
    ViaRegion = SELECT c2 FROM Regions:r <-(BILLED_IN)- Transaction <-(MADE)- Card:c1,
                       Regions:r <-(BILLED_IN)- Transaction <-(MADE)- Card:c2
                WHERE c1.card_id != c2.card_id
                ACCUM INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "region");

    Emails = {{EmailDomain.*}};
    ViaEmail = SELECT c2 FROM Emails:e <-(PURCHASER_EMAIL)- Transaction <-(MADE)- Card:c1,
                      Emails:e <-(PURCHASER_EMAIL)- Transaction <-(MADE)- Card:c2
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

**Second live-confirmed risk (Task 8's manual checkpoint):** `Card` doesn't have
`primary_id_as_attribute` set. `label_propagation_cc` below reads and writes
`c.card_id`/`c.ring_cluster_id` as dot-accessed attributes (`c.ring_cluster_id =
c.card_id`, `c.@min_label += nbr.ring_cluster_id`), and `build_shares_origin`
above filters `WHERE c1.card_id != c2.card_id` — both are exactly the pattern
Task 8 found broken. If either query fails on this, see Task 10's note (same
section header pattern) for the fix: a typed `VERTEX<Card>` query parameter
instead of attribute access, or — specific to this query, which needs the
primary id as a *string value* to assign into `ring_cluster_id`, not just to
filter — GSQL's built-in vertex-to-string conversion in an ACCUM context (check
current GSQL docs for the exact function name/syntax; this weakens the "prefer
official docs over guessing" pattern this plan has followed elsewhere, but no
verified syntax was available to write here without a live instance to test
against).

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

