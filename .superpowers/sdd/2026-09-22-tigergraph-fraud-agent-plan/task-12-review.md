# Task 12 review: LangGraph investigation flow + case write-back

**Verdict: APPROVE WITH FINDINGS — two items should be fixed before Task 13, not carried
forward.** The flow is spec-compliant, the 5 claimed bug fixes are real and independently
verified, and the write-back to the live graph is confirmed. But (1) the report's own "known
limitation, out of scope" framing of the `shared_device` false-positive issue undersells it —
it's a real, cheaply-fixable, scoring-relevant defect — and (2) my own independent re-run of
the report's own proof-of-life test **failed**, on a defect the report's single passing run
didn't surface: the LLM's structured-output prompt never states the schema's actual JSON key
names, and on my run the model consistently used `fraud_pattern` instead of `pattern` across
all 3 attempts, exhausting `generate_structured`'s retry budget. Both are detailed below.

## Spec compliance

- `src/agent/state.py`, `src/agent/graph_flow.py`, `src/run/__init__.py`, `src/run/run_case.py`,
  `tests/test_run_case_hhg017.py` all present, matching the brief's file list.
- Flow topology matches the brief exactly: `gather_evidence -> agentic_followup ->
  apply_followup -> assess -> [stop -> policy | request_evidence -> reassess -> policy]`.
  Verified by reading `build_graph` in `src/agent/graph_flow.py:385-418` against the brief's
  step 2 code block — same nodes, same conditional edge, same terminal `policy -> END`.
- `run_single_case(tg, case_row) -> AnswerFile` signature matches the brief's "Produces" line.
- The brief's explicitly-flagged open item (`flagged_amount` regex parsing) is implemented:
  `_AMOUNT_RE`, `_amount_from_trigger_text`, `_flagged_amount` in `graph_flow.py:56-79`, wired
  into `evidence_request_node`, `_findings_from_state`, and `run_case.py`'s `exposure_usd`/
  `tigergraph__add_nodes` write-back. The test fixture (`tests/test_run_case_hhg017.py`)
  deliberately omits `flagged_amount` — confirmed by reading the actual file, which has no such
  key in `case_row` — so the regex path is genuinely exercised, not bypassed.

## The 5 claimed bug fixes — all verified real by reading the actual code

1. **`flagged_amount` regex parsing.** Real, as above. `_flagged_amount` prefers an explicit
   `row["flagged_amount"]` when present and falls back to `_amount_from_trigger_text`, which is
   used consistently everywhere a dollar amount is needed (`evidence_request_node`,
   `_findings_from_state`'s `exposure_usd`, `run_case.py`'s `exposure_usd` and the graph
   write-back's `exposure_usd` field, which the brief's draft had hardcoded to `0.0`).

2. **Async-closure fix for `build_graph`.** Confirmed: `build_graph` no longer uses
   `workflow.add_node("gather_evidence", lambda s: gather_evidence_node(tg, s))` as the brief's
   draft did. It now defines real `async def _gather_evidence(s)` / `async def _apply_followup(s)`
   closures (`graph_flow.py:392-397`) with an inline comment explaining LangGraph detects
   node-async-ness by inspecting the callable itself. This is a correct and necessary fix —
   a lambda wrapping a coroutine function is not itself a coroutine function, so LangGraph
   would not `await` it, exactly as the comment says.

3. **Evidence-summarization for token budget.** Confirmed: `_clip_strings` +
   `_summarize_evidence_for_prompt` (`graph_flow.py:82-133`) cap list-shaped evidence to
   `max_items=8` with an explicit `_total_count`/`total_count` field and clip long strings to
   `max_text_len=300`, used in `assess_node`'s prompt (`graph_flow.py:291`). Independently
   verified the underlying cardinality claim live: `device_neighbors` for HHG-017's flagged
   transaction returns **299** card dicts (see below), which is consistent with the report's
   claimed ~15,300-token blowup without summarization.

