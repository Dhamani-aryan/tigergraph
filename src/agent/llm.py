from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import ollama
import openai
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

LLM_BACKEND = os.environ.get("LLM_BACKEND", "groq")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
OLLAMA_MODEL = "qwen3:4b-instruct"
# LLM_BACKEND=pi: ChatGPT Plus/Pro via Pi's openai-codex OAuth provider, through
# the persistent Node bridge in src/agent/pi_bridge.py (PI_PROVIDER / PI_MODEL).
PI_MAX_ATTEMPTS = 3
PI_MAX_RATE_LIMIT_WAIT_S = 60.0

_SYSTEM_PROMPT = (
    "You are a fraud investigation assistant. Respond with ONLY a single JSON "
    "object matching the requested schema. No prose, no markdown fences."
)


def _groq_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ["GROQ_API_KEY"],
    )


@dataclass
class ToolCallResult:
    tool_name: str | None
    tool_arguments: dict = field(default_factory=dict)
    final_text: str | None = None


class _RateLimited(Exception):
    """Raised on a Groq 429 so tenacity's retry can back off and try again."""


class TokenTracker:
    """Accumulates token usage across one case's worth of LLM calls. The answer
    JSON schema (Task 6) requires a `tokens` field per case -- Groq's responses
    report real usage, so this replaces what would otherwise be a hardcoded 0."""

    def __init__(self) -> None:
        self.total = 0

    def reset(self) -> None:
        self.total = 0

    def add_from_response(self, response: "openai.types.chat.ChatCompletion") -> None:
        if response.usage is not None:
            self.total += response.usage.total_tokens

    def add_tokens(self, count: int) -> None:
        self.total += int(count)


token_tracker = TokenTracker()


def _retry_after_seconds(exc: openai.RateLimitError) -> float | None:
    """Groq's 429 carries a `retry-after` response header with the exact
    wait, when it's honoring a per-minute TPM window (confirmed live: a
    direct 20-token 'say hi' call succeeded outright seconds after a full
    batch run's calls had been failing on _RateLimited -- i.e. this is a
    short TPM burst window, not a daily/hard quota -- so waiting the
    server's own stated duration is both correct and sufficient, rather
    than tenacity's fixed exponential schedule guessing at it."""
    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    try:
        return float(header) if header is not None else None
    except ValueError:
        return None


@retry(
    retry=retry_if_exception_type(_RateLimited),
    # Reliability fix (2026-09-24), REVISED: the real fix for sustained
    # rate-limiting is smaller prompts (see _summarize_evidence_for_prompt
    # in graph_flow.py -- Groq's cap is 8,000 tokens/MINUTE, shared across
    # every call this pipeline makes in that window, not per call). This
    # schedule is now a small fallback on top of the explicit Retry-After
    # sleep below, not a second independent backoff -- the first version of
    # this fix (max=90s here, STACKED on top of a separate up-to-65s sleep)
    # made a single retry cycle take minutes, which just burned wall-clock
    # time without fixing the actual cause and made an 11-case rerun take
    # 2 hours and still mostly fail.
    wait=wait_exponential(multiplier=1, min=1, max=15),
    stop=stop_after_attempt(6),
)
def _groq_chat(messages: list[dict], **kwargs) -> "openai.types.chat.ChatCompletion":
    client = _groq_client()
    try:
        response = client.chat.completions.create(model=GROQ_MODEL, messages=messages, **kwargs)
        token_tracker.add_from_response(response)
        return response
    except openai.RateLimitError as exc:
        retry_after = _retry_after_seconds(exc)
        # Diagnostic fix (2026-09-24): RetryError's own str() doesn't show
        # the underlying cause, so every failed batch run so far has only
        # ever logged "RetryError[<Future ... raised _RateLimited>]" -- no
        # visibility into Groq's actual error text or which limit tripped
        # (requests vs tokens vs a genuinely different 429 cause). Printed
        # directly here, not just attached to the exception, so it shows up
        # in run_all.py's per-case log even though that code only prints
        # the OUTER RetryError.
        print(f"    [groq 429] retry_after={retry_after} message={exc.message}", flush=True)
        if retry_after is not None:
            # Sleep the server's own stated duration -- this IS the wait,
            # not an addition to tenacity's own backoff (see the retry
            # decorator's docstring above for why stacking both was wrong).
            time.sleep(min(retry_after + 0.5, 20))
        raise _RateLimited from exc


def _pi_chat(messages: list[dict], tools: list[dict] | None = None):
    """One Pi completion. `messages` uses the same OpenAI-style role/content
    dicts as the Groq path; a leading system message becomes Pi's system
    prompt. Retries only transient failures (a short rate-limit window, a
    bridge timeout, or the bridge dying -- a fresh bridge is started on the
    next attempt); auth, model and malformed-response errors fail at once.
    Tokens are counted from Pi's own usage report for every completed call,
    including ones whose content is later rejected by schema validation."""
    from src.agent import pi_bridge

    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else _SYSTEM_PROMPT
    convo = [m for m in messages if m["role"] != "system"]
    for attempt in range(1, PI_MAX_ATTEMPTS + 1):
        try:
            completion = pi_bridge.get_bridge().complete(system, convo, tools=tools)
        except pi_bridge.PiBridgeError as exc:
            transient = exc.kind in ("timeout", "exited") or (
                exc.kind == "rate_limit"
                and (exc.retry_after_s is None or exc.retry_after_s <= PI_MAX_RATE_LIMIT_WAIT_S)
            )
            print(f"    [pi {exc.kind}] attempt {attempt}/{PI_MAX_ATTEMPTS}: {exc}", flush=True)
            if not transient or attempt == PI_MAX_ATTEMPTS:
                raise
            time.sleep(min(exc.retry_after_s or 5.0 * attempt, PI_MAX_RATE_LIMIT_WAIT_S))
            continue
        token_tracker.add_tokens(completion.total_tokens)
        return completion
    raise AssertionError("unreachable")


