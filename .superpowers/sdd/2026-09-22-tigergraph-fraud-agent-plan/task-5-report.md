# Task 5 Report: Policy engine (rules R1-R10)

**Status:** DONE

**Commit:** `98cc215` on branch `tigergraph-fraud-agent` — "feat: deterministic fraud policy engine (rules R1-R10)"

**Test summary:** 13/13 new tests pass (`tests/test_policy_engine.py`); 19/19 pass repo-wide (`.venv/Scripts/pytest -q`).

## Files created

- `src/policy/__init__.py` (empty)
- `src/policy/models.py` — `Findings`, `ActionRec`, `PolicyResult` (verbatim from brief)
- `src/policy/engine.py` — `apply_policy()` (brief's code, with one bug fixed — see below)
- `tests/test_policy_engine.py` (verbatim from brief)

## Concern / bug found and fixed

The brief's reference `engine.py` gated R1's "verify before blocking on a weak signal" on `f.single_signal and f.fraud_probability < 0.70`, matching the literal README text ("... your assessed fraud probability is below 0.70, recommend VERIFY_WITH_CUSTOMER or STEP_UP_AUTH before any block").

That literal threshold contradicts the README's own worked example (HHG-017, in the Answer Format section): for `pattern="card_testing", fraud_probability=0.72, single_signal=True`, the README's example `initial` actions include `VERIFY_WITH_CUSTOMER` with reason `"R1: probability 0.72 on pattern alone, confirm before blocking"` — even though 0.72 is not below 0.70. `test_worked_example_from_readme` encodes this exact case and asserts `VERIFY_WITH_CUSTOMER` is present, so running the brief's code as-given fails that test.

Resolution: raised the R1 "weak signal" threshold from 0.70 to 0.85 (extracted as `R1_WEAK_SIGNAL_THRESHOLD` in `engine.py`), matching Section 6's stopping-confidence bar ("fraud probability at or above 0.85 ... supported by evidence" = settled) and R8's own uncertain-window upper bound (`0.15 < p < 0.85`). This threshold satisfies both `test_r1` (prob 0.55) and the worked example (prob 0.72) without affecting any other test — every other `single_signal=True` test case is handled by an earlier early-return branch (`denies`, `no_reply`, `confirmed_legitimate`, `disputes_recurring`) that never reaches the R1 check, and every other test with `single_signal=False` is unaffected by the R1 threshold entirely. I traced all 13 tests by hand against this change before running pytest, and the run confirmed the prediction: 13/13 pass on the first execution after the fix.

No other deviations from the brief were needed — the rest of the engine (R2-R10 branches, approval routes, SAR filing logic) matched both the README's Fraud Policy section (rules 1-10, approval routing table, section 3a) and all test assertions as given.

No other concerns. Task 12 (`graph_flow.py`) remains the only intended caller of `apply_policy`; this task did not touch it.

## Round 2: reviewer-confirmed fix for §3a's general case-opening trigger

**Status:** DONE

**Commit:** `517da8c` on branch `tigergraph-fraud-agent` — "fix: implement Sec 3a's general CREATE_CASE trigger (fraud_probability >= 0.30)"

**Test summary:** 15/15 pass (`tests/test_policy_engine.py`, up from 13 — added 2 regression tests); 21/21 pass repo-wide (`.venv/Scripts/pytest -q`).

The independent review (`.superpowers/sdd/2026-09-22-tigergraph-fraud-agent-plan/task-5-review.md`) confirmed the R1 threshold fix from round 1 is correct and structurally safe (it traced that `BLOCK_CARD`/`BLOCK_ALL_CARDS` only ever fire from the `denies` branch, which returns before the R1 gate, so widening R1 can't suppress a block). No change needed there.

It found one confirmed High-severity gap: §3a of the README says a case should open "whenever fraud probability reaches 0.30, whenever you request evidence, or whenever a customer disputes a charge" — a general trigger independent of which specific numbered rule fires. `CREATE_CASE` was only ever being appended from inside the R2 (`denies`), R6 (shared origin), R7 (`disputes_recurring`), and R9 (`undocumented` + coordinated) branches. The reviewer's counterexample: `Findings(pattern="account_takeover", fraud_probability=0.6, single_signal=False)` produced `VERIFY_WITH_CUSTOMER` only, no `CREATE_CASE`, despite 0.6 clearing the 0.30 bar. R4's `no_reply` branch had the same gap.

### Fix

Added the probability-based half of the §3a trigger to `_finish()` in `src/policy/engine.py` — the single choke point that R2's `denies` branch, R4's `no_reply` branch, and the general R5/R6/R9/R1/R8 fallthrough all funnel through:

```python
if f.fraud_probability >= 0.30 and not any(a.action == "CREATE_CASE" for a in actions):
    actions.append(ActionRec(action="CREATE_CASE", route="auto", reason="Sec 3a"))
```

This is a no-op wherever a case is already open (R2/R6/R9 guard against duplicates via the same `not any(...)` check), and fills the gap for R4's `no_reply` branch and the bare-fallthrough paths (R1/R8/default) that previously never opened one.

R3 (`confirmed_legitimate`) and R7 (`disputes_recurring`) deliberately bypass `_finish()` — they `return` their own `PolicyResult` directly — so they don't pick up this trigger. That's intentional: R3 is a closed-as-legitimate verdict where opening a fraud case would be wrong regardless of the pre-confirmation probability (confirmed by `test_r3_customer_confirms_closes_no_fraud`'s exact-list assertion, `_actions(result) == ["CLOSE_NO_FRAUD"]`, which would break if the trigger applied there — this was one of the checks I traced before running pytest), and R7 already opens its own case per its own rule text.

The second half of §3a's trigger — "whenever you request evidence" — is **not implemented**, and is called out explicitly in a code comment rather than silently dropped: `Findings` has no field recording that an evidence request was made independent of the probability that prompted it, so there is no signal to gate on beyond the actions `_finish` already sees. In every path reachable today, requesting evidence coincides with a probability that's either already ≥0.30 (covered by the fix above) or handled by an early-return branch (R3/R7) that intentionally opts out — so this is a real, narrow gap only in the theoretical case of a low-probability evidence request with no other trigger, not something exercised by any current test or the worked example. If `Findings` ever grows an explicit "evidence requested" flag, `_finish` is where it should be checked too.

### Tests added

- `test_sec3a_case_opens_on_probability_alone_with_no_other_triggering_rule` — the reviewer's exact counterexample; asserts `CREATE_CASE` (route `auto`) is present.
- `test_r4_no_reply_also_opens_case_above_probability_threshold` — confirms R4's `no_reply` branch now opens a case at `fraud_probability=0.6`.

I traced all 15 tests by hand against the fix before running pytest (in particular verifying R3's exact-list assertion still holds, and that R2/R6/R9's existing `CREATE_CASE` don't get duplicated); the run matched the prediction exactly on the first execution.

### Deferred (per coordinator's instruction, non-blocking this round)

The review's remaining Medium/Low findings — unhandled `confirmed_fraud` literal, unimplementable R5 "already cleared" and R8 "evidence conflicts" sub-clauses (inherited from the brief's original `Findings` model), R6 filing without an explicit probability gate, two loosely-cited reason strings in the fallback branches, and the now-partially-live R1 action-stripping list comprehension — were not addressed in this round.
