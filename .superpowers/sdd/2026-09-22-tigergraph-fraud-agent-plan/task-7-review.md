# Task 7 Review: Bulk data loading into TigerGraph

Reviewed against: `task-7-brief.md`, `task-7-report.md`, `review-1e9f6fe..0bcffeb.diff` (commit `0bcffeb`), and the live TigerGraph workspace (`FraudInvestigation` graph on the Savanna-hosted instance referenced in `.env`). All verification queries below were run independently by this review via `.venv\Scripts\python`, not taken from the report.

## Spec compliance verdict: ✅ (with one reproducibility gap noted below, not blocking)

The brief's literal code (`tg.gsql()` with `DEFINE FILENAME "<path>"`, named `$"Col"` refs, raw `INSERT INTO VERTEX/EDGE`) does not work against this live server, for the three reasons the report gives — all independently plausible and each backed by a concrete, checkable symptom (parser rejection text, `validLine=0`, mis-parsed quoted rows). The brief itself explicitly designates its code as illustrative ("if you see a load error... fix and re-run"), and Task 7's actual deliverable is "populated graph. Task 8 ... depends on this having run successfully" — an outcome-oriented brief, not a literal-transcription one. Substituting `run_loading_job_with_data` for `tg.gsql()`, positional `$N` refs for named refs, and `add_nodes`/`add_edges` for raw `INSERT` are mechanism swaps that preserve every design constraint the brief actually cares about:

- **Card/OWNS/MADE use the resolved `card_id` from the start, never a bare `customer_id` or default-then-patch pattern.** Confirmed by reading `load_cards_and_made_edges` (`src/schema/loading_jobs.py:216-314`): `full_map` is built once via `card_id_for(cid, override_map)` for every customer (line 229), Phase 1 upserts `Card`/`OWNS` directly from `full_map.items()` (never from `customer_id` alone), and Phase 2's `MADE` edges look up `full_map[customer_id]` per transaction row (line 291) — the same resolved id, never the bare `customer_id`. No code path anywhere in this file constructs a `Card` vertex id via `f"{customer_id}-K1"` or similar as a default-then-fix step; `card_id_for`'s own `-K1` default (in `card_ids.py`, untouched) is the only place that string appears, and it's consulted before any vertex is created, not after.
- **Task 3's `card_ids.py` genuinely was not altered.** `git log --oneline -- src/schema/card_ids.py` shows exactly one commit, `8303f6d` (Task 3's own commit), and the reviewed range `1e9f6fe..0bcffeb`'s diffstat touches only `scripts/load_data.py`, `src/schema/columns.py`, and `src/schema/loading_jobs.py` — `card_ids.py` does not appear. Confirmed via `git diff`, not just the report's assertion.
- **`columns.py`'s diff is exactly the claimed `ts` fix, nothing broader.** The diff (`src/schema/columns.py`, 1 line added) adds only `"ts",` to the `_STRING_COLUMNS` set at line 19. No other line in the file changed.
- **Git hygiene**: local commit only (`0bcffeb`), on branch `tigergraph-fraud-agent`, nothing pushed (not independently re-verified here beyond reading `git log`, which shows no push-related state; consistent with the report).
- **`INVOLVES`/`CONNECTED_TO` correctly deferred to Task 8**, per the brief's own note.

### Live counts — independently verified (not the report's numbers)

Queried live via `tigergraph__get_vertex_count` / `tigergraph__get_edge_count`:

| Vertex/Edge | My independent count | Report's claim | Match |
|---|---|---|---|
| Customer | 13,553 | 13,553 | ✅ |
| Card | 13,574 | 13,574 | ✅ |
| Transaction | 590,742 | 590,742 | ✅ |
| EmailDomain | 59 | 59 | ✅ |
| BillingRegion | 332 | 332 | ✅ |
| ClosedCase | 5,565 | 5,565 | ✅ |
| OWNS | 13,574 | 13,574 | ✅ |
| MADE | 590,742 | 590,742 | ✅ |
| PURCHASER_EMAIL | 496,262 | 496,262 | ✅ |
| BILLED_IN | 525,003 | 525,003 | ✅ |
| ON_CARD | 5,565 | 5,565 | ✅ |

Every count matches the report exactly.

### Spot check — independently verified

`tigergraph__get_node(vertex_type="Card", vertex_id="C08623-K2")` returned:
```
{"v_id": "C08623-K2", "v_type": "Card", "attributes": {"customer_id": "C08623", "ring_cluster_id": "", "cluster_prior_fraud_rate": 0}}
```
Present, with `customer_id: "C08623"` — confirms Phase 1 resolved the non-default `-K2` override correctly rather than defaulting to `-K1`, exactly as the brief's Step 4 required.

### Additional independent checks (beyond what the report itself ran)

- Pulled **all 13,574** `Card` vertices (`get_nodes(vertex_type="Card", limit=50000)`, not a sample) and scanned locally: **zero** have a `card_id` missing the `-K` suffix (i.e., no card was ever created keyed by a bare `customer_id` — the single most important design constraint, confirmed across the entire dataset, not spot-checked), and **zero** have a blank `customer_id` attribute (confirms the 21 auto-created GSQL "stub" vertices the report describes patching are in fact fully populated in the live graph today).
- Pulled all 13,574 `OWNS` edges and independently counted customers with more than one outgoing `OWNS` edge: **21**, matching the report's claim exactly. Spot-checked `Customer C02575`'s `OWNS` edges directly: both `C02575-K1` and `C02575-K2` present, matching the report's cited example.

