# Task 5 Round-2 Re-review: §3a general `CREATE_CASE` trigger fix

**Scope:** verify only the reviewer's §3a finding (case must open whenever `fraud_probability >= 0.30`, independent of which rule fires) against commit `517da8c` (diff `98cc215..517da8c`), confirm no new Critical/Important breakage. Read-only; nothing modified.

**Verified myself, not taken on faith:**
- Read `src/policy/engine.py` in full (post-fix) and the diff.
- Ran `.venv\Scripts\pytest tests/test_policy_engine.py -v` → **15/15 passed** (13 prior + 2 new: `test_sec3a_case_opens_on_probability_alone_with_no_other_triggering_rule`, `test_r4_no_reply_also_opens_case_above_probability_threshold`).
- Ran `.venv\Scripts\pytest -q` (whole repo) → **21/21 passed**, matching the report's claim.
- Manually re-ran the reviewer's exact counterexample and independently confirmed `CREATE_CASE` now appears with `route="auto"`.
- Read README.md §3a and the full HHG-017 worked example (both `initial` and `final` action blocks) as ground truth.
- Read the R3 (`confirmed_legitimate`) and R7 (`disputes_recurring`) branches directly to confirm they never reach `_finish()`.

---

## Verdict: **ADDRESSED**

The specific finding — that a case should open whenever `fraud_probability >= 0.30` regardless of which rule fires, and that the original engine silently produced no `CREATE_CASE` for cases like `Findings(pattern="account_takeover", fraud_probability=0.6, single_signal=False)` — is fixed.

**Point 1 — code matches the claim.** `_finish()` in `src/policy/engine.py` (lines 148-149) now reads:
```python
if f.fraud_probability >= 0.30 and not any(a.action == "CREATE_CASE" for a in actions):
    actions.append(ActionRec(action="CREATE_CASE", route="auto", reason="Sec 3a"))
```
This is exactly what the report claims: route `auto`, citing "Sec 3a", gated on `fraud_probability >= 0.30`, guarded against duplicating an already-present `CREATE_CASE`.

**Point 2 — counterexample verified by direct execution**, not trust:
```
findings = Findings(pattern="account_takeover", fraud_probability=0.6, single_signal=False)
apply_policy(findings).actions -> includes CREATE_CASE (route=auto, reason="Sec 3a")
```
Confirmed via `test_sec3a_case_opens_on_probability_alone_with_no_other_triggering_rule` (passing) and by re-reading the trace by hand: R5/R6/R9/R1/R8 all stay silent on `CREATE_CASE` for this input, so before the fix the fallback branch produced `VERIFY_WITH_CUSTOMER` alone; the new `_finish()` check now adds `CREATE_CASE`. Full suite: 15/15 in the policy file, 21/21 repo-wide.

---

## Point 3 — R3/R7 safety check: **CONFIRMED SAFE, claim is true**

This is the highest-risk part of the fix and I traced the control flow directly rather than trusting the report's assertion.

