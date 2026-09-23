# Task 11 Review: LLM wrapper (Groq primary, Ollama fallback) with schema-validated retry

**Reviewed commit:** `634979a` (worktree HEAD, `tigergraph-fraud-agent` branch, clean working tree)
**Reviewer method:** read `src/agent/llm.py`/`simulator.py`/`conftest.py`/`tests/test_llm_wrapper.py` directly, diffed against the brief, ran the real test suite live (Groq + local TigerGraph MCP), and wrote independent scratch scripts to exercise retry/backoff/bound/fallback behavior with both live calls and controlled mocks.

## Verdict

- **Spec compliance: PASS.** All six verification points in the review brief check out against the actual code and live behavior, not just against the report's narrative.
- **Code quality: PASS, with medium/low findings below.** Nothing blocking; one medium-severity concern (blocking I/O in `async def`s) that matters specifically because of Task 14's upcoming batch run and should be looked at before that task assumes concurrency.
- **Ollama fallback: CONFIRMED GENUINELY WORKING END-TO-END.** This was the one thing the implementer's own report flagged as unverified — I ran it live and it works, including `generate_structured` producing valid, schema-conforming output from `qwen3:4b-instruct`, and `generate_with_tools` degrading gracefully rather than erroring.

---

## 1. `generate_structured` — real schema validation + real retry-with-feedback

Confirmed by reading the code and, more importantly, by an isolated test that mocks `_chat_raw` to return invalid JSON on the first call and valid JSON on the second:

- It really validates via `schema.model_validate(json.loads(raw))` against the caller-supplied Pydantic class — not a hardcoded/dummy schema.
- On `JSONDecodeError`/`ValidationError`, it appends the assistant's bad output **and** a user message containing the actual exception text (`f"That was not valid JSON matching the schema ({exc}). Try again, JSON only."`) before re-calling — i.e. the model genuinely sees its own mistake and the validator's specific complaint, not a bare "try again."
- Verified message construction directly:
  ```
  attempt 2 messages:
    - You are a fraud investigation assistant. Respond with ONLY a single JSON object...
    - give me json
    - not json at all                                                 <- echoed bad output
    - That was not valid JSON matching the schema (Expecting value: line 1 column 1 (char 0)). Try again, JSON only.
  ```
- Verified exhaustion behavior: with `max_retries=2`, it makes exactly 3 attempts total, then raises `RuntimeError("Failed to get valid structured output after 3 attempts")` chained (`from last_error`) to the real underlying validation error.
- Live-confirmed against real Groq (`openai/gpt-oss-120b`) and real Ollama (`qwen3:4b-instruct`) — both produced valid instances on the first attempt in my runs (see §5).

No issues. This is real, not a stub.

## 2. `generate_with_tools` — real function-calling, but bounded to exactly 1 call regardless of `max_tool_calls`

- Confirmed it inspects `message.tool_calls` from a real `ChatCompletion` (not a text-parsing hack), and live-verified against Groq: passing the brief's ambiguous prompt + a `lookup_region` tool definition genuinely returns a `tool_calls` entry the model chose to emit.
- Confirmed empirically (mocked `_groq_chat`, counting calls) that **the function makes exactly one underlying model call no matter what value is passed for `max_tool_calls`** — I called it with `max_tool_calls=5` and it still made 1 call. This matches the brief's literal intent ("one bounded round: the model may call at most one tool, or answer directly") and is *not* a deviation from the brief — the brief's own code has this same signature/behavior. But it does mean `max_tool_calls` is a **dead parameter with no effect on behavior** (see Code Quality Medium-2 below).
- Live-confirmed the Ollama degrade path is real, not theoretical (see §5): with `LLM_BACKEND=ollama`, it returns `ToolCallResult(tool_name=None, final_text="(tool-calling round skipped on ollama backend)")` immediately, without ever touching `ollama.chat`, and without raising.

No issues with the function-calling mechanics themselves; one API-design nit noted below.

## 3. Rate-limit retry/backoff — wired to the correct exception for the installed SDK

