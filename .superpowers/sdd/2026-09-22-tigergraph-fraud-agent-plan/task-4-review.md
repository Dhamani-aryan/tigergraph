# Task 4 Review: Graph schema creation

**Reviewer:** independent review session, read-only diff review + live TigerGraph verification.
**Diff reviewed:** `8303f6d..83893ca` (`cf38026` feat commit + `83893ca` fix commit), 3 files, 155 insertions.
**Repo:** `C:\Users\naman\Desktop\tigergraph\.worktrees\tigergraph-fraud-agent`, branch `tigergraph-fraud-agent`.

## Verdict summary

- **Spec compliance: ✅ PASS** (with one process-level finding on cross-task plan-doc consistency, detailed below — not a defect in Task 4's own deliverable).
- **Code quality: ✅ PASS**, no critical/high findings; a few medium/low items listed below.
- **Live schema existence: independently confirmed**, not taken on the report's word. See "Independent live verification" for the raw query and output.

---

## 1. Independent live verification (not trusting the report)

I ran my own read-only `LS` query against the live Savanna workspace, from this checkout, using the project's own `TigerGraphMCP` wrapper (`src/tg_client.py`), exactly as Step 6 of the brief prescribes:

```
PYTHONPATH=. .venv/Scripts/python -c "
import asyncio
from src.tg_client import TigerGraphMCP
async def main():
    async with TigerGraphMCP() as tg:
        print(await tg.gsql('LS'))
asyncio.run(main())
"
```

Result (`success: True`, parsed from the live GSQL `LS` output), independently confirms:

- **9 vertex types**: `Customer, Card, Transaction, DeviceProfile, EmailDomain, BillingRegion, ClosedCase, FraudCase, KnowledgeDoc`.
- **13 edge types**: 12 `DIRECTED EDGE` (`OWNS, MADE, FROM_DEVICE, PURCHASER_EMAIL, BILLED_IN, NEXT, INVOLVES, ON_CARD, CONNECTED_TO, CASE_INVOLVES, CASE_ON_CARD, CASE_CONNECTED_TO`) + 1 `UNDIRECTED EDGE` (`SHARES_ORIGIN`).
- **3 vector attributes, each 768-dim, HNSW, FLOAT, COSINE**: `ClosedCase.embedding`, `FraudCase.embedding`, `KnowledgeDoc.embedding` — exactly matching the report's claim and the brief's Step 3 requirement.
- **`Graph FraudInvestigation(...)`** binds all 9 vertex types and all 13 edge types.
- `Card` carries `ring_cluster_id STRING, cluster_prior_fraud_rate DOUBLE` — matches what the global constraints say Task 3/downstream tasks expect.
- `Transaction` has `WITH ... PRIMARY_ID_AS_ATTRIBUTE="true"` as specified, and its attribute list is the full CSV-header-derived set (V1–V339 plus `TransactionDT, TransactionAmt, ProductCD, card1–card6, addr1, addr2, dist1, dist2, P_emaildomain, R_emaildomain, C1–C14, D1–D15, M1–M9, customer_id, ts, channel, risk_score`), typed `STRING`/`DOUBLE` consistent with `_STRING_COLUMNS`.

**This independently corroborates every specific claim in the report's "Final addendum" section** (9 vertex / 13 edge / 3-vector-at-768-dim, `FraudCase` not `Case`). I did not just re-read the report — I queried the live workspace myself in this session and got matching output.

I also independently re-ran `generate_attrs('transactions.csv', primary_key='TransactionID')` from this checkout and got **396** attribute pairs, not the **389** the report states ("397 columns minus TransactionID"). The actual transactions.csv header has 397 columns total, so 397 − 1 = 396, not 389. This is a minor factual slip in the report's narrative (off by 7) — it does not affect correctness of the actual artifact, since the live schema was built by the same `generate_attrs()` function reading the same file, and what's live matches what the function produces (396), not what the report said (389). Flagged as a report-accuracy nit, not a code defect.

---

## 2. Spec compliance

### 2.1 Files delivered — ✅ matches brief exactly
`src/schema/columns.py`, `src/schema/build_schema.py`, `scripts/create_schema.py` — all three created, matching the brief's file list.

### 2.2 `columns.py` — ✅ byte-for-byte match to the brief's literal code
No deviation. `generate_attrs()` / `to_gsql_attr_list()` implemented exactly as specified.

### 2.3 `build_schema.py` / `create_schema.py` — ✅ matches brief, with two justified, well-documented deviations

**Deviation 1: `Case` → `FraudCase` rename.** The brief's own "Note on `Case` vertex type name" (brief line 141) explicitly anticipated this exact contingency and prescribed exactly this fix if `CREATE VERTEX Case` collided with a reserved word. The report confirms live that it did collide (`"The specified Identifier 'Case' is a reserved keyword, please use another one."`), and the rename was applied. I independently checked `src/schema/build_schema.py` line-by-line: the rename is applied **consistently** — the `CREATE VERTEX` statement, all 3 inbound edges' `FROM` clauses (`CASE_INVOLVES`, `CASE_ON_CARD`, `CASE_CONNECTED_TO`), the `CREATE GRAPH` vertex list, and the `add_vector_attributes()` loop tuple all say `FraudCase`, not `Case`. No stray `Case` references remain in this file. The live `LS` output confirms the graph itself was built with `FraudCase`. **This part is fully consistent.**

**Deviation 2: `USE GRAPH {GRAPH_NAME}` line removed.** Confirmed by reading the current `build_schema_gsql()` source: the function body now starts directly with `CREATE VERTEX Customer ...` — no `USE GRAPH` line anywhere. The commit `83893ca` diff is a clean 2-line-only removal (nothing else touched), and its commit message states the correct rationale (a fresh workspace has no graph yet, so `USE GRAPH FraudInvestigation` as the first statement always fails with "Graph does not exist", even though `CREATE VERTEX`/`CREATE EDGE` are global-catalog operations that don't need `USE GRAPH`, and `CREATE GRAPH` at the end of the same block is what actually defines the graph). I confirmed this doesn't remove anything needed — `CREATE VERTEX`/`CREATE EDGE` statements are indeed global-catalog operations in GSQL and don't require a graph context, and the live schema was verified to build cleanly end-to-end without it. **The fix is real, minimal, and correct.**

### 2.4 Cross-task naming consistency — ⚠️ real gap, but not a Task-4 code defect

This is the one finding worth flagging carefully, per the review brief's explicit ask to check it.

- **In code**: the rename is 100% consistent — nothing outside `src/schema/build_schema.py` references `Case` as a vertex type yet, because Tasks 10/12/13 haven't been implemented as code.
- **In the current plan document** (`docs/superpowers/plans/2026-09-22-tigergraph-fraud-agent-plan.md`), as of Task 4's own committed diff, Task 10's `retrieve_knowledge()` and Task 12's `_write_case_to_graph()` **still read `Case`**, not `FraudCase` — e.g. `INSERT INTO VERTEX Case VALUES (...)`, `tg.upsert_vectors("Case", "embedding", ...)`, `tg.search_top_k_similarity("Case", "embedding", ...)`, and the Self-Review Notes section ("new `Case` vertices were written..."). Since the live graph has no `Case` vertex type at all (only `FraudCase`), any implementer following the plan text literally for Task 10/12 would immediately fail against the real schema.
- The report itself is honest about this: its "Concerns / notes for downstream tasks" section explicitly says *"The `Case` → `FraudCase` rename is now load-bearing on the live graph, not just in this file. Task 12/13 (case write-back) and any spec references to a 'Case' vertex must use `FraudCase`."* That's the correct call-out, and it's Task 4's job to flag this, not to silently omit it — which it didn't.
- **However**, updating the plan document is arguably Task 12/13's concern, not Task 4's file-list (`src/schema/*`, `scripts/create_schema.py`) — so I'm not marking spec compliance ❌ over this.
- **Observed at review time**: there is an **uncommitted** working-tree edit to the plan doc (`git diff docs/superpowers/plans/...` shows 17 insertions/17 deletions, not part of either of Task 4's two commits) that updates most of these downstream references — Task 4's own note, Task 10's `retrieve_knowledge`, and Task 12's `_write_case_to_graph` all now read `FraudCase` in the working tree. This is presumably a concurrent/in-progress fix from elsewhere (this is a shared worktree), not something Task 4 did. It's incomplete: the Self-Review Notes prose at line 3635 ("new `Case` vertices were written but never embedded... `retrieve_knowledge` search `Case` alongside `ClosedCase`") was **not** updated and still says `Case`. This is cosmetic (prose only, not executable), but worth a follow-up cleanup pass.

**Net: the rename is real and consistently applied where Task 4 actually owns the artifact (code + live schema). The plan document's downstream sections had a genuine, confirmed inconsistency as of Task 4's commits; it is partially (not fully) remediated as of this review via an uncommitted edit outside Task 4's scope.**

### 2.5 Credential leakage — ✅ none found

- Full diff (`review-8303f6d..83893ca.diff`) contains no secrets, tokens, or passwords — only schema/GSQL code.
- `.env` was never committed: `git log --all --oneline -- .env` returns nothing.
- `.gitignore` correctly excludes `.env` and `.env.*` (with `.env.example` allowed).
- `git grep` across tracked `.py`/`.md` files for `TG_SECRET|TG_PASSWORD|TG_API_TOKEN` finds only one hit: a placeholder `TG_PASSWORD=changeme` in the plan doc's example `.env` snippet — not a real credential.
- The report (`task-4-report.md`, itself untracked/uncommitted in this worktree per `git log --follow` returning nothing) discusses the privilege saga and secret-auth detour in detail but only ever names the secret's **alias** (`mcpagentsecret`), never its value — confirmed by targeted grep for the alias and for any 32-char alphanumeric-looking secret pattern in the report; none found.

---

## 3. Code quality findings, by severity

**Critical / High: none.**

**Medium:**
- `identity_csv` is accepted as a parameter by both `build_schema_gsql()` and `apply_schema()` but is **never used** — no identity.csv column ever appears in the generated GSQL (only `transactions_csv` feeds `generate_attrs()`). This is inherited verbatim from the brief's own literal code (the brief's `apply_schema` signature already had this unused parameter), so it's not a new defect introduced by this implementation — but it's a real dead-parameter smell worth flagging for whoever revisits this file, since it silently implies identity-file columns are incorporated into the schema when they are not.
- No unit tests were added for `src/schema/columns.py` or `src/schema/build_schema.py` (`tests/` only has `test_card_ids.py` and `test_tg_client.py`). These are pure, cheaply-testable functions (`generate_attrs`, `to_gsql_attr_list`, `build_schema_gsql`) that could be verified offline without hitting the live workspace. The brief didn't explicitly require a test file for this task (unlike Task 3), so this isn't a spec deviation, but it's a missed opportunity for a regression safety net, especially given how much live-iteration churn this task went through (reserved-word collision, `USE GRAPH` bug).

**Low:**
- Report accuracy nit: the report states 389 generated `Transaction` attributes; independently re-running `generate_attrs()` against the live `transactions.csv` gives 396. Doesn't affect the actual delivered schema (which is self-consistently generated by the same function), just a narrative inaccuracy in the report.
- Plan-doc Self-Review Notes (line 3635) still says `Case` instead of `FraudCase` even after the partial uncommitted cleanup pass described in §2.4 — purely cosmetic/prose, but should be swept up in whatever change finishes that consistency pass.
- `build_schema_gsql()`'s docstring-less multi-hundred-line f-string is functionally fine but has zero inline documentation of the vertex/edge design itself (relies entirely on the brief/spec for context) — not a defect, just worth noting for future maintainers skimming the file in isolation.

---

## 4. Process observations (not scored, for context)

The privilege saga documented across the report's two addenda is a legitimate, well-isolated debugging trail: it correctly distinguished authentication failure (bad secret) from authorization failure (`WRITE_SCHEMA` denial for `tigergraph12`), used minimal single-statement repros before concluding, verified the baseline (`tigergraph12` username/password) still worked to rule out network/host issues, and reverted the dead-end secret detour cleanly once the role-grant fix (`GRANT ROLE globaldesigner ON GLOBAL TO tigergraph12`) resolved it. The final clean-slate re-verification (drop everything, re-run from scratch, confirm via both `LS` and `list_vector_attributes`) is exactly the right level of rigor for a task whose entire point was "prove this really happened on a live system" — and my own independent `LS` query in this review reproduces that same state today.

---

## 5. Bottom line

- **Spec compliance: ✅** — all required files, all required GSQL constructs, all three vector attributes at the correct dimension, live-verified independently. The one real gap (plan-doc downstream references to `Case` vs `FraudCase`) is a legitimate cross-task consistency issue correctly flagged by the report as a downstream concern, and Task 4's own artifact (code + live schema) is internally consistent.
- **Code quality: ✅** — clean, minimal, brief-compliant implementation; only medium/low nits (unused `identity_csv` param inherited from the brief, no unit tests, one report-accuracy slip, one leftover prose reference).
- **Live verification: confirmed independently in this review session**, not inferred from the report — 9 vertex types, 13 edge types, 3 vector attributes at 768 dimensions on `KnowledgeDoc`/`ClosedCase`/`FraudCase`, all bound into `Graph FraudInvestigation`.
