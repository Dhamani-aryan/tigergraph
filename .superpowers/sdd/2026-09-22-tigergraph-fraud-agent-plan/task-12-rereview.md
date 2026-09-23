# Task 12 re-review: fix round for `2fccdbe..cf476e4`

**Scope:** verify the two findings from `task-12-review.md` against the fix diff
(`review-2fccdbe..cf476e4.diff`, commit `cf476e4`), with live verification now that the
TigerGraph workspace outage mentioned in `task-12-report.md`'s "Post-review fix round" section
is confirmed back up. Both findings are **ADDRESSED**. No new Critical/Important breakage found
in the fix diff.

## Finding 1 (Important, reliability): LLM schema-key mismatch — **ADDRESSED**

**Claim:** embed the schema's actual field structure into the prompt sent to the model, not just
a cosmetic comment.

**Verified by reading the actual code** (`src/agent/llm.py:74-123`): `generate_structured` now
builds `schema_json = json.dumps(schema.model_json_schema())` and `field_names =
list(schema.model_fields)`, and puts both into the **very first** user prompt (`schema_prompt`,
not only the retry-correction message):

```python
schema_prompt = (
    f"{prompt}\n\n"
    f"Respond with a single JSON object that matches EXACTLY this JSON schema "
    f"(use these exact field names -- {field_names} -- and no others):\n{schema_json}"
)
```

