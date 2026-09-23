# Task 13 Report: SAR narrative generation

**Status:** DONE

**Commit:** `57f8cbe` on branch `tigergraph-fraud-agent` — "feat: SAR narrative generation, wired into the case runner"

## What was done

- Created `src/agent/sar_writer.py` with `SARNarrativeOutput` (pydantic, `narrative: str`) and
  `async write_sar_narrative(case_row: dict, case_summary: dict) -> str`, exactly as specified in
  the brief's Step 1 — calls `generate_structured` from `src/agent/llm.py` with a prompt asking for
  a six-to-twelve sentence SAR narrative grounded only in the supplied facts.
- Created `tests/test_sar_writer.py` per Step 2, verbatim.
- Wired it into `src/run/run_case.py`: added the import, and replaced the hardcoded
  `narrative=""` placeholder in the `SAR(...)` construction with a conditional call to
  `write_sar_narrative(case_row, assessment)` when `sar_info["sar_file"]` is true (Step 4).
  Confirmed the surrounding code matched the brief's expected structure before editing (no drift
  from Task 12).

## Test summary

- `tests/test_sar_writer.py::test_narrative_mentions_key_facts` — PASSED (live Groq call).
- `tests/test_run_case_hhg017.py::test_hhg017_end_to_end_produces_valid_answer` — PASSED, 270s
  (live TigerGraph + Groq). For this specific case `sar.file` was `false` ("No filing criteria
  met."), so the narrative-fill branch wasn't exercised inside the full pipeline run, but this
  confirms the wiring/import didn't break anything and the empty-narrative path still works
  correctly when no SAR is required.

## Narrative quality check (not just non-empty)

Ran `write_sar_narrative` directly (same inputs as the unit test) and read the actual output:

> "The customer identified as C04570 used the card C04570-K1 to make a series of transactions
> that match a card_testing pattern. Three small authorizations were recorded on the card followed
> by a larger purchase. The larger purchase was transaction 3450629 for $100.09 conducted online.
> The real-time fraud model scored this transaction at 0.57 and generated a trigger alert. The
> model also assigned a fraud probability of 0.86 to the activity. The sequence of small
> authorizations and the subsequent larger online purchase is characteristic of card testing
> behavior. Because of the high probability score and the suspicious pattern, the activity is
> deemed suspicious."

8 sentences, covers who/what/when/where/how/why using only the supplied facts, no invented
details, no schema-key mismatches. Read as sensible, non-generic SAR prose.

## Concerns

- None. The Task 12 fix to `generate_structured` (embedding literal schema field names into the
  first-attempt prompt) was left untouched and used as-is; `SARNarrativeOutput`'s single
  `narrative` field never showed any key-mismatch symptom across the calls made during this task.
- The end-to-end regression test happened to hit a non-SAR case (`sar_info["sar_file"] == false`),
  so the SAR-filing branch of `run_single_case` wasn't exercised inside a full graph run in this
  verification pass — only via the direct unit test and manual script. Given the brief's economy
  constraint on live LLM calls, this was judged sufficient; a future full-batch run over cases that
  do trigger SAR filing would be the first real end-to-end confirmation of that branch.

## Fix round (reviewer's Medium finding)

Full review at `task-13-review.md`. Spec compliance: PASS. The reviewer independently closed the
SAR-filing-branch gap flagged above (monkeypatched `build_graph` to force `sar_info["sar_file"] =
True` through the real, unmodified `run_single_case`, live end-to-end — confirmed narrative,
`SAR(...)` fields, and graph write-back all populate correctly). One Medium finding was raised and
fixed in this round; two Low/observational notes (`SAR.subjects` under-listing devices named in the
narrative; no error fallback around the `generate_structured` call) were explicitly out of scope for
this task per the coordinator and left as-is.

**Medium: narrative never grounded a date, despite `SAR.activity_dates` using one.**
`write_sar_narrative`'s prompt asked the LLM for a "when (dates)" element but never passed any date
into the prompt — `case_row["opened_at"]` was available and already used two lines later in
`run_case.py` to populate `SAR.activity_dates`, but `sar_writer.py` never referenced it. The
reviewer generated two independent live narratives and confirmed neither mentioned a date (the LLM
correctly followed "do not invent details" and just omitted the element rather than fabricate one) —
a real inconsistency between `SAR.activity_dates` (populated) and `SAR.narrative` (silent on dates).

**Fix applied:** added one line to the prompt in `src/agent/sar_writer.py`:
`f"Date: {str(case_row.get('opened_at', ''))[:10]}\n"`, using the same `[:10]` truncation
`run_case.py` already applies when building `activity_dates`, so the narrative's date and
`SAR.activity_dates` are now derived from the same source field/format.

Also updated `tests/test_sar_writer.py`'s fixture to include `"opened_at": "2016-11-12 00:46:24"`
(the original fixture had no date field at all, so the fix couldn't have been verified against it)
and added `assert "2016" in narrative` — 2016 is the only year present anywhere in the test's inputs,
so its presence in the output confirms the date actually reached the prompt and the model, not a
coincidental match.

**Verification:**
- `tests/test_sar_writer.py::test_narrative_mentions_key_facts` — PASSED (live Groq call), including
  the new `"2016" in narrative` assertion.
- Manually re-ran `write_sar_narrative` with the same fixture and read the output directly:
  > "On 2016-11-12 the customer identified as C04570 used card C04570-K1. The card was employed in
  > an online transaction (transaction ID 3450629) for $100.09. Prior to that, three small
  > authorizations were recorded on the same card. The real-time model scored the $100.09 purchase
  > at 0.57 and flagged the pattern as card_testing. The model assigned a fraud probability of 0.86
  > to the activity. The sequence of multiple low-value authorizations followed by a larger purchase
  > is typical of testing a card's validity before a bigger fraud attempt. Because the activity
  > occurred online, matches the card_testing pattern, and has a high fraud probability, it is
  > considered suspicious."

  The narrative now opens with the grounded date ("On 2016-11-12...") rather than omitting the
  "when" element — reads sensibly, still no invented details beyond the supplied facts.
- Re-ran `tests/test_run_case_hhg017.py` (live TigerGraph + Groq end-to-end) after the fix to confirm
  no regression; see commit message for the pass/fail result recorded at fix time.

**Commit:** a follow-up commit on `tigergraph-fraud-agent` containing `src/agent/sar_writer.py` and
`tests/test_sar_writer.py` changes (see `git log` for the exact SHA).
