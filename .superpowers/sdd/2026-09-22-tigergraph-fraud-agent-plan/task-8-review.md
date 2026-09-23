# Task 8 Review: Derived entities (DeviceProfile) + pipe-separated edges + manual case checkpoint

**Reviewed:** commit `621ec99` on branch `tigergraph-fraud-agent`
**Method:** Read brief/report/diff/checkpoint/README, then independently re-ran live queries against the actual TigerGraph instance (same `.env`/`tg_client.py` the implementation uses) and independently recomputed every count from the raw CSVs in Python, without consulting the report's numbers first.

## Spec compliance verdict: PASS

All five brief steps are satisfied:

- **Step 1** (`src/schema/derive_entities.py`): present, matches the brief's provided code essentially verbatim (docstrings added, no logic changes). `device_id_for`, `load_device_profiles`, `_flush_device_batch`, `load_closed_case_multi_edges` all match the specified signatures and batching behavior (500-row flush, `INVOLVES`/`CONNECTED_TO` never mixed in one `add_edges` call).
- **Step 2** (`scripts/derive_entities.py`): present, matches the brief verbatim.
- **Step 3** (run it): live run confirmed completed successfully — see "Independent verification" below for count cross-checks.
- **Step 4** (manual checkpoint, `docs/manual-case-checkpoint.md`): present, covers every required element — raw transaction, card history, DeviceProfile linkage, and a by-hand verdict/pattern/recommended action under the fraud policy. Go/no-go conclusion stated explicitly ("Go").
- **Step 5** (commit): `621ec99`, working tree clean, contains exactly the brief's three required files plus one reasonable addition (`tests/test_derive_entities.py`, mirroring the existing `tests/test_loading_jobs.py` convention — not required but not scope creep either).

