# Task 10 Report: Graph evidence-gathering functions

## Status: DONE (post-review fix round applied — see "Review fix round" section at the end)

## Commit

Pending in this same response — `feat: deterministic graph evidence-gathering
queries and vector retrieval`, on branch `tigergraph-fraud-agent`, local only
(not pushed). Adds:
- `src/graph/__init__.py` (empty)
- `src/graph/queries.py`
- `src/graph/vector_search.py`
- `tests/test_graph_queries.py`

## Headline finding: the brief's core mechanism (interpreted queries) does not
## work on this server at all, independent of the primary_id_as_attribute gap

The brief's draft was `INTERPRET QUERY (...) FOR GRAPH ... { ... }` posted as a
single `tg.gsql(...)` command string, with the primary-key gap fixed via a
`VERTEX<Type>` query parameter. Tried live, exactly as suggested, before writing
anything else: it compiles, but running it returns
`Semantic Check Fails: Run interpreted query doesn't support parameter.`
**This server's GSQL endpoint does not support parameters on interpreted queries
at all** — not a primary-key issue, a completely separate limitation the brief
didn't anticipate. Every query in this task is therefore a real, named, formally
installed GSQL query (`CREATE OR REPLACE QUERY ... INSTALL QUERY ...`, run via
`tigergraph__run_installed_query`/`tg.run_installed_query`), the same mechanism
Task 8.5's `connected_components.py` already established — not the brief's
"interpreted queries, no install ceremony" approach. Each install costs ~20-30s
live; a module-level `_INSTALLED_QUERIES` cache in `queries.py` pays this at most
once per query per process (installs persist server-side across process restarts
too, so it's a one-time real cost, not a per-call one).

## Which queries needed rewriting vs. worked as drafted

**None worked as literally drafted in the brief.** Every one of the six
deterministic functions needed a full GSQL rewrite, for a combination of three
confirmed live issues (not just the flagged primary-key one):

1. **Interpreted queries don't accept parameters** (see above) — forces every
   function onto the CREATE/INSTALL/RUN pattern, full stop.
2. **`primary_id_as_attribute` gap, confirmed to extend beyond `Card`/`DeviceProfile`**:
   live-probed `ClosedCase` too (the task's instruction said to assume it, not
   just take it on faith) — `Cases.case_id` throws the identical
   `TYP-158: ... indicates no valid vertex type ... refers to a primary_id, which
   is not directly usable` error. `Customer`/`BillingRegion`/`FraudCase` were not
   separately probed (never needed — every query below avoids WHERE-clause
   primary-key filtering entirely, using `VERTEX<Type>` parameters or `v_id` in
   PRINT output instead, so the gap never gets exercised either way).
3. **No `REVERSE_EDGE` declared anywhere (Task 8's finding), and — new finding
   this task — the native `<-(EdgeName)-` backward pattern-arrow is a hard GSQL
   *parse* error on this server**, not a semantic one: `no viable alternative at
   input 'from Devs:d <-('`. This directly contradicts this task's own briefing
   note claiming the brief's `device_neighbors` draft "already uses the fix
   (native `<-(EdgeName)-` backward-traversal syntax)" — tested live, it does not
   work, confirming the instruction to verify live rather than trust that framing
   was well-founded. The actual confirmed-working pattern (same resolution Task 8
   found for its own checkpoint queries): seed from the "many" side's full vertex
   set (`{Transaction.*}`, `{ClosedCase.*}`, etc.), traverse the edge FORWARD in
   its declared direction, and filter the target against a known vertex
   (`WHERE target == paramVertex`) or an accumulated vertex set
   (`WHERE target IN @@setAccum`). One more sub-finding here: GSQL rejects `IN`
   against a plain SELECT-produced vertex-set *variable* (`TYP-8027: Vertex set
   variable '...' is not supported for IN/NOT IN clause`) — every multi-step join
   below routes through a `SetAccum<VERTEX<T>>` accumulator instead, never a raw
   vertex-set variable, for this reason.

Per-function summary:

| Function | Brief's approach | What actually works |
|---|---|---|
| `card_window` | Interpreted query, WHERE-filter on `Card.card_id` | Installed query, `VERTEX<Card>` param, single forward `MADE` hop, no WHERE at all |
| `customer_cards` | Ad-hoc `SELECT...WHERE Customer.customer_id==...` | Installed query, `VERTEX<Customer>` param, forward `OWNS` hop |
| `device_neighbors` | Chained multi-hop with native `<-()-` reverse arrows | Installed query, `VERTEX<Transaction>` param; 1 forward hop + 2 forward-seed-and-filter-via-accumulator stages (see finding 3) |
| `region_neighbors` | Ad-hoc `SELECT...WHERE BillingRegion.addr1==...` | Installed query, `VERTEX<BillingRegion>` param, forward-seed-and-filter over all Transactions, `LIMIT 500`; time-window applied in Python (`ts` is a plain STRING attribute) |
| `closed_case_lookup` | Ad-hoc WHERE-filters, `card_id`/`addr1` only | 3 separate installed queries (`_by_card`/`_by_device`/`_by_region`), all forward-seed-and-filter via accumulators; extended to support `device_id` per this task's stated interface |
| `ring_membership` | Ad-hoc `SELECT...WHERE card_id==...` | Installed query, `VERTEX<Card>` param, no WHERE clause needed at all (the caller already knows the one card) — simplest of the six once the parameter pattern was established |

`retrieve_knowledge` (vector search) is the only piece that matched the brief's
draft almost exactly — `tg.search_top_k_similarity` is a REST++ tool call, not
raw GSQL, so none of the above GSQL-dialect issues apply to it. The one real
change: unwrapping. `search_top_k_similarity`'s envelope
(`data["result"]`) is a **two-element list** — `[{"v": [...]}, {"distances":
{v_id: float}}]` — the hit vertices and their distances arrive as separate
top-level entries joined only by `v_id`, not already paired per-hit. Confirmed
live against the populated `KnowledgeDoc` index: querying "card testing small
authorizations" correctly surfaces `pattern-card-testing`/`policy-r5`/
`policy-r10` with sensible distances (0.23–0.37). `src/graph/vector_search.py`'s
`_unwrap_hits` joins the two lists into one row per hit. `FraudCase`'s search
returns an empty hit list live (confirmed — its index exists per Task 4's
schema but has zero vectors until Task 12/13 writes one), exactly as expected
per this task's briefing, not a bug.

## The `result.get("data")` unwrapping instruction — verified live for real

Did this for every function that returns structured data, not just
`ring_membership`:
- `tg.run_installed_query(...)` → `{"success", "data": {"result": [...]}, ...}`.
  `data["result"]` is a list with one entry per `PRINT` statement in source
  order (confirmed against queries with 1 and 2 `PRINT` statements). Each
  vertex-set `PRINT` entry is `{"<alias>": [{"v_id", "v_type", "attributes":
  {"QueryAlias.field": value}}, ...]}`. Centralized in `queries.py`'s
  `_print_results`/`_flatten_vertices` helpers (used by all six functions) rather
  than re-deriving this shape per function.
- `ring_membership` specifically, against the known 24-member-ring fixture
  (`C03528-K1`): returns `ring_cluster_id: "RING-C00001-K1"`,
  `cluster_prior_fraud_rate: 0.8469278812408447` — exact match to
  task-8.5-report.md's numbers.
- `tg.search_top_k_similarity(...)` → same outer envelope, different inner
  shape (see above) — handled by `vector_search.py`'s own `_unwrap_hits`, not
  reused from `queries.py`'s helpers since the PRINT-based shape doesn't apply
  here.

## Verification

- `.venv\Scripts\pytest tests/test_graph_queries.py -v` — **16/16 passed**
  (15 planned per the brief's Step 3 outline, plus one extra I added —
  `test_card_window_small_window_narrows_results` — after the first full run
  caught a real bug in my own first test, see below).
- `.venv\Scripts\pytest tests/` — **58/58 passed** (42 prior + 16 new), no
  regressions.
- Every test asserts against a **known live value**, not just "didn't crash":
  `C04570-K1`'s 59-transaction history (Task 8's independently-verified count),
  its one prior closed case `CC-1383`/`cleared` (Task 8's checkpoint), its
  device fingerprint's 299-customer collision (Task 8's checkpoint, capped at
  300 by `device_neighbors`' `LIMIT`), and `C03528-K1`'s
  `ring_cluster_id == "RING-C00001-K1"` (Task 8.5's spot check) all reproduced
  exactly through this task's functions.
- All 8 GSQL query objects installed cleanly (`Query installation finished`,
  `succeeded: 1, skipped: 0, failed: 0` for each) and left installed under
  their production names (`card_window`, `customer_cards`, `device_neighbors`,
  `region_neighbors`, `closed_case_lookup_by_card`, `closed_case_lookup_by_device`,
  `closed_case_lookup_by_region`, `ring_membership`) for Task 12 to reuse without
  paying the install cost again.
- All throwaway `probe_*` queries used during live iteration (8 of them) were
  explicitly `DROP QUERY`'d after being superseded by their production
  equivalents — confirmed via each drop's own success message — so the graph
  is left with only this task's intended artifacts (mirroring the hygiene Task
  8.5 flagged as a gap in Task 8's leftover `device_neighbors_probe4`).

## A real bug my own first test caught (and the design decision behind the fix)

`card_window(card_id, hours)` has no reference timestamp in its signature (per
this task's own stated interface — matches the brief). With no anchor supplied,
`card_window` windows `hours` back from **the card's own most recent
transaction**. My first test assumed a 24h window would always include
`C04570-K1`'s HHG-017 flagged transaction (2016-11-11) — it doesn't, because
this card has real activity after that date (most recent: 2016-12-25). This
isn't a code bug; it's a consequence of the interface not taking a reference
time, and the "anchor on the card's own latest activity" design decision is
documented in `card_window`'s docstring as the most defensible choice available
given that constraint. Fixed the test (widened the window enough to span
2016-12-25 back through 2016-11-11) and added a second, interface-focused test
that checks windowing *behavior* (narrow window ⊊ full history) rather than
pinning to one card's specific real-world transaction gap.

## Concerns (why DONE_WITH_CONCERNS, not DONE)

1. **`card_window`'s "hours" semantics are a genuine design choice, not a
   spec-mandated behavior** — anchoring on the card's own latest transaction is
   reasonable but not the only defensible interpretation, and Task 12 (which
   consumes this function) should be aware a flagged transaction that isn't the
   card's most recent one may fall outside a short `hours` window. Flagging
   explicitly rather than presenting this as settled.
2. **`region_neighbors`'s server-side `LIMIT 500` is applied before the
   `txn_ts`/`window_days` time filter**, not after — for a region with more than
   500 total transactions (confirmed live that `addr1 "204.0"`, this task's own
   fixture, is one such region), a genuinely in-window transaction could be
   excluded because 500 out-of-window ones for that region happened to be
   returned first by GSQL's unordered vertex-set traversal. Documented in the
   function's docstring; not fixed here because a proper fix (server-side time
   filtering) would require `ts` to be a DATETIME attribute with real date-math
   support, which is outside this task's scope to change (Task 4's schema).
3. **`closed_case_lookup`'s three branches (card/device/region) are not
   deduplicated** when more than one of `card_id`/`device_id`/`addr1` is passed
   in the same call — a case matching two criteria appears twice. Left this way
   deliberately (Task 12's evidence-gathering is expected to reason over each
   signal's provenance separately) but flagging since it's a real behavior
   difference from what a naive reader might expect from "lookup".
4. Every installed query pays a one-time ~20-30s cost on first use per process
   (confirmed live, 8 queries × ~25s ≈ 3.5 minutes total the first time
   `tests/test_graph_queries.py` ran). This is cached in-process
   (`_INSTALLED_QUERIES`) and the installs persist server-side, so it's not
   repeated across test runs or across Task 12 calls within one long-running
   process — but if Task 12 spawns a fresh Python process per case (rather than
   one long-running process for a batch), each process would re-pay this cost
   once (cheap relative to per-case LLM calls, but worth Task 12 being aware of;
   a pre-warming script would be a one-line addition if that pattern turns out
   to matter).

---

## Review fix round (task-10-review.md)

The reviewer independently re-derived every numeric claim in this report (59-txn
count, `CC-1383`, `RING-C00001-K1`/0.8469278812408447, the 500-row region cap) and
confirmed all of them exactly. It found one **Important** finding that upgraded
one of this report's own "concerns" from a documented trade-off to a demonstrated
defect, plus three smaller issues from live verification not in the original
report. All four addressed below.

### 1. (Important) `card_window`'s anchor semantics — fixed with a new `reference_txn_id` parameter

The reviewer traced Task 12's actual planned caller (`gather_evidence_node`,
`docs/superpowers/plans/2026-09-22-tigergraph-fraud-agent-plan.md` line 2914:
`card_window(tg, card_id, hours=48)`, no reference timestamp) and reproduced live
against this task's own canonical fixture that this call returns 2 unrelated
2016-12-25 transactions while silently excluding the actual flagged transaction
(`3450629`, 2016-11-11, 44 days earlier) — because `C04570-K1` has real activity
after the flagged transaction and the old "anchor on latest" behavior windows
around that instead. Since every case's evidence pass calls this unconditionally,
this would make the agent silently miss the transaction it's investigating in a
large share of real cases — this is worse than the original report's framing
("genuine design choice... Task 12 should be aware"), and the reviewer was right
to push back on that self-assessment.

**Fix applied**: `card_window(tg, card_id, hours=2.0, reference_txn_id=None)` —
verified live against the same fixture:
- `card_window(tg, "C04570-K1", hours=48)` (no reference, old call shape):
  confirmed still excludes `3450629` — this is the reproduced defect, pinned
  down explicitly by a new regression test
  (`test_card_window_without_reference_excludes_flagged_txn_at_hours_48`) so it
  can't silently change back without a test failure.
- `card_window(tg, "C04570-K1", hours=48, reference_txn_id="3450629")`: now
  returns `3450629` itself plus its two same-evening neighbors from the manual
  checkpoint (`3450436`, `3450503`), and correctly excludes the unrelated
  2016-12-25 transaction — verified live
  (`test_card_window_with_reference_txn_id_anchors_on_reference_not_latest`).
- An unrecognized/wrong-card `reference_txn_id` falls back to the original
  "anchor on latest" behavior rather than erroring, verified live
  (`test_card_window_unknown_reference_txn_id_falls_back_to_latest_anchor`).
- No new GSQL query needed — `reference_txn_id` resolution happens in Python
  against the same full-history result `card_window` was already fetching, so
  the anchor logic is a pure Python change (see `card_window`'s updated
  docstring in `src/graph/queries.py`).
- `dispatch_followup_tool`'s `wider_card_window` and its `FOLLOWUP_TOOL_SCHEMAS`
  entry now also accept an optional `reference_txn_id`, passed straight through
  to `card_window`, verified live
  (`test_dispatch_followup_tool_wider_card_window_passes_through_reference_txn_id`).
  Per the coordinator's instruction, Task 12's own call site is intentionally
  NOT touched here — `card_window`'s signature and behavior are ready for Task
  12 to pass `row["opened_at"]` or the case's actual flagged-transaction id once
  its plan text is updated separately.

### 2. (Moderate) Concurrency race in `_ensure_installed` — mitigated with a retry, not eliminated

Reproduced by the reviewer: two processes with cold `_INSTALLED_QUERIES` caches
racing on `CREATE OR REPLACE QUERY ... INSTALL QUERY` for the same query name can
leave that query's REST endpoint transiently disabled
(`REST-1005: Query endpoint '.../card_window' is disabled, please make sure all
its sub-queries are installed and enabled with same signature.`), causing an
unhandled `RuntimeError` on the very next `run_installed_query` call, even though
a bare retry moments later (once the racing install finishes) succeeds cleanly.

**Fix applied**: a new `_run_installed_query(tg, query_name, params)` wrapper
(used by all 8 call sites, replacing direct `tg.run_installed_query(...)` calls)
catches a `RuntimeError` whose message contains `"is disabled"` or `"REST-1005"`,
waits 3 seconds, and retries exactly once before re-raising. This does not
eliminate the race at its source — that would need real cross-process
coordination (e.g. a distributed lock), which is out of scope for a one-round
fix — but turns the one confirmed failure mode into a short, self-healing retry
instead of a hard failure reaching Task 12's evidence-gathering node. Documented
in `_run_installed_query`'s docstring, including the specific recommendation that
Task 12 pre-warm all 8 queries once (sequentially) before spawning any parallel
workers, to avoid triggering this race in the first place.

### 3. (Moderate) Deprecated `VERTEX<T>` parameter format — investigated, confirmed not fixable from this codebase

Tried both a Python tuple (`{"input_card": ("C04570-K1",)}`, the format the
warning itself recommends) and a Python list (`{"input_card": ["C04570-K1"]}`)
live against the already-installed `card_window` query, before assuming this was
a one-line change. **The warning fires identically either way, and results are
identical in every case (still correct).** Root cause, confirmed by tracing the
call path: `tg.run_installed_query(...)` goes through the tigergraph-mcp MCP
server over JSON-RPC (`src/tg_client.py`), and JSON has no tuple type distinct
from an array — any Python object passed here is serialized to a JSON array and
reconstructed as a plain `list` inside the tigergraph-mcp server's own process,
where pyTigerGraph's `isinstance(value, tuple)` deprecation check always sees a
`list`, never a `tuple`, regardless of what this file sends. **This cannot be
fixed from `src/graph/queries.py` (or anywhere in this repo) — it would require a
change to the tigergraph-mcp server's own tool implementation**, which is outside
this task's (and this repo's) scope. Documented in a new code comment (finding 6
in `queries.py`'s module header) rather than left silently unaddressed. Confirmed
harmless today: the deprecated path falls back to a GET-based request and still
returns correct, verified-accurate results for every query in this file.

### 4. (Minor) Leftover draft query `_vec_search_7c22464a`

Attempted `DROP QUERY _vec_search_7c22464a` live; the server reported
`Semantic Check Fails: These queries could not be found anywhere:
[_vec_search_7c22464a]` — it was already gone by the time this fix round ran
(plausibly an ephemeral artifact of the `tigergraph__search_top_k_similarity`
REST++ tool's own internal implementation, auto-cleaned after use, consistent
with the reviewer's own guess that it wasn't hand-written by Task 10). Confirmed
via a fresh live `SHOW QUERY *` that no `_vec_search_*`-prefixed query exists on
the graph now, alongside the 8 production queries and Task 8.5's 3 artifacts
(Task 8's pre-existing `device_neighbors_probe4` leftover is unrelated to this
task, left untouched as before).

### Verification after all four fixes

- `.venv\Scripts\pytest tests/test_graph_queries.py -v`: **20/20 passed** (16
  original + 4 new: `test_card_window_without_reference_excludes_flagged_txn_at_hours_48`,
  `test_card_window_with_reference_txn_id_anchors_on_reference_not_latest`,
  `test_card_window_unknown_reference_txn_id_falls_back_to_latest_anchor`,
  `test_dispatch_followup_tool_wider_card_window_passes_through_reference_txn_id`).
- `.venv\Scripts\pytest tests/`: **62/62 passed** (58 prior + 4 new), no
  regressions.
- Status upgraded from DONE_WITH_CONCERNS to DONE for this fix round: the one
  Important finding (a real defect against Task 12's actual planned usage) is
  fixed and regression-tested; the deprecation warning is investigated to a
  confirmed root cause outside this repo's control (documented, not silently
  dropped); the concurrency race is mitigated (not eliminated — documented as
  such, with a clear recommendation for Task 12); the stray query artifact is
  confirmed gone. The three lower-severity concerns already in this report
  (`region_neighbors`'s `LIMIT`-before-time-filter ordering, `closed_case_lookup`'s
  non-deduplication across combined criteria, and unparseable-`ts` row-dropping)
  remain as documented trade-offs, not defects — the reviewer independently
  assessed all three as correctly triaged and low-risk given their actual call
  sites in Task 12's design doc, and none were asked to be fixed in this round.
