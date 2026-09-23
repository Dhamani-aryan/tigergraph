# Task 11 Report: LLM wrapper (Groq primary, Ollama fallback) with schema-validated retry

## Status: DONE

## Commit

`634979a` — "feat: Groq-backed LLM wrapper (structured output + bounded
tool-calling) with Ollama fallback", on branch `tigergraph-fraud-agent`, local
only (not pushed). Adds:
- `src/agent/llm.py` (verbatim from the brief)
- `src/agent/simulator.py` (verbatim from the brief)
- `tests/test_llm_wrapper.py` (verbatim from the brief)
- `conftest.py` (new, not in the brief — see below)

## Groq model verification (done live, before writing any code)

Per the coordinator's context note that `llama-3.3-70b-versatile` 404'd one day
before this task ran, re-verified live rather than trusting the brief's
hardcoded default:

1. `GET https://api.groq.com/openai/v1/models` with the real `.env` key —
   `openai/gpt-oss-120b` is present and `"active": true` (context window
   131072). No sign of it disappearing.
2. Ran a real `chat.completions.create(..., tools=[...], tool_choice="auto")`
   call against `openai/gpt-oss-120b` with the exact ambiguous prompt from the
   brief's own tool-calling test, before writing `llm.py`. It correctly
   returned a `tool_calls` entry invoking `lookup_region` with
   `{"addr1": "444"}` — genuine function-calling support confirmed, not just
   plain-text completion.

**`GROQ_MODEL` was NOT changed.** `openai/gpt-oss-120b` is confirmed live,
active, and tool-calling-capable today (2026-09-23). `.env` is unmodified.

Also confirmed `openai.RateLimitError` still exists in the installed `openai`
package (version `3.17.0`) with the expected exception hierarchy, so
`tenacity`'s `retry_if_exception_type(_RateLimited)` / the `except
openai.RateLimitError` catch in `_groq_chat` is wired to the right class —
the brief flagged this as a specific failure mode to check, and it checks out.

## Ollama fallback verification

Confirmed the Ollama daemon was running (`GET localhost:11434/api/tags`) and
`qwen3:4b-instruct` is pulled and reports `"capabilities": ["completion",
"tools"]`, before running anything that would exercise the `LLM_BACKEND=ollama`
path. Not switched to for this run since Groq worked cleanly (see below), but
available to the test suite's fallback branch as the brief anticipates.

## Code changes vs. the brief

`src/agent/llm.py`, `src/agent/simulator.py`, and `tests/test_llm_wrapper.py`
are copied verbatim from the brief — no changes needed to the logic.

**One addition beyond the brief's file list**: a root-level `conftest.py`
(`from dotenv import load_dotenv; load_dotenv()`). Without it, every test
importing `src.agent.llm` failed with `KeyError: 'GROQ_API_KEY'` — nothing in
this repo currently loads `.env` into `os.environ`. The existing
`TigerGraphMCP` (`src/tg_client.py`) parses `.env` via `dotenv_values()` into
its own private dict, not into `os.environ`, so it never hit this gap; but
`llm.py`'s brief-specified code reads `os.environ["GROQ_API_KEY"]` and
`os.environ.get("LLM_BACKEND", ...)` directly, at both module-import time and
call time. `python-dotenv` was already a listed dependency (used by
`tg_client.py`), so this is a minimal, non-invasive addition — it only adds
env vars that are otherwise unset; it doesn't touch or reinterpret any of the
brief's verbatim logic. Flagging this clearly since it wasn't in the brief's
step list, in case Task 12 or a real entrypoint script also needs `.env`
loaded and would otherwise hit the same gap outside of pytest.

## Test summary

`.venv\Scripts\pytest tests/test_llm_wrapper.py -v` — **5/5 passed**, two of
them real live Groq API calls (no mocking):
- `test_generate_structured_returns_valid_instance` — real call, `openai/gpt-oss-120b`
  followed the JSON-schema instruction correctly on the first attempt.
- `test_generate_with_tools_calls_a_tool_when_ambiguous` — real call, picked
  `lookup_region` as expected.
- `test_token_tracker_resets_and_accumulates`, `test_simulator_anomalous_amount_denies`,
  `test_simulator_typical_amount_confirms` — pure unit tests, no network.

Full regression check: `.venv\Scripts\pytest tests/` — **62/62 passed**
(including the 5 new ones), confirming the new `conftest.py` doesn't disturb
any of the 57 pre-existing tests (some of which make their own live
TigerGraph calls).

No 429s encountered during any of this — one exploratory tool-calling call,
plus one test-suite run of the two live-hitting tests. Did not loop or retry
against Groq beyond what the test suite itself runs once.

## Concerns

1. The `conftest.py` addition (see above) is a real, if small, deviation from
   the brief's exact file list. It's necessary for the brief's own verbatim
   code to work at all under pytest and is limited to loading already-declared
   `.env` values into the process environment — flagging for visibility, not
   because I believe it's wrong.
2. `.env` still contains the unrelated leftover `TG_SECRET`-adjacent
   troubleshooting comment block noted in the task context; left untouched as
   instructed, not relevant to this task.
3. Did not exercise the `LLM_BACKEND=ollama` code path end-to-end in this run
   (Groq worked cleanly throughout), only confirmed the daemon and model are
   available. If Task 12 relies on the fallback path being battle-tested, a
   quick `LLM_BACKEND=ollama .venv\Scripts\pytest tests/test_llm_wrapper.py`
   run would be worth doing once, separately.