- Verified directly: installed `openai==3.17.0` (confirmed via `pip`/import), and `openai.RateLimitError` exists with MRO `RateLimitError -> APIStatusError -> APIError -> OpenAIError -> Exception -> BaseException`. The report's claim checks out.
- Verified the *behavior*, not just the class name, with a mock that raises a real `openai.RateLimitError(message, response=<httpx.Response 429>, body=None)` from a stand-in `client.chat.completions.create`:
  - 3 injected 429s, 3rd call succeeds → `_groq_chat` returns the success value after 2 backoff waits (measured elapsed ≥ 2.0s, consistent with `wait_exponential(multiplier=1, min=2, max=30)`).
  - Always-429 → exactly 5 attempts made (`stop_after_attempt(5)`), then raises `tenacity.RetryError` wrapping the internal `_RateLimited` (which itself is chained `from` the original `openai.RateLimitError`).
- The `try/except openai.RateLimitError: raise _RateLimited` translation layer inside `_groq_chat` is necessary because `tenacity`'s `retry_if_exception_type` is applied to the decorated function's raised exception, and using a private sentinel exception (rather than retrying on `openai.RateLimitError` directly) is a reasonable, deliberate choice — it means only genuine 429s trigger the backoff loop, not any other `APIStatusError` subclass (auth errors, 400s, etc. propagate immediately, which is correct — you don't want to retry-with-backoff on a malformed request).

No issues. Genuinely wired correctly, confirmed live-class-identity and live-behavior.

## 4. `TokenTracker` — genuine accumulation, not a stub

- `_groq_chat` unconditionally calls `token_tracker.add_from_response(response)` on every successful real Groq response, before returning it — this is in the hot path of both `generate_structured` (via `_chat_raw`) and `generate_with_tools`, so every real Groq call anywhere in the system feeds the tracker.
- `add_from_response` reads `response.usage.total_tokens` off the real `ChatCompletion` object Groq returns (guarded by `is not None`), not a hardcoded value.
- The bundled unit test (`test_token_tracker_resets_and_accumulates`) only exercises the accumulate/reset arithmetic with fake objects — it does not prove the wiring into `_groq_chat`. I confirmed that wiring by reading `_groq_chat` directly (line `token_tracker.add_from_response(response)` executes unconditionally on the success path) and by inspection of the live test run, which makes two real Groq calls that pass through this exact code path.
- Expected/acceptable gap: the Ollama backend path never touches `token_tracker` (Ollama responses do carry `eval_count`/`prompt_eval_count`, which could be mapped in later if per-run token accounting is ever needed for the local fallback, but the brief doesn't require this and Ollama has no cost implication).

No issues — genuine, not a stub.

## 5. Ollama fallback — independently exercised end-to-end (the report's flagged gap)

The report explicitly said it only confirmed the daemon was up and the model was pulled, not that the code path worked. I ran it for real, in a fresh subprocess with `LLM_BACKEND=ollama` set **before** importing `src.agent.llm`, because `LLM_BACKEND`/`GROQ_MODEL`/`OLLAMA_MODEL` are all resolved once at module-import time into plain module-level constants — setting `os.environ["LLM_BACKEND"]` after the module is already imported in the same process has no effect (I hit this myself while writing the verification script; worth knowing for Task 12/14, see Low-6 below).

Results:
- `generate_structured("...answer field containing 'ok'...", _TinySchema)` against real `qwen3:4b-instruct` → returned `answer='ok'`, a valid `_TinySchema` instance, on the **first** attempt (no retry needed). Took **56.45s** — this is Ollama's cold model load into memory, not per-call latency.
- A second, more realistic call — a fraud-probability/reasoning schema fed a concrete anomalous-transaction scenario — returned `fraud_probability=0.95, reasoning="The transaction amount is 40 times the customer's median spending, and the device is entirely new to the account, indicating a high likelihood of fraudulent activity."`, a valid instance, in **4.47s** (warm).
- `generate_with_tools` under `LLM_BACKEND=ollama` returned `ToolCallResult(tool_name=None, tool_arguments={}, final_text='(tool-calling round skipped on ollama backend)')` — confirmed the documented graceful-degrade path, no exception, no attempt to call `ollama.chat` with `tools=`.

**Conclusion: the Ollama fallback genuinely works end-to-end for `generate_structured`, and `generate_with_tools` genuinely degrades gracefully rather than erroring, exactly as documented.** This is the safety net Task 14 depends on if Groq's free tier gets rate-limited, and it is real, not aspirational.

One operational point worth flagging to whoever runs Task 14: switching to Ollama mid-run is not something the current code supports automatically — because the backend is a module-level constant fixed at import time, the only way to actually fall back is to restart the process with `LLM_BACKEND=ollama` in `.env` (as the brief's own troubleshooting note for Task 11 says to do), not to catch a persistent-rate-limit condition and switch backends within the same run. If Task 14's batch runner wants automatic mid-batch failover, `llm.py` would need `LLM_BACKEND` to be re-read per call (or made an explicit parameter) rather than cached at import. This is consistent with the brief's stated design ("Backend is selected by the `LLM_BACKEND` env var") so it is not a deviation, just a limitation worth surfacing given how the task brief frames Ollama as "the safety net the whole project falls back on."

## 6. Test runs

- `.venv\Scripts\pytest tests/test_llm_wrapper.py -v` → **5 passed** (matches report), including the two live-Groq tests (`test_generate_structured_returns_valid_instance`, `test_generate_with_tools_calls_a_tool_when_ambiguous`).
- `.venv\Scripts\pytest tests/ -v` → **67 passed** in 318.6s, zero failures. **This does not match the report's claimed "62/62 passed."** The report says 62 total (57 pre-existing + 5 new); the actual, independently-run count on this exact commit is 67 total (62 pre-existing + 5 new). All tests pass either way, so this doesn't change the correctness verdict, but the specific numbers in the report are off by 5 and should be corrected if the report is kept as a historical record. (I ran this on the exact same clean-tree commit the report was written against, so this isn't drift from later commits — it looks like a simple miscount in the report.)

## Assessment of `conftest.py` (not in the brief's file list)

```python
from dotenv import load_dotenv
load_dotenv()
```

**Verdict: a reasonable, correctly-scoped fix, not a mask of a different problem.**

- It's necessary: `llm.py`'s brief-mandated code reads `os.environ["GROQ_API_KEY"]` and `os.environ.get("LLM_BACKEND"/"GROQ_MODEL", ...)` directly, and nothing else in the repo puts `.env` values into `os.environ` — confirmed by grep across `src/` that no other module reads `os.environ` at all (the only other env-related code is `src/tg_client.py`'s `dotenv_values()` call, discussed below). Without it, `KeyError: 'GROQ_API_KEY'` is unavoidable at import time under pytest.
- It's minimal and narrowly scoped: only file of its kind in the repo (`find . -name conftest.py` → just this one, at repo root), so it applies once per test session, not per-module in a way that could double-load.
- It's safe against precedence surprises: `load_dotenv()` defaults to `override=False`, so it only fills environment variables that are otherwise unset; it cannot clobber a real shell-exported value with a stale `.env` value.
- **No interaction issue with `TigerGraphMCP`'s separate `.env` handling.** `TigerGraphMCP.__aenter__` calls `dotenv_values(Path(self._env_path).resolve())` to build a *private dict*, merges it with `get_default_environment()`, and passes that dict as the `env=` argument to a **spawned subprocess** (the `tigergraph-mcp` server process) via `StdioServerParameters`. It never reads from or writes to `os.environ` of the pytest process itself. So the two mechanisms operate on entirely separate namespaces — one populates the current process's `os.environ` (for `llm.py`), the other builds an isolated env mapping for a child process (for the TigerGraph MCP server) — and there is no double-loading, no precedence conflict, and no way for one to shadow the other.
- Since no other `src/` module reads `os.environ`, loading `.env` globally for the test session cannot silently change the behavior of any of the 62 pre-existing tests — confirmed empirically too, since the full suite (67 tests) passes clean.
- Minor forward-looking note (not a current defect): because this now loads `.env` for the *entire* test session, any future test that wants to assert behavior when an env var is genuinely absent (e.g., a default-value test for `GROQ_API_KEY` unset) would need to explicitly `monkeypatch.delenv(...)`, since it's no longer naturally unset under pytest. No such test exists today.

## Code quality findings, by severity

**Medium — blocking I/O inside `async def`s.** `generate_structured`, `_chat_raw`, and `generate_with_tools` are declared `async` but their actual network calls (`_groq_chat` → `client.chat.completions.create(...)`, and `ollama.chat(...)`) are fully synchronous and run directly on the event loop thread — no `await`, no `asyncio.to_thread`/`run_in_executor`, and no async OpenAI/Ollama client. Today, with a single call at a time, this is invisible. It becomes a real problem the moment Task 14's 20-case batch tries to get any concurrency out of `asyncio.gather` to work around Groq's free-tier rate limits — every "concurrent" call would actually serialize on the event loop, and `tenacity`'s exponential backoff (`time.sleep` under the hood in the sync retry decorator) would freeze the whole process, not just one task, during each backoff window (up to 30s per attempt, up to 5 attempts). Recommend wrapping the synchronous client calls in `asyncio.to_thread(...)` (or switching to `openai.AsyncOpenAI`/`ollama.AsyncClient`) before Task 14 assumes any concurrency here.

**Medium — `max_tool_calls` parameter has no effect.** `generate_with_tools(prompt, tools, max_tool_calls=1)` always makes exactly one model call and only ever inspects `message.tool_calls[0]`, regardless of the value passed for `max_tool_calls` (verified: passing `max_tool_calls=5` still produces exactly 1 underlying call). This matches the brief's own literal code and its "one bounded round" design intent, so it's not a deviation — but the public signature advertises a tunable knob that silently does nothing, which could mislead a Task 12 author into believing they can request multiple tool-calling rounds by raising the number. Consider either documenting this loudly in the signature/docstring (partially done already) or having the function assert/ignore-with-warning if a caller passes anything other than 1, so a future misuse fails loudly instead of silently under-delivering.

**Low — new `openai.OpenAI()` client constructed on every `_groq_chat` call.** `_groq_client()` builds a fresh client (and its underlying `httpx.Client`) per call rather than reusing one; needless connection/resource churn across a 20-case batch with multiple LLM calls per case. Cheap fix: lazily construct once at module scope.

**Low — unguarded `json.loads(call.function.arguments)`.** If the model returns malformed tool-call argument JSON (which does happen occasionally with smaller/looser models), this raises a bare `JSONDecodeError` straight out of `generate_with_tools` rather than being caught/retried or wrapped in a clearer error message tied to the tool-calling context.

**Low — module-level backend constants frozen at import time.** `LLM_BACKEND`, `GROQ_MODEL`, `OLLAMA_MODEL` are computed once from `os.environ` at import. This is consistent with the brief's "backend selected by env var" design and is fine for a single-backend-per-process run, but it means the backend cannot be flipped within a running process (I had to use a fresh subprocess to verify the Ollama path for this reason). Worth a one-line docstring note so a future maintainer (or Task 14's batch runner) doesn't try `os.environ["LLM_BACKEND"] = "ollama"` mid-process and wonder why nothing changed.

**Low — report test-count inaccuracy.** See §6: report says 62/62 for the full suite; actual is 67/67 on the same commit. Not a code defect, just a reporting correction.

## Summary

Everything the brief specifies is genuinely implemented, not stubbed: schema validation really validates and really retries with the validation error fed back to the model; tool-calling really inspects `tool_calls` from a real API response and really caps at one call; the rate-limit retry really catches the correct exception class for the installed `openai==3.17.0` and really backs off/gives up on schedule; `TokenTracker` really accumulates real usage from real responses; and — the one thing flagged as unverified by the implementer's own report — the Ollama fallback really works end-to-end for structured generation and really degrades gracefully (not silently, not by erroring) for the tool-calling round. The `conftest.py` addition is a reasonable, minimal, correctly-scoped fix with no interaction risk against `TigerGraphMCP`'s separate env handling. The only things worth acting on before Task 14 are the blocking-I/O-in-async-functions issue if any concurrency is planned, and awareness that the Ollama fallback requires a process restart rather than in-process failover.
