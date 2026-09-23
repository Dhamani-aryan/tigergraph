# Task 12 report: LangGraph investigation flow + case write-back

**Status: DONE_WITH_CONCERNS** (see "Post-review fix round" below — both review findings are
fixed and live-verified, but a full clean test-suite run against a healthy TigerGraph instance
is still outstanding due to an external Cloud outage, not a code defect).

**Commit:** `2fccdbe` — "feat: LangGraph investigation flow and single-case runner" on branch
`tigergraph-fraud-agent` (worktree `.worktrees/tigergraph-fraud-agent`). Files:
`src/agent/state.py`, `src/agent/graph_flow.py`, `src/run/__init__.py`, `src/run/run_case.py`,
`tests/test_run_case_hhg017.py`.

## What was verified against real code before writing anything

Per the task's own warning not to trust the brief's embedded signatures blindly, read the
actual current files first:

- `src/graph/queries.py` — `card_window(tg, card_id, hours, reference_txn_id=...)`,
  `customer_cards`, `device_neighbors`, `region_neighbors`, `closed_case_lookup`,
  `ring_membership`, `FOLLOWUP_TOOL_SCHEMAS`, `dispatch_followup_tool` all matched the
  brief's assumed signatures and return shapes exactly (the brief had already been kept in
  sync with Task 10's live rewrite).
- `src/graph/vector_search.py` — `retrieve_knowledge(tg, query_text, top_k)` returns
  `{"knowledge": [...], "similar_cases": [...]}`, matches brief.
- `src/agent/llm.py` — `generate_structured(prompt, schema, max_retries=2)` returns a
  validated `BaseModel` instance; `generate_with_tools(prompt, tools, max_tool_calls=1)`
  returns `ToolCallResult(tool_name, tool_arguments, final_text)`; `token_tracker` is a
  module-level `TokenTracker` instance with `.reset()`/`.total`. All matched.
- `src/policy/engine.py` / `src/policy/models.py` — `apply_policy(Findings) -> PolicyResult`,
  confirmed field names.
- `src/tg_client.py` — confirmed `tg.call("tigergraph__add_nodes", ...)`,
  `tg.upsert_vectors(vertex_type, vector_attribute, vectors)` with
  `{"vertex_id", "vector"}` items, matching the brief.

No signature mismatches were found — the brief's draft code was accurate. The real gaps were
all found by actually running the pipeline live (see below).

## Fixes made beyond the brief's draft

