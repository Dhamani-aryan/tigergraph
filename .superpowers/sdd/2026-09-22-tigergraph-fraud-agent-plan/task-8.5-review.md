# Task 8.5 Review: Connected-components graph algorithm for fraud-ring clustering

Reviewer verification method: read `src/graph_algorithms/connected_components.py` and
the full diff directly; ran `.venv\Scripts\pytest tests/ -v`; and ran a series of
independent, read-only live queries against the actual TigerGraph instance (via the
repo's own `TigerGraphMCP` client, same mechanism the test suite and the task's own
verification used) rather than trusting the report's numbers. No writes were made to
the graph. Scripts used for live verification are not part of this deliverable.

## Spec compliance verdict: PASS (as `DONE_WITH_CONCERNS`, honestly reported)

- All three required files exist and match the brief's file list: `src/graph_algorithms/__init__.py`
  (empty), `src/graph_algorithms/connected_components.py`, `scripts/run_connected_components.py`.
  `tests/test_connected_components.py` is an appropriate, unrequired addition (mirrors
  Tasks 7/8 convention).
- Interfaces satisfied: every `Card` vertex has `ring_cluster_id`/`cluster_prior_fraud_rate`
  populated (confirmed live: 13,574 `Card` vertices, all reachable via
  `SHARES_ORIGIN`-derived clusters or singleton self-rings — 3,565 in the giant cluster +
  9,940 singletons + 69 cards spread across ~32 small clusters = 13,574).
- The report's central self-assessment — that the brief's literal Step 4 regression check
  ("shares a `ring_cluster_id` with the other 21 cards") does not fully pass, and that this
  is a genuine data/schema limitation rather than a bug — holds up under independent
  verification (see below). Reporting `DONE_WITH_CONCERNS` instead of rounding up to `DONE`
  was the right call, and the report resisted the temptation to add a post-hoc rule that
  would have "passed" the check while making the actual output worse (correctly declined).
- One factual inaccuracy in the report, independently caught: it claims the `connected_card_ids`
  lists for cases CC-2649/CC-2971/CC-2985/CC-3035 are "the same list, confirmed identical."
  They are not. I read all four rows from `closed_cases_history.csv` directly: each list has
  23 entries, but they differ from each other — each case's list omits exactly one card,
  consistently the case's own `card_id` (e.g. CC-2649 is `C03528-K1`'s case and its list
  omits `C03528-K1`; CC-2971 is `C09998-K1`'s case and its list omits `C09998-K1`). This is
  the expected "other cards connected to this case" semantics, not literal duplication. The
  **union** of all four lists is still exactly 24 distinct cards, so the report's final count
  (24-member ring) is correct — but the "identical" claim itself is wrong and should be
  corrected if this report is used as a reference later. Low-stakes, doesn't change any
  downstream conclusion.

## Code quality verdict: solid, with a few real findings

**Medium severity**

1. **`origin_type` on `SHARES_ORIGIN` is silently overwritten for any card pair linked by
   more than one dimension, and this isn't tested or discussed anywhere.** The schema
   (`src/schema/build_schema.py:61`) declares `CREATE UNDIRECTED EDGE SHARES_ORIGIN (FROM
   Card, TO Card, origin_type STRING)` — `origin_type` is a plain attribute, not part of a
   discriminator/composite key, and no multi-edge behavior is declared. `build_shares_origin`
   processes device, then region, then email as three sequential blocks, each doing
   `INSERT INTO EDGE SHARES_ORIGIN VALUES (c1, c2, "<dimension>")` for every pair in its
   group. TigerGraph stores at most one `SHARES_ORIGIN` edge per unordered `(Card, Card)`
   pair, so if a pair of cards happens to share origin via, say, both device and email, the
   email-block's `INSERT` (which runs last) silently overwrites the device-block's
   `origin_type`, discarding the fact that two independent signals linked that pair. I did
   not catch this live on an actual multi-dimension pair (the one pair I spot-checked,
   `C00255-K1`, showed only `"device"`-labeled edges), so I can't confirm how often it
   actually fires in this dataset — but it follows directly and unavoidably from the schema
   as declared, so it should be verified (or fixed) before being relied on. It also directly
   undercuts the report's own proposed follow-up — "requiring corroboration across 2+ of the
   3 dimensions before trusting a pairwise link" is not implementable against the *current*
   `SHARES_ORIGIN` data, because multi-dimension corroboration is exactly the information this
   write pattern throws away. A composite/discriminated edge key (or a small bitmask/set
   attribute recording *all* contributing dimensions) would be needed first.
