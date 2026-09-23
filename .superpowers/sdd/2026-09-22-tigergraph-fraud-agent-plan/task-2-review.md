# Task 2 Review: Persistent MCP client wrapper

Reviewed: `93f58cc..aaae0e3` (`src/__init__.py`, `src/tg_client.py`, `tests/test_tg_client.py`, `pytest.ini`)
Review method: read brief/report/diff, cross-checked all four tool signatures against `docs/tigergraph-mcp-tools.json` programmatically, independently re-ran the connectivity test in a genuinely non-activated shell (bash tool session — `.venv/Scripts` was confirmed *not* on `$PATH`), and inspected the installed `mcp` SDK's `CallToolResult` type. No files modified.

## Spec compliance: ✅ PASS

- `class TigerGraphMCP` is an async context manager (`__aenter__`/`__aexit__` via `AsyncExitStack`) exposing `.call(tool_name, arguments) -> Any` with the specified JSON-decode-else-raw-text behavior, plus `.gsql()`, `.upsert_vectors()`, `.search_top_k_similarity()`, `.run_installed_query()`. Matches the brief's interface contract exactly.
- File list matches (`src/__init__.py` empty, `src/tg_client.py`, `tests/test_tg_client.py`), plus the justified `pytest.ini` addition (see below).
- Global constraints honored: only `mcp` SDK (`from mcp import ClientSession, StdioServerParameters`) is used — no `pyTigerGraph` anywhere in the diff or repo. `.env` is gitignored (`.env`, `.env.*`, `!.env.example` in `.gitignore`) and never read into a printed/logged value. Single local commit `aaae0e3` on `tigergraph-fraud-agent`, not pushed (`git status` clean, `git log` confirms no remote push).
- Step 3 (live test against Savanna) — I independently re-ran `pytest tests/test_tg_client.py -v` myself in a shell where `.venv\Scripts` was not on `PATH`: **1 passed**, confirming both live connectivity and the report's claim.

## Verification of the three "known context" items

