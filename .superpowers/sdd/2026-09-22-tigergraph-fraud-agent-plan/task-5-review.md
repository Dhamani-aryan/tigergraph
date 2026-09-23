# Task 5 Review: Deterministic fraud policy engine (rules R1-R10)

**Reviewer:** independent read of `README.md` §Fraud Policy / §Answer Format, `src/policy/engine.py`, `src/policy/models.py`, `tests/test_policy_engine.py`, and the task-5 brief/report. Read-only review; nothing in the worktree was modified.

**Verified myself, not taken on faith:**
- `.venv\Scripts\pytest tests\test_policy_engine.py -v` → **13/13 passed**, all real assertions (each checks a specific action list, route, or `sar_file`/`sar_reason` value — none is a tautology or a no-op assert).
- `.venv\Scripts\pytest -q` (whole repo) → **19/19 passed**.
- Working tree is clean at commit `98cc215`; the diff under review is the entire and only content of that commit.
- Hand-traced all 13 tests, the HHG-017 worked example, and several scenarios *not* covered by any test, against the literal README rule text, line by line through `engine.py`.

---

## 1. Spec compliance verdict: **Conditional pass — correct on R1-R10 as coded, but §3a (case-opening triggers) is materially incomplete**

The rule-by-rule logic (R1-R10) is a faithful, well-reasoned translation of the README, with one deliberate and defensible deviation (R1's threshold, discussed at length in §3 below). Approval routing (§2 of the policy) is implemented **exactly** right in every branch I checked — `BLOCK_CARD` at `L1`/`L2` on the correct $2,500 boundary, `FILE_REPORT` always `L2`, `DECLINE_TRANSACTION` always `L1`, everything else `auto` — matching the routing table with no exceptions found.

However, I found a real gap unrelated to the R1 question, more consequential than it: **§3a's general case-opening triggers are not implemented.** §3a says:

> "Open one [CREATE_CASE] whenever fraud probability reaches 0.30, whenever you request evidence, or whenever a customer disputes a charge."

`engine.py` only ever emits `CREATE_CASE` from four specific branches: R2 (`denies`), R7 (`disputes_recurring`), R6 (shared origin), and R9 (undocumented+coordinated). It never fires on the general "`fraud_probability >= 0.30`" trigger, and never fires when evidence is requested via `VERIFY_WITH_CUSTOMER`/`STEP_UP_AUTH` alone. Concretely:

- `Findings(pattern="account_takeover", fraud_probability=0.6, single_signal=False, exposure_usd=100)` — no shared-origin flags, not card_testing, not undocumented, no customer response yet. Trace: R5 skip, R6 skip, R9 skip, R1 skip (`single_signal=False`), R8 skip (`exposure_usd=100` is not `>500`), falls to the `if not actions` default → `VERIFY_WITH_CUSTOMER` only. **No `CREATE_CASE`, despite `fraud_probability=0.6` far exceeding the 0.30 open-a-case bar.**
- R4 (`no_reply`) never emits `CREATE_CASE` either — but `no_reply` only happens *after* evidence was requested (a verification was sent and 24h passed with no answer), which is explicitly one of §3a's three triggers.
- The R1 weak-signal fallback (`VERIFY_WITH_CUSTOMER` for a single weak signal) is itself "requesting evidence" per §3a's own framing, and also never opens a case.

None of the 13 given tests catches this because every test either routes through R2/R6/R7/R9 (which do open a case) or only asserts the presence of an unrelated action (`test_r8` checks `ESCALATE_TO_ANALYST` is present but never checks whether `CREATE_CASE` is present at `fraud_probability=0.5`). This is a real hole in an otherwise carefully-built engine, and it matters more than it looks: §3a itself says "a report always has a case behind it," and case existence feeds directly into the case-memory/graph-write parts of the answer format (Part 1, `written_to_graph`/`graph_case_id`) that Task 12 will build on top of this engine's output. I'd flag this as the top actionable finding from this review, independent of the R1 question. It's a **spec gap in the implementation**, not ambiguous like the R1 threshold — §3a's text is unambiguous and simply isn't wired up.

---

## 2. Code quality verdict: pass, with findings by severity

### HIGH
- **§3a general `CREATE_CASE` triggers (`fraud_probability >= 0.30`, "whenever you request evidence") are unimplemented.** See §1 above. Affects R4's `no_reply` branch and the R1/fallback weak-signal branches, which recommend requesting evidence or acting on a moderate probability without ever opening a case.

### MEDIUM
- **`customer_response == "confirmed_fraud"` is a defined `Literal` in `models.py` but has no dedicated branch in `engine.py`.** It silently falls through to the generic R5/R6/R9/R1/R8 logic — treated no differently than "no response yet" (`None`). If a caller (Task 12) ever sets this value to mean "the customer confirms this was fraud," the engine would under-react relative to `"denies"` (which triggers immediate `BLOCK_CARD` + `CREATE_CASE`), even though the semantics arguably call for the same urgency. Worth either handling it explicitly (alias it to the R2 `denies` path, if that's the intended meaning) or documenting in `models.py` what distinguishes it from `denies`.
- **R5's "if a purchase over $100 has already cleared, recommend `BLOCK_CARD`" sub-clause is unimplementable as modeled** — `Findings` has no field capturing whether the triggering purchase has already cleared vs. is still a pending authorization. This isn't something the implementer introduced (it's inherited unchanged from the brief's reference `Findings`/`engine.py`), and it happens not to bite any of the 13 tests or the worked example (in HHG-017, the $259.98 purchase is still a pending authorization at the `initial` stage — that's *why* `DECLINE_TRANSACTION` rather than `BLOCK_CARD` is correct there, confirmed by `DECLINE_TRANSACTION`'s own definition: "Decline the flagged authorization only"). But it's a real, silent gap in R5 coverage.
- **R6's shared-origin branch fires `CREATE_CASE`/`FILE_REPORT`/`sar_file=True` purely off the three boolean flags, with no check on `fraud_probability`.** §3a requires filing a report only when "fraud is confirmed or strongly suspected **and**" one of the listed factors holds — R6 as coded satisfies the "and" list but never checks the "confirmed or strongly suspected" half. This is defensible only if the caller (Task 12) is contractually guaranteed to never set `shared_device`/`shared_region`/`shared_email` unless the shared origin is itself already established as fraud-linked (not e.g. "two family members' cards share a home Wi-Fi device profile, no fraud confirmed on either"). That contract isn't stated anywhere — `models.py`'s docstrings don't say it. Worth documenting explicitly, since otherwise this rule can over-file SARs on weak evidence.
- **Reason-string mislabeling in the two fallback branches.** The `if not actions:` fallback cites `"R1"` for its `VERIFY_WITH_CUSTOMER` even on paths where `single_signal=False` (R1's own precondition), and cites `"R8"` for `CLOSE_NO_FRAUD` at `fraud_probability <= 0.15`, though R8 is about escalating uncertain+exposed cases, not closing confident-legitimate ones (that inference is really drawn from §6's stopping criterion, not R8). §7 of the policy requires citing "the rule number" for every recommendation — these two citations are inaccurate about which rule actually justifies the action. Low-cost fix: relabel or add a distinct comment ("inferred from §6 stopping threshold," not a numbered rule).

### LOW / nit
- The R1 branch's action-stripping list comprehension (removing any pre-existing `BLOCK_CARD`/`BLOCK_ALL_CARDS`/non-card-testing `DECLINE_TRANSACTION`) and the `not any(a.action == "CREATE_CASE" ...)` guards in R6/R9 are currently **dead code** given the control flow: every branch that could populate `BLOCK_CARD` (only R2's `denies` branch) returns early, before R1's code ever runs. I traced this explicitly (see §3 below) — it's not wrong, just inert defensive code today. Worth a one-line comment noting it's forward-looking (e.g., in case R5's missing "already cleared" clause above is ever added).
- R8's "or the evidence conflicts" clause has no corresponding `Findings` field and is simply not implementable today — minor, parallels the R5 gap above.

