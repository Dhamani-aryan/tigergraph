# Task 13 Re-review: SAR narrative "when" element fix

**Scope:** scoped re-review of the fix round only (commit `3736cd0`, 2 files, 6 insertions) for the
single Medium finding raised in `task-13-review.md`: the SAR narrative never mentioned a date
despite the README requiring "when (dates)" as one of six required elements.

**Verification method:** read the actual diff and the current `src/agent/sar_writer.py` and
`tests/test_sar_writer.py`; cross-checked against `src/run/run_case.py`'s `activity_dates`
construction; ran `tests/test_sar_writer.py -v` live (Groq); independently re-ran
`write_sar_narrative` with the same fixture outside pytest and read the raw output directly.

## Verdict: ADDRESSED

1. **Source change matches the claimed fix and the format used elsewhere.**
   `src/agent/sar_writer.py` line 19 now adds
   `f"Date: {str(case_row.get('opened_at', ''))[:10]}\n"` to the prompt, right after the
   `Customer:`/`Card:` line and before `Pattern:`. This uses `.get(..., '')` (safer than a direct
   index, though `run_case.py` line 95 already accesses `case_row["opened_at"]` directly, so the
   field is guaranteed present on the only real call path) and the same `[:10]` truncation that
   `run_case.py` line 95 uses to build `SAR.activity_dates`
   (`[str(case_row["opened_at"])[:10]] * 2`). Both derive from the same `opened_at` field and the
   same YYYY-MM-DD truncation, so the narrative's date and `activity_dates` are now consistent by
   construction, exactly as claimed.

2. **Test fixture and assertion are legitimate, not tautological.** `tests/test_sar_writer.py` adds
   `"opened_at": "2016-11-12 00:46:24"` to the fixture (previously absent) and
   `assert "2016" in narrative`. 2016 is the only year appearing anywhere in the test's inputs, so a
   pass genuinely demonstrates the date reached the prompt and the model, not a coincidental match.

3. **Ran the live test myself:**
   ```
   tests/test_sar_writer.py::test_narrative_mentions_key_facts PASSED [100%]
   ============================== 1 passed in 2.88s ==============================
   ```

4. **Read the actual generated narrative directly** (re-ran `write_sar_narrative` outside pytest,
   same fixture, live Groq call, output written to a file and read back to avoid relying on the
   test's boolean assertion alone):

   > "On 2016-11-12, the customer identified as C04570 attempted a series of transactions using card
   > C04570-K1. The activity began with three small authorizations that were quickly followed by a
   > larger purchase. The larger purchase was transaction 3450629 for $100.09 made through an online
   > channel. A real-time fraud model scored this transaction at 0.57, triggering a review. The
   > pattern of activity matches a card-testing scenario, where low-value attempts are used to
   > verify card validity before a higher-value charge. The model assigned a fraud probability of
   > 0.86, indicating a high likelihood of illicit use. Because the sequence of small authorizations
   > followed by a larger online purchase aligns with known testing behavior, the activity is deemed
   > suspicious."

   The narrative opens with **"On 2016-11-12,"** — the real, specific date from the fixture's
   `opened_at`, in the exact `YYYY-MM-DD` format `activity_dates` uses — not a generic placeholder
   ("recently", "on the date in question", etc.) and not a hallucinated date. This closes the gap
   the original review flagged: the "when" element of the six required SAR elements is now
   genuinely grounded and consistent with `SAR.activity_dates`.

## New breakage: none

- `opened_at` is a schema-defined field on every case row (`src/schema/build_schema.py` line 33,
  `src/schema/loading_jobs.py` line 124) and is already accessed unconditionally (no `.get`) at
  `run_case.py` line 95 on the same code path that calls `write_sar_narrative`. So `sar_writer.py`'s
  new `.get('opened_at', '')` can never actually hit its `''` fallback in production — it's a purely
  defensive no-op, not a new failure mode.
- No other callers of `write_sar_narrative` exist outside `run_case.py` and the two test files
  checked, so the signature/behavior change (same signature, just uses an already-available dict
  key) has no other call sites to break.
- Diff is exactly as sized/described: 2 files, 6 insertions, 0 deletions, no unrelated changes.

## Summary

| Check | Result |
|---|---|
| Fix matches claim (adds `opened_at`, `[:10]`-truncated, to prompt) | Confirmed by source read |
| Format matches `run_case.py`'s `activity_dates` truncation | Confirmed identical (`[:10]` on the same field) |
| `tests/test_sar_writer.py -v` (live) | PASSED |
| Generated narrative genuinely contains a real date, not a placeholder | Confirmed — "On 2016-11-12," opens the narrative |
| New breakage from this change | None found |

**Overall: ADDRESSED. No new breakage.**