1. **Windows PATH-resolution fix — verified real, not a no-op.** `_resolve_tigergraph_mcp_command()` checks `Path(sys.executable).parent` (the venv's own Scripts/bin dir) for `tigergraph-mcp.exe`/`tigergraph-mcp` before falling back to `shutil.which`. I independently confirmed the underlying bug claim by running `shutil.which("tigergraph-mcp")` directly in the same non-activated shell — it returned `None`, which is exactly the condition that would make the brief's literal `command="tigergraph-mcp"` fail via `FileNotFoundError: [WinError 2]` once handed to `CreateProcess`. I then ran the actual test suite in that same shell and it passed, so the fix demonstrably resolves the executable correctly in the failure condition it claims to fix. Real fix, genuinely re-tested.
2. **`upsert_vectors`/`search_top_k_similarity` signature corrections — verified against `docs/tigergraph-mcp-tools.json` directly (not just the report's claim).** Parsed the JSON myself: `tigergraph__upsert_vectors` requires `vertex_type`, `vector_attribute`, `vectors` (exactly what the code sends); `tigergraph__search_top_k_similarity` requires `vertex_type`, `vector_attribute`, `query_vector`, with the count field named `top_k` (default `10` in the schema — code defaults to `5`, a cosmetic, non-breaking choice since it's just the wrapper's own default, not a required-vs-provided mismatch). The `VectorData` item shape (`vertex_id`, `vector`, optional `attributes`) documented in the code's comment also matches the schema's `$defs.VectorData` exactly. Confirmed accurate.
3. **`pytest.ini` addition** — legitimate; without `pythonpath = .`, `tests/test_tg_client.py`'s `from src.tg_client import TigerGraphMCP` fails under pytest's default rootdir behavior. Minimal and scoped to exactly that problem.

## Code quality verdict: ✅ PASS, with one Important finding

**Important — tool-level errors are never surfaced.** `TigerGraphMCP.call()` reads `result.content` and returns parsed/raw text unconditionally; it never checks `result.is_error`. Per the installed `mcp` SDK's own `CallToolResult` docstring: *"Errors that originate from the tool SHOULD be reported inside the result with `is_error` set to true, not as an MCP protocol-level error, so the LLM can see and self-correct."* I confirmed `is_error: bool = False` is a real field on the installed SDK's `CallToolResult`, and grepped the whole repo — `is_error`/`isError` is checked nowhere. Practical consequence: if a GSQL statement fails, an upsert is rejected, or a query errors out, `.call()` will still return whatever error text the server sent back as if it were a normal result (parsed as JSON if it happens to parse, otherwise as a string) rather than raising or otherwise signaling failure. This also means the connectivity test itself (`assert result is not None`) would pass even if `tg.gsql("HELP")` had errored server-side, as long as some text came back — the test doesn't actually prove success, only that *something* was returned. Since every later task (schema creation, bulk vector upserts, installed-query runs) goes through this class exclusively, this is worth fixing before Task 6/7's data-loading tasks build on it, so failures don't get silently absorbed into "successful" pipeline runs. Suggested fix: in `call()`, check `result.is_error` and raise (e.g. `RuntimeError(joined or "tool call failed")`) before attempting the JSON parse.

No other correctness or style issues found. The code is otherwise clean: consistent type hints, sensible `AsyncExitStack` usage, a well-documented rationale comment for the non-obvious Windows fix, accurate inline comments cross-referencing the real schema, and method signatures that stay faithful to the brief's convenience-wrapper intent (`.call()` remains available directly for anyone who needs `search_top_k_similarity`'s optional `ef`/`return_vectors` params the convenience method doesn't expose — a reasonable, non-blocking scope choice).

## Requested judgment: is the `_resolve_tigergraph_mcp_command()` double-fallback sound?

```python
return shutil.which("tigergraph-mcp") or "tigergraph-mcp"
```

**My read: it's a reasonable degrade-gracefully default, but a slight miss for a foundation class, and I'd tighten it rather than block on it.**

Reasoning:
- The venv-Scripts-dir check (the actual fix) covers the realistic failure mode this was written for (unactivated venv shell), and I verified it works. The two fallbacks below it (`shutil.which`, then the bare string) only matter in the genuinely-not-installed case — `tigergraph-mcp` isn't in this venv *and* isn't on `PATH` anywhere.
- In that residual case, returning the bare string and letting `StdioServerParameters`/`CreateProcess` raise its own `FileNotFoundError: [WinError 2]` is not wrong — it doesn't hide the failure, doesn't silently degrade to a broken-but-running state, and doesn't invent behavior beyond what the OS already provides. "Let the OS give its own natural error" is a defensible minimalist choice, and precedent exists for it (many CLI wrappers do the same).
- However, given the effort already put into `_resolve_tigergraph_mcp_command()` specifically to make failures here *more* legible (the whole point of the function, per its own docstring, is that the unadorned error is confusing), silently falling through to that same unadorned `WinError 2` in the one case where resolution genuinely fails feels like it undercuts its own purpose. A user hitting the true "not installed anywhere" case gets the exact same cryptic low-level error the fix exists to avoid — just one layer further removed, since it now looks like it came from inside a "resolution" function that gave up without saying so.
- I'd suggest, not require, raising something explicit instead — e.g. `raise FileNotFoundError("tigergraph-mcp not found in this venv's Scripts/bin directory or on PATH; run 'pip install tigergraph-mcp' in this environment (see Task 1) or activate the venv shell.")` — a few lines that turn a debugging dead-end into a one-line fix instruction. This isn't a blocking defect (nothing currently depends on triggering this branch, and it doesn't corrupt state or mask a false success — unlike the `is_error` finding above, this is a pure error-message-quality question), so I would not fail the task over it, but it's worth a follow-up if the class gets touched again.

## Summary

- Spec compliance: ✅
- Code quality: ✅ (one Important finding: `is_error` not checked in `.call()`, recommend fixing before data-writing tasks build on it)
- Windows PATH fix: verified real and effective, independently re-tested
- Schema corrections: verified byte-for-byte against `docs/tigergraph-mcp-tools.json`
- Fallback-to-bare-string behavior: sound as a non-blocking default; a follow-up to raise a clearer error would improve debuggability but isn't required
