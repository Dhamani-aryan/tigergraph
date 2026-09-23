# Task 2 Fix Round 1 — Scoped Re-review

Scope: verify only the reviewer's original finding ("`TigerGraphMCP.call()` never checked `result.is_error`, so a failed tool call would be silently returned as if it succeeded") against fix commit `bad147b` (diff `6ab44a0..bad147b`). Flags new Critical/Important issues introduced by the fix itself. Read-only — no files modified. Ran the live test suite independently as part of verification (non-mutating GSQL calls only).

## Verdict: ADDRESSED

`src/tg_client.py::TigerGraphMCP.call()` (current repo, lines 92–120) now checks both failure signals before returning:

```python
tool_failed = result.is_error or (envelope is not None and envelope.get("success") is False)
if tool_failed:
    error_detail = envelope.get("error") or envelope.get("summary") if envelope else None
    raise RuntimeError(
        f"TigerGraph MCP tool '{tool_name}' failed: {error_detail or joined or '<no error text returned>'}"
    )
```

- Confirmed by reading the actual code (not just the report's description) that this matches the diff exactly — no drift between `review-6ab44a0..bad147b.diff` and the working tree's `src/tg_client.py` / `tests/test_tg_client.py`.
- `result.is_error` is still checked (protocol-level signal, point 1 of the literal finding), **and** the new `_extract_json_envelope()` helper parses the ` ```json ` -fenced envelope embedded in the response text and checks `success: false` — which the fix report says is the *actual* failure signal this live server uses, since it never sets `is_error=True`.
- The raised `RuntimeError` includes both the tool name (`tool_name`, e.g. `tigergraph__gsql`) and useful error detail: `envelope.get("error")` first, falling back to `envelope.get("summary")`, then to the raw joined text, then to a final placeholder. (Note: the ternary `envelope.get("error") or envelope.get("summary") if envelope else None` reads ambiguously at a glance, but Python's conditional-expression precedence is lower than `or`, so it correctly evaluates as `(envelope.get("error") or envelope.get("summary")) if envelope else None` — verified this isn't a latent bug.)
- No longer possible for a genuine tool failure to fall through to the old "return parsed-or-raw text as if it succeeded" path — both signals are checked before any return.

**Point 2 — test genuineness:** `tests/test_tg_client.py::test_gsql_invalid_command_raises` exists exactly as the report describes: `async with TigerGraphMCP() as tg: with pytest.raises(RuntimeError, match="tigergraph__gsql"): await tg.gsql("NOT VALID GSQL AT ALL")`. Checked for mocking/monkeypatching in the test file and for a `conftest.py` anywhere in the project (only third-party `.venv` package conftests exist, none touching this) — none found. `TigerGraphMCP.__aenter__` unconditionally spawns the real `tigergraph-mcp` subprocess via `stdio_client`/`StdioServerParameters`; there is no offline/mock code path in the class at all. This is a genuine live assertion, not a mock.

I additionally **independently re-ran the live suite** myself (`.venv/Scripts/pytest.exe tests/test_tg_client.py -v`) against the real Savanna workspace using the repo's own `.env`: both tests passed (`2 passed in 3.74s`), matching the report's claimed output. This confirms the failure path genuinely raises against the live server, not just in the report's self-reported log.

## New Critical/Important breakage introduced by the fix diff

None found. Specifically checked:
- Empty-content edge case (`texts` empty, `is_error` True): `joined` is `""`, `envelope` stays `None`, `tool_failed` is still `True` via `result.is_error`, and the error message correctly falls back through `error_detail` (None) → `joined` (empty, falsy) → the `'<no error text returned>'` placeholder. No crash, no silent success.
- Regex-miss case (no ` ```json ` fence, whole text isn't valid JSON): `_extract_json_envelope` returns `None`, `call()` falls back to the pre-fix `json.loads`-else-raw-string behavior on the success path — an unchanged, safe fallback.
- No new exception types leak out unhandled; only `RuntimeError` is raised for tool failures, matching the test's expectation.

## Point 3 — new risk from the success-path return-shape change

**No current breakage.** The success path changed from "parsed JSON or raw string" to "parsed envelope dict when one exists, else the old fallback," which is a real behavior change beyond the literal finding — but nothing in the current repo depends on the old shape:

- Grepped the whole repo for `tg_client`/`TigerGraphMCP` usage: only `src/tg_client.py` (the definition) and `tests/test_tg_client.py` reference it. No task 3+ code exists yet in this worktree to be affected.
- `tests/test_tg_client.py::test_gsql_show_returns_something` only asserts `result is not None` — shape-agnostic; passes whether `result` is a dict, string, or anything else non-`None`.
- The convenience methods `gsql()`, `upsert_vectors()`, `search_top_k_similarity()`, `run_installed_query()` all just `return await self.call(...)` directly with no post-processing of the return value — none of them index into, destructure, or otherwise assume a particular shape of the result. So none of them break under the new envelope-dict return type.

This is nonetheless worth flagging forward (not blocking here): every later task brief that consumes `.call()`'s return value should be written/reviewed against the *new* envelope shape (`{"success": ..., "operation": ..., "data": ...}`) rather than the brief's original "JSON-decoded if possible, else raw text" framing, since real responses almost always hit the envelope path now. That's a forward-looking note for Task 3+ briefs/reviews, not a defect in this fix.

## Deferred / out-of-scope notes (non-blocking)

- The original review's separate "requested judgment" item about `_resolve_tigergraph_mcp_command()`'s bare-string fallback (raise vs. let `CreateProcess` fail naturally) was explicitly marked non-blocking by the original reviewer and is untouched by this fix diff — still open as a possible future follow-up, out of scope for this re-review.
- `_extract_json_envelope`'s fallback of trying to `json.loads` the *entire* joined text when no fenced block is found is a reasonable belt-and-suspenders default but was not separately exercised by a new test (only the fenced-envelope path is tested, via both the success and failure live tests). Not a defect — just noting the untested branch for awareness.

## Summary

- Original finding: **ADDRESSED** — `call()` now checks both `result.is_error` and the envelope's `success` field, raises `RuntimeError` with tool name + error detail, verified in actual code and independently re-run live (2 passed).
- New Critical/Important issues from the fix diff: **none found**.
- Success-path return-shape change (point 3): **no existing code breaks** — only the two files in `src/`/`tests/` reference `TigerGraphMCP`, and neither assumes the old bare-value shape. Flagged as a forward-looking note for Task 3+ briefs, not a current defect.