## Code quality verdict: ✅ with findings

### Major
- **The "21 stub vertex" fix is not reproducible from committed code.** The report describes patching 21 auto-created `Card` stub vertices' blank `customer_id` and adding their `OWNS` edges as a live, one-off action taken during the session ("patched all 21 stub vertices' `customer_id` attribute... and added their `OWNS` edges"). I independently confirmed the live graph genuinely has this fix applied (zero blank-`customer_id` Cards, `OWNS` count of 13,574 matching `Card` count exactly). However, `git status` shows a clean working tree and neither `loading_jobs.py` nor any other committed file contains logic to detect or patch these stub vertices — I grepped the full `src/` tree for `stub`/`patch`/`21` and found nothing. This means **`scripts/load_data.py`, run today from an empty graph, would not reproduce the current correct state**: `closed_cases_loading_job_gsql`'s `ON_CARD` edge load would still auto-create the same 21 stub vertices (with blank `customer_id`, no `OWNS` edge), and nothing in the committed code would fix them afterward. The report's own "Concerns for downstream tasks" section flags two issues but not this one — the gap between "what was done live" and "what the script does" is a real omission that should be closed (either a short idempotent post-load patch step added to `run_all_loading_jobs`, or at minimum an explicit call-out in the report that re-running from scratch requires a manual follow-up). This is a correctness-of-reproducibility issue, not a correctness-of-current-data issue — the live graph is fine right now, but the deliverable (script + graph) is not self-consistent.

### Minor
- **No automated tests for `loading_jobs.py`.** `tests/` has coverage for `card_ids.py`, the policy engine, schemas, and `tg_client.py`, but nothing exercises `transactions_loading_job_gsql`/`closed_cases_loading_job_gsql`'s positional-column-index generation or `_run_job_from_csv`'s batching/retry logic, even at a unit level (e.g. mocking `tg.call` to verify `full_map` is threaded correctly into `MADE` edge payloads, or that `_read_header`/`col_index` positional mapping is correct for a synthetic header). Given the brief didn't explicitly ask for tests here and the logic was validated end-to-end against live data (a stronger signal than a unit test for this particular class of bug — GSQL/server quirks), this is a nice-to-have, not a blocker.
- **`_run_job_from_csv`'s `validLine=0` retry-and-raise is explicitly labeled a mitigation, not a root-cause fix**, both in the code comment and the report's own "Concerns" section. This is appropriately transparent — it raises loudly on a second failure rather than silently succeeding, which is the right conservative default given the root cause is genuinely unknown (suspected server-side eventual-consistency lag, observed once). Adequate as shipped; nothing more is needed before this task can be considered complete, but a later task hitting the same pattern should not assume it's fully solved.
- Broad `except Exception` around the `validLine` extraction (lines ~180-184) is intentionally permissive ("don't block on it, just skip the check") — reasonable given it's parsing an external tool's response shape defensively, but worth noting it would also silently swallow a genuinely malformed response rather than surfacing it. Low risk given the explicit `valid_lines is None` fallback still lets the load proceed rather than masking a real failure.

### Assessment of the report's own two flagged concerns
1. **Card not strictly 1:1 with Customer (13,574 vs 13,553).** Independently confirmed as a genuine, verified data characteristic (21 customers each legitimately have two historical cards per `closed_cases_history.csv`), not a loading defect — the `C02575` example checks out exactly. This is adequately handled: it's real data, correctly loaded, and clearly documented for downstream consumers who might assume a 1:1 `OWNS` cardinality. No further action needed on this one.
2. **`validLine=0` retry-and-raise being a mitigation, not a root-cause fix.** Adequately handled as shipped, per above — it converts a silent no-op into a loud failure, which is the correct posture when the root cause is genuinely not understood. Nothing more is needed before this task can be considered complete; it's appropriately flagged for whoever touches loading code next (Task 8 does exactly that).

Neither of the report's own two flagged concerns needs more work before Task 7 can be considered complete. The concern this review adds (the 21-stub-patch reproducibility gap) is more significant than either — it means the *script*, not just the *data*, has an unaddressed edge case — but it does not invalidate the currently-loaded graph, which is what Task 8 and beyond actually depend on and which this review independently confirmed is correct in its present state.

## Overall

Spec compliance: ✅. Code quality: ✅ with one Major reproducibility finding (21-stub-vertex patch not captured in committed code) that should be fixed before anyone re-runs `load_data.py` from an empty graph, and two Minor notes (no unit tests for loading_jobs.py; mitigation-not-root-cause status of the validLine guard, already transparently documented). The live graph itself — the actual dependency for Task 8 onward — was independently verified correct across every requested count and the required non-default-suffix spot check, plus additional checks (full-population `-K`-suffix scan across all 13,574 Cards, independent 21-customer multi-card count) beyond what the report itself ran.
