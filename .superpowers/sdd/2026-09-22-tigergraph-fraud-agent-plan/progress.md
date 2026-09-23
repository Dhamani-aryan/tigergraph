# SDD ledger — plan: docs/superpowers/plans/2026-09-22-tigergraph-fraud-agent-plan.md

Spec: docs/superpowers/specs/2026-09-22-tigergraph-fraud-agent-design.md
Worktree: .worktrees/tigergraph-fraud-agent, branch tigergraph-fraud-agent
Base commit before execution: d0fff7c (includes pre-flight fixes below)

## Pre-flight scan

Scanned every task pair sharing a file or interface, plus each task's internal
consistency (code vs. the tests/values it specifies against itself).

| Task A | Task B | Shared file/interface | Finding |
|---|---|---|---|
| 3 (card_ids.py) | 7 (loading_jobs.py) | `build_card_id_map`/`card_id_for` | **Bug found and fixed.** Task 7 originally keyed `Card` by bare `customer_id`, ignoring Task 3 entirely. Rewrote Task 7 to resolve `card_id` via Task 3's functions before creating any Card/OWNS/MADE data. |
| 3 (card_ids.py) | 8 (derive_entities.py, orig.) | `fix_card_ids` post-hoc patch | **Bug found and fixed.** Even a "default -K1, patch exceptions after" design is unsound — a customer has one transaction set, so `MADE` edges would stay pointed at the wrongly-defaulted card while the patched, correctly-suffixed card sits empty. Removed `fix_card_ids` entirely; Task 7 now resolves correctly from the start. |
| 4 (schema) | 7 (loading_jobs.py) | `Card` attribute count (4 declared, 2 supplied at insert) | Flagged, not blocking — Task 8.5 unconditionally overwrites `ring_cluster_id` on its first pass regardless of any default GSQL assigns to the omitted attributes, so a partial-attribute INSERT (if GSQL even requires flagging this) is self-correcting. Left a note with the explicit-default fallback in Task 7's text. |
| 4 (schema) | 12 (run_case.py) | `Case` vertex field order | **Bug found and fixed.** `_write_case_to_graph` never received the `verdict`/`status` values `run_single_case` already computed, so it hardcoded `status="open"` and wrote `pattern` into the `verdict` slot. Threaded both through as explicit parameters; added a field-order comment against Task 4's schema. |
| 3 | 4, 7, 10 (stale prose) | Interfaces bullet text | Task 3 claimed "Task 4's loading job and Task 10's graph tools" import `card_id_for` — neither is true; only Task 7 does. Corrected. |
| 8 | commit file list | `scripts/fix_card_ids.py` | Referenced in Task 8's Step 5 commit command but never created anywhere (the function lived inside `derive_entities.py`). Removed the phantom path from the commit list (moot now that `fix_card_ids` itself is gone). |
| 6 (schemas.py) | 12, 14, 15 | `AnswerFile`/`CaseRecord`/`SAR`/`NextBestActionSet` field names | Clean — all three consumers use exactly the README-derived field names Task 6 defines; Task 6's own tests parse the README's worked example verbatim, so this is grounded, not assumed. |
| 5 (policy engine) | 12 (`_findings_from_state`) | `Findings` field names | Clean — every field `_findings_from_state` constructs matches Task 5's `Findings` model exactly. |
| 10 (queries.py) | 12 (graph_flow.py) | function names (`card_window`, `ring_membership`, `dispatch_followup_tool`, `FOLLOWUP_TOOL_SCHEMAS`, etc.) | Clean — every name Task 12 imports is defined in Task 10 with matching signature. |
| 11 (llm.py) | 12, 13 | `generate_structured`, `generate_with_tools`, `token_tracker` | Clean — matches. |
| 13 (sar_writer.py) | 12 (run_case.py wiring) | `write_sar_narrative` | Clean — Task 13 explicitly shows the edit to `run_case.py`'s `SAR(...)` construction. |
| 12 (`InvestigationState`) | its own node functions | every state key read/written | Clean — `cluster_prior_fraud_rate` and `_pending_followup` (added for Task 8.5/the agentic round) are present in the TypedDict; `.get()` is used everywhere a key might not yet be set (e.g. `evidence_requests` before the first evidence-request round), so no KeyError risk. |

