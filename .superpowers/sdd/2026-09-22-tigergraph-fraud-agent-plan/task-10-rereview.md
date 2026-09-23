# Task 10 Re-review: fix round (ed33193..4048fd1)

## Scope

Scoped re-review of the fix round applied in response to `task-10-review.md`'s four
findings. Read the brief, the original report, the original review, and the fix diff
(`review-ed33193..4048fd1.diff`); read the full current `src/graph/queries.py` and
`tests/test_graph_queries.py`; ran both test suites live; independently called
`card_window`/`dispatch_followup_tool` and `SHOW QUERY`/`DROP QUERY` directly against
the live TigerGraph server, not just re-reading the report's claims.

## Verdict summary

| # | Finding | Verdict |
|---|---|---|
| 1 | (Important) `card_window` reference-anchor defect | **ADDRESSED** |
| 2 | (Minor/Moderate) `_ensure_installed` concurrency race | **ADDRESSED** (mitigated, not eliminated — as claimed) |
| 3 | (Minor) Deprecation warning on `VERTEX<T>` params | **ADDRESSED** (investigated, correctly documented as not fixable here) |
| 4 | (Minor) Leftover draft query `_vec_search_7c22464a` | **ADDRESSED** (confirmed already gone) |

**No new Critical/Important breakage found in the fix diff.**

---

## 1. `card_window` reference-anchor defect — ADDRESSED (thoroughly verified)

