# Task 7 Report: Bulk data loading into TigerGraph

## Status: DONE

## Summary

Loaded the full real dataset (590,742 transactions, 708MB) into the live
TigerGraph schema (Task 4). All target counts verified live and correct.
Three mechanisms in the brief's literal code do not work at all against
this specific Savanna-hosted TigerGraph 4.2.5 instance; each was diagnosed
with an isolated live probe before being fixed and re-applied at scale.
Also hit and fixed the "type mismatch" contingency the brief explicitly
anticipated, plus one data-quality issue (quoted-CSV parsing) and one
data-characteristic edge case (customers with two distinct historical
cards) discovered only by verifying counts rather than trusting job
"success" responses.

## Files

- `src/schema/loading_jobs.py` (new)
- `scripts/load_data.py` (new)
- `src/schema/columns.py` (modified: added `ts` to `_STRING_COLUMNS`)

## Live-discovered deviations from the brief's literal code

1. **`tg.gsql()` cannot load local files at all.** `DEFINE FILENAME` with
   any path is resolved against the *GSQL server's own filesystem*, not
   this machine. A relative path resolved server-side into
   `.../4.2.5/dev/gdk/gsql/...` and was rejected as a "sensitive
   directory"; a made-up absolute path failed at `CREATE` time with "File
   or directory ... does not exist!" (existence is checked immediately,
   not just at `RUN` time). Fixed by using GSQL's "runtime data" mode
   (`DEFINE FILENAME <tag>;` with no path, positional `$N` column refs
   instead of `$"ColName"`) and running jobs via the
   `tigergraph__run_loading_job_with_data` MCP tool (which actually POSTs
   local bytes to `/ddl/{graph}`), not `tg.gsql()`.