Every retry message also now restates `field_names` explicitly (previously just "that was not
valid JSON, try again"). This is a real fix, not cosmetic — the actual rendered JSON schema
(`"properties"`, real key names) is what reaches the model, confirmed by the new unit test
`test_generate_structured_prompt_states_exact_schema_field_names` asserting `'"pattern"'` and
`'"properties"'` are literally present in the first captured prompt (mocked `_chat_raw`, no live
call) — ran this: **passes**.

**Independent live verification (this re-review, not reused from the report):**
- `tests/test_llm_wrapper.py` + `tests/test_graph_flow.py` (offline/mocked, 10 tests): **10
  passed in 4.63s**, including both new tests for this fix and the two synthetic
  `test_generate_structured_recovers_from_wrong_key_name_via_retry` /
  `..._states_exact_schema_field_names`.
- Re-ran `tests/test_run_case_hhg017.py -v -s` live myself (real Groq + real TigerGraph, the
  exact test that failed non-deterministically in the original review with `fraud_pattern`
  instead of `pattern`): **PASSED in 225.29s**, no schema-key mismatch, valid `AssessmentOutput`
  on both the initial `assess` call and the `reassess` call. This is one more independent
  live pass on top of the report's own two post-fix re-runs (208.56s, 206.27s) — three
  consecutive live passes total since the fix landed, zero recurrences of the failure mode.

The report's claimed "better, not required" alternative (Groq strict `json_schema` mode) was not
adopted — the review itself only asked for the "smallest fix" (embedding
`model_json_schema()`), which is what landed. Not a gap against the finding as stated.

## Finding 2 (Important, accuracy): `shared_device` false-positive gap — **ADDRESSED**

**Claim:** `shared_device = (bool(neighbors) and len(neighbors) < 20) or coordinated`.

**Verified by reading the actual code** (`src/agent/graph_flow.py:50-64, 202-223`): the landed
fix is a distinct-card-count gate, slightly more careful than the claim's literal
`len(neighbors)`:

```python
DEVICE_NEIGHBORS_COLLISION_CAP = 20
...
distinct_neighbor_cards = len({n.get("id") for n in neighbors if n.get("id")})
device_signal_is_meaningful = 0 < distinct_neighbor_cards <= DEVICE_NEIGHBORS_COLLISION_CAP
...
"shared_device": device_signal_is_meaningful or coordinated,
```

This matches the claimed logic's intent exactly (cap=20, OR'd with the untouched `coordinated`
branch) and is arguably better than the literal claim text since it de-dupes by card `id` before
counting rather than trusting raw list length — consistent with the module's own comment
explaining why (`device_neighbors`' `SharedCards` select could in principle repeat a card).

**Independent live verification (this re-review):** wrote a standalone script
(`verify_shared_device.py`) that calls the real `device_neighbors`/`ring_membership` functions
against the live graph for HHG-017 (`3450629` / `C04570-K1`) and runs the exact gating logic by
hand, independent of any cached test output:

```
neighbors count (raw): 299
distinct_neighbor_cards: 299
ring: {'id': 'C04570-K1', 'ring_cluster_id': 'RING-C00001-K1', 'cluster_prior_fraud_rate': 0.8469278812408447}
cluster_rate: 0.8469278812408447
coordinated: False
device_signal_is_meaningful: False
shared_device: False
```

Confirms, live and independently: the 299-card collision is still there (workspace outage didn't
change the underlying data — Card count 13,574 unchanged per the task's own confirmation), the
gate correctly excludes it (`299 > 20`), `coordinated` is correctly `False` (`0.8469 < 0.95`), so
`shared_device` now evaluates to `False`, exactly as claimed.

**Downstream effect, confirmed via this re-review's own live pytest run** (not reused from the
report): with `shared_device=False`, `next_best_actions` came back:

```
initial: [VERIFY_WITH_CUSTOMER (R1), CREATE_CASE (Sec 3a)]
final:   [BLOCK_CARD (R2, L1), CREATE_CASE (R2)]
sar.file: false, reason: "No filing criteria met."
```

No spurious `FILE_REPORT`/`MONITOR_CONNECTED_CARDS`, matching the report's claimed post-fix
output and the by-hand `docs/manual-case-checkpoint.md` conclusion almost exactly. Also spot-checked
`src/policy/engine.py`'s R2 (line 45/49) and R6 (line 73) gates by reading them directly: both are
literally `f.exposure_usd > 1000 or f.shared_device or f.shared_region or f.shared_email` /
`f.shared_device or f.shared_region or f.shared_email` — with all three now `False`/`False`/`False`
for this case, R6 cannot fire and R2's extra `FILE_REPORT`/`MONITOR_CONNECTED_CARDS` clauses
cannot fire either, exactly matching the observed output. The three new synthetic unit tests in
`tests/test_graph_flow.py` (299-card low-rate → `False`, 2-card genuine → `True`, 50-card but
`coordinated` also clears → `True`) all passed in the offline run above, confirming the gate
doesn't over- or under-correct.

## New Critical/Important breakage introduced by the fix diff — **none found**

- Diff is scoped exactly to the two findings: `src/agent/graph_flow.py` and `src/agent/llm.py`
  (32 and 30 lines changed respectively, per `git show --stat cf476e4` — 4 total deletions
  across both, everything else additive), plus two test files (`tests/test_graph_flow.py` new
  at 122 lines, `tests/test_llm_wrapper.py` +58). No other files touched; `git status`/`git log`
  confirm the worktree is clean at `cf476e4` with no uncommitted drift.
- No signature changes, no changes to `apply_policy`, `Findings`, `AnswerFile`, or any
  cross-task interface — nothing that could ripple into Task 13's contract with this task.
- The embedded `model_json_schema()` JSON adds prompt size, but it's small relative to
  `_summarize_evidence_for_prompt`'s existing caps (`max_items=8`, `max_text_len=300`); the live
  run above used 5,358 tokens total, comfortably under the 8,000 TPM cap the original report
  hit before that cap-fix, so no new token-budget regression.
- Pre-existing tests that call `generate_structured` live
  (`test_generate_structured_returns_valid_instance` in `tests/test_llm_wrapper.py`) still pass
  with the new prompt shape — ran as part of the 10-test offline+live batch above.

## Deferred-minor notes (out of scope for this scoped re-review, don't block)

- The original review's Medium-severity `single_signal` heuristic finding (treats any non-empty
  `closed_case_lookup` result as disqualifying, even a `cleared` one) and its Low-severity notes
  (`undocumented_coordinated`'s duplicated threshold expression instead of reading
  `shared_region`; three different probability-band constants across `run_case.py`,
  `policy/engine.py`, `graph_flow.py`; `region_neighbors`' `LIMIT 500`-before-filter issue) are
  all untouched by this fix diff and were not part of the two findings this round was scoped to
  verify. They remain open for a future pass, not blockers here.
- The report's own "Outstanding" section (full clean `pytest tests/ --ignore=...` run blocked by
  the TigerGraph outage) is superseded by the task's own confirmation that the workspace is back
  up and a recent full run (73 tests, ~8 min) already passed clean; this re-review additionally
  re-confirmed the two most relevant slices live (the flaky HHG-017 test, plus the 10
  fix-specific unit tests) rather than re-running the entire suite a third time, per the
  instruction to be economical with Groq calls.

## Verdict

**Both findings ADDRESSED, real fixes (not cosmetic), independently re-verified live against the
now-healthy TigerGraph workspace and Groq.** No new Critical/Important issues introduced by the
fix diff. Task 12 is clear to proceed to Task 13 on this basis; only the pre-existing, explicitly
out-of-scope deferred-minor items above remain open.
