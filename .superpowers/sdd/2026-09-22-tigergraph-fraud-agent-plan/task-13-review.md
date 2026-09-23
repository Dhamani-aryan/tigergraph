# Task 13 Review: SAR narrative generation

**Reviewer verification method:** read the brief, report, and diff; read
`src/agent/sar_writer.py`, `src/run/run_case.py`, `src/policy/engine.py`,
`src/policy/models.py`, and `src/agent/llm.py` directly (not just the diff);
read the README's SAR section verbatim; ran `tests/test_sar_writer.py -v`
live; generated two independent live narratives via `write_sar_narrative`
directly to assess prose quality; and — to close the implementer's own
flagged gap — wrote a standalone script that monkeypatches `build_graph` to
return a synthetic `final_state` with `sar_info["sar_file"] = True` (an R6
shared-device scenario, `pattern="undocumented"`, `fraud_probability=0.91`)
and ran the **real, unmodified** `run_single_case` against it, live, end to
end.

## Spec compliance verdict: PASS

- `src/agent/sar_writer.py` matches the brief's Step 1 exactly: `SARNarrativeOutput(BaseModel)`
  with a single `narrative: str` field; `write_sar_narrative(case_row, case_summary)` builds a
  prompt asking for a six-to-twelve sentence narrative covering who/what/when/where/how/why, and
  calls `generate_structured(prompt, SARNarrativeOutput)`.
- `tests/test_sar_writer.py` matches Step 2 verbatim.
- The `run_case.py` edit matches Step 4 exactly: `narrative = ""` is only overwritten by a call to
  `write_sar_narrative(case_row, assessment)` when `sar_info["sar_file"]` is true, and the `SAR(...)`
  construction uses that `narrative` variable in place of the old hardcoded `narrative=""`
  placeholder. No LLM call happens on the non-SAR path.
- The prompt's who/what/when/where/how/why wording is essentially a verbatim restatement of the
  README's SAR requirement (README.md line 343: *"who (customer, cards, merchants, devices), what
  happened, when (dates), where (locations, channels), how it was carried out, and why it is
  suspicious... Six to twelve sentences"*) — the prompt text in `sar_writer.py` lines 14-17 mirrors
  this almost word for word, including the "six to twelve sentences" bound.
- Diff is minimal and mechanical (3 files, 51 insertions, 1 deletion) and matches the brief with no
  unrequested scope creep.

## SAR-filing branch: NOW GENUINELY EXERCISED END TO END (implementer's flagged gap is closed)

The implementer was right to flag this as unresolved: `tests/test_run_case_hhg017.py` hits a case
where `sar_info["sar_file"]` is `false`, so inside that specific test the `if sar_info["sar_file"]:`
branch in `run_case.py` line 86 was never taken, and only the direct unit test / manual script
exercised `write_sar_narrative` in isolation.

I closed this myself by forcing the branch through the real code path rather than mocking
`write_sar_narrative` itself. The script monkeypatches only `run_case_mod.build_graph` (so no real
TigerGraph traversal/LLM assessment call is needed to *construct* the scenario) to return a fixed
`final_state` with `final_policy_result = {"sar_file": True, "sar_reason": "R6: shared
device/region/email links this to other cards.", "actions": [...FILE_REPORT...]}` and a plausible
`assessment` (pattern `"undocumented"`, `fraud_probability=0.91`, two evidence claims naming a
shared device and two cards). Everything else — `run_single_case`'s real, unmodified control flow,
the real `write_sar_narrative` call (a live Groq call), the real `SAR(...)` construction, the real
`AnswerFile` assembly, and even the real `_write_case_to_graph` write-back — ran as production code.

Result, live:

```
sar.file         : True
sar.reason       : R6: shared device/region/email links this to other cards.
sar.subjects     : ['C09999', 'C09999-K1']
sar.total_amount : 4200.0
sar.activity_dates: ['2016-11-12', '2016-11-12']

sar.narrative:
"Customer C09999 used card C09999-K1 in a transaction that was authorized from device DVC-771.
Within 90 seconds, a second authorization was recorded for card C08888-K1 from the same device
DVC-771. The two cards are assigned to different customers and there is no prior shared history
between those accounts. The detection system flagged a shared-device cluster across the two cards,
triggering an alert. The pattern of activity is undocumented in the system's known legitimate
behavior models. The fraud probability assigned to the event is 0.91, indicating a high likelihood
of illicit use. These combined factors make the activity suspicious and warrant further
investigation."
```

