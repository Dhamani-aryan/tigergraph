# Task 10 Review: Graph evidence-gathering functions

## Overall verdict: DONE_WITH_CONCERNS confirmed — spec-compliant, functionally solid, but one Important finding the report under-characterizes

This review re-derived every material claim in `task-10-report.md` independently: read
`src/graph/queries.py` and `src/graph/vector_search.py` in full, cross-checked every GSQL
query's edge directions against `src/schema/build_schema.py`, ran `tests/test_graph_queries.py`
and the full suite live, called all six deterministic functions plus `dispatch_followup_tool`
by hand against the live graph, and ran `SHOW QUERY *` on the server. All specific factual
claims in the report that could be independently checked (transaction counts, case IDs,
cluster IDs, cap-hit counts) checked out exactly. One of the report's three "flagged
concerns" (the `card_window` anchor semantics) is more serious than the report presents it —
it is a demonstrated defect in how the function's primary caller will actually use it, not
just a documented design trade-off — and a few things live-verification surfaced (a real
concurrency race, a deprecation warning on every call, one leftover unaccounted-for server
artifact) aren't mentioned in the report at all.

---

## 1. Spec compliance

**Verdict: COMPLIANT.**

Confirmed by direct reading of `src/graph/queries.py`, independently of the report's own
claims:

| Function | Uses `CREATE QUERY`/`INSTALL QUERY` | Typed `VERTEX<Type>` param | No WHERE-clause primary-key filter |
|---|---|---|---|
| `card_window` | Yes (`CARD_WINDOW_GSQL`, line 165) | `VERTEX<Card> input_card` | Yes — seeds `{input_card}` directly |
| `customer_cards` | Yes (line 213) | `VERTEX<Customer> input_customer` | Yes |
| `device_neighbors` | Yes (line 241) | `VERTEX<Transaction> input_txn` | Yes |
| `region_neighbors` | Yes (line 294) | `VERTEX<BillingRegion> input_region` | Yes |
| `closed_case_lookup` (×3 installed queries: by_card/by_device/by_region) | Yes (lines 363, 384, 405) | `VERTEX<Card>`/`VERTEX<DeviceProfile>`/`VERTEX<BillingRegion>` | Yes |
| `ring_membership` | Yes (line 481) | `VERTEX<Card> input_card` | Yes |

All six call through `_ensure_installed` → `tg.gsql(CREATE OR REPLACE QUERY ... INSTALL
QUERY ...)` then `tg.run_installed_query(...)`. None use a raw `INTERPRET QUERY` or a
WHERE-clause comparison against a primary-key-typed attribute — exactly the pattern Task 8
proved fails on `Card`/`DeviceProfile`, and which this task additionally confirmed extends to
`ClosedCase`. Verified live via `SHOW QUERY *` (see §3) that all 8 named production queries
(`card_window`, `customer_cards`, `device_neighbors`, `region_neighbors`,
`closed_case_lookup_by_card/_by_device/_by_region`, `ring_membership`) are genuinely installed
server-side with exactly this signature shape — this isn't just source code that claims to do
this, it's live on the graph.

Interface conformance: matches the brief's authoritative "Produces" line exactly, including
`closed_case_lookup`'s `device_id` parameter (present in the Interfaces bullet even though the
brief's own inline code draft omitted it — correctly implemented per the authoritative
signature, not the draft). `region_neighbors`'s `txn_ts`/`window_days` are optional with
defaults rather than required positionals — a reasonable, backward-compatible loosening, not a
deviation that breaks any caller.

Diff scope check: `review-7d21659..ed33193.diff` touches exactly four files —
`src/graph/__init__.py` (confirmed empty, 0 bytes), `src/graph/queries.py`,
`src/graph/vector_search.py`, `tests/test_graph_queries.py` — matching the report's stated
file list with no unaccounted scope creep.

`retrieve_knowledge` in `vector_search.py` uses `tg.search_top_k_similarity` (a REST++ tool,
not GSQL), as specified; its `_unwrap_hits` two-list-join logic matches what I independently
observed the tool actually returns (see §3).

---

## 2. Independent live verification

