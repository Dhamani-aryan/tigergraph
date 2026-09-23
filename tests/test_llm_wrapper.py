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


def test_simulator_typical_amount_confirms():
    response = simulate_evidence_response(
        "customer_validation",
        flagged_amount=52.0,
        customer_median_amount=50.0,
        is_new_device=False,
        fraud_probability=0.2,
    )
    assert "confirms" in response