---

## 3. The R1 threshold: independent conclusion

**My conclusion: the implementer's fix (raising the threshold from 0.70 to 0.85, gated only on `single_signal`) is the right call, and I can show it's safe by tracing the control flow, not just by re-arguing plausibility. I'd keep 0.85, but I'd also flag the README's own R1 text as an erratum that should be corrected upstream, and I'd add one more test to pin the exact boundary.**

### Is the README self-contradictory here? Yes — genuinely, not resolvable by a "single signal" reinterpretation.

The brief's reviewer question suggests a way out: maybe HHG-017 isn't actually "single signal" at the point `VERIFY_WITH_CUSTOMER` is recommended, since the case has three evidence items (graph sequence, device link, later customer denial) — so R1's literal 0.70 gate would just be inapplicable to it, and the `"R1"` citation in the reason string is loose wording.

I checked this directly against the worked example's own evidence list and reason strings, and it doesn't hold up:

- At the `initial` stage (before the customer is asked), only evidence items 1 and 2 exist — item 3 (customer denial) is explicitly the *response* to `evidence_requests[0]`, which happens after. So yes, there are technically two evidence items at that point (the transaction-sequence match, and the device link to a prior closed case + another card).
- But the reason string is explicit and probability-based, not signal-count-based: `"R1: probability 0.72 on pattern alone, confirm before blocking"`. "On pattern alone" is the README author's own framing that the fraud-probability figure of 0.72 is being treated as resting on the pattern match alone (i.e., `single_signal=True` is the intended reading) — the device-link evidence is investigative context connecting this card to another one, not treated as a second independent signal that this *transaction* is fraud.
- More tellingly: R5's literal text says a card-testing sequence with "a purchase over $100 already cleared" should recommend `BLOCK_CARD` directly — but the example's `initial` actions don't include `BLOCK_CARD`, they substitute `VERIFY_WITH_CUSTOMER` (citing R1) in a case where the underlying purchase is only a pending authorization, not yet cleared (confirmed by `DECLINE_TRANSACTION` being recommended, which only applies to "the flagged authorization," i.e., not-yet-cleared). So this particular near-miss doesn't actually invoke R5's already-cleared clause — but the reason string still names R1, tied explicitly to the *probability figure* 0.72, not to any missing-signal argument.

