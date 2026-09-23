import pytest

from src.agent.sar_writer import write_sar_narrative


@pytest.mark.asyncio
async def test_narrative_mentions_key_facts():
    case_row = {
        "customer_id": "C04570",
        "card_id": "C04570-K1",
        "opened_at": "2016-11-12 00:46:24",
        "trigger_text": "Real-time model scored transaction 3450629 ($100.09, online) at 0.57.",
    }
    case_summary = {
        "pattern": "card_testing",
        "fraud_probability": 0.86,
        "evidence_claims": ["Three small authorizations then a larger purchase"],
    }
    narrative = await write_sar_narrative(case_row, case_summary)
    assert len(narrative) > 100
    assert "C04570" in narrative or "card" in narrative.lower()
    # Medium finding from review: the narrative's "when" element must actually be
    # grounded in a date, not omitted -- 2016 is the only year present anywhere in
    # the inputs, so its presence confirms the date reached the prompt and the LLM.
    assert "2016" in narrative