Ran directly against the live TigerGraph server (not just reading the test file):

- **`card_window("C04570-K1", hours=100000)`** (full history): 59 transactions, includes
  flagged transaction `3450629`. Matches Task 8's independently-established checkpoint exactly
  (`docs/manual-case-checkpoint.md` line 54: "Card `C04570-K1` has **59 transactions** from
  2016-07-17 to 2016-12-25").
- **`card_window("C04570-K1", hours=48)`** — i.e. the *exact* call Task 12's own design doc
  makes (`docs/superpowers/plans/2026-09-22-tigergraph-fraud-agent-plan.md` line 2914:
  `window = await card_window(tg, card_id, hours=48)`): returned **only 2 transactions**, both
  dated 2016-12-25, and does **not** include the flagged transaction (2016-11-11). See §4a —
  this is the basis for my disagreement with the report's severity rating on concern 1.
- **`ring_membership("C03528-K1")`**: `{'ring_cluster_id': 'RING-C00001-K1',
  'cluster_prior_fraud_rate': 0.8469278812408447}` — exact match to the report and to Task
  8.5's report (which also independently derived this exact number for this exact card).
- **`closed_case_lookup(card_id="C04570-K1")`**: returns exactly one case, `CC-1383`,
  `outcome: cleared` — exact match to the report and to `docs/manual-case-checkpoint.md` line
  81.
- **`FOLLOWUP_TOOL_SCHEMAS`**: 3 schemas (`wider_region_check`,
  `closed_case_lookup_by_region`, `wider_card_window`), matching the brief.
  **`dispatch_followup_tool`**: manually invoked all three code paths —
  `wider_card_window("C04570-K1", hours=100000)` → 59 rows (correct, routes to `card_window`);
  `wider_region_check("204.0")` → exactly 500 rows (hits the documented `LIMIT 500` cap live —
  confirms concern 4b is reproducible, not theoretical); `closed_case_lookup_by_region("204.0")`
  → 411 rows. All route correctly and return sane data. Point 5 of the task brief
  (does `dispatch_followup_tool`/`FOLLOWUP_TOOL_SCHEMAS` still work after the query rewrites):
  **yes, confirmed working live**, not just passing a mocked test.
- **`SHOW QUERY *`** (live, full text read): all 8 production queries listed above are present
  and installed (`# installed v2`), plus Task 8.5's three artifacts
  (`label_propagation_cc`, `cluster_fraud_rate`, `build_shares_origin`) and one query,
  `device_neighbors_probe4`, that is **not** this task's — it's Task 8's own leftover checkpoint
  artifact, already flagged as a pre-existing gap in Task 8.5's review (concern 4 there). Also
  found one item the report does not mention — see §5.

### Test execution (run myself, not just re-reading the report)

- `tests/test_graph_queries.py -v`, run in isolation: **16/16 passed** — matches the report's
  claim exactly.
- Full suite `tests/`, run in isolation: **58/58 passed** in 345s — matches the report's claim
  exactly. Test count independently verified by grepping all `test_*.py` files for `def test_`
  before running: 4+4+4+16+5+3+15+2+3+2 = 58, confirming the file-count math checks out too.
- **One caveat surfaced during verification, not a contradiction of the report's numbers**: my
  first run of `test_graph_queries.py -v` was executed *while* a second Python process of mine
  was independently and concurrently calling the same query functions (cold module-level
  `_INSTALLED_QUERIES` cache, so both processes issued `CREATE OR REPLACE QUERY ...
  INSTALL QUERY card_window` at roughly the same time). That run produced **15 passed, 1
  failed**, with `run_installed_query('card_window', ...)` failing:
  `"Query endpoint '/query/FraudInvestigation/card_window' is disabled, please make sure all
  its sub-queries are installed and enabled with same signature." (REST-1005)`. Re-running the
  same single test alone immediately afterward passed cleanly (25s), and the full suite run
  afterward in isolation was 58/58 clean — so the report's numbers are accurate for how it was
  tested (one process at a time). But this is a real, reproducible concurrency hazard in the
  code itself; see §4c.

---

## 3. Code quality

### Strengths

- **Extensive, falsifiable documentation.** Nearly every docstring and inline comment cites a
  specific live-observed number (transaction counts, case IDs, distances, error codes). I
  independently re-derived a representative sample of these (59-txn count, `CC-1383`,
  `RING-C00001-K1`/0.8469278812408447, the 500-row region cap) and every one checked out
  exactly. This is unusually high-integrity documentation for a fast-moving task — it reads as
  genuinely reporting what was observed, not reverse-engineered to look good.
- **No copy-paste drift.** `_print_results`/`_flatten_vertices`/`_parse_ts`/`_ensure_installed`
  are shared helpers used consistently by all six functions; the PRINT-result-unwrapping logic
  lives in exactly one place.
- **Edge directions are all correct.** Cross-checked every traversal in every query against
  `src/schema/build_schema.py`'s `CREATE DIRECTED EDGE` declarations
  (`OWNS: Customer→Card`, `MADE: Card→Transaction`, `FROM_DEVICE: Transaction→DeviceProfile`,
  `BILLED_IN: Transaction→BillingRegion`, `INVOLVES: ClosedCase→Transaction`,
  `ON_CARD`/`CONNECTED_TO: ClosedCase→Card`) — every query traverses forward in the schema's
  declared direction and filters the target via `VERTEX<T>` parameter equality or a
  `SetAccum` membership check, exactly the pattern the module's own header comment describes.
  No direction bugs.
- **`_ensure_installed`'s success detection is correctly non-trivial.** It correctly
  distinguishes "the MCP tool call HTTP-succeeded" from "the GSQL actually compiled and
  installed" (`tg.gsql`'s wrapper reports `success: true` even for a query that failed to
  install as a draft) by string-matching the response body — a real, non-obvious pitfall,
  correctly handled and exercised by every passing live test.
