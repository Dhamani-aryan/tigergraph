# Task 2 Report: Persistent MCP client wrapper

## Status: DONE_WITH_CONCERNS

## Commit
`aaae0e3` — "feat: persistent TigerGraph MCP client wrapper"
(branch `tigergraph-fraud-agent`, worktree `C:\Users\naman\Desktop\tigergraph\.worktrees\tigergraph-fraud-agent`, not pushed)

Files added: `src/__init__.py`, `src/tg_client.py`, `tests/test_tg_client.py`, `pytest.ini`.

## Test summary
`.venv\Scripts\pytest.exe tests/test_tg_client.py -v` — **1 passed** (verified live against the Savanna workspace; `tg.gsql("HELP")` returned a non-null result, confirming end-to-end MCP connectivity and no permission error on this call).

## Schema discrepancies found (per brief's instruction to check `docs/tigergraph-mcp-tools.json`)
- `tigergraph__gsql` and `tigergraph__run_installed_query`: matched the brief's assumed signatures exactly (`command`; `query_name`/`params`).
- `tigergraph__upsert_vectors`: real schema requires an additional **`vector_attribute`** parameter (not just `vertex_type`/`vectors`). Added it to `.upsert_vectors(vertex_type, vector_attribute, vectors)`.
- `tigergraph__search_top_k_similarity`: real schema also requires **`vector_attribute`**, and the result-count field is named **`top_k`**, not `k`. Updated `.search_top_k_similarity(vertex_type, vector_attribute, query_vector, top_k=5)` accordingly.

## Concerns / deviations from the brief worth flagging
1. **Executable resolution bug (fixed in the wrapper).** The brief's literal `__aenter__` code (`command="tigergraph-mcp"`) fails with `FileNotFoundError: [WinError 2]` when run via `.venv\Scripts\pytest.exe` from a shell where the venv isn't activated (i.e. `.venv\Scripts` not on `PATH`) — reproduced identically in both PowerShell and Bash. Root cause: on Windows the `mcp` library resolves the command via `shutil.which()` against the *real process* `PATH`, not the custom `env` dict passed to `StdioServerParameters` (that dict only becomes the child's environment after the executable is already located). Since this class is the foundation every later task depends on, I added a small `_resolve_tigergraph_mcp_command()` helper that checks the running interpreter's own `Scripts`/`bin` directory first, falling back to `shutil.which`. Re-verified passing in a fresh, non-activated PowerShell session after the fix.
2. **Added `pytest.ini`** (`pythonpath = .`) — not listed in the brief's file list, but without it `tests/test_tg_client.py` can't `import src.tg_client` (pytest's default rootdir/import-mode doesn't put the project root on `sys.path` when there's no `conftest.py` there). Minimal, non-invasive addition.
3. Per the context notes, `tg.gsql("HELP")` did **not** hit any permission error (unlike `list_graphs`/`get_global_schema` in Task 1) — connectivity and auth are confirmed healthy for this account on this call.

## Fix round 1 (reviewer finding: silently swallowed tool failures)

### Status: DONE

### Commit
`bad147b` — "fix: surface tool-call failures instead of swallowing them"
(branch `tigergraph-fraud-agent`, on top of `aaae0e3`, not pushed)

### What the reviewer found
`TigerGraphMCP.call()` never checked `result.is_error` on the MCP SDK's `CallToolResult`, so a failed tool call (bad GSQL, rejected upsert, permission error, etc.) would return as if it succeeded — a real risk for tasks that batch hundreds of sequential calls (Task 7's ~591 `MADE`-edge inserts, Task 8's device-profile batching) without inspecting each one.

### What I found while fixing it (important: literal fix alone would not have worked)
Before changing code, I probed the live server to confirm the fix would actually catch something. Result: **the installed `tigergraph-mcp` server never sets `is_error=True`, even on genuine failures.**

- `tigergraph__gsql("NOT VALID GSQL AT ALL")` → `result.is_error == False`. The actual failure is only visible inside the text content, which is a `` ```json `` -fenced envelope: `{"success": false, "error": "GSQL command returned an error: ...", "error_code": "SCHEMA_ERROR", ...}`, followed by a markdown-rendered duplicate of the same data.
- `tigergraph__run_installed_query("this_query_does_not_exist_xyz", {})` → same pattern: `result.is_error == False`, but the envelope has `"success": false"` and a 404 error message.
- A genuine success (`tigergraph__gsql("HELP")`) returns the same envelope shape with `"success": true`.

So checking only `result.is_error` — the literal ask — would have satisfied the letter of the review but not the actual concern (silent failures in batched calls), because this server signals failure via its own JSON envelope, not the MCP protocol flag. I implemented both checks:

1. `result.is_error` (protocol-level; kept for correctness/future-proofing against a server that does use it).
2. A new `_extract_json_envelope()` helper that pulls the `` ```json ... ``` `` fenced block (or the whole text as a fallback) out of the joined content and parses it; if the parsed dict has `"success": false`, that also raises.

`call()` now raises `RuntimeError(f"TigerGraph MCP tool '{tool_name}' failed: {error_detail}")` where `error_detail` is the envelope's `error`/`summary` field, falling back to the raw joined text if no envelope was parseable.

Side effect (intentional, not scope creep for its own sake): on the success path, `call()` now returns the parsed envelope dict rather than attempting `json.loads()` on the *entire* joined text (which almost never succeeded before, since real responses are fenced JSON followed by trailing markdown — so the original implementation was silently falling back to returning an opaque string on nearly every real call, not actually delivering "JSON-decoded if possible" against this server's real output shape). This is a strict improvement for every later task that reads `.call()`'s return value.

### Test added
`tests/test_tg_client.py::test_gsql_invalid_command_raises` — calls `tg.gsql("NOT VALID GSQL AT ALL")` inside `pytest.raises(RuntimeError, match="tigergraph__gsql")`, run live against Savanna.

### Covering test run and output
```
.venv\Scripts\pytest.exe tests/test_tg_client.py -v

tests/test_tg_client.py::test_gsql_show_returns_something PASSED         [ 50%]
tests/test_tg_client.py::test_gsql_invalid_command_raises PASSED         [100%]

============================== 2 passed in 3.59s ==============================
```
Both ran live against the Savanna workspace (no mocking) — happy path via `HELP`, failure path via malformed GSQL that the live GSQL parser genuinely rejects.

### Note on an unrelated concurrent commit
Between my Task 2 commit and this fix, commit `6ab44a0` ("Fix vector-attribute gap surfaced by Task 2's real tool-schema check") landed on this branch — it only edits the plan document (`docs/superpowers/plans/2026-09-22-tigergraph-fraud-agent-plan.md`) to reflect the `vector_attribute` discrepancy noted above; it does not touch `src/` or `tests/`. No conflict with this fix.