Given that, there's no reading of the README that resolves the contradiction by reinterpreting "single signal" — the worked example plainly says "R1 gates on probability, and 0.72 is still below the R1 bar," which flatly contradicts R1's own stated "below 0.70" text (0.72 > 0.70). This is a genuine authoring inconsistency in the README, not an artifact of the implementer's interpretation.

### Does the widened threshold actually create the danger the review brief worried about?

I traced this concretely rather than reasoning abstractly. The worry: does R1 now firing across a wider band (0.70–0.85) *suppress a block that should happen* for a single-signal case with otherwise strong evidence in that band?

Tracing `apply_policy`'s control flow: `BLOCK_CARD` and `BLOCK_ALL_CARDS` are **only ever appended in the `customer_response == "denies"` branch (R2)**, and that branch `return`s immediately (via `_finish`) before the R1 code (further down the function) ever runs. R3, R4, and R7 similarly return early. The *only* path that reaches the R1 gate is the case where `customer_response` is `None` (or the unhandled `"confirmed_fraud"` — see Medium finding above) — and on that path, nothing upstream of R1 ever adds `BLOCK_CARD`/`BLOCK_ALL_CARDS` (R5's card-testing branch only ever adds `DECLINE_TRANSACTION`/`STEP_UP_AUTH`, never `BLOCK_CARD`, because — as noted above — the "already cleared" sub-clause of R5 isn't implemented at all).

So concretely: **raising the R1 threshold to 0.85 cannot suppress any `BLOCK_CARD` recommendation anywhere in the current engine**, because every code path capable of emitting `BLOCK_CARD` bypasses R1 entirely by construction (early return). The list-comprehension in the R1 branch that strips `BLOCK_CARD`/`BLOCK_ALL_CARDS`/non-card-testing `DECLINE_TRANSACTION` is, today, a no-op — there's never anything in `actions` for it to strip at that point. This is exactly the LOW/dead-code finding above, but it's also the reassurance the threshold question needed: the change is structurally inert with respect to the failure mode the review brief was worried about. (It *would* matter if R5's missing "already cleared → `BLOCK_CARD`" clause were ever implemented — at that point the R1 gate would legitimately need to decide whether to suppress that block too, and 0.85 vs. 0.70 would make a real difference. But that's future work, not a regression introduced now.)