- Defensive `ValueError` on unknown follow-up tool name, tested and confirmed working.

### Findings by severity

**Important**

1. **`card_window`'s "anchor on the card's own latest transaction" semantics is a demonstrated
   defect for its actual primary caller, not just a noted design trade-off.** The report
   frames this (its concern #1) as "a genuine design choice... Task 12 should be aware." That
   undersells it. Task 12's own design document
   (`docs/superpowers/plans/2026-09-22-tigergraph-fraud-agent-plan.md`, `gather_evidence_node`,
   line 2914) calls `card_window(tg, card_id, hours=48)` on **every single case, with no
   reference timestamp**, as the very first piece of evidence gathered — its entire purpose is
   showing the LLM activity around the transaction that triggered the case. Live-verified
   against the project's own canonical fixture (HHG-017 / `C04570-K1`, used throughout Tasks
   8/8.5/10 as the reference case): this exact call returns 2 transactions, both from
   2016-12-25 — and **excludes the actual flagged transaction** (3450629, 2016-11-11 23:46:24,
   confirmed via `docs/manual-case-checkpoint.md`), because this card has ~44 days of routine
   activity after the flagged one. The failure mode is silent and plausible-looking: the LLM
   is handed two real transactions with real amounts and timestamps that have nothing to do
   with the case being investigated, with no error or empty-result signal that anything is
   wrong. This isn't an edge case — it will happen for every case where the flagged
   transaction isn't the card's most recent activity, which given the "opened_at" vs.
   transaction-history spread pattern (see `docs/manual-case-checkpoint.md`) is plausibly the
   common case, not the rare one.
   The interface itself (both the brief and the design doc) doesn't give `card_window` a
   reference-timestamp parameter, so this isn't something Task 10 could have unilaterally
   fixed without deviating from its stated interface — reasonable to leave as-is *for this
   task*. But the data needed to fix it is already sitting in `case_row` (`opened_at`:
   `"2016-11-12 00:46:24"` for this exact fixture, 41 seconds after the flagged transaction's
   real timestamp) and never reaches `card_window`. **Recommend addressing before or alongside
   Task 12**: add an optional `ref_ts: str | None = None` parameter to `card_window`
   (defaulting to today's "anchor on latest" behavior when omitted, for backward
   compatibility), and have `gather_evidence_node` pass `row["opened_at"]`.

**Moderate**

