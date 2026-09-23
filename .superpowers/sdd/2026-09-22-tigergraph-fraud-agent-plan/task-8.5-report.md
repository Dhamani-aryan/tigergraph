# Task 8.5 Report: Connected-components graph algorithm for fraud-ring clustering

## Status: DONE_WITH_CONCERNS

## Commit

Pending (see below) — `feat: connected-components graph algorithm for fraud-ring
clustering`, on branch `tigergraph-fraud-agent`, local only (not pushed). Adds:

- `src/graph_algorithms/__init__.py` (empty)
- `src/graph_algorithms/connected_components.py`
- `scripts/run_connected_components.py`
- `tests/test_connected_components.py` (not required by the brief's file list,
  added to mirror Tasks 7/8's testing convention for the one piece of this
  task's logic that's meaningfully unit-testable without a live server — the
  Python-side grouping/ring-naming logic)

## What ran, live, against the graph

`PYTHONPATH=. .venv\Scripts\python scripts\run_connected_components.py` — completed
successfully (exit 0), twice (idempotency check — identical numbers both runs).
Every `Card` vertex now has `ring_cluster_id`/`cluster_prior_fraud_rate` populated.
Full test suite: `PYTHONPATH=. .venv\Scripts\python -m pytest tests/` — 37/37 passed
(33 prior + 4 new).

## Live GSQL findings (this task's syntax/mechanism discoveries, for future tasks)

1. **This GSQL dialect does not support chaining more than one hop in a single
   FROM-clause path expression**, even with `SYNTAX v2` on the query.
   `SELECT d FROM Cards:c -(MADE)-> Transaction:t -(FROM_DEVICE)-> DeviceProfile:d`
   fails to parse (`mismatched input '->'`); the identical query split into two
   single-hop SELECT statements (`Txns = SELECT t FROM Cards:c -(MADE)-> Transaction:t;`
   then `Devs = SELECT d FROM Txns:t -(FROM_DEVICE)-> DeviceProfile:d`) parses and
   returns the exact expected count (140,784, matching Task 8's independently
   verified `FROM_DEVICE` count). Every multi-hop traversal in this task's code is
   written as a sequence of one-hop SELECTs for this reason, not the brief's
   chained-arrow draft.

2. **The brief's `reverse_FROM_DEVICE`-style backward traversal turned out to be
   unnecessary for this specific query**, not just broken. Task 8 needed it because
   it was starting from a specific known `DeviceProfile`/`Card` vertex and asking
   "what points at me" (the "one" side wanting the "many" side). This task's join
   only ever needs the *forward* direction already declared in the schema
   (`Card -(MADE)-> Transaction -(FROM_DEVICE)-> DeviceProfile`, etc.), so no
   `REVERSE_EDGE`/backward-traversal workaround was needed at all — a different
   resolution than Task 8's own finding, worth distinguishing for later tasks
   deciding whether they need Task 8's `VERTEX<T>`-parameter workaround or can
   just traverse forward like this one.

3. **`Card`'s missing `primary_id_as_attribute`** (Task 8's finding) affects this
   task's two queries differently:
   - `build_shares_origin`'s `WHERE c1.card_id != c2.card_id` (brief's draft) was
     avoided entirely by comparing the **vertex values themselves**
     (`WHERE c1 != c2`) and inserting edges with the vertex aliases directly
     (`INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "device")`), never a string
     card_id. No workaround needed — the attribute was simply never touched.
   - `label_propagation_cc` **does** need the primary id as a real STRING value
     (to seed and later name each connected component), and there is genuinely no
     way around that with the vertex-identity trick. Investigated live (per the
     brief's explicit instruction not to guess): a WebSearch against current GSQL
     docs and a TigerGraph community-forum thread on this exact question, plus
     direct probes against this server, all agree — **GSQL has no expression that
     converts a VERTEX to its primary-id STRING without `primary_id_as_attribute`.**
     `c.ring_cluster_id = c` and `c.ring_cluster_id = "" + c` both fail with "no
     type can be inferred"; the documented vertex functions (`getvid`, `to_vertex`,
     `elementId`, `getAttr`) either go the wrong direction or return TigerGraph's
     internal id, never the primary key string. The forum's own answer: either add
     `primary_id_as_attribute` at CREATE time (not viable here — `Card` already has
     13,574 vertices and every MADE/OWNS/ON_CARD/CONNECTED_TO/SHARES_ORIGIN edge in
     the graph; dropping and recreating it to add the flag would cascade-delete all
     of that) or fall back to "external tools like pyTigerGraph... it will include
     the vertex ids." **Resolution used**: the actual label-propagation ALGORITHM
     (the min-label WHILE loop) still runs entirely inside GSQL as a real installed
     query, using `getvid(c)` (TigerGraph's internal numeric vertex id — documented,
     works on any vertex regardless of `primary_id_as_attribute`) as the label
     instead of `card_id`, since only relative ordering/equality matters for
     connected components. Only the unavoidable last step — mapping each card's
     final numeric label back to a `ring_cluster_id` STRING — happens in Python,
     because GSQL's `PRINT`/JSON serialization (unlike its expression language)
     *does* include each vertex's real primary id automatically: confirmed live,
     `PRINT Cards[Cards.@minLabel]` returns entries like
     `{"v_id": "C07660-K1", ..., "attributes": {"Cards.@minLabel": 220201130}}`.
     The STRING write-back uses `tigergraph__add_nodes` (REST++ upsert), the same
     mechanism `src/schema/loading_jobs.py` and `src/schema/derive_entities.py`
     already established for writes this server's `tg.gsql("INSERT ...")` rejects.