1. **Implemented real `flagged_amount` parsing** (the task's explicitly-flagged open item).
   Added `_amount_from_trigger_text` (regex `\$([\d,]+\.\d{2})`) and `_flagged_amount(row)`
   in `graph_flow.py`, used in `evidence_request_node`, `_findings_from_state`, and
   `run_case.py` (including the previously-hardcoded `exposure_usd: 0.0` in the graph
   write-back, which now carries the real parsed amount). The HHG-017 test case deliberately
   omits an explicit `flagged_amount` field so the test actually exercises the regex path
   against real `trigger_text` prose, not a bypass value.

2. **`build_graph`'s lambdas returned un-awaited coroutines.** LangGraph detects whether a
   node is async by inspecting the callable itself, not its return value —
   `lambda s: gather_evidence_node(tg, s)` raised
   `InvalidUpdateError: Expected dict, got <coroutine object ...>` on the very first live
   run. Fixed with real `async def` closures (`_gather_evidence`, `_apply_followup`).

3. **`assess_node`/`reassess_node` blew Groq's token budget.** Passing `state['evidence']`
   verbatim into the prompt produced a ~15,300-token request against this account's 8,000
   TPM cap (`413 Request too large`) — `device_neighbors` alone can return up to 300 card
   dicts (confirmed against this exact fixture: 621 shared transactions / up to 300 shared
   cards, matching `docs/manual-case-checkpoint.md`). Added
   `_summarize_evidence_for_prompt` (caps list-shaped evidence to a sample + total count,
   clips long strings) and used it in `assess_node`.

4. **`agentic_followup_node`'s LLM hallucinated a tool argument.** Its prompt deliberately
   withholds real card/region ids (to avoid the same token blowup as #3), so on live run 2 it
   called `wider_card_window(card_id="unknown", hours=...)`, which TigerGraph's
   `run_installed_query` rejected outright (`Failed to convert user vertex id for parameter
   input_card`). Added `_resolve_followup_arguments`, which overrides identifier-shaped tool
   arguments (`card_id`, `addr1`) with values already known deterministically from state
   before dispatch, leaving only genuinely free parameters (e.g. `hours`) as the LLM chose.

5. **Test-only Unicode encoding crash.** The LLM's free-text evidence claims contained a
   Unicode non-breaking hyphen (U+2011); a plain `print()` of the JSON dump crashed with
   `UnicodeEncodeError` on the Windows cp1252 console. Fixed by writing raw UTF-8 bytes to
   `sys.stdout.buffer` in the test instead of `print()`.

Everything else (thresholds, `FraudCase` vertex type, `tigergraph__add_nodes` write-back,
`upsert_vectors` shape) matched the brief as given and needed no changes.

## End-to-end test output for HHG-017

`.venv\Scripts\pytest tests/test_run_case_hhg017.py -v -s` — **PASSED** (206s; each run makes
6 real graph queries + a bounded agentic tool call + 2-3 real Groq LLM calls, live against
TigerGraph Cloud). Also ran the full pre-existing suite (`pytest tests/ -k "not live"
--ignore=tests/test_run_case_hhg017.py`): **67 passed**, no regressions.

Actual produced answer (abridged):

```json
{
  "case_id": "HHG-017",
  "case": {
    "status": "escalated",
    "verdict": "uncertain",
    "fraud_probability": 0.45,
    "pattern": "card_not_present_fraud",
    "exposure_usd": 100.09,
    "evidence": [
      "Three online transactions within 1 hour window",
      "High ring cluster prior fraud rate 0.85",
      "Device neighbors include many high-fraud accounts",
      "Card belongs to ring cluster RING-C00001-K1",
      "Previous escalated case with same card and pattern",
      "Customer denies purchase and retains card; amount aligns with typical spend, device new to account"
    ],
    "written_to_graph": true,
    "graph_case_id": "CASE-HHG-017"
  },
  "evidence_requests": [{"type": "customer_validation", "assumed_response": "Customer states they did not make this $100.09 purchase and still has the card. (Simulated: amount is 1.0x their typical transaction from a device new to this account.)"}],
  "next_best_actions": {
    "initial": ["CREATE_CASE (R6)", "FILE_REPORT (R6)", "MONITOR_CONNECTED_CARDS (R6)"],
    "final": ["BLOCK_CARD (R2, L1)", "CREATE_CASE (R2)", "FILE_REPORT (R2)", "MONITOR_CONNECTED_CARDS (R2/R6)"],
    "what_changed": "Simulated evidence response changed the recommended actions."
  },
  "sar": {"file": true, "reason": "R2: exposure exceeds $1,000 or activity connects to a shared origin.", "subjects": ["C04570", "C04570-K1"]},
  "stop_reason": "Customer response settled the verdict.",
  "tool_calls": 6,
  "tokens": 5234,
  "latency_s": 203.3
}
```

**Compared against `docs/manual-case-checkpoint.md`'s by-hand ground truth for HHG-017**
(pattern: none of the five known patterns; fraud_probability ~0.25–0.35; single signal =
risk score only; R1 applies → `VERIFY_WITH_CUSTOMER` + `CREATE_CASE`; explicitly *not* a real
shared-device/ring signal — "the shared device profile is a false positive from fingerprint
collision, not a real ring signal"):

- **Same neighborhood, not identical**, as the task instructions said to expect. The agent
  landed on `fraud_probability=0.45` (uncertain band, same rough zone as the by-hand
  ~0.25–0.35) and `pattern=card_not_present_fraud` rather than "none/undocumented" — a
  reasonable LLM read of "3 same-region ~$100 online purchases within an hour," just less
  conservative than the by-hand analyst's judgment that risk-score-alone plus no burst
  pattern doesn't clear any of the five named patterns.
- **The material divergence**: the by-hand analysis explicitly discounts the device/ring
  signal as a coarse-fingerprint false positive and treats this as a genuine single-signal
  case (→ R1). The pipeline's `gather_evidence_node` treats *any* non-empty
  `device_neighbors` result as `shared_device=True` (per the brief's own design — "either
  signal is enough to flag a shared origin"), so it fed R2/R6-driven actions
  (`BLOCK_CARD`/`FILE_REPORT`/`MONITOR_CONNECTED_CARDS`) instead of R1's
  `VERIFY_WITH_CUSTOMER`. This is a known, previously-documented limitation (Task 8/8.5's own
  findings about coarse device/region dimensions), carried forward by design rather than
  introduced here — the task brief explicitly scoped fixing ring-signal quality out of Task
  12 ("documented, not a bug to 'fix' here").
- Both the by-hand and the agent's answer converge on **R2's behavior if the customer
  denies** (`BLOCK_CARD` route `L1`, `CREATE_CASE`), which is exactly the evidence-request
  branch the pipeline actually took (the simulated customer response was "denies").

The pipeline ran genuinely end-to-end on real, non-fixtured behavior: 6 real graph tool
calls (including one live-resolved agentic follow-up call, `wider_card_window`), 2 real Groq
assessment calls (initial + reassess after the simulated evidence request), a real policy
run for both initial and final findings, and a real graph write-back — `FraudCase`
`CASE-HHG-017` upserted via `tigergraph__add_nodes` and its summary embedding upserted via
`tg.upsert_vectors("FraudCase", "embedding", ...)`, both confirmed `written_to_graph: true`
in the output and visible in the live debug log (`upsertVertices`/`upsertVertex` succeeded).

## Concerns / known limitations carried forward (not fixed here, by design)

- ~~**Device/region signal coarseness** (see above) — inherited from Tasks 8/8.5, explicitly
  out of scope for Task 12 per the brief.~~ **Superseded — fixed in the post-review round
  below.** The reviewer correctly pushed back on this framing: the brief never said ring-signal
  *quality* was out of scope, only that its own draft code lacked a cardinality guard. See
  "Post-review fix round" for the `shared_device` cardinality gate that resolves this.
- **`single_signal` heuristic** (`gather_evidence_node`) treats *any* non-empty
  `closed_case_lookup` result as disqualifying "single signal" status, even when that closed
  case's outcome was `cleared` rather than `confirmed_fraud` (true for HHG-017's own prior
  case `CC-1383`). Not fixed — the brief's own note flags several such "rough joins between
  the LangGraph state and the answer schema" as simplified first-draft behavior expected to
  be revisited after seeing real output, and the task instructions say exact matching to the
  by-hand checkpoint isn't the bar for this task.
- **Live run cost**: each HHG-017 run takes ~200s and consumes real Groq tokens (~5,200 this
  run) — expected and budgeted for per the task's own instruction, but worth knowing for
  Task 13's batch-of-20 run (roughly 200s × 20 ≈ 65 min serial, unless Task 13 parallelizes;
  the module-level query-install cache in `queries.py` means only the *first* case in a
  process pays the ~20-30s GSQL install cost).
- Groq rate limits on this account tier (8,000 TPM) are tight relative to a verbose evidence
  pack; `_summarize_evidence_for_prompt`'s caps (`max_items=8`, `max_text_len=300`) were
  tuned against this one fixture and worked, but a case with an unusually large
  `customer_cards`/`closed_cases` list could still approach the cap — not hit in this run,
  flagged for Task 13 to watch for.

## Post-review fix round

**Commit:** `cf476e4` — "fix: state exact schema field names in LLM prompt; gate shared_device
on cardinality" (`src/agent/graph_flow.py`, `src/agent/llm.py`, `tests/test_llm_wrapper.py`,
new `tests/test_graph_flow.py`).

The reviewer (`task-12-review.md`) confirmed the original 5 bug fixes and the write-back as
real, but found two further issues and pushed back on this report's original framing of the
`shared_device` gap as an acceptable, out-of-scope limitation. Both are fixed below.

### 1. (Reliability) `generate_structured` never stated the schema's literal JSON key names

**Root cause:** Groq's `response_format={"type": "json_object"}` only guarantees syntactically
valid JSON, not that the model uses the schema's actual field names. The prompt described the
desired content in prose ("classify the fraud pattern...") but never stated the literal
required keys anywhere. The reviewer's independent re-run of `test_run_case_hhg017.py` hit this
exactly: the model produced `fraud_pattern` instead of `AssessmentOutput`'s real `pattern`
field, consistently across all 3 retry attempts (the old retry message — "that wasn't valid
JSON, try again" — never told it *which* key was wrong, so a consistent misread didn't
self-correct). This is a real risk for Task 13's batch-of-20 run: any case could hit this and
hard-fail with a `RuntimeError` instead of producing an `AnswerFile`.

**Fix:** `generate_structured` (`src/agent/llm.py`) now embeds `schema.model_json_schema()`
directly into the very first prompt sent to the model (not just the retry-correction message),
and every retry message also restates the exact required field names.

**Verification:**
- Two new unit tests in `tests/test_llm_wrapper.py`, both using a monkeypatched `_chat_raw` (no
  live LLM call): `test_generate_structured_prompt_states_exact_schema_field_names` asserts the
  rendered JSON schema (`"properties"`, `"pattern"`) is present in the very first prompt;
  `test_generate_structured_recovers_from_wrong_key_name_via_retry` deterministically
  reproduces the exact `fraud_pattern`-then-`pattern` sequence the reviewer hit live and
  confirms the retry loop recovers.
- Re-ran `tests/test_run_case_hhg017.py -v -s` **twice** live (real Groq + real TigerGraph),
  per instruction to build confidence without unlimited Groq calls: **both PASSED**
  (208.56s and 206.27s). No `fraud_pattern`/schema-key mismatch in either run.

### 2. (Accuracy) `shared_device` had no false-positive cardinality guard

**Root cause:** `shared_device` was set from a bare `bool(neighbors)` — any non-empty
`device_neighbors` result counted as a genuine shared-device signal, with no check on how many
cards shared that device fingerprint. Its sibling `shared_region`/`coordinated` signal, by
contrast, is correctly gated behind `cluster_prior_fraud_rate >= 0.95` specifically because a
low bar let a large, generic collision through as if it were real ring evidence. The reviewer
confirmed live that HHG-017's own `device_neighbors` call returns exactly 299 cards — the same
generic Windows/Chrome/1920x1080 fingerprint collision `docs/manual-case-checkpoint.md` already
identified as noise (cluster fraud rate ~0.847, indistinguishable from the 83.83% dataset
baseline) — and traced by hand that gating on cardinality would flip HHG-017's next-best-actions
to match the by-hand checkpoint almost exactly.

**Fix:** Added `DEVICE_NEIGHBORS_COLLISION_CAP = 20` (mirroring Task 8.5's own `SHARES_ORIGIN`
edge-building `cap=20` precedent) in `src/agent/graph_flow.py`, and `shared_device` now requires
`0 < distinct_neighbor_cards <= 20` (in addition to the existing `coordinated` OR-branch, which
is untouched and still fires independently for a large collision that also clears the 0.95
cluster-fraud-rate bar).

**Verification:**
- Three new synthetic unit tests in `tests/test_graph_flow.py`, all calling
  `gather_evidence_node` directly with monkeypatched graph-query functions (no live graph or
  LLM call): a 299-card low-rate collision → `shared_device=False`; a 2-card genuine collision →
  `shared_device=True` (confirms the gate doesn't over-correct); a 50-card collision that also
  clears the 0.95 coordinated threshold → `shared_device=True` (confirms the `coordinated`
  OR-branch still works independently of the cardinality gate).
- Live-verified against the real HHG-017 case in both of the re-runs above. **Before the fix**,
  `next_best_actions` was `initial: [CREATE_CASE (R6), FILE_REPORT (R6), MONITOR_CONNECTED_CARDS
  (R6)]`, `final: [BLOCK_CARD (R2), CREATE_CASE (R2), FILE_REPORT (R2), MONITOR_CONNECTED_CARDS
  (R2/R6)]`. **After the fix, both live runs produced:** `initial: [VERIFY_WITH_CUSTOMER (R1),
  CREATE_CASE (Sec 3a)]`, `final: [BLOCK_CARD (R2), CREATE_CASE (R2)]` — no spurious
  `FILE_REPORT`/`MONITOR_CONNECTED_CARDS`. This is now an almost exact match to
  `docs/manual-case-checkpoint.md`'s by-hand conclusion ("R1 applies... Recommended action:
  `VERIFY_WITH_CUSTOMER`... plus `CREATE_CASE`"; "if the customer denies... R2 —
  `BLOCK_CARD`... and `CREATE_CASE`; no `FILE_REPORT` unless exposure is later found to exceed
  $1,000..."). As a side effect, this also resolved the reviewer's lower-severity "compounding
  issue" (the simulated customer-response text no longer falsely claims "a device new to this
  account" for a transaction the dataset's own `identity.csv` marks as `id_15: "Found"`, i.e.
  not new) — no code change was needed for that part beyond fixing `shared_device` itself, since
  `evidence_request_node` was already deriving `is_new_device` from the same (now-corrected)
  signal.

### Outstanding: full clean test-suite run blocked by an external TigerGraph Cloud outage

Per the coordinator's instruction, attempted a bounded (`--timeout=120`) full-suite run
(`pytest tests/ --ignore=tests/test_run_case_hhg017.py`) to confirm no regressions from the two
fixes above. Partway through, the live TigerGraph Cloud instance itself started failing:

- 19 of 72 tests failed, all with the identical error
  `RuntimeError: TigerGraph MCP tool 'tigergraph__gsql' failed: 500, message='Internal Server
  Error'` — every failure was a test that makes a real call through `tigergraph-mcp` to the live
  graph (`test_graph_queries.py`, `test_tg_client.py`); every test that doesn't touch the live
  graph (schema, policy engine, LLM wrapper incl. the two new tests, and the three new
  `test_graph_flow.py` synthetic tests) passed. 53 passed.
- Confirmed this is not our code, not the known `_run_installed_query` install-race (that
  produces a distinct `REST-1005: query endpoint ... disabled` error, not a blanket 500), and
  not a client-side hang: a bare `HELP` GSQL command still returned the same 500, and a raw
  `Invoke-WebRequest` HTTP GET to the instance's own `/api/ping` endpoint — no Python code of
  ours involved at all — also returned 500. This is an external TigerGraph Cloud infrastructure
  issue (most likely, per the coordinator, a suspended free-tier workspace needing a manual
  restart via the Savanna console), not something fixable from this codebase and not something
  either of this round's two fixes could have caused.
- Per the coordinator's direction, stopped polling for recovery and am reporting now rather than
  waiting further on an external dependency.

**What this means for this task's status:** both review findings are fixed, live-verified end
to end (two full live `test_run_case_hhg017.py` runs, both PASSED, both showing the corrected
policy output), and covered by new fast/offline unit tests. The one thing NOT yet re-confirmed
is a full, clean, all-green run of the entire pre-existing test suite against a *healthy*
TigerGraph instance — the last attempt is a mix of 53 real passes and 19 outage-caused failures,
not evidence of a regression, but also not the clean bounded run that was asked for. **Task 13
(or whoever resumes this) should re-run `pytest tests/ --ignore=tests/test_run_case_hhg017.py
--timeout=120` once the TigerGraph Cloud workspace is confirmed back up**, before treating this
task as fully closed.
