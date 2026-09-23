# Task 7 Re-review (scoped): 21 stub-Card reproducibility fix

Reviewed against: `task-7-brief.md`, `task-7-report.md` (including the "Addendum: reproducibility
fix for the 21 stub Card vertices" section), `task-7-review.md` (original finding), and
`review-dd73361..b4704e5.diff` (commit `b4704e5`, "fix: reproduce the 21 stub-Card customer_id
patch as committed code"). Verified independently against the live TigerGraph workspace via
`.venv\Scripts\python`, not taken from the report's numbers.

## Verdict: ADDRESSED

The original finding — that the live one-off patch for 21 blank-`customer_id` stub `Card`
vertices was applied by hand during the session but never captured in committed code, so
`scripts/load_data.py` run fresh against an empty graph would not reproduce today's correct
state — is fixed. `customer_id_from_card_id` + `backfill_stub_card_customer_ids` are now
committed in `src/schema/loading_jobs.py` (commit `b4704e5`, working tree clean, `git log`
confirms this is HEAD on `tigergraph-fraud-agent`) and wired into `run_all_loading_jobs` so a
fresh run reproduces the corrected state automatically, with no manual follow-up.

## Point-by-point

**1. Parsing logic matches `card_ids.py`'s `-K` convention — confirmed by direct read, not by trusting the claim.**

`src/schema/card_ids.py:25` (inside `build_card_id_map`, when splitting a `connected_card_ids`
entry back into its owning customer): `customer_id = card_id.split("-K")[0]`.

`src/schema/loading_jobs.py:328` (`customer_id_from_card_id`): `return card_id.split("-K")[0]`.

Byte-for-byte identical split. `card_ids.py` itself was not touched by this diff (confirmed —
the diff only touches `src/schema/loading_jobs.py` and adds `tests/test_loading_jobs.py`), so
the "same convention" claim is real, not asserted.

**2. Wiring and ordering — confirmed correct.**

`run_all_loading_jobs` (`src/schema/loading_jobs.py:415-428`) runs, in order: transactions load →
`load_cards_and_made_edges` (creates the real Card/OWNS/MADE from the resolved map) →
`closed_cases_loading_job_gsql` CREATE → `_run_job_from_csv` for closed_cases (this is what
actually runs the `ON_CARD` edge load and is what auto-creates the blank stubs in the first
place) → `backfill_stub_card_customer_ids(tg)` as the final call. The backfill genuinely runs
after the `ON_CARD` load, not before it — ordering is correct, matching the docstring's claim
and the module's own ordering comment.

**3. Tests — run myself, genuine, all pass; full suite has no regressions.**

```
.venv\Scripts\pytest tests/test_loading_jobs.py -v
```
5 passed: `test_customer_id_from_card_id_basic`,
`test_customer_id_from_card_id_handles_double_digit_suffix`,
`test_customer_id_from_card_id_matches_card_ids_module_convention` (this one is genuinely
non-tautological — it goes through `card_ids.py`'s own `build_card_id_map` on a synthetic
`connected_card_ids` row and asserts `customer_id_from_card_id` derives the same customer_id
from the resulting card_id, i.e. it's an actual cross-module consistency check, not a
restatement of the implementation), `test_backfill_stub_card_customer_ids_patches_only_blank_ones`
(verifies a third, already-populated Card is *excluded* from the patch batch — not just that the
function runs), and `test_backfill_stub_card_customer_ids_is_a_noop_when_nothing_blank` (verifies
exactly one call — the read — happens with zero write calls, which is what actually makes the
idempotency claim meaningful at the unit level).

```
.venv\Scripts\pytest -q
```
29 passed, 0 failed — no regressions anywhere else in the suite.

**4. Live verification — zero blank-`customer_id` Cards, confirmed directly (not the report's numbers).**

Pulled all 13,574 `Card` vertices live via `tigergraph__get_nodes` (limit 50,000, i.e. the exact
call the backfill function itself uses) and scanned for `not attributes.get("customer_id")`:

```
Total Card vertices: 13574
Blank customer_id count: 0
Blank ids: []
```

Also independently confirmed `OWNS` edge count = 13,574, matching `Card` count exactly (every
stub's `OWNS` edge is present, not just its `customer_id` attribute). Both match the report's
addendum claims.

On methodology for the report's "idempotency verified with a synthetic stub" claim: this is a
believable, non-hand-wavy approach. Creating a real `Card` vertex with `customer_id=""` that
matches the function's actual filter predicate (`not attributes.get("customer_id")`), running the
function twice, and checking that the first run patches it while the second finds nothing (plus
cleanup via `delete_node` afterward) is a legitimate way to exercise the active-patch code path
live, which the report's own automated tests (mocked `tg.call`) don't reach — the mocked tests
prove the payload shape is correct in isolation, the live synthetic-stub run proves the actual
`add_nodes`/`add_edges` calls against the real server behave as expected end-to-end. This is a
reasonable division of labor between unit and live verification, not a substitute for one or the
other.

**5. New Critical/Important breakage in the fix diff — none found.**

The diff is purely additive (two new functions + one new line in `run_all_loading_jobs` + a new
test file); it doesn't modify any existing function body. Points considered and ruled out as
blocking:
- `get_nodes(..., limit=50_000)` re-fetches all Card vertices on every run to find stubs — fine
  at the current dataset size (13,574), but the limit is a hardcoded ceiling; if the Card
  population ever grew past 50,000 without the limit being raised, stubs beyond that window
  would silently be missed. Not a real risk at this dataset's fixed size, and not something this
  fix could reasonably be expected to future-proof — noted as a deferred-minor, not blocking.
- The backfill intentionally does not add `MADE` edges for the stub's card (documented,
  consistent with the established 1:1 card1-per-customer design) — correct scope, not a gap.
- No behavior change to `load_cards_and_made_edges`, `transactions_loading_job_gsql`,
  `closed_cases_loading_job_gsql`, or `_run_job_from_csv` — confirmed by diff inspection, so none
  of the original review's already-resolved spec-compliance points are disturbed.

## Deferred-minor (non-blocking, out of scope for this re-review)

- The `limit=50_000` ceiling on `backfill_stub_card_customer_ids`'s `get_nodes` call noted above.
- Everything else the original review already classified as Minor (no broader unit coverage for
  `loading_jobs.py`'s GSQL-generation/batching logic; `validLine=0` retry being a mitigation, not
  a root-cause fix) remains as previously assessed — explicitly out of scope for this round per
  the report, and this re-review did not re-litigate either.

## Summary

- **Verdict: ADDRESSED.**
- Live check: **0 of 13,574** `Card` vertices have a blank `customer_id` right now; `OWNS` count
  (13,574) matches `Card` count exactly.
- Tests: 5/5 new tests pass (genuine, not tautological), full suite 29/29 pass, no regressions.
- New breakage: **none** found in the fix diff.
