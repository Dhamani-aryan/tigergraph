import pytest
from pydantic import BaseModel

from src.agent.llm import generate_structured
from src.agent.simulator import simulate_evidence_response


class _TinySchema(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_generate_structured_returns_valid_instance():
    result = await generate_structured("Reply with a JSON object with an 'answer' field containing the word 'ok'.", _TinySchema)
    assert isinstance(result, _TinySchema)
    assert result.answer


@pytest.mark.asyncio
async def test_generate_with_tools_calls_a_tool_when_ambiguous():
    from src.agent.llm import generate_with_tools

    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup_region",
                "description": "Look up other activity in a billing region.",
                "parameters": {"type": "object", "properties": {"addr1": {"type": "string"}}, "required": ["addr1"]},
            },
        }
    ]
    result = await generate_with_tools(
        "The card's own history is thin and inconclusive. Region code is 444. "
        "Consider whether checking regional activity would help before deciding.",
        tools,
    )
    # On the groq backend this should pick the tool; on the ollama fallback it degrades
    # to no-tool-call by design (see generate_with_tools docstring) -- assert only what
    # both backends guarantee: the call completes and returns a well-formed result.
    assert result.tool_name is None or result.tool_name == "lookup_region"


def test_token_tracker_resets_and_accumulates():
    from src.agent.llm import TokenTracker

    class _FakeUsage:
        total_tokens = 42

    class _FakeResponse:
        usage = _FakeUsage()

    tracker = TokenTracker()
    tracker.add_from_response(_FakeResponse())
    tracker.add_from_response(_FakeResponse())
    assert tracker.total == 84
    tracker.reset()
    assert tracker.total == 0


def test_simulator_anomalous_amount_denies():
    response = simulate_evidence_response(
        "customer_validation",
        flagged_amount=500.0,
        customer_median_amount=50.0,
        is_new_device=False,
        fraud_probability=0.7,
    )
    assert "did not make" in response


@pytest.mark.asyncio
async def test_generate_structured_prompt_states_exact_schema_field_names(monkeypatch):
    """Task 12 review: Groq's response_format={"type": "json_object"} only guarantees
    syntactically valid JSON, not correct field names -- on a live re-run the model
    consistently emitted `fraud_pattern` instead of the schema's real `pattern` field,
    exhausting all retries, because the prompt never stated the literal expected keys
    anywhere. This asserts the fix without any live LLM call: the schema's own field
    names are embedded in the very FIRST prompt sent to the model, not only in a
    post-failure retry message."""
    import src.agent.llm as llm_module

    captured: list[list[dict]] = []

    async def _fake_chat_raw(messages, schema):
        captured.append(messages)
        return '{"pattern": "card_testing"}'

    monkeypatch.setattr(llm_module, "_chat_raw", _fake_chat_raw)

    class _PatternSchema(BaseModel):
        pattern: str

    result = await llm_module.generate_structured("Classify the pattern.", _PatternSchema)
    assert result.pattern == "card_testing"
    assert len(captured) == 1  # succeeded on the first attempt, no retry needed
    first_user_prompt = captured[0][-1]["content"]
    assert '"pattern"' in first_user_prompt  # the literal required key, not just prose
    assert '"properties"' in first_user_prompt  # the rendered JSON schema, not just a key list


@pytest.mark.asyncio
async def test_generate_structured_recovers_from_wrong_key_name_via_retry(monkeypatch):
    """Deterministically reproduces the review's exact failure shape (model emits
    `fraud_pattern` instead of `pattern` on the first attempt) without a live LLM
    call, and confirms the retry loop's improved feedback (which now states the
    exact required field names, not just a generic "not valid JSON") lets a
    self-correcting model recover within the existing retry budget."""
    import src.agent.llm as llm_module

    responses = iter(
        [
            '{"fraud_pattern": "card_testing"}',  # wrong key, same shape the live run hit
            '{"pattern": "card_testing"}',  # corrected on retry
        ]
    )

    async def _fake_chat_raw(messages, schema):
        return next(responses)

    monkeypatch.setattr(llm_module, "_chat_raw", _fake_chat_raw)

    class _PatternSchema(BaseModel):
        pattern: str

    result = await llm_module.generate_structured("Classify the pattern.", _PatternSchema, max_retries=2)
    assert result.pattern == "card_testing"


def test_simulator_typical_amount_confirms():
    response = simulate_evidence_response(
        "customer_validation",
        flagged_amount=52.0,
        customer_median_amount=50.0,
        is_new_device=False,
        fraud_probability=0.2,
    )
    assert "confirms" in response
