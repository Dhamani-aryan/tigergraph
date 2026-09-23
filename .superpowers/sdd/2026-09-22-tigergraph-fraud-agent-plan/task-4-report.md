# Task 4 Report: Graph schema creation

## Status: DONE (see final addendum — resolved via role grant, not the secret)

## Summary

Implemented all four files the brief specifies (`src/schema/columns.py`,
`src/schema/build_schema.py`, `scripts/create_schema.py`) and ran the schema
creation against the real Savanna workspace. Schema creation is genuinely
blocked by a database-user privilege gap, confirmed with an isolated,
minimal repro — this is exactly the open question the brief flagged, now
resolved (in the negative) rather than left open.

## What was built

- `src/schema/columns.py` — `generate_attrs()` / `to_gsql_attr_list()`,
  exactly as specified in the brief. Verified against the live
  `transactions.csv` header: produces 389 attribute pairs (397 columns minus
  `TransactionID` as PK), correctly typed per `_STRING_COLUMNS`.
- `src/schema/build_schema.py` — `build_schema_gsql()`, `apply_schema()`,
  `add_vector_attributes()`. **One deviation from the brief's literal code:**
  the vertex type `Case` is a GSQL reserved keyword (confirmed live —
  see below) and was renamed to `FraudCase` throughout this file (the
  `CREATE VERTEX` statement, its 3 inbound edges' `FROM` clauses, the
  `CREATE GRAPH` vertex list, and the `add_vector_attributes` loop). This is
  the exact contingency the brief called out in its "Note on `Case` vertex
  type name" and prescribes the same fix. No other file references `Case`
  yet since downstream tasks (12/13) haven't been written.
- `scripts/create_schema.py` — exactly as specified.

Confirmed generated GSQL structure before running live: 9 `CREATE VERTEX`,
12 `CREATE DIRECTED EDGE`, 1 `CREATE UNDIRECTED EDGE` (13 edge types total),
matching the brief's 9/13 counts.

## Live test results — this is the privilege-question answer

**Schema-write operations do NOT work for the `tigergraph12` user. This is
a real, confirmed privilege gap, not a red herring from the earlier
`READ_SCHEMA` denial.**

1. First full run (`scripts/create_schema.py`, before the `Case` rename)
   failed on `CREATE VERTEX Case (...)`:
   `"The specified Identifier 'Case' is a reserved keyword, please use another one."`
   — a genuine GSQL syntax issue, per the brief's own anticipated
   contingency. Fixed by renaming to `FraudCase` (see above).

2. Re-running the full multi-statement schema block after the rename failed
   immediately with:
   ```
   Graph 'FraudInvestigation' does not exist.
   User 'tigergraph12' does not have the permission to run the command. Required privilege on global: WRITE_SCHEMA.
   ```

3. To rule out that this was some artifact of the `USE GRAPH
   FraudInvestigation` prefix statement referencing a not-yet-created graph
   (a plausible alternative, non-privilege explanation), isolated the
   simplest possible case: a single bare statement, no `USE GRAPH` prefix,
   no other statements in the block, against a throwaway vertex name:
   ```
   CREATE VERTEX TestProbe (PRIMARY_ID id STRING)
   ```
   This **also** failed, with the same error:
   ```
   User 'tigergraph12' does not have the permission to run the command. Required privilege on global: WRITE_SCHEMA.
   ```
   Nothing was created by this probe (the statement was rejected outright,
   not partially applied), so no cleanup was needed.

This isolated repro is conclusive: the `tigergraph12` Savanna user lacks
`WRITE_SCHEMA` at the global level, independent of GSQL statement structure,
`USE GRAPH` context, or the graph's existence. Combined with Task 1's
`READ_SCHEMA` denial, this account currently has neither schema-read nor
schema-write privileges — read-side data operations (Task 1's successful
checks) apparently use a different, narrower privilege than either of
these.

**Per the task instructions, I stopped here rather than attempting a
workaround.** No `CREATE GRAPH`, `CREATE VERTEX`, or `ADD VECTOR ATTRIBUTE`
call has succeeded against the live workspace. Nothing exists on the
Savanna workspace as a result of this task's runs.

## Action needed (outside what I can do)

A human needs to grant the `tigergraph12` user `WRITE_SCHEMA` (and likely
`READ_SCHEMA`, per Task 1) global privilege via the Savanna Access
Management UI. Once granted, Steps 5–6 of the brief (`python
scripts\create_schema.py`, then the `LS` verification) should be re-run
as-is — no code changes are expected to be needed beyond what's already
committed.