2. **A real concurrency race, not mentioned in the report.** `_ensure_installed` has a
   per-process cache (`_INSTALLED_QUERIES: set[str]`) but no locking and no retry around the
   `CREATE OR REPLACE QUERY ... INSTALL QUERY` round trip. Live-reproduced (see §2): two
   processes with cold caches calling the same query function concurrently can race on
   TigerGraph's server-side query (re)installation and leave the query's REST endpoint
   transiently *disabled*, causing an unhandled `RuntimeError` on the very next call — not a
   graceful retry, a hard failure that propagates straight up through `card_window` (or
   whichever function). It self-heals (a retry moments later succeeds), but nothing in the code
   retries automatically. This is a plausible real-world scenario for Task 12 — any
   process-level parallelism when batch-processing a case pack, or simply two people/processes
   touching this shared graph at once (which this review itself demonstrated by accident) — and
   is foundational-code-adjacent enough to be worth a one-line mitigating comment or a retry-once
   wrapper before Task 12 builds heavily on top of it.
3. **Every one of the six functions passes `VERTEX<Type>` query parameters in a format the
   underlying library flags as deprecated**, confirmed via a live warning on every single call:
   `"Deprecated parameter format detected: plain values for VERTEX<T> parameters (e.g. {"p":
   1}) are deprecated and will be removed in a future release. Use a 1-tuple instead: {"p":
   (1,)}."` All six functions call `tg.run_installed_query(name, {"input_x": some_string})`
   rather than `{"input_x": (some_string,)}`. It currently still works (the library falls back
   to a GET-based compatibility path), so nothing is broken today, but it's a real
   forward-compatibility landmine baked identically into all six functions — a future dependency
   bump will break every one of them at once. Not caught or mentioned anywhere in the report.
4. **Report slightly overstates artifact-cleanup completeness.** The report claims "All
   throwaway `probe_*` queries used during live iteration (8 of them) were explicitly `DROP
   QUERY`'d... the graph is left with only this task's intended artifacts." Literally true for
   anything named `probe_*`, but `SHOW QUERY *` (run live, this review) shows one additional
   item not accounted for by any task's report: `_vec_search_7c22464a(LIST<FLOAT> query_vec,
   INT k)`, marked `# pendingInstall` (draft, never actually installed/enabled), whose body
   (`vectorSearch({KnowledgeDoc.embedding}, query_vec, k, ...)`, `SYNTAX v3`) is unmistakably
   related to this task's vector-search work — no other task touches `KnowledgeDoc.embedding`
   via a native GSQL query. It's possible this was auto-generated by the `tigergraph-mcp`
   server's `search_top_k_similarity` tool implementation itself rather than hand-written by
   the implementer (its randomized-hex name suggests tooling, not a manual probe), in which
   case it isn't really "this task's mess" so much as a side effect of the REST++ tool it calls
   — but either way it's inert clutter on the shared graph the report's cleanup claim didn't
   catch. Low severity (never installed/enabled, causes no functional issue) but worth a
   `DROP QUERY` pass before calling this fully clean.

**Minor (report already surfaces these correctly; assessed independently below — see §4)**

5. `region_neighbors`'s `LIMIT 500` applied before the `txn_ts`/`window_days` filter (report's
   concern #2).
6. `closed_case_lookup`'s three branches not de-duplicated when combined (report's concern
   #3).
7. `card_window`/`region_neighbors` silently drop rows with an unparseable `ts` when a window
   filter is active (falls out of the `valid` list) — theoretical only; `ts` is a populated
   STRING attribute on every `Transaction` per schema, never observed to actually be missing.

---

## 4. Assessment of the report's three flagged design concerns

### (a) `card_window`'s hours-window anchor — **upgrade to Important, not "noted limitation"**

Addressed in full in §3 finding 1 above. Summary: the report treats this as a reasonable,
merely-worth-flagging interpretation. Having read Task 12's actual planned call site
(`gather_evidence_node`, which calls `card_window(tg, card_id, hours=48)` unconditionally,
every case, as the first and most central piece of deterministic evidence) and having
reproduced the failure live against the project's own reference fixture, I don't think "genuine
design choice" is the right frame. The interface as specified genuinely has no
reference-timestamp parameter to work with, so Task 10 implemented defensibly *given that
constraint* — but the constraint itself needs to be revisited before Task 12 is built on it,
not carried forward as an accepted limitation. This is the one place I'd push back hardest on
the report's own self-assessment.