Read the actual code change in `src/graph/queries.py` (lines 239–309). The GSQL itself
is unchanged (`card_window` still pulls the card's full transaction history); the fix
is a new `reference_txn_id: str | None = None` parameter with real Python logic behind
it, not an accepted-and-ignored argument:

- When `reference_txn_id` is given and found among the card's own transactions, the
  window becomes `[ref_ts - hours, ref_ts + hours]` (symmetric), replacing the old
  "hours before latest" cutoff entirely for that call.
- When omitted, or when the id isn't found on this card, it falls through unchanged to
  the original "hours before the card's own latest transaction" behavior — this is a
  real fallback, not a placeholder; confirmed by the code path (`reference_ts` stays
  `None` and execution falls to the `latest = max(...)` block).

**Live verification, called directly (not through pytest), against the real server:**

```
card_window(tg, "C04570-K1", hours=48)
  -> count: 2, ids: ['3573010', '3573022']   (3450629 present: False)

card_window(tg, "C04570-K1", hours=48, reference_txn_id="3450629")
  -> count: 3, ids: ['3450436', '3450503', '3450629']
     3450629 present: True, 3450436 present: True, 3450503 present: True
     3573010 present (should be False): False

dispatch_followup_tool(tg, "wider_card_window",
    {"card_id": "C04570-K1", "hours": 48, "reference_txn_id": "3450629"})
  -> count: 3, 3450629 present: True
```

This exactly reproduces both halves of the claim: the no-reference call still excludes
the flagged transaction (preserving backward-compatible fallback behavior, correctly
pinned by a regression test rather than silently "fixed" back), and the
`reference_txn_id`-anchored call now includes it plus its two real same-evening
neighbors while excluding the unrelated 44-days-later transaction.

**Regression tests** (`tests/test_graph_queries.py`) genuinely pin this down, not just
"runs without erroring":
- `test_card_window_without_reference_excludes_flagged_txn_at_hours_48` asserts
  `KNOWN_TXN not in txn_ids` for the no-reference case.
- `test_card_window_with_reference_txn_id_anchors_on_reference_not_latest` asserts the
  flagged transaction and its two specific real neighbors (`3450436`, `3450503`) ARE
  present and the unrelated transaction (`3573010`) is NOT — all four assertions are
  specific transaction-id checks, not existence/type checks.
- `test_card_window_unknown_reference_txn_id_falls_back_to_latest_anchor` asserts the
  fallback-id path produces the identical result set as the no-reference call.

**`dispatch_followup_tool`'s `wider_card_window` path** was updated consistently:
`FOLLOWUP_TOOL_SCHEMAS`'s `wider_card_window` entry gained a `reference_txn_id`
property (optional, not in `required`), and the dispatch body passes
`reference_txn_id=arguments.get("reference_txn_id")` straight through to
`card_window`. Covered by a dedicated new test
(`test_dispatch_followup_tool_wider_card_window_passes_through_reference_txn_id`) and
independently confirmed live above.

Cross-checked against the design doc: `docs/superpowers/plans/2026-09-22-tigergraph-fraud-agent-plan.md`
line 2914 does call `card_window(tg, card_id, hours=48)` exactly as both the report and
review describe — this is the real call site the defect was about. The report is
explicit and correct that Task 12's own call site is intentionally NOT touched by this
fix (that's tracked separately); the fix only makes `card_window`'s signature/behavior
ready for it.

**Verdict: ADDRESSED**, verified independently at every layer (code reading, live
manual calls both ways, regression test content, dispatch-path consistency).

---

## 2. Concurrency race in `_ensure_installed` — ADDRESSED (mitigation, not elimination — as claimed)

New `_run_installed_query(tg, query_name, params)` wrapper (`queries.py` lines
149–177) replaces all **8** direct `tg.run_installed_query(...)` call sites (verified
by reading every call site: `card_window`, `customer_cards`, `device_neighbors`,
`region_neighbors`, `closed_case_lookup`'s three branches, `ring_membership` — all
route through the wrapper now). It catches `RuntimeError` whose message contains
`"is disabled"` or `"REST-1005"`, sleeps 3s, and retries exactly once before
re-raising anything else. This matches the report's description exactly and is a
real code change, not just a docstring.

This does not eliminate the race (correctly disclosed as such in both the report and
the code's own docstring) — a 3-second fixed backoff may not always be long enough
given the review's own observation that a losing install can take up to ~20-30s to
finish, so a hard failure could in principle still surface under sustained contention.
That residual risk is explicitly owned in the docstring, not hidden, and matches the
report's own "mitigated, not eliminated" framing — this is a deferred-minor
observation, not something the fix round claimed to fully solve.

**Verdict: ADDRESSED** as scoped (a retry wrapper was the agreed fix, and it was
delivered, applied everywhere, and honestly characterized).

---

## 3. Deprecation warning on `VERTEX<T>` params — ADDRESSED (investigated, correctly resolved as out-of-scope)

Independently reproduced live during this re-review's own `card_window` calls above:
the log shows `WARNING:pyTigerGraph.pytgasync.pyTigerGraphQuery:Deprecated parameter
format detected: ... Retrying with GET for backward compatibility (REST-30000: 'id' is
not found in the VERTEX parameter 'input_card'.)` on every call, and every call still
returned correct results (GET fallback works). This matches the report's account of
the root cause (JSON has no tuple type distinct from a list; the tigergraph-mcp
server's own process reconstructs a plain `list` regardless of what this repo sends)
and its claim that both a tuple and a list were tried live with identical results.

The resolution is documented as a new code comment (finding 6 in `queries.py`'s module
header, lines 94–112) rather than silently dropped, which is what the task asked for
("investigated and confirmed NOT fixable client-side... documented as an out-of-scope
server-side limitation"). No code behavior change was expected or needed here.

**Verdict: ADDRESSED** as an investigation-and-document resolution, not a code fix —
correctly scoped given the confirmed root cause is outside this repo.

---

## 4. Leftover draft query `_vec_search_7c22464a` — ADDRESSED (confirmed gone, independently)

Ran `DROP QUERY _vec_search_7c22464a` live myself (not just re-reading the report):

```
{'success': True, 'operation': 'gsql', 'data': {'result':
 'Semantic Check Fails: These queries could not be found anywhere:
 [_vec_search_7c22464a].'}, ...}
```

and a fresh `SHOW QUERY *` (full text, this re-review) lists exactly: the 8 production
queries from this task, Task 8.5's 3 artifacts (`label_propagation_cc`,
`cluster_fraud_rate`, `build_shares_origin`), and Task 8's pre-existing
`device_neighbors_probe4` (unrelated to this task, already flagged elsewhere) — no
`_vec_search_*`-prefixed query of any kind remains.

**Verdict: ADDRESSED** — the artifact is confirmed gone, independently reproduced.

---

## New Critical/Important breakage introduced by the fix diff — none found

Reviewed the full diff for regressions, not just the intended fix:

- The new symmetric `±hours` windowing only activates when `reference_txn_id`
  resolves to a real transaction on the card; the no-reference code path is byte-for-
  byte the same logic as before (`latest = max(...)`; `cutoff = latest - timedelta(...)`),
  so no existing caller's behavior changed. Confirmed by both full test suites passing
  (see below) and this review's own live no-reference call reproducing the pre-fix
  result exactly.
- `_run_installed_query` only intercepts `RuntimeError`s matching the specific known
  failure string; any other exception propagates unchanged — no silent error
  swallowing introduced.
- All 8 call sites were updated to the new wrapper consistently; no call site was
  missed (verified by reading the whole file, not sampling).
- `FOLLOWUP_TOOL_SCHEMAS`'s new `reference_txn_id` property is correctly optional (not
  added to `required`), so existing callers that don't pass it are unaffected.

## Test verification (run live, this re-review)

- `.venv\Scripts\pytest tests/test_graph_queries.py -v`: **20/20 passed** in 312s —
  matches the report's claimed count exactly, all four new tests present and passing.
- `.venv\Scripts\pytest tests/`: **62/62 passed** in 317s — matches the report's
  claimed count exactly, no regressions.

## Deferred-minor notes (out of scope, non-blocking)

- The concurrency-race retry's fixed 3s backoff may be shorter than an in-progress
  losing install (~20-30s observed), so sustained contention could still surface a
  hard failure occasionally. Already disclosed by the implementer; worth a longer or
  multi-attempt backoff if Task 12 ends up running truly parallel workers without
  pre-warming, but not a defect in this fix round.
- `region_neighbors`'s `LIMIT 500`-before-time-filter and `closed_case_lookup`'s
  cross-branch non-deduplication (both carried over, unchanged, from the original
  report/review as correctly-triaged Minor items) were not in scope for this fix round
  and were not touched — consistent with what was asked.

## Overall

All four findings are ADDRESSED, verified independently through direct code reading,
live manual calls (not just re-running the provided tests), and full-suite test runs.
No new Critical or Important issues were introduced by the fix diff itself.