4. **`ACCUM INSERT INTO EDGE SHARES_ORIGIN VALUES(...)` (inside an installed query
   body) does actually persist on this server**, unlike the bare top-level
   `tg.gsql("INSERT INTO ...")` Task 7 found rejected — confirmed by writing,
   reading back via `tigergraph__get_edges`, and independently re-counting via
   `SELECT count(*) FROM Card-(SHARES_ORIGIN)-Card`. One gotcha: immediately after
   the `RUN`, `tigergraph__get_edge_count` reported `0` (stale cached stats) even
   though the edges were genuinely there — `get_edges`/`SELECT count(*)` both
   showed the correct count right away. Don't trust `get_edge_count` as a
   freshness check right after a write-heavy query on this server.

5. **The ternary operator (`cond ? a : b`) is not supported** — `cluster_fraud_rate`
   was rewritten from the brief's draft to IF/ELSE. `MapAccum<STRING,
   SumAccum<INT>>.get(key)` (nested map read), by contrast, worked exactly as
   drafted.

6. GSQL query parameters support default values (`CREATE QUERY
   build_shares_origin(INT capDegree=20)`), confirmed live — used to make the
   degree cap tunable without editing the query text.

## The degree-cap investigation (why a cap was needed, and why 20)

The brief flagged one specific manual-checkpoint finding (a generic Windows/Chrome
device shared by 621 transactions/299 customers) and suggested checking whether
that generalizes before deciding on a cap. It does, and **far more severely than
that one example suggested** — this was investigated live with full histograms
across all three origin dimensions before writing any capped/uncapped edges:

| Dimension | # groups (≥2 cards) | Max group size | Groups >20 cards |
|---|---|---|---|
| DeviceProfile | 9,705 total (4,788 with ≥2) | **842** | 523 |
| BillingRegion | 332 total (184 with ≥2) | **2,006** | 73 |
| EmailDomain | 59 total (all with ≥2) | **8,933** (`gmail.com`) | 57 of 59 |

Every dimension in this dataset is dominated by coarse-categorical collisions, not
genuine shared origins — `gmail.com`/`yahoo.com`/`anonymous.com` alone touch
8,933/5,751/4,720 of the 13,574 cards; a single `addr1` region touches 2,006. A
per-group degree cap of **20** (matching the brief's suggested 15-20 range,
applied uniformly to all three dimensions since region/email turned out worse
than device, not better) was added to `build_shares_origin` via a `capDegree`
query parameter: any DeviceProfile/BillingRegion/EmailDomain connecting more than
20 distinct Cards contributes zero `SHARES_ORIGIN` edges. Live result at cap=20:
4,265/111/2 device/region/email groups used, 523/73/57 skipped as over cap,
43,805 `SHARES_ORIGIN` edges total.

**A second, deeper problem was then found live, beyond what the brief
anticipated**: even after capping every individual group at 20, connected
components still produces **one dominant supercluster of 3,565 cards** (26% of
all 13,574 Cards) — not from any single oversized hub (those are already excluded
by the cap), but from **transitive chaining across thousands of small, individually
-plausible-looking groups** (4,265 device groups alone, sizes 2-20) that happen to
overlap enough in membership to percolate into one giant component. This was
confirmed by testing per-dimension in isolation (device-only clustering alone
produces a 3,476-member component; region-only produces 230; email-only produces
only 18) and by testing much lower caps offline against the raw group-membership
data (cap=3 still produces a 1,406-member component). **Lowering the cap further
does not fix this** — it shrinks the supercluster somewhat (1,406 at cap=3 vs
3,565 at cap=20) but never removes it, because the underlying `DeviceProfile`
dimension (Task 8's dedup key: `DeviceInfo`/`id_30`/`id_31`/`id_33`, with no time
component) is simply too coarse across the *entire* dataset, not just in a few
outlier fingerprints, for "shares a device fingerprint" to reliably mean "is the
same physical device." No cap value tested (3 through 100) eliminates this
without eliminating essentially all cross-card connectivity. **I kept the cap at
20** (consistent with the brief's suggested range, and raising it only makes the
supercluster larger — see the regression-check analysis below for why raising it
doesn't even help the one case it might seem to help) and am reporting this as a
genuine, data-driven limitation rather than tuning the threshold to hide it.

## Required spot check: card `C03528-K1` / case `CC-2649`'s ring

**Result: partial pass, not a full pass, and I want to be explicit about that
rather than round it up.**

The brief's literal Step 4 query fails exactly as Task 8 predicted (confirmed
live): `SELECT card_id, ... FROM Card WHERE card_id == "C03528-K1"` →
`Semantic Check Fails: The attribute card_id doesn't exist in vertex type Card`.
Verification instead used `tigergraph__get_node`/`get_nodes` (REST++, addresses by
real primary id, same workaround Task 8 established).