### (b) `region_neighbors`'s `LIMIT 500` before the time filter — **Minor, correctly triaged by the report**

Real and live-reproducible (§2: `wider_region_check("204.0")` returns exactly 500, the cap).
But checking the actual call graph in `docs/superpowers/plans/...`: `region_neighbors` is
**not called anywhere in the deterministic per-case evidence pass** — `gather_evidence_node`
never calls it. It's reachable *only* through the bounded one-shot LLM follow-up round
(`wider_region_check`), which fires at most once per case and only when the LLM explicitly
judges the deterministic evidence ambiguous enough to ask for it. That's a narrow blast radius:
worst case, one follow-up call per case, in a region busy enough to exceed 500 transactions,
silently missing some in-window rows. Agree with the report: real, correctly documented,
correctly triaged as low severity given where it's actually reachable from.

### (c) `closed_case_lookup`'s branches not de-duplicated — **Minor, correctly triaged, and lower-risk in practice than the report implies**

Real as described. But every current call site in the design doc passes exactly one of
`card_id`/`device_id`/`addr1` at a time (`gather_evidence_node` calls it with `card_id=` only;
the only follow-up tool that reaches it, `closed_case_lookup_by_region`, passes `addr1=` only)
— so under the currently planned usage, the multi-argument path that would actually produce
duplicates is never exercised. It's a real behavioral surprise for a future caller that passes
two identifiers at once, worth the docstring note it already has, but not a live risk today.
Agree with the report's triage.

---

## 5. Follow-up-tool / Task 12 dependency check (task point 5)

`FOLLOWUP_TOOL_SCHEMAS` (3 schemas) and `dispatch_followup_tool` were both exercised live in
this review (§2), independent of the test suite: correct routing to `region_neighbors`,
`closed_case_lookup(addr1=...)`, and `card_window` respectively, with sane live data returned
from each. This piece survived the GSQL rewrite intact and functions exactly as the brief
specifies — Task 12 can build on it as-is, modulo the `card_window` anchor-semantics concern
above, since `wider_card_window` inherits that same behavior.

## 6. Debug/probe artifact check (task point 6)

Covered in §2/§3 finding 4. Net result: this task's own named throwaway probes were genuinely
cleaned up (no `probe_*`-named artifact from this task remains); Task 8's pre-existing
`device_neighbors_probe4` leftover is unrelated to Task 10 (already flagged in Task 8.5's
review); one uninstalled draft query (`_vec_search_7c22464a`) of uncertain but
plausibly-this-task-or-tooling origin remains and should be dropped, though it's inert.

---

## Summary

- **Spec compliance: pass.** All 6 functions genuinely rewritten to `CREATE
  QUERY`/`INSTALL QUERY` with typed `VERTEX<Type>` parameters; verified both by reading the
  source and by querying the live server directly. Interfaces match the brief. Test claims (16
  and 58) reproduced exactly when run the same way the implementer ran them.
- **Code quality: solid, with one Important gap.** Clean structure, accurate and
  independently-verified documentation, correct edge directions throughout, no copy-paste
  drift. But `card_window`'s anchor semantics is a live-demonstrated defect against its actual
  planned caller (not just a documented trade-off), and three things — a real install-time
  concurrency race, a deprecated parameter format used on every call, and one unaccounted-for
  server artifact — surfaced during independent live verification but aren't mentioned in the
  report at all.
- **Recommendation:** do not block Task 10 on this — it correctly implemented the interface it
  was given, under real, well-documented server constraints, and the report's own honesty about
  DONE_WITH_CONCERNS is largely justified. But before Task 12 is built on `gather_evidence_node`
  calling `card_window(hours=48)` with no reference timestamp, revisit that interface — this is
  the one place where "ship as documented" would let a real, silent evidence-quality bug flow
  straight into the agent every downstream task depends on.