2. **This server's `/gsql/v1/statements` endpoint does not accept
   `INSERT` as a top-level statement at all**, with or without
   `USE GRAPH`/`BEGIN...END` wrapping (confirmed: a bare single
   `INSERT INTO VERTEX Card VALUES(...)` was rejected by the parser,
   whose "expecting one of" list includes `upsert`, `use`, `select`, etc.
   but never `insert`). `load_cards_and_made_edges`'s Card/OWNS/MADE
   creation now goes through `tigergraph__add_nodes`/`add_edges` (which
   wrap pyTigerGraph's `upsertVertices`/`upsertEdges` REST++ batch calls)
   instead of raw `INSERT` statements. The Card/OWNS/MADE *design*
   (resolve `card_id` before creating anything) is unchanged from the
   brief — only the execution mechanism changed.

3. **`closed_cases_history.csv` has quoted fields with embedded commas**
   (2,324 of 5,565 rows) that GSQL does not quote-parse by default. The
   first real run without `quote="DOUBLE"` silently mis-parsed/dropped
   346 rows (5,219 `ClosedCase` vertices instead of 5,565) — caught only
   by Step 4's count check, not by any error. Fixed by adding
   `quote="DOUBLE"` to `closed_cases_loading_job_gsql`'s `USING` clauses;
   cleared and reloaded `ClosedCase` cleanly (5,565/5,565 confirmed both
   by the server's own `validObject` count and by `get_vertex_count`).
   (`transactions.csv` has zero quote characters, so it never needed this.)

## The anticipated type-mismatch contingency (hit exactly as predicted)

The brief called out: "A common failure mode is a type mismatch on one of
the 393 generated `Transaction` attributes... add it to `_STRING_COLUMNS`,
re-run `create_schema.py` after `DROP VERTEX Transaction`." This happened:
the `ts` column (a timestamp string like `2016-07-02 00:02:21`) wasn't in
`_STRING_COLUMNS`, so `generate_attrs` typed it as `DOUBLE`, and every
`Transaction` row failed to load (`invalidAttribute: 2000/2000` on a test
chunk). Scanned the *entire* file (not just a sample) for other bad
`DOUBLE` columns first — `ts` was the only one. Fixed by adding `"ts"` to
`_STRING_COLUMNS`. Since nothing meaningful had been loaded yet, dropped
the whole graph/schema with `DROP GRAPH FraudInvestigation CASCADE` +
`DROP EDGE`/`DROP VERTEX` for all 13/9 types (same procedure Task 4's own
report used) and cleanly re-ran `scripts/create_schema.py` before
reloading data.

## A live-observed transient failure mode (now guarded against)

Once, a `RUN` sent immediately after (re)creating the `load_closed_cases`
job returned `success: True` with a plausible-looking response, but the
server had actually parsed **zero** valid lines (`validLine: 0`) — a
silent no-op. A retry of the identical call a few seconds later loaded
every row correctly, suggesting eventual-consistency lag on the server's
loading-job catalog rather than a data problem. `tg.call()` only raises on
an outright tool failure, not a "successful" call that quietly loaded
nothing, so `_run_job_from_csv` now inspects the reported `validLine`
count itself, retries once after a short delay, and raises if it's still
zero the second time.

## A genuine dataset edge case (Task 3 limitation, not a Task 7 bug)

Card count came out as 13,574, not 1:1 with Customer's 13,553 as the
brief expected. Root cause: **21 customers have two distinct closed cases
on two different cards** (e.g. customer `C02575` has case `CC-0113` on
card `C02575-K2` and case `CC-4153` on card `C02575-K1`). Task 3's
`build_card_id_map` (explicitly out of scope for me to alter — the brief
says to trust it as-is) is a plain `dict` keyed by `customer_id`, so it
can only hold one card_id per customer; the later CSV row silently
overwrites the earlier one. The "losing" card_id is still referenced by
`closed_cases_loading_job_gsql`'s `ON_CARD` edge load, which auto-creates
a GSQL vertex "stub" (primary key only, blank `customer_id`) for any
`Card` id it references that doesn't already exist.

This is squarely a Task 7 (loading-completeness) concern, not a Task 3
(card_id-resolution) one, so I fixed it at the loading layer without
touching `card_ids.py`: patched all 21 stub vertices' `customer_id`
attribute (derived unambiguously from the card_id's own `<customer>-K<n>`
naming convention already used everywhere in this codebase — not
fabricated) and added their `OWNS` edges, so they're not orphaned. No
`MADE` edges were added for these second cards (per the established design,
`card1` is 1:1 with `customer_id` in the transaction data itself — there's
no way to re-attribute specific transactions to a customer's second card
from the raw data, and doing so would be a Task 3-level redesign, not a
Task 7 loading fix).

## Verified live counts (Step 4)

| Vertex/Edge | Count | Brief's expectation | Note |
|---|---|---|---|
| Customer | 13,553 | ~13,500 | matches |
| Card | 13,574 | ~13,500, 1:1 with Customer | 13,553 normal + 21 legitimate second cards (see above) |
| Transaction | 590,742 | 590,742 | exact match |
| EmailDomain | 59 | — | |
| BillingRegion | 332 | — | |
| ClosedCase | 5,565 | 5,565 | exact match (after the quote-parsing fix) |
| OWNS | 13,574 | — | matches Card count |
| MADE | 590,742 | — | matches Transaction count, every transaction has exactly one |
| PURCHASER_EMAIL | 496,262 | — | rows with non-null `P_emaildomain` |
| BILLED_IN | 525,003 | — | rows with non-null `addr1` |
| ON_CARD | 5,565 | — | matches ClosedCase count |

**Spot check** (brief's required non-default-suffix check):
`Card` `"C08623-K2"` — present, `customer_id: "C08623"`. Confirms Phase 1
correctly resolved the non-default `-K2` override rather than defaulting
to `-K1`.

## Commit

`0bcffeb` — `feat: bulk loading jobs for transactions and closed cases,
with resolved card_id from the start`. Adds `src/schema/loading_jobs.py`,
`scripts/load_data.py`; modifies `src/schema/columns.py`. Local commit
only on `tigergraph-fraud-agent`, not pushed, working directory otherwise
clean (`git status` confirmed).

## Concerns for downstream tasks

- **Card is not strictly 1:1 with Customer** (13,574 vs 13,553): 21
  customers legitimately own two cards. Any downstream logic that assumes
  exactly one `Card` per `Customer` (e.g. a `SELECT` that expects a single
  `OWNS` target) should account for this. This reflects real, verified
  data — not a loading defect.
- **`_run_job_from_csv`'s validLine=0 retry logic is a mitigation, not a
  root-cause fix** — the underlying server-side eventual-consistency lag
  (if that's really what it is) was only observed once and isn't fully
  understood. If Task 8 or later tasks run more `CREATE LOADING JOB`
  immediately followed by `RUN`, watch for the same silent-no-op pattern;
  the guard will now raise loudly instead of silently succeeding.
- **`INVOLVES` and `CONNECTED_TO`** (pipe-separated `txn_ids`/
  `connected_card_ids` from `closed_cases_history.csv`) were intentionally
  *not* loaded here, per the brief's own note — that's Task 8's job.
- Anyone re-running `scripts/load_data.py` from a totally empty graph
  should expect it to take real wall-clock time: ~30 chunked uploads for
  the 590K-row transactions file, ~14 batches for Card/OWNS, ~591 batches
  for MADE edges, then the closed_cases job. All of this was verified
  working end-to-end in this session (partly via a direct resume from
  Phase 1 rather than re-running the already-successful transactions load
  a second time, but the same `loading_jobs.py`/`load_data.py` code path
  that's committed is what ran throughout).

## Addendum: reproducibility fix for the 21 stub Card vertices

The task reviewer (`task-7-review.md`) independently re-verified all 11
counts and the spot check as correct on the live graph, going further than
this report by scanning all 13,574 `Card` vertices (not a sample) and
confirming zero have a blank `customer_id` today. It flagged one real
**Major** finding: the live patch applied to those 21 stub vertices during
the original session (see "A genuine dataset edge case" above) was never
captured as committed code — `git grep` for `stub`/`patch` found nothing,
so `scripts/load_data.py` run today from an empty graph would reproduce
the 21 blank-`customer_id` stubs, not the corrected state actually on the
live graph. That's a real gap between "what was done" and "what the script
does," fixed here.

### What changed

- **`src/schema/loading_jobs.py`**: added two new pieces, wired into
  `run_all_loading_jobs` as a final step after the `load_closed_cases` job
  (the ON_CARD edge load is what creates the stubs in the first place, so
  the backfill must run after it):
  - `customer_id_from_card_id(card_id: str) -> str` — the same
    `card_id.split("-K")[0]` convention `card_ids.py` already relies on
    internally, kept in `loading_jobs.py` (not `card_ids.py`) since it's a
    loading-time backfill helper, not part of Task 3's card_id
    *resolution* logic. `card_ids.py` remains untouched (confirmed again:
    `git diff` for this change touches only `loading_jobs.py` and
    `tests/`).
  - `backfill_stub_card_customer_ids(tg) -> list[str]` — fetches every
    `Card` vertex (`tigergraph__get_nodes`, limit 50,000, comfortably
    above the current 13,574), finds any with a blank `customer_id`
    attribute, backfills that attribute from the card_id's own prefix via
    `add_nodes`, and adds the corresponding `OWNS` edge via `add_edges`.
    If nothing is blank, it prints a one-line no-op message and returns
    `[]` without issuing any write calls.
  - `run_all_loading_jobs` now calls this automatically as its last step,
    so `scripts/load_data.py` run fresh today reproduces the exact
    corrected state that's on the live graph right now — no manual
    follow-up required.

### Idempotency verification (live, not just by inspection)

1. Ran `backfill_stub_card_customer_ids` directly against the current,
   already-corrected live graph: found zero blank-`customer_id` Cards,
   printed the no-op message, returned `[]`. `Card` count (13,574) and
   `OWNS` count (13,574) unchanged before/after — confirms it's a true
   no-op against already-correct data, not just "doesn't crash."
2. To verify the *patching* path (not just the no-op path) is itself
   idempotent, manually created a synthetic stub (`Card` vertex
   `ZZTEST99-K1` with `customer_id=""`, unrelated to any real data), then
   ran the function twice: the first run found and patched it
   (`customer_id` correctly backfilled to `ZZTEST99`, confirmed via a
   direct `get_node` read), the second run found zero stubs (the vertex no
   longer matched the blank-`customer_id` filter) and made no write calls.
   Deleted the synthetic `Card`/`Customer` test vertices afterward via
   `tigergraph__delete_node` — no residue left in the graph.

### Tests

Added `tests/test_loading_jobs.py` (5 new tests, all passing; full suite
`PYTHONPATH=. .venv\Scripts\python -m pytest tests/` — 29/29 passed,
including the two live-TigerGraph tests in `test_tg_client.py`, confirming
nothing else regressed):

- `test_customer_id_from_card_id_basic` / `_handles_double_digit_suffix` —
  the pure parsing logic, no live TigerGraph needed.
- `test_customer_id_from_card_id_matches_card_ids_module_convention` —
  cross-checks this function stays byte-for-byte consistent with
  `card_ids.py`'s own `card_id.split("-K")[0]` convention (via
  `build_card_id_map`'s `connected_card_ids` parsing), since it's patching
  vertices created from those exact same card_id strings.
- `test_backfill_stub_card_customer_ids_patches_only_blank_ones` — mocks
  `tg.call` (no live server) to verify: only the two blank/missing-key
  entries get patched (a third, already-populated entry is left alone),
  the derived `customer_id` values are correct, and the `OWNS` edge
  payloads are correctly shaped.
- `test_backfill_stub_card_customer_ids_is_a_noop_when_nothing_blank` —
  mocks `tg.call` to confirm that when nothing is blank, only the single
  read (`get_nodes`) call happens and no write calls are made — this is
  what makes re-running the function against an already-correct graph
  safe, verified here at the unit level in addition to the live check
  above.

### Commit

`b4704e5` — `fix: reproduce the 21 stub-Card customer_id patch as
committed code`. Adds the backfill function to
`src/schema/loading_jobs.py` and `tests/test_loading_jobs.py`. Local
commit only on `tigergraph-fraud-agent`, not pushed. (Note: an unrelated
commit `dd73361`, "Cascade Task 7's raw-INSERT fix through Tasks 8, 9,
12", landed on this branch from a separate concurrent session between the
original Task 7 commit and this fix; it only touches the plan document,
not `loading_jobs.py`/`tests/`, so it did not conflict with this change.)

### Status

**DONE.** The Major reproducibility gap is closed: `run_all_loading_jobs`
now reproduces the corrected state automatically, the fix is verified
idempotent both live (against the real graph, in both the no-op and
active-patch cases) and at the unit-test level, and the full test suite
passes. The two Minor notes from the review (no broader test coverage for
`loading_jobs.py`'s GSQL-generation/batching logic; the `validLine=0`
retry being a mitigation rather than a root-cause fix) were explicitly
flagged by the coordinator as not required this round and are left as-is,
consistent with the review's own assessment that neither blocks
completion.