This confirms, with a live call, that:
1. `sar_info["sar_file"] = True` correctly triggers the `write_sar_narrative` call (it was skipped
   entirely in every prior test run, including Task 12's regression test).
2. The narrative is genuinely written into the final `AnswerFile`'s `sar.narrative` field — not
   left empty, not a placeholder.
3. The narrative is grounded only in the facts I injected (device `DVC-771`, cards
   `C09999-K1`/`C08888-K1`, probability `0.91`) — no hallucinated transaction IDs, dates, or
   amounts beyond what was supplied.
4. The surrounding `SAR(...)` fields (`subjects`, `total_amount_usd`, `activity_dates`) populate
   correctly alongside the narrative when the branch fires.

**Verdict: the gap the implementer flagged is now closed.** The SAR-filing branch is demonstrated
working end to end through the real `run_single_case` function, not just via
`write_sar_narrative` called directly or a manual script bypassing the runner.

## Code quality verdict: PASS, with one Medium finding worth fixing

### Medium: the narrative's "when" component is never actually grounded in a date

Per README.md line 343, the SAR narrative must cover "when (dates)" as one of its six required
elements, and the `sar_writer.py` prompt (lines 14-15) explicitly asks the LLM for it. But
`write_sar_narrative`'s signature only receives `case_row` and `case_summary` (`assessment`), and
the prompt only ever interpolates `case_row['customer_id']`, `case_row['card_id']`,
`case_summary['pattern']`, `case_summary['fraud_probability']`, `case_summary['evidence_claims']`,
and `case_row.get('trigger_text', '')` — **no date field is ever passed in**, even though
`case_row['opened_at']` is sitting right there and is already used two lines later in
`run_case.py` (`activity_dates=... [str(case_row["opened_at"])[:10]] * 2`).

I confirmed this is a real, reproducible effect rather than a one-off: I generated narratives twice
independently (once via the unit test's fixture inputs, once via my synthetic end-to-end run) and
**neither ever mentioned an actual date** — the LLM correctly followed the "do not invent details"
instruction and simply omitted the "when" element rather than fabricate one, since it was given
nothing to ground it. E.g. the unit-test-input narrative says "Over a short period, the card was
presented for three small authorizations..." with no date; the end-to-end narrative says "Within 90
seconds, a second authorization was recorded..." with no date either.

Net effect: the `SAR.activity_dates` field (correctly populated with `case_row["opened_at"]`) and
the `SAR.narrative` field (which never mentions that date, or any date) are inconsistent with each
other inside the same regulatory document — a real gap against the README's explicit six-element
requirement, and something a regulator reading the narrative prose alone would notice is missing.

**Fix:** pass a date (e.g. `case_row.get("opened_at")`) into the prompt in `sar_writer.py`, e.g.
adding a line like `f"Date: {str(case_row.get('opened_at', ''))[:10]}\n"` alongside the existing
`Customer:`/`Pattern:`/`Trigger:` lines.

### Low / observational (not blocking, not necessarily this task's scope)

- `SAR.subjects` is hardcoded in `run_case.py` (from Task 12's own brief-mandated Step 4 code,
  which Task 13 did not alter) to `[case_row["customer_id"], case_row["card_id"]]` only. The
  README defines `subjects` as "IDs of the customers, cards, **merchants, and devices** named in
  the narrative" — and the narrative can and does name devices (e.g. `DVC-771` in my synthetic
  test). This means `subjects` can under-list what the narrative actually names. This is a
  pre-existing structural choice from Task 12's brief, not something Task 13's diff introduced or
  was asked to fix, so I flag it only as an observation, not a Task 13 finding.
- `write_sar_narrative` has no error handling around the `generate_structured` call. If the live
  Groq call fails or exhausts `generate_structured`'s retries when `sar_info["sar_file"]` is true,
  `run_single_case` raises and the entire case fails rather than degrading to an empty/placeholder
  narrative. This is consistent with the rest of the codebase's current style (no fallback wrapping
  elsewhere either), so it's a pre-existing pattern rather than a Task-13-introduced regression, but
  worth noting since a SAR-required case is exactly the case you don't want to lose to a transient
  LLM hiccup.
- `sar_writer.py` has no comments, unlike the surrounding codebase's habit of documenting non-obvious
  decisions inline (e.g. `run_case.py`, `llm.py`, `policy/engine.py` are all heavily annotated).
  The file is simple enough that this isn't a real problem, just a minor style inconsistency worth
  a one-line docstring if this file is touched again.

## Narrative quality assessment (read directly, not just non-empty)

I generated two live narratives independently of the implementer's report (not trusting its quoted
output) and read them directly:

1. **Unit-test-fixture inputs** (`C04570` / card-testing / 0.86 / three small auths then a
   larger purchase): 9-sentence narrative. Correctly covers who (`C04570`, `C04570-K1`), what
   (three small authorizations then a $100.09 online purchase — card testing), where (online
   channel, explicitly called out), how (probing card validity before a larger purchase), and why
   (0.86 probability, real-time model score of 0.57, evidence of a deliberate testing pattern).
   "When" is absent — see Medium finding above. No hallucinated transaction IDs or merchant names
   beyond what was supplied.

2. **Synthetic R6 end-to-end inputs** (shared-device cluster, `undocumented` pattern, 0.91): 7-sentence
   narrative, covers who (`C09999`, cards `C09999-K1`/`C08888-K1`), what (two cards authorized from
   the same device 90 seconds apart), where (device `DVC-771`), how (shared-device cluster
   detection), why (0.91 probability, undocumented/coordinated pattern). Again no "when" — no date
   was supplied, consistent with the Medium finding.

Both read as coherent, policy-appropriate SAR prose grounded in the given facts — not generic
filler, no invented transaction numbers, merchants, or amounts. The one systematic content gap is
the missing "when" element, traced to a concrete, fixable cause (the date field simply isn't part
of the prompt's inputs).

## Summary

| Area | Verdict |
|---|---|
| Spec compliance (brief Steps 1–6) | PASS — diff matches brief exactly |
| SAR-filing branch exercised end-to-end | PASS — closed via live monkeypatched `run_single_case` run (was open before this review) |
| `write_sar_narrative` uses `generate_structured` + `SARNarrativeOutput`, grounded prose | PASS — confirmed by direct source read and two live generations |
| `run_case.py` wiring (no placeholder, conditional LLM call) | PASS — confirmed by diff + source read |
| `tests/test_sar_writer.py -v` | PASS (live) |
| Narrative reads as coherent SAR prose (not filler) | PASS, with one gap (see below) |
| README's six-to-twelve-sentence / who-what-when-where-how-why requirement | PARTIAL — prompt wording matches README verbatim, but "when (dates)" is structurally never grounded because no date is passed into the prompt (Medium finding) |

**Overall: approve, with one Medium fix requested** (pass a date field, e.g. `case_row["opened_at"]`,
into `write_sar_narrative`'s prompt so the "when" element of the SAR narrative is actually
groundable instead of systematically omitted).