def _strip_json_fence(raw: str) -> str:
    """The Codex Responses endpoint has no json_object response_format, so a
    reply occasionally arrives wrapped in a ```json fence. Only the fence is
    removed; the content still has to parse and pass schema validation."""
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
        if text[:4].lower() == "json":
            text = text[4:].lstrip()
    return text


def _openai_tools_to_pi(tools: list[dict]) -> list[dict]:
    """OpenAI-style function schemas -> Pi tool definitions (name, description,
    JSON-schema parameters). Declarations only: Pi never executes them."""
    converted = []
    for tool in tools:
        fn = tool.get("function", tool) if tool.get("type", "function") == "function" else None
        if not fn or not fn.get("name"):
            raise ValueError(f"unsupported tool schema for the pi backend: {tool!r}")
        converted.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted


async def generate_structured(
    prompt: str, schema: type[BaseModel], max_retries: int = 2
) -> BaseModel:
    """Task 12 review finding (confirmed live, reproducibly): Groq's
    `response_format={"type": "json_object"}` only guarantees syntactically valid
    JSON, not that the model uses the schema's actual field names. With only prose
    describing the desired content ("classify the fraud pattern...") and no literal
    key names anywhere in the prompt, the model inferred a plausible-but-wrong key
    (`fraud_pattern` instead of `AssessmentOutput`'s real `pattern` field) and
    repeated the exact same mistake across all `max_retries` attempts -- the old
    generic "that wasn't valid JSON, try again" retry message never told it WHICH
    key was wrong, so a consistent misread wasn't self-correcting. This is a
    real, non-negligible failure mode for Task 13's batch run (one independent
    reviewer re-run of the exact same test hit it, even though the original run
    didn't). Fix: state the schema's own `model_json_schema()` explicitly in the
    very first prompt (not just the retry-correction message), so the model has
    concrete key-name guidance before it ever guesses wrong, and repeat the exact
    required field names on every retry too.
    """
    last_error: Exception | None = None
    schema_json = json.dumps(schema.model_json_schema())
    field_names = list(schema.model_fields)
    schema_prompt = (
        f"{prompt}\n\n"
        f"Respond with a single JSON object that matches EXACTLY this JSON schema "
        f"(use these exact field names -- {field_names} -- and no others):\n{schema_json}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": schema_prompt},
    ]
    for attempt in range(max_retries + 1):
        raw = await _chat_raw(messages, schema)
        try:
            return schema.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That was not valid JSON matching the schema ({exc}). "
                        f"Use exactly these field names: {field_names}. Try again, JSON only."
                    ),
                }
            )
    raise RuntimeError(
        f"Failed to get valid structured output after {max_retries + 1} attempts"
    ) from last_error


async def _chat_raw(messages: list[dict], schema: type[BaseModel]) -> str:
    if LLM_BACKEND == "pi":
        return _strip_json_fence(_pi_chat(messages).text) or "{}"
    if LLM_BACKEND == "groq":
        response = _groq_chat(
            messages,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"
    response = ollama.chat(model=OLLAMA_MODEL, messages=messages, format=schema.model_json_schema())
    return response["message"]["content"]


async def generate_with_tools(
    prompt: str, tools: list[dict], max_tool_calls: int = 1
) -> ToolCallResult:
    """One bounded round: the model may call at most one tool, or answer directly.
    Ollama's tool-calling support is inconsistent across small local models, so this
    function only runs meaningfully on the Groq backend; when LLM_BACKEND=ollama it
    degrades gracefully to 'no tool call' (final_text only) rather than erroring,
    since the spec explicitly treats the local backend as a reliability fallback,
    not a feature-parity requirement.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You may call at most one tool if the evidence so far is genuinely "
                "ambiguous. If it's already clear, answer directly with no tool call."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    if LLM_BACKEND == "pi":
        pi_tools = _openai_tools_to_pi(tools)
        completion = _pi_chat(messages, tools=pi_tools)
        if completion.tool_calls:
            call = completion.tool_calls[0]
            allowed = {t["name"] for t in pi_tools}
            if call.name not in allowed:
                # Never hand an undeclared tool name on to dispatch.
                raise RuntimeError(f"Pi requested undeclared tool {call.name!r}; allowed: {sorted(allowed)}")
            return ToolCallResult(tool_name=call.name, tool_arguments=dict(call.arguments))
        return ToolCallResult(tool_name=None, final_text=completion.text)
    if LLM_BACKEND != "groq":
        return ToolCallResult(tool_name=None, final_text="(tool-calling round skipped on ollama backend)")

    response = _groq_chat(messages, tools=tools, tool_choice="auto")
    message = response.choices[0].message
    if message.tool_calls:
        call = message.tool_calls[0]
        return ToolCallResult(
            tool_name=call.function.name,
            tool_arguments=json.loads(call.function.arguments),
        )
    return ToolCallResult(tool_name=None, final_text=message.content)