`closed_cases_history.csv`'s `connected_card_ids` for case `CC-2649` (and the same
list, confirmed identical, on cases `CC-2971`/`CC-2985`/`CC-3035`) lists **23**
other cards, so the full ring is **24** cards including `C03528-K1` itself (the
brief said "22-member ring... the other 21 cards" — off by two from what the CSV
actually contains; I'm reporting the CSV's real count rather than the brief's).

Investigating *why* this ring exists in the source data: the case's own analyst
note says the fraud transactions came from "a Samsung SM-G935F on Chrome for
Android behind an anonymous proxy... **two other cardholders** reported the same
device profile this month" — i.e. the dataset's intended ground-truth signal for
this ring is a device shared by roughly 3 cards. I hashed the flagged
transactions' actual `identity.csv` device fields myself
(`device_id_for("SM-G935F Build/NRD90M", "Android 7.0", "chrome 62.0 for android",
"1920x1080")` → `D7e7ac97b8377`) and queried its live `DeviceProfile` card set:
**52 distinct cards**, not ~3 — and all 24 ring members are among those 52 (the
other 28 are the same kind of fingerprint-collision noise Task 8 flagged, just for
this specific model/OS/browser/resolution combination rather than a generic one).
This 52-card group is exactly the kind of oversized hub the degree cap is designed
to exclude (52 > 20), so at cap=20 **none** of the ring's connectivity comes from
its own defining device.

Checked live, after the real run (cap=20): of the 24 ring members, **15 (including
`C03528-K1` itself) ended up merged into the 3,565-member supercluster** described
above (sharing `ring_cluster_id = RING-C00001-K1`, `cluster_prior_fraud_rate =
0.847`) — via *other*, unrelated small device/region/email groups elsewhere in
their transaction histories, not via the ring's true device. The remaining
**9 ring members had zero `SHARES_ORIGIN` edges at all** under the cap (every one
of their device/region/email group memberships exceeds 20) and are singleton
rings (`ring_cluster_id == their own card_id`, `cluster_prior_fraud_rate = 0.0`)
— literally the exact failure signature the brief calls out as a bug indicator.
Spot-checked 4 of these 9 directly via `get_node_edges` to rule out a code bug:
`C06617-K1`/`C12132-K2`/`C12900-K2`/`C09998-K1` all confirmed to have 0
`SHARES_ORIGIN` edges live, each because literally every group they belong to
(2 device groups sized 110/52, 1 region group sized 1,939, up to 6 email groups
sized 468-8,933) exceeds the cap.

I tested whether raising the cap to admit the ring's 52-card device group would
fix this (it directly would, since a 52-clique guarantees mutual connectivity for
all 24 regardless of anything else): at `capDegree=52`, all 24 ring members do
merge into one cluster — but that cluster balloons to **4,731 members** (35% of
all cards), i.e. raising the cap to satisfy this one case makes the systemic
collision problem measurably worse, not better, exactly as the cap-sensitivity
analysis above predicts. There is no cap value in the range I tested (3-100) that
both keeps cluster sizes at a plausible "fraud ring" scale and fully unifies this
specific 24-card ring, because the ring's true signal and the dataset-wide
fingerprint-collision noise are, at the `DeviceProfile` granularity available from
this schema, genuinely inseparable by group-size alone.

I did not lower the pass bar by adding a post-hoc "cluster too big → explode into
singletons" rule (I considered it): at cap=20 that would convert `C03528-K1` from
"partial pass" to "isolated card, rate 0.0" — literally worse against the required
check, for no real gain in the giant-supercluster's usefulness (see the base-rate
comparison below).