**Known, already-flagged (not a scan finding, not blocking):** Task 12's note that `_amount_from_trigger_text` needs to actually be threaded through `graph_flow.py`'s `row.get('flagged_amount', ...)` placeholders remains an open item for that task's implementer to close — it's explicitly called out in the task text with the exact regex/approach, not a silent gap.

## Task 6: Answer-JSON Pydantic schemas

Task 6: complete (commits 517da8c..1e9f6fe, review clean — spec ✅ field-by-field
audit against README's Answer Format section passed with zero discrepancies
including the highest-risk `pattern` 7-value enum; quality ✅ PASS, 2 low-severity
non-blocking notes: no 0-1 range constraint on fraud_probability, ActionEntry.action
is plain str not a Literal of the 14 policy action names -- both defensible given
this task's scope, deferred).

## Task 5: Deterministic policy engine (rules R1-R10)

Task 5: fix round 1/5 (1 addressed, 0 open; commits 98cc215..517da8c). Two genuine
judgment calls surfaced during this task, both on the single most heavily-scored
piece of logic in the system:

1. **R1 threshold (0.70 -> 0.85), implementer's own deviation from the brief's
   reference code.** The brief's literal code matched R1's literal text ("below
   0.70") but contradicts the README's own worked example (HHG-017: VERIFY_WITH_CUSTOMER
   at probability 0.72, citing R1). Implementer raised the threshold to 0.85 (reusing
   README §6/R8's own "settled" boundary). Reviewer independently traced the code and
   proved this is structurally safe -- BLOCK_CARD/BLOCK_ALL_CARDS only ever fire from
   the early-return `denies` branch, never reachable through the R1 gate, so the
   widening is purely additive caution and cannot suppress any block. Accepted.
2. **Sec 3a general CREATE_CASE trigger, found by the reviewer (Important, confirmed
   with a concrete counterexample):** cases only opened from specific rule branches
   (R2/R6/R7/R9), never from the README's general "probability >= 0.30" trigger.
   Fixed in the fix round; re-review confirmed ADDRESSED and independently verified
   the highest-risk part (R3/confirmed_legitimate and R7/disputes_recurring must stay
   unaffected) is a structural guarantee -- both branches `return` before `_finish()`
   is ever reached, not just test luck.
   Re-review surfaced a NEW, non-blocking tension: this fix now puts CREATE_CASE in
   the worked example's `initial` actions (prob 0.72 >= 0.30), but the README's own
   example only shows it in `final`. No test regresses; this is the same class of
   README self-inconsistency as finding #1, pointing the other direction.

Ruling: kept the Sec 3a fix as-is (literal policy text applies unconditionally,
CREATE_CASE is a low-cost auto-approved action with no customer impact) rather than
special-casing the engine to suppress it and match one worked example's narrative
omission. Reasoning: the general rule text is explicit and unambiguous for all 20
real case-pack cases, none of which have a worked example to defer to; narrowing
the engine's behavior to fit one specific example's incidental omission would make
it LESS policy-compliant everywhere else. Cost if wrong: HHG-017 itself isn't one
of the 20 graded cases (it's the README's own illustrative example), so this
ruling has zero direct scoring impact even if the interpretation is later judged
wrong -- the risk is purely about matching the README's implied house style, not
about any graded case's correctness.

Task 5: complete (commits 43d6dfd..517da8c, 1 fix round addressed, review clean).
Deferred minors from the original review (none load-bearing, not re-verified after
the fix round since out of its scope): unhandled `confirmed_fraud` Findings literal
(dead code -- Task 12's response-mapping never actually produces this value), R5's
"purchase already cleared" and R8's "evidence conflicts" sub-clauses not
implementable from the current Findings model (would need richer signals from
Task 12), R6 fires FILE_REPORT without an explicit probability gate (partially
mitigated since shared_device/shared_region are themselves derived from
high-cluster-prior-fraud-rate evidence, per Task 8.5), two loosely-labeled
fallback reason strings, minor dead defensive code in the R1 branch.

## Task 13: SAR narrative generation

Task 13: fix round 1/5 (1 addressed, 0 open; commits 57f8cbe..3736cd0). Implementer's
own flagged gap (SAR-filing branch never exercised end-to-end since HHG-017 doesn't
trigger one) closed by the reviewer via a monkeypatched run_single_case forcing
sar_file=True through the real code path -- narrative filled correctly, grounded,
no hallucination. Reviewer found one real Medium finding: the narrative never
mentioned a date despite README's SAR spec requiring "when (dates)" as one of six
elements, because opened_at was never passed into the prompt even though it's used
two lines later for SAR.activity_dates (an internal inconsistency between the two
fields). Fixed and re-verified live: narrative now opens "On 2016-11-12, the
customer identified as C04570..." in the same format activity_dates uses.
Task 13: complete (commits cf476e4..3736cd0, 1 fix round addressed, review clean).
Deferred (low, non-blocking): subjects list can under-represent devices named in
the narrative (a Task 12 design choice, not Task 13's); no error-handling fallback
if the live SAR-narrative call fails.

**STOPPING HERE per user instruction** -- user wants to review a teammate's
codebase/workflow before proceeding to Task 14, then run Task 14 once aligned,
then build a more interactive frontend (beyond the plan's basic Streamlit
dashboard) for Task 15. Task loop paused, not abandoned -- resume at Task 14
when instructed.

## Task 12: LangGraph investigation flow + case write-back

Task 12: fix round 1/5 (2 addressed, 0 open; commits 634979a..cf476e4). First
real end-to-end integration of everything (Tasks 3,5,6,8.5,9,10,11) against live
TigerGraph + live Groq. 5 genuine bugs found and fixed during implementation:
flagged_amount regex parsing (a previously-known open item, now closed), a
LangGraph async-closure bug (node lambdas returned un-awaited coroutines),
evidence payload exceeding Groq's 8000 TPM cap (added evidence summarization),
the bounded agentic follow-up hallucinating card_id="unknown" (added
_resolve_followup_arguments to override with known state values), a Windows
console Unicode crash in tests.
Review found two Important, scoring-relevant findings, both fixed and
independently re-verified live:
1. LLM schema-key mismatch (non-deterministic: passed for the implementer,
   failed on the reviewer's own re-run -- LLM produced `fraud_pattern` instead
   of `pattern`, exhausting retries). Fixed by embedding the schema's actual
   field names into the prompt from the first attempt, not just the retry
   message. Re-review ran the live test a third consecutive clean time.
2. `shared_device` had no false-positive guard unlike its sibling `coordinated`
   signal (which already required cluster_prior_fraud_rate >= 0.95). Confirmed
   live that HHG-017's device_neighbors returns the same 299-card generic-
   fingerprint supercluster Task 8 already identified as noise. Fixed with a
   cardinality cap (<=20, mirroring Task 8.5's own precedent). Re-review
   independently verified live: shared_device now correctly evaluates False for
   this case, and critically, the resulting next_best_actions now MATCH the
   by-hand manual checkpoint (VERIFY_WITH_CUSTOMER+CREATE_CASE initial,
   BLOCK_CARD+CREATE_CASE final, no spurious FILE_REPORT/MONITOR_CONNECTED_CARDS).
Operational note: a genuine TigerGraph Cloud outage (workspace auto-suspend,
not a code bug) interrupted verification for several hours mid-review; killed
a hung stuck process during it, workspace was manually resumed by the user,
all data confirmed intact afterward (exact pre-outage counts), full 73-test
suite re-run clean (471s, all live).
Task 12: complete (commits 634979a..cf476e4, 1 fix round addressed, review clean).
Deferred (medium/low, non-blocking): single_signal closed-case heuristic,
duplicated undocumented_coordinated threshold expression, scattered probability
constants, region_neighbors' LIMIT-before-filter (already triaged Minor by
Task 10's own review).

## Task 11: LLM wrapper (Groq/Ollama)

Task 11: complete (commits 755db8f..634979a, review clean — spec ✅ PASS, quality
PASS with Medium/Low findings deferred). Groq model reconfirmed live and
unchanged (`openai/gpt-oss-120b`, 131072 context, real tool-calling verified).
Reviewer independently confirmed the one thing the implementer's own report
flagged as unverified: the `LLM_BACKEND=ollama` fallback path genuinely works
end-to-end (ran `generate_structured` against real `qwen3:4b-instruct` in a
fresh subprocess, got valid schema-conforming output on two different schemas)
-- this is the safety net the whole project falls back on if Groq's free tier
gets rate-limited during Task 14's batch run, so confirming it for real mattered.
New `conftest.py` (`.env` loading, not in the brief's file list) assessed as a
reasonable, correctly-scoped fix with no interaction risk against
`TigerGraphMCP`'s separate env-parsing approach.
Deferred (medium, non-blocking, confirmed non-issue given actual design): async
functions do blocking I/O with no `asyncio.to_thread` -- would matter if
something used `asyncio.gather` for concurrency, but Task 14's batch run is a
plain sequential `for` loop (verified), so this never actually bites. Also
deferred: `max_tool_calls` param has no effect on call count (empirically still
bounded to one call), a few low-severity nits, and a report miscount (62 vs
actual 67 full-suite tests, cosmetic only).

## Task 10: Graph evidence-gathering functions

Task 10: fix round 1/5 (1 addressed, 0 open; commits ed33193..4048fd1). Major live
finding independent of the already-known primary_id_as_attribute gap: `INTERPRET
QUERY` rejects parameters outright on this server -- all 6 deterministic query
functions needed a full rewrite to `CREATE QUERY`/`INSTALL QUERY` with typed
`VERTEX<Type>` parameters (the same installed-query pattern Task 8.5 already used
successfully -- this wasn't a new failure mode, just a different query-submission
path hitting it first). Reviewer independently verified every function via live
calls against known fixtures, not just the test suite.
Review found one Important, live-demonstrated defect (not just a noted limitation):
`card_window` anchored on the card's own latest transaction regardless of intent,
and the reviewer traced Task 12's actual planned caller and reproduced live that
this silently excludes the flagged transaction whenever it isn't the card's most
recent activity (44-day-old flagged transaction dropped entirely). Fixed with a
`reference_txn_id` parameter; re-review independently verified both directions
live (no-reference fallback preserved byte-for-byte, with-reference correctly
anchors on the real transaction). Also fixed: a reproducible concurrency race in
query installation (mitigated with retry, disclosed as mitigation not elimination),
a deprecation warning (root-caused as unfixable client-side, documented not
ignored), one leftover draft query (confirmed removed).
Task 10: complete (commits 7d21659..4048fd1, 1 fix round addressed, review clean).
**Cascade (controller, commit 755db8f):** updated Task 12's `gather_evidence_node`
call site to actually pass `reference_txn_id=flagged_txn_id` -- the fix existed but
nothing used it yet, which would have silently reintroduced the exact bug just fixed.

## Task 9: Knowledge ingestion (GraphRAG)

Task 9: complete (commits fa1ddd9..7d21659, review clean — spec ✅, quality ✅
APPROVE, 2 low-severity cosmetic nits deferred: stale link-count comments, an
overstated "front matter" claim about the OFAC cap). Reviewer independently
verified live counts (KnowledgeDoc 660, ClosedCase 5,565, both vector indexes
Ready_for_query) and, critically, ran real `search_top_k_similarity` queries
itself confirming semantically correct retrieval (not just non-empty vectors) --
e.g. "customer denies a transaction" correctly ranked policy-r2 above r3/r7;
"card testing" surfaced the right policy/pattern docs and a matching confirmed-
fraud ClosedCase. Bug found and fixed during implementation: chunk_text had no
hard cap on oversized paragraphs (pypdf sometimes extracts ~140K chars with no
blank-line break), crashed ollama.embeddings() with a context-length error --
fixed with a whitespace-boundary hard-split, reviewer traced the fix by hand and
confirmed no remaining overflow path. Honest, distinct handling of 8 known-HTML
skips vs. 1 genuine PDF failure (403), and a defensible, clearly-logged judgment
call capping the 21M-char OFAC SDN list to 300/19,339 chunks (sanctions-name
lookup isn't this task's purpose).

## Task 8.5: Connected components graph algorithm

Task 8.5: complete (commits f2b4581..b30b568, review clean — spec ✅ PASS, quality
solid with findings deferred as non-blocking). Real, hard data problem, not a code
bug: device/region/email are all too coarse relative to 13,574 cards (332 regions,
59 email domains, and even DeviceProfile has heavy-collision outliers), so any
degree-cap choice trades off ring-unification against forming a near-meaningless
giant supercluster. Implementer explored the tradeoff space thoroughly (cap
sensitivity data down to cap=3, still percolating); reviewer independently
re-derived every core number from scratch (not copy-checked) and confirmed them
exactly: 13,574 cards, 83.83% baseline, 3,565-card supercluster at 0.847,
43,805 SHARES_ORIGIN edges, full per-dimension histograms. Reviewer also
independently tested the "drop region/email, device-only" alternative and
confirmed it does NOT materially help (3,476 vs 3,565 cards, 2.5% difference) --
validates the implementer's decision not to chase that instead of documenting
the limitation honestly. cap=20 with all three dimensions kept as the
least-bad, evidence-grounded choice.
Deferred (medium, non-blocking): SHARES_ORIGIN's undirected edge has no
discriminator for when a card pair is linked by multiple origin types
(device+region+email) -- sequential inserts overwrite origin_type, which
forecloses a promising future "require 2+ dimension corroboration" refinement.
Also deferred: no partial-batch-failure handling in write_ring_cluster_ids,
one leftover diagnostic query still installed live (harmless).
**Consequential finding, fixed proactively before Task 12 dispatch (commit
fa1ddd9 -- controller work):** Task 12's planned `cluster_prior_fraud_rate >=
0.5` "coordinated" threshold is confirmed meaningless against the verified
83.83% baseline (clears for nearly any populated cluster, including the
near-baseline giant supercluster). Raised to 0.95 per the reviewer's
independently-derived recommendation; a proper minimum-sample-size floor was
considered but would require reopening Task 8.5 for a new schema attribute,
documented as a deferred limitation instead rather than done now.

## Task 8: Derived entities + manual case checkpoint

Task 8: complete (commits b4704e5..621ec99, review clean — spec ✅, quality ✅ PASS,
3 low-severity nits deferred). Reviewer independently reproduced every load count
from raw CSVs (not the report's numbers) and confirmed both live-discovered schema
gaps by reproducing the actual failing queries itself (`Semantic Check Fails` on
`Card.card_id`; `SEM-40: reverse_FROM_DEVICE is not a valid edge type`).

Manual checkpoint on real case HHG-017 did NOT reproduce a card_testing/fraud
verdict -- by-hand recommendation was VERIFY_WITH_CUSTOMER + CREATE_CASE under R1.
This is judged (both by the implementer and independently by the reviewer, who
pulled the card's full 59-transaction history rather than a sample) as an honest,
well-supported finding, not a fabrication: the README's worked JSON example reuses
the HHG-017 label but uses transaction IDs (`T04...`) that cannot exist in this
dataset's real numeric ID format -- the worked example is a format illustration,
not the real answer key for the real case. This does not affect Task 5's earlier
R1-threshold ruling, which was grounded in the policy text's own internal
consistency (README §6/R8's 0.85 boundary), not in HHG-017's specific narrative.

Two forward-looking findings, fixed proactively in the plan text before Task 8.5/10
dispatch (commit f2b4581 -- controller work, not part of Task 8's own scope):
1. No edge has a declared REVERSE_EDGE -- Task 8.5's `reverse_FROM_DEVICE`-style
   references fail. Fixed with GSQL's native `<-(EdgeName)-` backward-traversal
   syntax (no schema change needed).
2. `Card`/`DeviceProfile` lack `primary_id_as_attribute` (only `Transaction` has
   it) -- breaks `WHERE x.primary_key == "..."` filtering, which nearly every
   Task 10 query and part of Task 8.5's label-propagation query does. Flagged
   prominently with the concrete fix pattern (typed `VERTEX<Type>` query
   parameters) rather than blind-rewriting every query's GSQL without a live
   instance to verify against.
Also flagged: a coarse `DeviceProfile` derivation causes one common device
fingerprint to collide across 621 transactions / 299 unrelated customers --
reviewer cross-checked those customers' fraud rate against baseline (88.2% vs
83.8%, not meaningfully elevated) confirming this is fingerprint noise, not a
real ring; noted in `build_shares_origin` as a reason to sanity-check cluster
sizes before trusting device-based `SHARES_ORIGIN` edges.

## Task 7: Bulk data loading

Task 7: fix round 1/5 (1 addressed, 0 open; commits 0bcffeb..b4704e5). Reviewer
independently re-verified live (all 13,574 Card vertices pulled directly, not a
sample) and found one Major reproducibility gap: a live one-off patch for 21
"stub" Card vertices was never committed as code. Fixed by adding
`customer_id_from_card_id`/`backfill_stub_card_customer_ids` to
`src/schema/loading_jobs.py`, wired into `run_all_loading_jobs` after the
`ON_CARD` edge load. Re-review independently confirmed: convention matches
`card_ids.py`'s own split exactly (untouched by the diff), correct wiring order,
5/5 new tests + 29/29 full suite passing, and live graph shows 0/13,574 blank-
customer_id Cards. One deferred minor: `get_nodes(limit=50_000)` hardcoded
ceiling, non-blocking at this dataset's fixed size.
Task 7: complete (commits 1e9f6fe..b4704e5, 1 fix round addressed, review clean).

Major live findings during implementation, each diagnosed with an isolated probe
before fixing at scale:
1. `tg.gsql()` cannot load local files -- `DEFINE FILENAME` resolves server-side,
   not against this machine. Fixed via GSQL runtime-data mode (positional $N refs,
   no path) + `tigergraph__run_loading_job_with_data` (actually uploads local bytes).
2. **This server rejects bare top-level `INSERT INTO VERTEX/EDGE` outright** via
   `tg.gsql()` -- confirmed via isolated probe, parser's "expecting one of" list
   never includes "insert". Fixed via `tigergraph__add_nodes`/`add_edges` (REST++
   batch upsert). Cascaded this fix through Tasks 8/9/12's plan text (commit
   dd73361 -- controller work, not part of Task 7's own scope) since all three
   still used the same now-broken raw-INSERT pattern; without this they would each
   have hit the identical failure when dispatched.
3. `closed_cases_history.csv` has 2,324/5,565 rows with quoted embedded commas;
   GSQL doesn't quote-parse by default, silently dropped 346 rows on the first
   real run (caught only by the count check, not an error). Fixed with
   `quote="DOUBLE"`.
Also hit the brief's own anticipated type-mismatch contingency exactly as
predicted (`ts` column typed DOUBLE, fixed by adding to `_STRING_COLUMNS`,
clean drop-and-recreate), a live transient validLine=0 no-op (now guarded with
a retry-and-raise), and a genuine data characteristic (21 customers legitimately
own two cards from separate closed cases -- Card count 13,574 vs Customer 13,553,
correctly resolved as a Task 7 loading-completeness fix, not a Task 3 redesign).
Verified live counts all match or exceed expectations exactly; spot-check for a
known non-default-suffix card (`C08623-K2`) confirmed correct.

## Task 4: Graph schema creation

Task 4: BLOCKED -> unblocked -> complete (commits cf38026, 83893ca). Timeline:
1. First live run confirmed the privilege gap definitively: `tigergraph12` lacks
   `WRITE_SCHEMA` (global). Also found/fixed, unrelated: `Case` is a GSQL reserved
   keyword, renamed to `FraudCase` throughout (plan's own pre-written contingency).
2. Tried a workaround (a TigerGraph "secret" created under the workspace owner's
   AdminPortal profile) across all three documented auth mechanisms (raw bearer
   token, `__GSQL__secret` Basic auth, explicit token exchange) -- all three failed
   at the AUTHENTICATION layer itself (not authorization), while baseline
   tigergraph12 credentials kept authenticating fine and were only blocked at
   authorization. Concluded the secret itself was invalid/misconfigured, abandoned
   this path rather than keep guessing at TigerGraph's secret mechanics blind.
3. Real fix: researched TigerGraph's actual RBAC docs (role-management and
   user-management pages) rather than continue guessing at Savanna UI navigation.
   Found the built-in `globaldesigner` global role includes designer's privileges
   (which include WRITE_SCHEMA) at global scope, and the exact GRANT syntax gap --
   `GRANT ROLE <role> TO <user>` without an explicit global-scope clause falls back
   to some default graph context rather than global. User ran
   `GRANT ROLE globaldesigner ON GLOBAL TO tigergraph12` via the Savanna Query
   Editor (as the workspace owner, who has WRITE_ROLE) -- succeeded.
4. Resumed implementer with the fix; confirmed via isolated probe, then found one
   more real bug: the generated schema GSQL's leading `USE GRAPH FraudInvestigation`
   always fails on a workspace where that graph doesn't exist yet (even though
   every subsequent statement, including CREATE GRAPH itself, succeeds) -- a
   false-negative failure mode. Fixed by removing that line from
   `build_schema_gsql()`. Proved the fix with a full clean drop-and-recreate:
   9 vertex types, 13 edge types, the graph, and all 3 GraphRAG vector attributes
   (768-dim, COSINE) created with zero errors, independently verified via `LS` and
   `list_vector_attributes`.

Ruling: this whole sequence (definitive diagnosis -> abandoned workaround ->
docs-grounded real fix) is recorded in full because it's exactly the kind of
external-dependency blocker the SDD process's four stop conditions exist for --
nothing here was a judgment call I made unilaterally; every step was either a live
test result or the user's own action in their Savanna account. Cost if any single
step's diagnosis was wrong: none realized -- the final state is independently
verified (clean recreate, `LS` + `list_vector_attributes` confirmation), not taken
on the implementer's word alone.

Task 4: complete (commits cf38026..83893ca, review clean — spec ✅, quality ✅ PASS).
Reviewer independently re-verified the live schema itself (9 vertex types, 13 edge
types, 3×768-dim vector attributes) via its own `LS`/vector-attribute queries against
Savanna, not just the report's claim. Findings, none blocking (Medium/Low, no fix
loop needed per the skill -- only Critical/Important trigger the loop):
Task 4: minor (deferred): unused identity_csv param in apply_schema (inherited from brief)
Task 4: minor (deferred): no unit tests for the pure schema-generation functions (generate_attrs etc.) -- live-run verification substitutes, per this task's own established pattern
Task 4: minor (deferred): report's attribute-count claim (389) doesn't match live-verified actual (396) -- report-accuracy nit only, the live schema itself is correct
Cascading fix (controller): reviewer caught that the Case->FraudCase rename (confirmed
live, GSQL reserved-word collision) hadn't yet propagated through Task 10/12's plan
text or the Self-Review Notes narrative at review time -- this was already in progress
concurrently (commits 9d197c6, 43d6dfd) and is now fully closed; reviewer's own findings
list reflects this as the last gap, not an open one.

## Task 3: card ID derivation

Task 3: complete (commits 21eda23..8303f6d, review clean — spec ✅ exact match to brief, quality ✅ no findings, 4/4 tests independently re-run and confirmed passing)

## Task 2: persistent MCP client wrapper

Task 2: fix round 1/5 (1 addressed, 0 open — unchecked result.is_error silently
swallowed tool failures; commits aaae0e3..bad147b). Fix discovered live that the
real tigergraph-mcp server never sets is_error at all -- failure is signaled via
a "success": false field in a JSON envelope embedded in the response text. call()
now checks both signals and raises RuntimeError with tool name + detail. Re-review
independently verified in code (not just report claims) and by re-running the live
test suite itself: 2 passed.
Task 2: complete (commits 93f58cc..bad147b, 1 fix round addressed, review clean)

**Cascading fix (controller, not part of Task 2's own scope):** the fix changed
`call()`'s success-path return value from a bare parsed result to an envelope dict
(`{"success", "operation", "data", ...}`). Scanned the whole plan for every
structural indexing of a `tg.gsql()`/`.call()` result (not just print/pass-through
usages) -- found exactly one real break: Task 10's `ring_membership` did
`result[0] if isinstance(result, list)`, which would now always fall through to
`{}` since `result` is a dict. Fixed to unwrap `result.get("data")` first, with an
explicit live-verification note for the exact nested shape rather than guessing a
third time (commit 21eda23). Everything else in the plan either prints results for
human inspection or passes them through generically into evidence lists/LLM
prompts with no shape assumptions -- confirmed via grep, not assumed clean.

Ruling: fixed this proactively in the plan text before Task 3+ dispatch rather than
waiting to discover it mid-Task-10. Cost if wrong: the live-verification note means
Task 10's implementer still has to confirm the exact `data` shape by hand regardless
-- worst case this guess needs one more adjustment at that point, same as any other
"first draft, expect live iteration" GSQL-adjacent note already in this plan.

## Task 1: env setup, tigergraph-mcp install, live connectivity proof

Task 1: complete (commits d0fff7c..91ae655, review clean — spec ✅, quality ✅ approved)
Task 1: minor (deferred): discover_mcp_tools.py doesn't mkdir docs/ before writing json (inherited from brief's own code)
Task 1: minor (deferred): no error handling around the MCP connection in the throwaway discovery script
Controller commit 93f58cc (not part of Task 1's review scope): updated all plan-text
references from the now-retired `llama-3.3-70b-versatile` to the live
`openai/gpt-oss-120b` the implementer verified — mechanical find/replace,
grep-verified zero stale references remain, no functional code involved.

**Open gate before Task 2 dispatch:** implementer discovered `tigergraph12` lacks
`READ_SCHEMA` (confirmed via real authenticated permission-denied response, not a
connection failure); `FraudInvestigation` graph doesn't exist yet (expected). Reviewer
independently flagged (Important) that this has only been tested against read/enumerate
operations — Task 2's `CREATE GRAPH`/`CREATE VERTEX` are write-side schema operations
that were never actually probed, so "should be fine" is optimistic, not verified. This
is an external Savanna account/RBAC issue outside my access — asked the user to check
Access Management for a Roles tab and grant `tigergraph12` a schema-capable role
(superuser/globaldesigner/admin) before Task 2 is dispatched. Not a ledger ruling since
it's not my decision to make — genuinely blocked on the user's action, not a judgment
call I'm deferring.

Ruling: both bugs were fixed directly in the plan text (commit d0fff7c on this
branch) rather than left as ledger-only notes, since every task dispatch reads
the brief verbatim — an unpatched plan would have propagated both bugs into
Task 7 and Task 12's actual implementation before any reviewer caught them.
Cost if this judgment is wrong: negligible — both fixes are small, targeted,
and independently verifiable against Task 4's schema and Task 3's functions,
which the task reviewers will still check against the (now-corrected) plan text.