4. **`_resolve_followup_arguments` hallucinated-ID guard.** Confirmed in
   `graph_flow.py:242-270`: overrides `card_id`/`addr1` on `wider_card_window` /
   `wider_region_check` / `closed_case_lookup_by_region` with values already known from state
   (`state["card_id"]`, or `addr1` read back from the card's own flagged transaction in the
   `card_window` evidence), leaving only genuinely free parameters (e.g. `hours`) as the LLM
   supplied them. This is a sound design — it doesn't trust the small model to reproduce exact
   identifiers it was deliberately not shown, while still letting it choose *which* tool to
   call and *how wide* to make the follow-up query.

5. **Windows console encoding fix.** Confirmed in `tests/test_run_case_hhg017.py:24-31`: writes
   raw UTF-8 bytes to `sys.stdout.buffer` instead of `print()`, with a comment explaining the
   U+2011 / cp1252 crash. Correct and appropriately scoped (test-only, doesn't touch production
   code paths that don't have this problem).

All five are real, correctly targeted fixes, not padding.

## Live verification of write-back

Queried the live graph directly for the case this pipeline wrote:

```
tigergraph__get_node(vertex_type="FraudCase", vertex_id="CASE-HHG-017")
-> {'v_id': 'CASE-HHG-017', 'v_type': 'FraudCase', 'attributes': {
     'customer_id': 'C04570', 'card_id': 'C04570-K1', 'status': 'escalated',
     'verdict': 'uncertain', 'fraud_probability': 0.45, 'pattern': 'card_not_present_fraud',
     'exposure_usd': 100.09, 'summary': 'Case CASE-HHG-017 on card C04570-K1: pattern
     card_not_present_fraud, probability 0.45. Three online transactions within 1 hour
     window High ring cluster prior fraud rate 0.85 ...', 'written_at': 'now'}}
```

This matches the report's abridged JSON output exactly (same probability, pattern, exposure,
verdict, status, evidence claims baked into the summary). The `tigergraph__add_nodes` call and
the `embed()`/`upsert_vectors("FraudCase", "embedding", ...)` call are both inside the same
`try` block in `_write_case_to_graph`, and it returns `True` (which the report's output shows)
only if neither raised — so `written_to_graph: true` is good evidence the embedding upsert also
succeeded, not just the vertex write. Write-back claim: **confirmed**.

## The most important thing: independent judgment on the `shared_device` false-positive question

**This is a real, scoring-relevant defect, not a defensible design choice.** Here's the
reasoning, backed by live verification:

### What the code actually does

`gather_evidence_node` (`graph_flow.py:143-200`):

```python
neighbors = await device_neighbors(tg, str(row["flagged_txn_id"]))
...
cluster_rate = ring.get("cluster_prior_fraud_rate", 0.0) or 0.0
coordinated = (
    cluster_rate >= CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD and bool(ring.get("ring_cluster_id"))
)
...
"shared_device": bool(neighbors) or coordinated,
"shared_region": coordinated,
```

`shared_device` is `True` whenever `device_neighbors` returns *anything at all* — no check on
how many cards/customers share that device profile. Contrast with `shared_region`/`coordinated`,
which is gated behind `cluster_prior_fraud_rate >= 0.95` (a threshold the module's own comment
explains at length was raised from 0.5 specifically *because* a low bar let a 3,565-card,
0.847-fraud-rate supercluster — "indistinguishable from baseline noise, not a real ring
signal" — count as coordinated). The asymmetry is real: one signal in the same function has a
quality gate against exactly this kind of collision, the other does not.

### Live confirmation this isn't hypothetical

I called `device_neighbors` and `ring_membership` directly against the live graph for HHG-017's
flagged transaction (`3450629`, card `C04570-K1`), independent of any cached test output:

```
device_neighbors("3450629") -> 299 cards, e.g.
  {'id': 'C09981-K2', 'ring_cluster_id': 'RING-C00001-K1', 'cluster_prior_fraud_rate': 0.8469...}
  {'id': 'C05560-K2', 'ring_cluster_id': 'RING-C00001-K1', 'cluster_prior_fraud_rate': 0.8469...}
  {'id': 'C11309-K1', 'ring_cluster_id': 'RING-C00001-K1', 'cluster_prior_fraud_rate': 0.8469...}
  ... (299 total)

ring_membership("C04570-K1") -> {'ring_cluster_id': 'RING-C00001-K1',
  'cluster_prior_fraud_rate': 0.8469278812408447}
```

Two things stand out:

1. **299 is exactly** the count Task 8's by-hand checkpoint (`docs/manual-case-checkpoint.md`)
   already identified as a fingerprint-collision false positive — a common Windows/Chrome/
   1920x1080 profile shared by 621 transactions across 299 distinct, unrelated customers,
   explicitly confirmed there to *not* correlate with elevated fraud rate.
2. **The device-neighbor cards all carry the same `ring_cluster_id`/`0.8469 cluster_prior_fraud_rate`**
   as the card's own — this is precisely the 3,565-card supercluster the module's own comment
   names as the reason the 0.95 threshold exists ("indistinguishable from baseline noise, not a
   real ring signal"). The `coordinated` gate correctly excludes it (0.8469 < 0.95, so
   `shared_region=False`), but `shared_device` picks up the exact same collision unconditionally
   through the ungated `bool(neighbors)` path. The two signals in the same function look at
   substantially the same underlying population and reach opposite conclusions about whether it
   counts as evidence, purely because one has a cardinality/rate guard and the other doesn't.
3. There is already a pre-existing test (`tests/test_graph_queries.py:122-130`,
   `test_device_neighbors_matches_known_collision_count`) whose own name and comment call this
   fixture's 299-card result a "known collision" — so the fact that this specific
   `device_neighbors` result is not real ring evidence was already documented, in this exact
   codebase, before Task 12 wrote the ungated `bool(neighbors)` check. This wasn't an
   unknowable edge case; the information needed to guard against it was sitting in the test
   suite Task 12's own imports depend on.

### Downstream impact — traced through the actual policy engine, not asserted

I traced `_findings_from_state` -> `apply_policy` (`src/policy/engine.py`) by hand for this
case to confirm the divergence is not cosmetic:

- **Initial findings** (before any customer response): `pattern=card_not_present_fraud`,
  `fraud_probability=0.45`, `shared_device=True` (bug), `shared_region=False` (correctly gated),
  `single_signal=False` (a separate, related bug — see below). R5 (card_testing) doesn't apply.
  **R6 fires purely because `shared_device=True`**: `CREATE_CASE`, `FILE_REPORT`,
  `MONITOR_CONNECTED_CARDS`, `sar_file=True` — exactly what the report's actual output shows
  (`"initial": ["CREATE_CASE (R6)", "FILE_REPORT (R6)", "MONITOR_CONNECTED_CARDS (R6)"]`).
  **If `shared_device` were correctly `False`** for this case (299-way collision, no cardinality
  gate cleared): R6 does not fire, R1 does not fire either (blocked by the separate
  `single_signal` bug, not this one), R8 does not fire (`exposure_usd=100.09` is not `> 500`), so
  the action list falls through to the final `if not actions:` fallback, which for
  `fraud_probability > 0.15` appends exactly `VERIFY_WITH_CUSTOMER (R1)`. `_finish`'s Sec 3a
  clause then adds `CREATE_CASE` (probability `0.45 >= 0.30`). Result:
  **`VERIFY_WITH_CUSTOMER (R1) + CREATE_CASE (Sec 3a)`** — which matches the by-hand checkpoint's
  recommended initial action *exactly* ("R1 applies... Recommended action: `VERIFY_WITH_CUSTOMER`
  (route auto), plus `CREATE_CASE`").
- **Final findings** (after the simulated "customer denies" response): with the bug,
  `shared_device=True` drives R2's `exposure_usd > 1000 or shared_device or ...` check to add
  `FILE_REPORT` and `MONITOR_CONNECTED_CARDS` on top of `BLOCK_CARD`/`CREATE_CASE` — matching the
  actual output (`"final": ["BLOCK_CARD (R2, L1)", "CREATE_CASE (R2)", "FILE_REPORT (R2)",
  "MONITOR_CONNECTED_CARDS (R2/R6)"]`). With `shared_device` correctly `False` (and
  `exposure_usd=100.09` not `>1000`), that condition is false, so the result would be just
  **`BLOCK_CARD (R2, L1) + CREATE_CASE (R2)`** — again matching the by-hand checkpoint's "if the
  customer denies" recommendation *exactly* ("R2 — `BLOCK_CARD` (`L1`...) and `CREATE_CASE`; no
  `FILE_REPORT` unless exposure is later found to exceed $1,000 or a real... connected-fraud
  link turns up").

This is about as clean a confirmation as this kind of review gets: gating `shared_device` on
cardinality would not just be theoretically more correct, it would make the pipeline's actual
next-best-action output match the by-hand ground truth almost exactly, on both the initial and
final action sets, for the one case this task's own test exercises end-to-end.

### A compounding, related issue found while tracing this

`evidence_request_node` passes `is_new_device=state.get("shared_device", False)` into
`simulate_evidence_response` (`src/agent/simulator.py`). This conflates two different concepts:
`shared_device` measures whether this transaction's device fingerprint is shared with *other
cards/customers* (a ring-origin question); `is_new_device` is asked as whether the device is
new *to this account*. They are not the same thing, and here they're actively contradictory:
`docs/manual-case-checkpoint.md` reports this transaction's `identity.csv` row has `id_15:
"Found"` — i.e. explicitly **not** a new device for this account. Because `shared_device=True`
(itself the false positive above), `simulate_evidence_response` marks `looks_anomalous=True`
partly on that basis and its returned string literally says "...from a device new to this
account" — a simulated statement that contradicts the dataset's own ground truth for this case.
This doesn't change the R1-vs-R6 divergence above (which runs through `Findings.shared_device`
directly, not through the simulated response text), but it means the same root cause also
taints the LLM-facing narrative the reassessment step reads, not just the policy engine's
inputs.

### Verdict on this specific concern

**Real, scoring-relevant defect.** Not a defensible design choice, and not adequately scoped
out by the report's "known limitation... explicitly out of scope for Task 12 per the brief"
framing — the brief never actually says ring-signal *quality* is out of scope; it only says the
brief's *draft code* (which the report correctly followed) didn't include a cardinality guard,
the same way the `coordinated`/cluster threshold was itself a fix layered on top of the brief's
draft (0.5 -> 0.95) once it was found to be too permissive. This is the same category of
problem, on the sibling signal, and the fix pattern already exists twice over in this codebase:
Task 8.5's own `SHARES_ORIGIN` edge-building query already caps and excludes buckets over 20
members from contributing an edge at all (`task-8.5-report.md`'s "cap=20" pattern), and this
task's own `CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD` does the analogous thing for
`shared_region`. `shared_device` is the one signal in `gather_evidence_node` with no such guard,
and it happens to be the one signal this task's own end-to-end test fixture (HHG-017) hits the
collision on.

### Recommended smallest fix

Gate `shared_device` on the collision size, mirroring the existing patterns:

```python
DEVICE_NEIGHBORS_COLLISION_CAP = 20  # mirror Task 8.5's SHARES_ORIGIN cap=20 pattern

...
distinct_neighbor_cards = len({n.get("id") for n in neighbors if n.get("id")})
device_signal_is_meaningful = 0 < distinct_neighbor_cards <= DEVICE_NEIGHBORS_COLLISION_CAP
...
"shared_device": device_signal_is_meaningful or coordinated,
```

This is a ~5-line change, requires no schema change (unlike the aspirational
`CLUSTER_MIN_SIZE_FOR_COORDINATED` idea the module already declares but doesn't wire in), reuses
a cap value this same codebase already validated (Task 8.5's `cap=20`), and — per the hand
trace above — would flip this exact test case's next-best-action output to match the by-hand
checkpoint almost exactly. `device_neighbors`'s own `LIMIT 300` means the raw list length is
already capped at 300 and won't itself distinguish "2 related cards" from "299 unrelated
customers" without this additional check on the actual returned count. Whether 20 is the
perfectly-tuned number is a fair question for a follow-up (a real 3-6 card ring should still
clear it; this dataset's device collisions run into the hundreds, so the exact cutoff between
~20 and ~300 likely doesn't matter much for this specific dataset), but *some* such gate,
rather than none, is the fix — and it directly serves next-best-action quality, which the task
brief itself weights at 25% of the hackathon score.

I'd also flag (lower severity, same area) the `is_new_device=shared_device` conflation in
`evidence_request_node` as worth fixing in the same pass, since it's the same signal leaking
into a second, semantically different question with no code change required beyond not passing
it there (or deriving a real is-new-device signal from `card_window`/`customer_cards`
evidence instead, if one is available).

## Other code quality findings

**High severity — reproduced live, contradicts the report's "PASSED" claim**

- **`generate_structured`'s prompt never states the schema's actual JSON key names, and the
  model got one wrong on my re-run, exhausting all 3 retries.** The report claims
  `tests/test_run_case_hhg017.py::test_hhg017_end_to_end_produces_valid_answer` **PASSED**
  (206s). I ran the identical test once, live, independently. It **FAILED** after 286s:

  ```
  pydantic_core._pydantic_core.ValidationError: 1 validation error for AssessmentOutput
  pattern
    Field required [type=missing, input_value={'fraud_pattern': 'card_n...lar_prior_case_ids': []}, ...]
  ...
  RuntimeError: Failed to get valid structured output after 3 attempts
  ```

  Root cause, confirmed by reading `assess_node`'s prompt (`graph_flow.py:287-299`) and
  `generate_structured`/`_chat_raw` (`src/agent/llm.py:74-108`): the system prompt says only
  "Respond with ONLY a single JSON object matching the requested schema" and the user prompt
  describes the desired content in prose ("classify the fraud pattern... estimate
  fraud_probability... list evidence_claims... similar_prior_case_ids") but **never states the
  literal JSON key names**, and the Groq call uses the loose `response_format={"type":
  "json_object"}` (valid-JSON-shaped, not schema-enforced) rather than a strict JSON-schema mode
  or `schema.model_json_schema()` (which the Ollama path *does* pass, per `_chat_raw`'s second
  branch — so this gap is Groq-path-specific, and Groq is the primary/graded backend). The model
  inferred a key name from the prose ("classify the fraud **pattern**") and produced
  `fraud_pattern` instead of `pattern`, all 3 attempts in a row — this wasn't one unlucky
  attempt self-correcting on retry, it was a consistent misread that the retry loop's generic
  "that was not valid JSON... try again" feedback didn't fix in 3 tries. This is exactly the
  kind of thing `assess_node` and `reassess_node` both do (same `generate_structured` call
  pattern), so this failure mode is not confined to one node.

  This directly matters for Task 13's batch-of-20 run: if this failure mode recurs at any
  meaningfully non-trivial rate across 20 cases (plausible — the model was consistent about it
  within this one run, not flaky within-run), some fraction of the batch will hard-fail with a
  `RuntimeError` rather than produce a (possibly imperfect but valid) `AnswerFile`, which is a
  harder failure than a wrong verdict. The report's single passing run cannot rule this out;
  the fact that my one independent re-run hit it says the failure rate is not negligible.

  **Smallest fix**: put the literal expected JSON shape into the prompt explicitly (e.g.
  `f"Respond with exactly this JSON shape: {AssessmentOutput.model_json_schema()}"` or a
  hand-written one-line example with the real key names), so the model isn't inferring key
  names from prose. Better: if Groq's OpenAI-compatible endpoint supports
  `response_format={"type": "json_schema", "json_schema": {...}, "strict": true}` for
  `openai/gpt-oss-120b` (worth checking directly — some Groq-hosted models do support this),
  switch to that, which would make this class of failure structurally impossible rather than
  retry-dependent.

**Medium severity**

- `single_signal`'s heuristic (`graph_flow.py:198`,
  `not evidence[3]["data"] and not coordinated`) treats *any* non-empty `closed_case_lookup`
  result as disqualifying single-signal status — even a `cleared` one. For HHG-017, the one
  prior closed case (`CC-1383`) is `outcome: cleared`, which the by-hand checkpoint treats as
  evidence *for* legitimacy, not as a second signal against it. This is the report's own
  self-flagged limitation, and it's real, but note it *doesn't actually change this case's
  final policy outcome* in the traced scenario above (the R1 path is reached through the
  `if not actions:` fallback, which doesn't check `single_signal` at all) — so fixing
  `shared_device` alone gets this specific case to the right next-best-action even with this
  second bug still present. Still worth fixing for other cases in Task 13's batch where the
  fallback path isn't the one taken.
- `region_neighbors`'s server-side `LIMIT 500` applied before the `window_days` time filter
  (documented candidly in `queries.py:429-439`) — a genuinely in-window transaction can be
  excluded if 500 out-of-window ones for that region sort first. Confirmed (by the module's own
  docstring) to actually bite on this task's own fixture region (`addr1` "204.0"). Inherited
  from Task 10, not introduced here, but Task 12 is a consumer of `region_neighbors` via
  `dispatch_followup_tool`'s `wider_region_check`, so it's live in this task's blast radius.

**Low severity / style**

- `_findings_from_state`'s `undocumented_coordinated` line
  (`state.get("shared_device", False) or state.get("cluster_prior_fraud_rate", 0.0) >= CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD`)
  duplicates the `coordinated` computation from `gather_evidence_node` instead of reading
  `state.get("shared_region")` (which already *is* `coordinated`). Functionally equivalent
  today since `shared_region` is set to exactly `coordinated`, but it's the same threshold
  constant checked in two places with two different expressions — a future change to one could
  silently desync from the other. Minor.
- `apply_followup_node`/`agentic_followup_node` correctly no-op when the LLM declines to call a
  tool; verified the graph edges (`agentic_followup -> apply_followup` unconditional,
  `apply_followup` early-returns on no pending followup) make this a true no-op rather than a
  wasted tool call.
- `run_case.py`'s `verdict`/`status` threshold logic (`>=0.7` fraud, `<=0.15` legitimate,
  else uncertain) is a reasonable first cut but is disconnected from `R1_WEAK_SIGNAL_THRESHOLD`
  (0.85) and `stopping_check`'s 0.85/0.15 bounds in `graph_flow.py` — three different
  probability-band definitions live in three places (`run_case.py`, `policy/engine.py`,
  `graph_flow.py`). None of these are wrong per se and the report already flags this file as a
  "rough join... simplified first draft," but worth consolidating into one shared set of
  thresholds before Task 13 if these bands need to move again.

## Test results

- **`pytest tests/ -k "not live" --ignore=tests/test_run_case_hhg017.py -q`** — ran live in this
  review: **67 passed in 506.59s (0:08:26)**. Matches the report's claimed "67 passed, no
  regressions" exactly. Note `-k "not live"` doesn't actually filter anything (grepped every
  test file: no test name or marker contains "live"), so this is the real full suite, including
  every test that hits the live TigerGraph instance directly (`test_graph_queries.py`,
  `test_tg_client.py`, `test_connected_components.py`, etc.) — the ~8.5 minute runtime is
  consistent with that (network round-trips + first-time GSQL query installs), not a sign of
  anything hanging. No regressions confirmed independently.
- **`pytest tests/test_run_case_hhg017.py -v -s`** — ran live once in this review (real Groq +
  TigerGraph calls). **FAILED in 285.99s**, contradicting the report's claimed "PASSED (206s)".
  See the High-severity finding above for the root cause (LLM produced `fraud_pattern` instead
  of `pattern`, exhausting `generate_structured`'s 3 attempts) — this is Groq's inherent
  run-to-run variability interacting with a real prompt gap (the schema's key names are never
  stated literally), not a flaky test harness or environment problem on my end (same command,
  same fixture, same live graph). I did not re-run it again after the failure, per the
  instruction to run this test once and be economical with Groq calls; the single failure is
  itself the finding — it demonstrates this specific gap is not merely theoretical.
- Independent of both pytest runs, I confirmed the write-back from the report's own prior run
  is present in the live graph (`tigergraph__get_node` on `FraudCase`/`CASE-HHG-017`, matching
  the report's abridged JSON exactly — see above), and confirmed `device_neighbors`/
  `ring_membership`/`closed_case_lookup` results directly against the live graph for HHG-017,
  which is the substantive claim this review most needed to check independently (the
  `shared_device` question) rather than trust the report's numbers.