## Other notes for whoever resumes this

- **The brief's literal run command doesn't work unmodified on this
  checkout.** `.venv\Scripts\python scripts\create_schema.py` from the repo
  root raises `ModuleNotFoundError: No module named 'src'`, because a
  directly-executed script's `sys.path[0]` is its own directory
  (`scripts\`), not the repo root — there's no `pyproject.toml`/`setup.cfg`
  installing this project as a package, only `pytest.ini`'s
  `pythonpath = .` (which only affects `pytest`, not plain `python`). Ran it
  as `PYTHONPATH=. .venv/Scripts/python scripts/create_schema.py` instead,
  which works. Whoever re-runs Step 5 after privileges are granted will
  need to do the same (or set `$env:PYTHONPATH = "."` in PowerShell).
- The `ollama.embeddings()` dimension probe **did** succeed live —
  `nomic-embed-text` returns 768-dim vectors, confirming the daemon is up
  and the brief's "commonly cited as 768-dim" assumption is correct for
  this install. That call happens after `apply_schema()` in
  `scripts/create_schema.py`'s `main()`, so it ran and printed before the
  script hit the (pre-rename) `Case` error inside `add_vector_attributes`'s
  loop on that first run.
- Only the `Case` → `FraudCase` rename was validated as a real fix; the
  remaining ~400 `Transaction` attribute names and the rest of the
  edge/vertex block have not been proven free of further reserved-word or
  type issues, since live testing is blocked past the privilege wall. Budget
  time for a second syntax-fixing pass once privileges are granted and
  Step 5 can actually run to completion.

## Commit(s)

`feat: TigerGraph schema creation from CSV headers, with vector attributes for GraphRAG` — adds `src/schema/columns.py`, `src/schema/build_schema.py` (with the `Case`→`FraudCase` rename), `scripts/create_schema.py`. Local commit only on `tigergraph-fraud-agent`, not pushed.

---

## Addendum: retest with the owner-scoped AdminPortal secret

The coordinator supplied a new credential — a TigerGraph "secret" created
under the workspace *owner's* own AdminPortal profile (alias
`mcpagentsecret`), intended to carry the owner's privileges rather than
`tigergraph12`'s restricted ones. `.env` initially had this placed in
`TG_API_TOKEN`. Tried both auth paths the coordinator suggested, plus
isolated the failure with a direct `pyTigerGraph` reproduction outside
`tigergraph-mcp` entirely. **Still blocked — but the failure mode changed
from "authenticated but under-privileged" to "authentication itself
rejected", across three independent auth mechanisms:**

1. **As-is (`TG_API_TOKEN` = raw secret, used directly as a bearer
   token).** Re-ran the same isolated single-statement probe
   (`CREATE VERTEX TestProbe2 (PRIMARY_ID id STRING)`). Result: a
   **transport-level `401 Unauthorized`** on the raw `POST
   .../gsql/v1/statements` call — not the GSQL-level `WRITE_SCHEMA` denial
   from before. This on its own proves the new credential *is* being picked
   up (the error shape changed), but a raw TigerGraph "secret" isn't a
   valid bearer token as-is — matches the `.env` file's own comment, and
   the coordinator's anticipated contingency #2.

2. **Correct wiring: `TG_SECRET` instead of `TG_API_TOKEN`.** Inspected
   `tigergraph_mcp`'s `connection_manager.py` (in
   `.venv/Lib/site-packages/tigergraph_mcp/connection_manager.py`): it maps
   env var `SECRET` → `AsyncTigerGraphConnection(gsqlSecret=...)`, separately
   from `API_TOKEN` → `apiToken=...`. `pyTigerGraph`'s
   `common/base.py` (lines ~152–157) shows that when `gsqlSecret` is set, it
   builds Basic auth using the **literal reserved username
   `__GSQL__secret`** with the secret as the password — TigerGraph's
   documented direct-secret auth mode, no `/requesttoken` exchange needed.
   Renamed the `.env` key from `TG_API_TOKEN` to `TG_SECRET` (same value,
   via `sed`, without ever printing the secret to output) so this path
   would actually engage instead of being shadowed by the `apiToken` param
   (which pyTigerGraph's auth-header priority — JWT > apiToken > Basic —
   would otherwise prefer). Re-ran the same isolated probe. Result:
   **`('User authentication failed', None)`** — a step further than the
   raw-token attempt (past the "is this even a token" layer, into actual
   credential validation), but still an auth rejection, not the `WRITE_SCHEMA`
   authorization error we're trying to get past.

3. **Explicit `/requesttoken`-equivalent exchange** (the coordinator's
   contingency #2, tried literally). Rather than guess at the raw REST
   shape, used `pyTigerGraph`'s own `getToken()` (which performs exactly
   this exchange — confirmed by reading
   `.venv/Lib/site-packages/pyTigerGraph/common/auth.py`'s
   `_prep_token_request`: for TG 4.x it's `POST {gsUrl}/gsql/v1/tokens`
   with body `{"secret": secret, "graph": graphname}`), called directly
   against a fresh `AsyncTigerGraphConnection` built straight from `.env`'s
   `TG_SECRET`, bypassing `tigergraph-mcp` entirely to rule out any
   subprocess/wiring issue. Result: **`TigerGraphException('User
   authentication failed', None)`** — same rejection, independent
   confirmation outside the MCP server process.

**Baseline sanity check:** re-ran the identical direct-`pyTigerGraph`
`getToken()` call with the original `TG_USERNAME`/`TG_PASSWORD`
(`tigergraph12`) instead of the new secret, against the same host/ports.
This **succeeded** and minted a real JWT (subject `tigergraph12`). This
confirms host, ports, and network path are all fine — the regression is
specific to the new secret value itself, not to `.env` plumbing, port
config, or the exchange mechanism. It also reconfirms today's earlier
finding: `tigergraph12` authenticates fine and is only blocked at the
authorization layer (`WRITE_SCHEMA` denial), a materially different failure
than what the new secret produces (rejected at authentication, before
authorization is even evaluated).

**Conclusion: the new secret value itself is not being accepted by
TigerGraph's auth layer, under any of the three documented ways to use a
TigerGraph secret/token.** This looks like it needs to be re-verified at
the source — re-check in the Savanna AdminPortal that the `mcpagentsecret`
secret is still valid/active (secrets can be revoked, or the copy into
`.env` could have been from a stale UI state), and that it was copied
into `.env` completely and correctly (the value that's there now is 32
alphanumeric characters, no visible truncation/whitespace/quoting
corruption — checked programmatically without printing the secret itself).
I did not attempt to create a new secret myself or otherwise touch the
AdminPortal — that's the same "outside what I can do" boundary as the
original privilege grant.

**`.env` state after this session:** the key was renamed from
`TG_API_TOKEN` to `TG_SECRET` (same value) to match the correct env-var
name `tigergraph_mcp` expects for secret-based auth — this is the
technically correct mapping and should be kept regardless of whether the
secret value itself gets refreshed. `TG_USERNAME`/`TG_PASSWORD` for
`tigergraph12` were left untouched. `.env` is gitignored, so none of this
touched git history.

No code changes were made in this addendum — `src/schema/*` and
`scripts/create_schema.py` are unchanged from the commit above. No new
commit.

---

## Final addendum: resolved via role grant — schema is live

The coordinator reports the user ran
`GRANT ROLE globaldesigner ON GLOBAL TO tigergraph12` as the workspace
owner in the Savanna Query Editor (owner has `WRITE_ROLE`, which
`tigergraph12` itself lacked). `globaldesigner` carries designer
privileges at global scope, including `WRITE_SCHEMA`. **This was the
actual, correct fix — the secret detour in the two addenda above turned
out to be an unnecessary/broken side-path; the real credential
(`tigergraph12` username/password) just needed the missing role.**

### Steps taken

1. **Reverted the secret detour.** Removed `TG_SECRET` from `.env`
   (`sed -i '/^TG_SECRET=/d' .env`, value never printed) so the connection
   goes through `TG_USERNAME`/`TG_PASSWORD` again — `gsqlSecret`, when set,
   takes priority over username/password in `pyTigerGraph` regardless of
   whether the latter are also present, so it had to be fully removed, not
   just left unused alongside the others.

2. **Isolated probe first, as instructed.** Re-ran the same minimal
   repro (`CREATE VERTEX TestProbe4 (PRIMARY_ID id STRING)`) via
   `tg_client.py`. Result: `"Successfully created vertex types:
   [TestProbe4]."` — confirms the role grant fixed the actual privilege
   gap. Dropped it immediately after (`DROP VERTEX TestProbe4` →
   `"Successfully dropped vertex types: [TestProbe4]."`), leaving no
   residue.

3. **Ran the brief's `scripts/create_schema.py` end-to-end.** This
   surfaced one more real (non-privilege) bug: the generated GSQL's first
   statement is `USE GRAPH FraudInvestigation`, which — on a workspace
   where the graph has never been created yet — always fails with
   `"Graph 'FraudInvestigation' does not exist."`, even though `USE GRAPH`
   isn't actually needed (`CREATE VERTEX`/`CREATE EDGE` are global-catalog
   operations; `CREATE GRAPH` itself is what defines the graph, at the end
   of the same block). `tg_client.py` correctly treats that one erroring
   statement as an overall tool failure and raised — but cross-checking
   against the server with `LS` showed every vertex type, every edge type,
   and the graph itself had *actually* been created successfully despite
   the exception (GSQL evidently keeps executing subsequent statements in
   a multi-statement block after one line errors). This was a false
   negative caused by a genuine, fixable bug in the generated GSQL, not a
   privilege problem, so — per the task's own instructions to fix and
   retry GSQL syntax/structural issues — removed the leading
   `USE GRAPH {GRAPH_NAME}` line from `build_schema_gsql()` in
   `src/schema/build_schema.py`.

4. **Verified the fix with a fully clean, from-scratch run**, rather than
   just trusting the already-created (if messily-reported) schema:
   dropped the graph and all 9 vertex / 13 edge types
   (`DROP GRAPH FraudInvestigation` + `DROP EDGE <name>` ×13 +
   `DROP VERTEX <name>` ×9, one call, `success: true`), then re-ran the
   fixed `scripts/create_schema.py` end-to-end. Result: **zero errors**,
   all 9 vertex types, all 13 edge types, the `FraudInvestigation` graph,
   and all three `embedding` vector attributes created in one clean pass —
   including Step 3 (vector attributes), which had never actually run to
   completion before (the earlier live attempts all failed/crashed before
   reaching it).

5. **Verified final live state two ways:**
   - `LS`: exactly 9 vertex types (`Customer, Card, Transaction,
     DeviceProfile, EmailDomain, BillingRegion, ClosedCase, FraudCase,
     KnowledgeDoc`) and exactly 13 edge types (12 directed + `SHARES_ORIGIN`
     undirected), all bound into
     `Graph FraudInvestigation(...)`. Matches the brief's 9/13 counts
     exactly.
   - `tigergraph__list_vector_attributes(graph_name='FraudInvestigation')`:
     confirms all three — `ClosedCase.embedding`, `FraudCase.embedding`,
     `KnowledgeDoc.embedding` — each `dimension: 768, index_type: HNSW,
     data_type: FLOAT, metric: COSINE`.

### Commit

`fix: drop the leading USE GRAPH statement from the schema-creation GSQL`
— the one-line removal in `src/schema/build_schema.py`, plus rationale and
the live verification summary, in the commit body. Local commit only on
`tigergraph-fraud-agent`, not pushed. (The original schema-code commit
from the first pass, `feat: TigerGraph schema creation from CSV headers,
with vector attributes for GraphRAG`, still stands underneath it —
`src/schema/columns.py`, `scripts/create_schema.py` needed no changes.)

### `.env` state

`TG_SECRET` removed (the detour is dead — leaving it in would silently
override username/password auth for anyone who re-runs this later).
`TG_USERNAME`/`TG_PASSWORD` (`tigergraph12`, now with `globaldesigner`)
are what's actually used. The stale explanatory comment block above where
`TG_SECRET` used to be is still in `.env` (harmless, describes history);
happy to strip it if wanted, left it since `.env` isn't tracked by git
either way.

### Concerns / notes for downstream tasks

- **The `Case` → `FraudCase` rename is now load-bearing on the live
  graph**, not just in this file. Task 12/13 (case write-back) and any
  spec references to a "Case" vertex must use `FraudCase`.
- **Column count note:** `LS`'s `Transaction` vertex definition shows
  attributes through `V339` plus `customer_id, ts, channel, risk_score` —
  more than the `V1..V61` visible in a truncated header preview earlier in
  this file's investigation. This is expected: `generate_attrs()` reads
  the *actual* CSV header (all ~390 columns), not a hardcoded list, so
  it's correctly capturing the real file, including the dataset's
  synthetic extra columns (`customer_id`, `ts`, `channel`, `risk_score`)
  alongside the original IEEE-CIS `V1..V339` columns.
- No loading jobs have been run yet — the graph has schema only, zero
  vertices/edges of data. That's out of scope for this task (Task 7) and
  expected.