The only observable effect of the widened threshold today is **additive**: for `single_signal=True` cases with `0.70 <= fraud_probability < 0.85`, the engine now also appends `VERIFY_WITH_CUSTOMER` (in the card-testing branch and the general fallback) where it previously wouldn't have. That's a strictly more cautious behavior change — consistent with R1's own stated purpose ("Blocking a legitimate customer on one signal is a policy breach") and with §0's framing that "a risk score is a reason to look. Never a verdict" (no upper-bound carve-out given there either). It does not weaken any other rule's SAR/escalation/reporting behavior, since R6, R8, and R9 don't gate on `single_signal` at all.

### Is 0.85 the *right* number, or just *a* safe number?

Any value strictly greater than 0.72 would satisfy the given tests (the worked example only pins the floor at >0.72; nothing pins the ceiling). So 0.85 specifically is the implementer's inference, not something the tests force. I think it's the right choice, not just a safe one, because:

1. It reuses a number the README *already* uses twice for the same underlying concept — "is this probability high enough to be treated as settled" — in §6 ("stop investigating when fraud probability is at or above 0.85 ... supported by evidence") and in R8's own uncertain-window upper bound (`0.15 < p < 0.85`, already used elsewhere in this same `engine.py` for the escalation check). Picking a fresh, unexplained number (e.g. 0.75, 0.80) would be more arbitrary than reusing the one number the spec itself treats as the "decision is settled" bar.
2. It makes the engine internally consistent: below 0.85, a single-signal case is "still uncertain, still weak" for *both* the R1 (verify-before-block) and R8 (escalate-if-uncertain) purposes; at/above 0.85, it's settled. One threshold, one meaning, used twice.

### What I'd actually recommend

- **Keep 0.85.** It's the best-justified resolution of a genuine README contradiction, and I've shown it can't regress any existing block/decline behavior.
- **Flag the README itself as needing a correction** — R1's literal text ("below 0.70") and its own worked example (0.72 still gated) directly contradict each other, and that should be fixed at the spec level, not just patched around in code with a comment. The current code comment in `engine.py` is good documentation of the reasoning, but it's still working around an upstream spec bug rather than the spec being self-consistent.
- **Add one boundary-pinning test** — something like `single_signal=True, fraud_probability=0.84` (expect `VERIFY_WITH_CUSTOMER`) and `fraud_probability=0.85` (expect it absent, all else equal). Right now no test would fail if someone quietly changed `R1_WEAK_SIGNAL_THRESHOLD` to, say, 0.80 or 0.95 — the existing suite only constrains it to `(0.72, ∞)` from one side. Given this constant's rationale is entirely non-obvious from the README's literal text, it deserves an explicit regression test, not just a comment.

---

## Summary for the task owner

- **Tests:** 13/13 genuine and passing (verified myself), 19/19 repo-wide.
- **R1 threshold:** the 0.70→0.85 change is correct and well-reasoned; I independently confirmed (a) the README's own worked example genuinely contradicts its own literal "below 0.70" rule text with no alternative reading available, and (b) by tracing the control flow, the widened gate cannot suppress any block the current engine is capable of producing, so it's a safe, purely-additive caution increase. Recommend keeping 0.85, filing the README contradiction as a spec erratum, and adding a boundary test.
- **Bigger finding, independent of R1:** §3a's general case-opening triggers (`fraud_probability >= 0.30`; "whenever you request evidence") are not implemented — `CREATE_CASE` only fires from R2/R6/R7/R9's specific branches, never from a bare probability threshold or from evidence-request paths (R1's fallback, R4's `no_reply`). This is a clear, unambiguous README requirement that's silently missing, not covered by any of the 13 tests, and worth fixing before Task 12 builds on this engine.
- Several smaller Medium/Low findings (unhandled `confirmed_fraud` literal, unimplementable R5 "already cleared" and R8 "evidence conflicts" sub-clauses, R6's missing probability gate before filing, two inaccurate rule citations in fallback reason strings, and some currently-dead defensive code) are detailed above with severities.