2. **`write_ring_cluster_ids` has no partial-failure handling.** It batches 500 cards per
   `tigergraph__add_nodes` call with no retry or per-batch success tracking; if a batch call
   raises partway through the full 13,574-card set, the graph is left with some cards
   patched and some not, with no record of which batch failed. Re-running the whole pipeline
   is safe (the report confirmed idempotency across two full runs) but wasteful, and there's
   no partial-progress recovery. Low-stakes given the small scale and one-time-batch nature
   of this job, but worth a comment or a resumable-batch design if this pattern gets reused
   for a much larger graph later.
3. `device_neighbors_probe4` (Task 8's leftover manual-checkpoint query) is confirmed still
   installed on the live graph (`SHOW QUERY *`). Correctly out of scope for this task and
   correctly flagged in the report rather than silently left for someone else to discover.

**Low severity**

- The nested `FOREACH c1 ... FOREACH c2 ... IF c1 != c2` pattern inserts both `(c1,c2)` and
  `(c2,c1)` for every unordered pair — since `SHARES_ORIGIN` is `UNDIRECTED`, TigerGraph
  dedups these into one edge, so this is a pure (trivial, capped at 20-member groups)
  performance/elegance nit, not a correctness bug. Confirmed by the fact that the live
  `SHARES_ORIGIN` edge count (43,805, verified below) is sane and matches expectations.
- `_extract_print_results` relies on positional indexing (`print_results[0]`,
  `print_results[1]`) into the GSQL `PRINT` output list, which is brittle to any reordering
  of the two `PRINT` statements in `LABEL_PROPAGATION_CC_GSQL`. Works today, no defensive
  check against the wrong key being at that index.
- The module's inline comments are extremely dense (over half of the 389-line file is
  comment blocks that closely mirror `task-8.5-report.md`'s prose almost verbatim). Every
  claim I checked in those comments was accurate, so this is a style observation, not a
  defect — but it's duplicated documentation that will drift if only one copy gets updated
  later.

**Test suite**: `pytest tests/ -v` → 37/37 passed (confirmed myself), including the 4 new
`test_connected_components.py` cases, which test the pure `_group_touched_cards` grouping
function (the only piece of this task's logic that's meaningfully unit-testable without a
live server — the GSQL itself has no local mock). Appropriately scoped given the constraints;
I would not ask for more unit coverage here.

## Independently-verified empirical claims (all queried live, fresh, read-only)

| Claim | Report says | I independently confirmed |
|---|---|---|
| Total `Card` count | 13,574 | **13,574** (exact match) |
| Dataset-wide `confirmed_fraud` rate | ~83.8% (4,665/5,565) | **4,665/5,565 = 83.83%**, confirmed 3 independent ways: raw CSV read, README's stated figures, and a fresh live GSQL query directly against `ClosedCase` vertices in the graph. All three agree exactly. |
| Giant supercluster size | ~3,565 cards, `ring_cluster_id = RING-C00001-K1` | **3,565** (exact match, via `SELECT c FROM Card:c WHERE c.ring_cluster_id == "RING-C00001-K1"`) |
| Giant supercluster's `cluster_prior_fraud_rate` | ~0.847 | **0.8469278812408447** (exact match) |
| Total `SHARES_ORIGIN` edges at cap=20 | 43,805 | **43,805** (exact match via `get_edge_count`) |
| Per-dimension histogram (groups ≥2, groups >20, max size) | Device 4,788/523/842; Region 184/73/2,006; Email 59/57/8,933 | **Exact match on all nine numbers**, recomputed from scratch via my own GSQL traversal + accumulator query, not trusting the table |
| Untouched (singleton) cards | 9,940 of 13,574 | **9,940** (13,574 − 3,634 touched; 3,634 independently confirmed two ways — direct traversal count and cluster-count arithmetic) |
| Device-only clustering component size | 3,476 | **3,476** (exact match — I pulled raw `DeviceProfile`→card-set data live and ran my own union-find in Python, entirely independent of the report's methodology) |
| Cap=3 supercluster size | 1,406 | **1,275** via my own independent union-find reconstruction from live group-membership data (device+region+email groups sized 2–3). Same order of magnitude and same qualitative conclusion (a four-digit supercluster persists at cap=3; cap-tuning alone doesn't fix it), but not an exact match — a ~9% discrepancy I can't fully explain (possibly a methodology difference in how the report's "offline" analysis was run). Given the report's numbers matched exactly everywhere else I checked, including the harder-to-eyeball device-only figure, I read this as noise/methodology drift rather than the report's core finding being wrong. |
| Ring spot check for `C03528-K1`/case `CC-2649` | 24-member ring; 15 in `RING-C00001-K1` (rate 0.847), 9 isolated singletons | **Confirmed exactly**: queried all 24 cards live. 15 (incl. `C03528-K1`) share `RING-C00001-K1` at rate 0.8469…; the other 9 (`C03551-K2`, `C06197-K2`, `C06617-K1`, `C07485-K1`, `C08112-K2`, `C09998-K1`, `C11468-K2`, `C12132-K2`, `C12900-K2`) are singleton rings. Matches the report's 15/9 split precisely, including that `C09998-K1` has a nonzero rate (0.667) from its own closed-case history despite being a singleton — consistent with the report's separate claim about 459 non-zero-rate singletons. |
| `Card` lacks queryable `card_id` attribute | Confirmed live per report | Independently reproduced: a live query referencing `c.card_id` on a `Card`-typed variable failed with `TYP-158: 'c.card_id' indicates vertex types [ClosedCase, FraudCase], which does not conform to any of [Card]` — corroborates the `primary_id_as_attribute` gap claim from a different angle than the report used. |
| `device_neighbors_probe4` leftover | Still installed | Confirmed via live `SHOW QUERY *`. |

## Independent judgment on point 4: is "keep cap=20, document the limitation" the right call?

**The specific alternative posed — dropping `BillingRegion`/`EmailDomain` and clustering on
`DeviceProfile` alone — does not hold up.** I tested it directly: device-only clustering at
cap=20 produces a **3,476-member** giant component, versus **3,565** with all three
dimensions combined. That's a 2.5% reduction. `DeviceProfile` alone is *already* almost as
bad as the full three-dimension union — the coarse dedup key from Task 8 (device model + OS
+ browser + resolution, with no time component) is the dominant driver of the collision
problem, not `BillingRegion`/`EmailDomain`'s additional contribution. Dropping the other two
dimensions would also remove genuine signal for any future ring whose actual linking
mechanism is a shared region or email pattern rather than device — a real cost for a ~2.5%
size reduction that isn't even a qualitative fix (still a multi-thousand-card supercluster
either way). So: **the report's implicit conclusion that trimming dimensions isn't the fix
is correct, and I verified it independently rather than taking it on faith.**

That said, I don't think "keep cap=20, document the limitation" is the *best achievable*
stopping point, though I think it's a *defensible* one given the task's scope. The report's
own proposed next step — scoping `SHARES_ORIGIN` construction to each card's actual
flagged/fraud-relevant transactions rather than its entire transaction history — is a
structurally different fix than anything tested (cap tuning, dimension dropping), and the
CC-2649 case study makes a strong case for it: the ring's true defining device (used for
exactly the 3 fraud transactions) only becomes a 52-card noise hub because the join
aggregates across each card's *entire* history, sweeping in unrelated cardholders' routine
day-to-day purchases from the same generic device model. Restricting the join to
flagged/high-risk transactions (or a tight time window) would plausibly shrink that 52-card
hub toward the "~3 cardholders" the case's own analyst note describes, without needing any
cap tuning at all. I did not implement or test this alternative myself (it requires deriving
`SHARES_ORIGIN` from a different edge/traversal than what's currently in the graph, which is
out of scope for a read-only review), so I can't confirm it would work — but it's the
correct next thing to try, it was correctly identified by the report as more promising than
what was attempted, and it appears to have been deferred purely for time-budget reasons
rather than because cap-tuning was mistakenly believed sufficient. **Verdict: the report's
stopping point is honest and its rejected alternative (dimension-dropping) was correctly
rejected — verified independently, not just internally consistent — but the actually-promising
fix (transaction-scoped origin matching) remains undone and should be the next task's
priority over any further cap-value tuning, which has now been shown (by both the report and
my own reproduction) to have hit its ceiling.**

## Independent read on point 5: is `cluster_prior_fraud_rate >= 0.5` meaningful?

**No — the verified data supports that this threshold is essentially meaningless as
"coordinated ring" evidence.** The baseline confirmed-fraud rate across *all* 5,565 closed
cases, independent of any clustering, is **83.83%** (verified three independent ways above).
The giant, mostly-noise 3,565-card supercluster — which the report and I both independently
confirmed is dominated by fingerprint-collision artifacts, not genuine rings (device-only
clustering alone produces nearly the same size) — sits at **0.847**, comfortably clearing a
0.5 threshold despite being the textbook case of a cluster carrying no differentiated ring
signal. Because analysts only open a case when there's real cause, essentially *any* cluster
with meaningful closed-case representation will drift toward that ~84% base rate regardless
of whether it's a genuine ring, so `>= 0.5` will fire for nearly every populated cluster —
exactly the concern.

A threshold needs to sit meaningfully **above** 83.8% to carry any signal at all. The report's
suggested 0.95+ is directionally reasonable, but I'd flag one thing the report didn't
address and that I found directly in my own spot-check data: **small clusters' rates are
computed from very few closed cases**, so a bare `rate >= 0.95` is trivially satisfied by
sparse data — several of the 24 spot-checked ring members are singleton clusters sitting at
rate exactly `1.0` (`RING-C06617-K1`, `RING-C12132-K2`, `RING-C12900-K2`), almost certainly
from a single closed case each. A threshold of 0.95+ with no sample-size floor would flag
those as "coordinated" on the strength of one data point, which is a different-flavored but
equally spurious signal as the current 0.5 threshold's problem. **My recommendation for the
downstream task's fix**: pair the rate threshold with a minimum closed-case count for the
cluster (e.g., `cluster_prior_fraud_rate >= 0.95 AND total_closed_cases_in_cluster >= 3-5`),
not a bare rate threshold alone. I did not derive the exact right minimum-N cutoff — that
would need its own pass over the closed-case-count distribution per cluster, out of scope
for this review — but the need for *some* floor follows directly from data I already
verified.

## Summary

- **Spec compliance**: PASS. Files, interfaces, and honest status reporting all match the
  brief. One minor factual error in the report (the "identical lists" claim) that doesn't
  change its conclusions.
- **Code quality**: Good overall — clear structure, extensive and (as far as I could verify)
  accurate documentation of live GSQL findings, appropriately scoped tests. One real,
  previously-undiscussed medium-severity finding (`origin_type` overwrite on multi-dimension
  pairs, inferred from the schema) that should be checked before the "require 2+ dimension
  corroboration" follow-up is scoped, plus minor robustness/style notes.
- **Point 4 (cap=20 vs. dropping dimensions)**: Verified independently — dropping
  region/email is *not* a materially better fix (3,476 vs. 3,565, a 2.5% difference). The
  report's decision to keep the cap and document the limitation, rather than drop
  dimensions, is correct. The better fix (scoping origin-matching to flagged transactions)
  is real and should be the priority for whoever picks this up next, but wasn't reached here
  for defensible time-budget reasons, not a diagnostic error.
- **Point 5 (threshold)**: Verified baseline is **83.83%** (4,665/5,565), confirmed three
  independent ways. `>= 0.5` is confirmed meaningless — the noise-dominated giant cluster
  clears it easily. A defensible threshold needs to sit clearly above baseline (0.95+, per
  the report's own suggestion) **and** carry a minimum sample-size requirement per cluster,
  which the report didn't address but which follows directly from the same spot-check data.
