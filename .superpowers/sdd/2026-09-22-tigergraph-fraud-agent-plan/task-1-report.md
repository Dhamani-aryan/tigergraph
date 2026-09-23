# Task 1 Report: Environment setup, MCP connectivity, and tool schema discovery

**Status: DONE_WITH_CONCERNS**

## Commit

- `91ae655` — "feat: environment setup and MCP tool schema discovery" on branch `tigergraph-fraud-agent`, in the worktree at `.worktrees/tigergraph-fraud-agent`. Not pushed. `master` and the parent repo were not touched.

Files committed: `requirements.txt`, `.env.example`, `scripts/discover_mcp_tools.py`, `docs/tigergraph-mcp-tools.json`, and a `.gitignore` fix (see Concerns). `.env` stays untracked/gitignored as required.

## Verification summary

Ran `scripts/discover_mcp_tools.py` against the live `tigergraph-mcp` server pointed at the real Savanna workspace: it wrote all 69 tool schemas to `docs/tigergraph-mcp-tools.json` (matches the ~69 expected by the brief), and the `tigergraph__list_graphs` sanity call returned a real, structured response from the live GSQL engine — not a network/TLS/port error — confirming the `.env` host/port/credentials are correct. Also independently confirmed with `tigergraph__gsql` (`ls`) and `tigergraph__show_graph_details`, both of which got authenticated, graph-engine-level responses from the server. Separately confirmed the Groq API key is live via a direct chat completion and a tool-calling round-trip against `openai/gpt-oss-120b`.

## What was done

1. Created `.venv` with the pinned Python 3.10.5 interpreter (`C:\Users\naman\AppData\Local\Programs\Python\Python310\python.exe`), not the ambient `python`, so it isn't accidentally 3.14 or a conda interpreter.
2. Installed all packages from `requirements.txt` (as specified in the brief), pulling `tigergraph-mcp-1.0.3`, `mcp-2.2.0`, `langgraph-1.2.12`, `openai-3.17.0`, `ollama-0.6.2`, etc. — no install failures.
3. Confirmed both required Ollama models are present: `qwen3:4b-instruct` and `nomic-embed-text:latest`.
4. Wrote `scripts/discover_mcp_tools.py` per the brief, with one required fix (see Concerns #1), and ran it successfully against the live Savanna workspace.
5. Added the Step 7 findings (confirmed exact parameter names for `gsql`, `create_graph`, `create_loading_job`, `upsert_vectors`, `search_top_k_similarity`, `run_installed_query`, `install_query`) as a comment block at the top of `scripts/discover_mcp_tools.py` for Tasks 2, 3, 8–11 to reference.
6. Left `.env`'s `TG_RESTPP_PORT`/`TG_GS_PORT` at `443` — confirmed correct, did not need the Community Edition fallback (see Concerns #2).
7. Updated `.env`'s `GROQ_MODEL` from the plan's `llama-3.3-70b-versatile` to `openai/gpt-oss-120b`, the current strongest general-purpose Groq model with confirmed tool/function-calling support (see Concerns #3). `.env.example`'s placeholder was updated to match.

## Concerns

1. **`mcp` library API drift from the brief's script.** Installed `mcp==2.2.0`'s `Tool` model exposes `input_schema` (snake_case), not `inputSchema` as the brief's script used (`t.inputSchema`). Fixed by changing that one line to `t.input_schema` — output dict keys are unchanged (`"inputSchema"` in the JSON), so `docs/tigergraph-mcp-tools.json` matches the brief's intended shape.

2. **`tigergraph__list_graphs` returns a permission error, not a clean success — but this is not a connectivity problem.** The live response: `"User 'tigergraph12' does not have the permission to run the command. Required privilege on global: READ_SCHEMA."` This is a real, authenticated GSQL-engine response (the MCP call itself succeeds structurally; `success: false` is in the payload), proving the host/port/auth chain works end-to-end — a wrong port or bad credentials would instead produce a connection-refused/timeout/401-at-transport error, which this is not. Likely explanation: `tigergraph12` is a workspace-scoped Savanna user without the *global* `READ_SCHEMA` privilege needed to enumerate all graphs on the server; graph-scoped operations should be unaffected. Also discovered via `show_graph_details`: **the `FraudInvestigation` graph does not exist yet** on this workspace (expected — that's presumably Task 2's `create_graph` job) — worth flagging in case Task 2 hits the same privilege wall when creating/reading schema.

3. **`GROQ_MODEL=llama-3.3-70b-versatile` no longer exists on Groq.** A live call returned `404 model_not_found`. Listed the account's actual available models and found no Llama-3 models at all anymore; picked `openai/gpt-oss-120b` as the strongest general-purpose model with confirmed tool-calling (verified with a real function-call round-trip). Updated both `.env` and `.env.example` accordingly. Task 11 (and anything else referencing this model name) should use `openai/gpt-oss-120b`.

4. **Free-tier Groq rate limits** (from live response headers, for Task 11's backoff logic): `x-ratelimit-limit-requests: 1000` (resets ~every several hours — looks like a daily quota), `x-ratelimit-limit-tokens: 8000` (token-bucket refills in well under a second at low usage — looks like TPM). Task 11 should size retry/backoff around an ~8000 TPM budget and treat the request quota as a slower-moving daily-ish limit.

5. **`.gitignore` bug found and fixed.** The existing `.env.*` rule also silently matched `.env.example`, which the brief requires to be committed. Added `!.env.example` immediately after it. Verified `.env` itself is still ignored (`git check-ignore` shows `!!`, i.e. ignored-but-was-requested) and is not staged/tracked.