One legitimate, disclosed deviation: the brief's literal `.venv\Scripts\python scripts\derive_entities.py` invocation fails with `ModuleNotFoundError: No module named 'src'` unless run with `PYTHONPATH=.`, which is this repo's established convention (`pytest.ini`, Task 7's report). Correctly flagged as `DONE_WITH_CONCERNS` rather than silently worked around.

## Code quality verdict: PASS, no blocking issues

**High/Medium severity:** none found.

**Low severity / nits:**
1. **`docs/manual-case-checkpoint.md`, "Card history" section — internal inconsistency.** The text says `addr1` 204.0 "recurs at least 4 other times in the card's history (7/28, 7/29, 10/27)" but only three dates are listed and my independent pull of the raw `transactions.csv` history for this card found exactly three prior occurrences of `addr1=204.0` (2016-07-28, 2016-07-29, 2016-10-27) outside the November 11 cluster. The "4" appears to be a miscount or leftover edit; the substantive conclusion ("204.0 is not a new region for this card") is correct regardless. Cosmetic, not load-bearing.
2. **No dedup within a 500-row `DeviceProfile` batch before `add_nodes`.** Not a bug — `add_nodes`/`add_edges` are REST++ upserts, confirmed idempotent by the exact-match recount below — but worth a one-line comment for the next reader, since it looks at first glance like it could double-insert.
3. `load_device_profiles`'s skip condition (`not device_info and not os_ and not browser`) ignores `screen` — i.e. a row with only `id_33` populated and the other three blank is still skipped and produces no `DeviceProfile`. This is inherited unchanged from the brief's own Step 1 code, not something the implementer introduced, so it isn't a code-quality fault of this task, but it's worth flagging forward since it means `device_id_for`'s 4-field hash key isn't quite matched by the skip check's 3-field test.

No dead code, no swallowed exceptions, no missing error handling beyond what `TigerGraphMCP.call`'s existing failure-detection already provides (confirmed by reading `src/tg_client.py`: it explicitly raises on both MCP `is_error` and envelope `"success": false`, so a failed batch mid-load would not be silently ignored). Tests are meaningful (determinism of the hash, the skip branch, and the never-mixed-batch invariant), not tautological.

## Independent live verification

All numbers below were produced by me, live, against the real TigerGraph instance and the real CSVs — not copied from the report.

| Check | My independent result | Report's claim | Match |
|---|---|---|---|
| `DeviceProfile` vertex count (live `get_vertex_count`) | 9,705 | 9,705 | ✅ |
| `DeviceProfile` distinct fingerprints (recomputed from `identity.csv` in Python) | 9,705 | 9,705 | ✅ |
| `FROM_DEVICE` edge count (live) | 140,784 | 140,784 | ✅ |
| `identity.csv` row recount: total / skipped (no device signal) / expected edges | 144,432 / 3,648 / 140,784 | same | ✅ |
| `INVOLVES` edge count (live) | 14,955 | 14,955 | ✅ |
| `CONNECTED_TO` edge count (live) | 92 | 92 | ✅ |
| `closed_cases_history.csv` recount: rows / sum(txn_ids) / sum(connected_card_ids) | 5,565 / 14,955 / 92 | same | ✅ |
| `ON_CARD` edge count (unaffected, sanity check) | 5,565 | 5,565 | ✅ |
| `Card` primary-id-as-attribute query (`WHERE Card.card_id == "..."`) | Fails live: `Semantic Check Fails: Vertex_Name.Attribute "Card.card_id" syntax may not be used...` | Fails with same class of error | ✅ |
| `Transaction` primary-id-as-attribute query | Succeeds, returns the exact transaction (amount 100.09, risk_score 0.57, etc.) | Succeeds | ✅ |
| `get_node_edges` on `DeviceProfile` (target-only vertex) | Returns 0 outgoing edges even though 621 `FROM_DEVICE` edges target it | Same claim | ✅ |
| `get_neighbors(Card, C04570-K1, MADE)` count | 59 | 59 | ✅ |
| `reverse_FROM_DEVICE` in an installed query | Fails live: `SEM-40: reverse_FROM_DEVICE is not a valid edge type` | Same error code cited | ✅ |
| Device fingerprint `Dec9ef04aa023` — sharing transactions / distinct customers (installed probe query, `{Transaction.*}` forward-traversal + `WHERE dp == d` pattern) | 621 / 299 | 621 / 299 | ✅ |
| `device_id_for("Windows","Windows 10","chrome 65.0","1920x1080")` | `Dec9ef04aa023` | same | ✅ |
| `ClosedCase CC-1383` raw attributes | `outcome: cleared`, `pattern: none`, card `C04570-K1`, customer `C04570`, note matches verbatim | same | ✅ |
| Card `C04570-K1` full transaction history (pulled all 59 rows directly from `transactions.csv`) | Min amount $34.01, no sub-$5 charges anywhere in the card's entire 6-month history, three ~$100 transactions on 2016-11-11 (99.96–100.09) exactly as the checkpoint's table shows | same pattern description | ✅ |
| `pytest tests/` | 33 passed | 33 passed (29 prior + 4 new) | ✅ |
| `git status --short` after commit | clean | clean | ✅ |

Every number the report states was independently reproduced. I did not find a single fabricated or rounded-up figure. The schema-creation code (`src/schema/build_schema.py`) was also read directly, independent of the checkpoint's own description, and confirms both claimed gaps at the source: only `Transaction` is declared `WITH primary_id_as_attribute="true"`; no `CREATE DIRECTED EDGE` statement in the file declares a `REVERSE_EDGE`. These are not inferred from error messages alone — they are visibly true in the schema DDL.

## Assessment of the "621 transactions / 299 customers = noise" finding

**Real, and independently reproduced exactly (621/299).** I additionally cross-referenced this device cluster against `ClosedCase` outcomes to sanity-check the "noise, not ring" call, since the checkpoint's own justification for calling it noise was essentially "299 is a lot of unrelated customers," not a fraud-rate comparison:

- Of the 299 customers sharing device `Dec9ef04aa023`, 1,176 closed cases exist among them: 1,037 confirmed fraud, 139 cleared → **88.2%** confirmed-fraud rate.
- Baseline across the entire `closed_cases_history.csv` (5,565 cases): 4,665 confirmed / 900 cleared → **83.8%** confirmed-fraud rate.

88.2% vs. 83.8% is not a meaningfully elevated rate — and the baseline itself is already very high because `closed_cases_history.csv` is a curated set of investigations (analysts already triaged these to be mostly real), not a random population sample. This supports, rather than undermines, the checkpoint's "noise, not a ring signal" conclusion. It's worth noting this specific fraud-rate cross-check is *not* in the checkpoint doc itself — the checkpoint's stated reasoning is volume-based ("a common device-category collision... captures a device category, not a unique physical device") rather than rate-based. That's a defensible call for a manual by-hand pass (the rate-based version of this — `cluster_prior_fraud_rate` — is explicitly Task 8.5's job, not Task 8's), but it means the checkpoint's own writeup slightly overstates its evidentiary rigor on this specific point ("Sharing this device profile with 299 other customers is not meaningful... it is noise" is stated as a settled fact rather than the reasonable-but-unverified inference it actually was at checkpoint-writing time). This is a documentation-precision nit, not a wrong conclusion — my independent check shows the conclusion holds.

**Important, separate finding for Task 8.5:** this does confirm a real design concern the report calls out — `DeviceProfile`'s dedup key (`DeviceInfo`/`id_30`/`id_31`/`id_33`) captures a device *category* (OS + browser + screen resolution), not a unique physical device, so common combinations collide across hundreds of unrelated real people. Task 8.5's `SHARES_ORIGIN`/connected-components design needs to account for this — a naive "shares a `DeviceProfile`" edge will create enormous, meaningless components. This is exactly the kind of thing the checkpoint step is supposed to surface before more code is built on a wrong assumption, and it did.

## Assessment of the negative finding (no fraud at HHG-017): well-supported, not premature

I evaluated this on its own terms rather than taking the report's framing at face value.

**On the README's worked example being fictional, not ground truth:** this conclusion is correct, and it's checkable without trusting the implementer. The README's Answer Format example uses `case_id: "HHG-017"` but transaction IDs `T0412877`/`T0412878`/`T0412879`/`T0412883` and card IDs `C00377-K1`/`C00877-K1`. The README's own "columns we added" section states plainly that `TransactionID` was "disguised (new IDs...)" and the dataset's real IDs are numeric throughout (every transaction ID I pulled live and from the raw CSV — `3450629`, `3450436`, `3573010`, etc. — is purely numeric). A `T`-prefixed ID literally cannot occur in this dataset. The case pack's real `HHG-017` row (`case_pack.csv`, reproduced in the README's own case table) names flagged transaction `3450629`, card `C04570-K1`, customer `C04570` — which is exactly what the checkpoint investigated. The "it's a format illustration, not this case's real answer" claim is not a rationalization; it is directly verifiable from the README text itself and does not require trusting the implementer's judgment.

**On whether the investigation stopped too early:** I don't think so, for these reasons:
- The card's **entire 59-transaction, 6-month history** (which I pulled independently, not just the report's 3-row excerpt) has a minimum transaction amount of $34.01 — there is no sub-$5 (or even sub-$30) transaction anywhere on this card, at any time, which is the load-bearing fact for ruling out `card_testing` (Policy R5 requires "three or more tiny... authorizations, often under $5"). This is a complete, not cherry-picked, check.
- `id_15 == "Found"` (not "New") directly rules out `card_not_present_new_device` per the README's own pattern definition — this is a categorical fact, not a judgment call.
- The prior closed case on this exact card/customer (`CC-1383`) is independently confirmed `cleared`, with an analyst note about confirmed travel — this is real historical evidence pointing away from fraud, and it's the *only* prior case on this customer, so there's no cherry-picking among multiple priors.
- The device-sharing angle was checked (not skipped) and, as shown above, the "noise" call is statistically defensible once cross-referenced against baseline fraud rates.
- `addr1` 204.0 recurring on this card (3 independently-confirmed prior occasions, not a brand-new region) further weakens the case for anything unusual about the November 11 cluster.

Nothing in my independent pulls contradicts the checkpoint's verdict, and nothing suggests a more thorough pass would have found evidence the checkpoint missed — I pulled the full transaction history myself (not a windowed excerpt) specifically to check for this, since "did they only look at 3 rows and miss a testing pattern earlier in the history" was the most likely way this finding could be premature, and it isn't: no small-value transactions exist anywhere in the card's history.

The one place I'd push back slightly: the checkpoint states the "noise, not ring" conclusion about the shared device with more confidence than its own stated reasoning supports (volume alone, not a fraud-rate comparison) — see above. That's a precision issue in how the finding is written up, not a gap in the underlying investigation, and my own fraud-rate cross-check resolves it in the checkpoint's favor anyway.

**Conclusion:** the manual checkpoint's negative finding for HHG-017 is well-supported by the actual data, not a rationalization for a checkpoint that "didn't go as expected." The implementer appears to have genuinely investigated rather than pattern-matched to the README's example, which was the explicit point of this task.

## Summary

- **Spec compliance:** PASS — all 5 steps complete, one disclosed/reasonable deviation (`PYTHONPATH=.`).
- **Code quality:** PASS — no high/medium findings; three low-severity nits (a cosmetic "4 vs 3" inconsistency in the checkpoint doc, an unremarked-but-harmless lack of intra-batch dedup, and an inherited-from-brief minor mismatch between the hash key's 4 fields and the skip check's 3 fields).
- **Load counts:** all four (DeviceProfile, FROM_DEVICE, INVOLVES, CONNECTED_TO) independently verified live and against raw-CSV recounts — exact matches, no discrepancies.
- **Schema/query gaps (`primary_id_as_attribute`, missing `REVERSE_EDGE`):** both confirmed real by direct inspection of `src/schema/build_schema.py` and by independently reproducing the exact failing queries live (`Semantic Check Fails` on `Card.card_id`, `SEM-40: reverse_FROM_DEVICE is not a valid edge type`). These are genuine, not overstated, blockers for Task 8.5 and should be treated as confirmed prerequisites for whoever picks that task up.
- **HHG-017 negative finding:** independently assessed as well-supported, not premature. The "README example is fictional" claim is directly verifiable from the README text and dataset ID format, not merely asserted.

No blocking issues. This task can be marked DONE — the `DONE_WITH_CONCERNS` status in the report is appropriately conservative but every "concern" listed is a genuine, correctly-scoped forward-looking flag for Task 8.5, not a defect in Task 8 itself.
