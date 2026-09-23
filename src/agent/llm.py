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
    # Reliability fix (2026-09-24): confirmed live during Task 14's batch
    # run that the OLD schedule (5 attempts, max 30s backoff -- ~60s total)
    # was not patient enough: every one of 11 cases run back-to-back hit
    # Groq's TPM window and exhausted all 5 attempts before the window
    # cleared. This schedule's own exponential ceiling (90s) plus the
    # explicit Retry-After sleep below comfortably rides out a standard
    # 60s TPM window even across several stacked calls.
    wait=wait_exponential(multiplier=1, min=2, max=90),
    stop=stop_after_attempt(8),
)
def _groq_chat(messages: list[dict], **kwargs) -> "openai.types.chat.ChatCompletion":
    client = _groq_client()
    try:
        response = client.chat.completions.create(model=GROQ_MODEL, messages=messages, **kwargs)
        token_tracker.add_from_response(response)
        return response
    except openai.RateLimitError as exc:
        retry_after = _retry_after_seconds(exc)
        if retry_after is not None:
            # Sleep the server's own stated duration BEFORE handing off to
            # tenacity's backoff, so the very next attempt (not just some
            # later one) lands after the window actually clears.
            time.sleep(min(retry_after + 1, 65))
        raise _RateLimited from exc


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