## Is the giant supercluster's `cluster_prior_fraud_rate` meaningless?

Checked this directly rather than assuming: the giant 3,565-card cluster's
`cluster_prior_fraud_rate` is **0.847**. The overall `confirmed_fraud` rate across
*all* 5,565 `ClosedCase` rows, independent of any clustering, is **0.838**
(4,665/5,565, computed directly from `closed_cases_history.csv`). These are
statistically indistinguishable — the giant cluster's rate is just the dataset's
base rate, carrying essentially no differentiated ring signal, which is exactly
what you'd expect from a cluster that's mostly fingerprint-collision noise rather
than a real ring. By contrast, the smaller clusters the same pipeline produced
*are* differentiated: the largest non-giant cluster (6 cards) has
`cluster_prior_fraud_rate = 1.0`; several 2-3 card clusters have rate 0.0 or 1.0.
459 of the 9,940 singleton "rings" (cards with no `SHARES_ORIGIN` edge at all)
have a nonzero rate from their own individual closed-case history. This is
evidence the mechanism itself works correctly for cards it can meaningfully
cluster — the specific failure mode is confined to the DeviceProfile-driven
supercluster, not the pipeline in general.

## Why DONE_WITH_CONCERNS, not DONE

The required regression check does not fully pass as literally worded ("shares a
`ring_cluster_id` with the other 21 cards in that ring") — 9 of the 23 other ring
members do not share `C03528-K1`'s cluster. I'm confident this is a genuine data/
schema limitation (Task 8's `DeviceProfile` dedup key has far less entropy than
the dataset's narrative implies "same device" should have, and `BillingRegion`/
`EmailDomain` are categorically too coarse at any cap that also controls the
dataset-wide collision problem) rather than a bug in this task's query logic —
every traversal was individually verified against independent counts before being
combined (140,784/590,742/4,438/308 pair and hop counts all matched expectations),
the WHILE-loop change-detection was verified to converge (7 rounds, stable across
two independent full runs), and the specific "9 isolated ring members" were
spot-checked at the edge level to confirm they truly have zero qualifying
`SHARES_ORIGIN` edges rather than a propagation bug. But I can't respond to a
required, explicitly-worded verification step with "it's fine" when the literal
result doesn't match — flagging honestly, per the same "report reality, don't
reverse-fit" instruction Tasks 7/8 followed.

**Recommendation for whoever picks this up next** (not implemented here — out of
this task's time budget): the most promising fix is scoping `SHARES_ORIGIN`
membership to each card's *specific* flagged/fraud-relevant transactions rather
than a card's entire transaction history (this task's design, per the brief,
builds origin-groups from ALL of a card's transactions, which is what lets a
card's routine day-to-day purchases drag it into unrelated device/region/email
groups), and/or requiring corroboration across 2+ of the 3 dimensions before
trusting a pairwise link (I checked partially: none of the ring-intersecting
`BillingRegion`/`EmailDomain` groups are small enough to usefully corroborate
without also being computationally intractable to enumerate at their raw size —
worth a dedicated follow-up task rather than a rushed addition here).

## Concerns summary

1. **The device-collision supercluster (3,565 cards, 26% of the graph) is real
   and not fully solvable by degree-cap tuning alone** — documented above with
   the cap-sensitivity data (cap 3 → 1,406-card supercluster; cap 20 → 3,565;
   cap 52+ → 4,731+). Kept cap=20 as the least-bad, brief-recommended choice.
2. **The required spot check partially fails**: `C03528-K1` and 14 of its 23
   true ring-mates share one `ring_cluster_id` (the giant, low-signal
   supercluster); the other 9 ring-mates are isolated singletons under this cap.
3. **The giant supercluster's `cluster_prior_fraud_rate` (0.847) carries no real
   differentiated signal** — it's statistically indistinguishable from the
   dataset-wide base rate (0.838). Downstream tasks (Task 10) reading
   `ring_cluster_id`/`cluster_prior_fraud_rate` for evidence-gathering should be
   aware that a match against the 3,565-card cluster specifically is not
   meaningful ring evidence, while matches against the smaller clusters (and the
   9,940 singleton self-rings, which reduce to each card's own closed-case prior)
   are.
4. `device_neighbors_probe4` — an installed query left over from Task 8's own
   manual checkpoint (their report said all three probe queries were dropped
   afterward; this one wasn't) — is still present in the graph. Not touched here
   since it's Task 8's artifact, not this task's; flagging in case it's confusing
   to whoever looks at `SHOW QUERY *` next.