- **R3** (`f.customer_response == "confirmed_legitimate"`, engine.py lines 21-26): the `if` block ends in `return PolicyResult(...)` — a direct return of a fully-constructed `PolicyResult`. `_finish()` is never called on this path. `test_r3_customer_confirms_closes_no_fraud` (`_actions(result) == ["CLOSE_NO_FRAUD"]`, an exact-list assertion, not just membership) still passes, confirming no `CREATE_CASE` leaks in even at `fraud_probability=0.4` (which is ≥ 0.30 and would trigger the Sec 3a rule if it ever reached `_finish()`).
- **R7** (`f.customer_response == "disputes_recurring"`, engine.py lines 29-38): same pattern — `return PolicyResult(...)` directly, never touching `_finish()`. `test_r7_disputed_but_recurring_does_not_block` passes and separately confirms `CREATE_CASE` is present exactly once (from R7's own explicit action list, reason `"R7"`, not duplicated or overwritten by Sec 3a).

Both functions literally `return` before the line that calls `_finish(...)` is ever reached — this is a structural guarantee (early `return` inside an `if`), not a coincidence of current test coverage. The claim in the report is accurate.

---

## Point 4 — "whenever you request evidence" half of §3a: honestly left undocumented-as-unimplemented

Confirmed directly in code (not just in the report prose): `_finish()` in `engine.py` (lines 140-147) contains an explicit comment stating the "whenever you request evidence" trigger is not implementable because `Findings` has no field recording that an evidence request was made independent of the probability that prompted it, and notes where to wire it in if such a field is ever added. This is a findable, honest in-code note (not a silent drop), matching the report's framing.

---

## Point 5 — new Critical/Important breakage: none in the strict sense, but one Important divergence worth flagging

No existing test regresses, no R3/R7 leakage, no duplicate-`CREATE_CASE` bug found in R2/R6/R9 paths (all guard with the same `not any(...)` pattern and I confirmed no double-append occurs by tracing each).

**However — an Important, untested divergence from the README's own worked example (HHG-017) was introduced by this fix**, and it deserves attention before Task 12 builds on this engine:

The README's worked example (§ Answer Format, lines 408-412) gives the *authoritative* expected `initial` action list for `Findings(pattern="card_testing", fraud_probability=0.72, single_signal=True)` as exactly:
```
[DECLINE_TRANSACTION, VERIFY_WITH_CUSTOMER]
```
I ran this exact input through the post-fix engine directly:
```
apply_policy(Findings(pattern="card_testing", fraud_probability=0.72, single_signal=True)).actions
-> [DECLINE_TRANSACTION, STEP_UP_AUTH, VERIFY_WITH_CUSTOMER, CREATE_CASE]
```
`STEP_UP_AUTH` was already an extra (pre-existing, unrelated to this fix — R5 always appends it, out of scope for this re-review). But **`CREATE_CASE` is new** as of this round-2 diff: since `fraud_probability=0.72 >= 0.30`, `_finish()` now appends it, and no early-return branch intercepts this path (customer_response is `None` at the `initial` stage). The README's own worked example does not show `CREATE_CASE` in the `initial` block — it only appears later in the `final` block once the customer denies (R2).

This is the same category of tension the round-1 reviewer already flagged and resolved for the R1 threshold (literal rule text vs. the worked example as the more authoritative concrete statement) — except here the fix went the other direction: it followed the literal §3a text over the worked example's concrete output, and nothing in the test suite catches it, because `test_worked_example_from_readme`'s `initial_result` assertions only check for presence of `VERIFY_WITH_CUSTOMER`/`DECLINE_TRANSACTION`, never an exact list (unlike R3's test, which does pin the exact list and would have caught an analogous issue there).

This is not a regression against any currently-passing test, and it doesn't break R3/R7 or duplicate any action — so it does not block the ADDRESSED verdict on the original finding. But it is a real, unflagged conflict between the fix's literal-spec interpretation and the README's own ground-truth example, on the exact same worked example the round-1 fix leaned on as authoritative. Recommend: either (a) treat it as expected/correct and add an exact-list assertion to `test_worked_example_from_readme`'s `initial` block to pin it deliberately, or (b) treat the worked example as authoritative (as round 1 did for R1) and reconsider whether `CREATE_CASE` should fire this early, e.g. only once evidence comes back or a customer response exists. Either way, this should not be left silently un-pinned.

## Deferred / out-of-scope (non-blocking, carried over from the original review, not re-verified here)

- Unhandled `confirmed_fraud` `Literal` value (Medium, original review §2).
- R5 "already cleared → `BLOCK_CARD`" and R8 "evidence conflicts" sub-clauses unimplementable given current `Findings` fields (Medium/Low, inherited from brief).
- R6 files a report on shared-origin flags alone with no probability/confirmation gate (Medium, original review §2).
- Two loosely-cited reason strings in the fallback branches (`"R1"`/`"R8"` mislabeling) (Medium, original review §2).
- R1's action-stripping list comprehension is currently dead code given control flow (Low, original review §2) — still true post-fix, not re-verified in depth here.

---

## Summary for the task owner

- **Original finding: ADDRESSED.** `_finish()` now adds `CREATE_CASE` (route `auto`, reason "Sec 3a") whenever `fraud_probability >= 0.30` and no case is already open, on every path that reaches `_finish()`. Verified by direct code read, by independently re-running the reviewer's counterexample, and by running the full test suite (15/15 policy tests, 21/21 repo-wide).
- **R3/R7 safety (point 3): CONFIRMED SAFE.** Both branches `return` their own `PolicyResult` before `_finish()` is ever called — a structural guarantee, not just test coverage — so neither can pick up the new Sec 3a trigger. R3's exact-list test (`_actions(result) == ["CLOSE_NO_FRAUD"]`) at `fraud_probability=0.4` (≥0.30) is the sharpest possible check here and it passes.
- **New breakage: none that fails a test or leaks into R3/R7**, but one Important, untested divergence from the README's own HHG-017 worked example was introduced: the `initial` stage now emits an un-listed `CREATE_CASE` at `fraud_probability=0.72`, which the ground-truth worked example doesn't show. Flagged above for follow-up; does not block this round's ADDRESSED verdict.
