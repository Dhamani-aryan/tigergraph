from __future__ import annotations

from pydantic import BaseModel

from src.agent.llm import generate_structured


class SARNarrativeOutput(BaseModel):
    narrative: str


async def write_sar_narrative(case_row: dict, case_summary: dict) -> str:
    prompt = (
        "Write a suspicious activity report narrative, six to twelve sentences, covering "
        "who (customer, cards, merchants, devices), what happened, when (dates), where "
        "(locations, channels), how it was carried out, and why it is suspicious. Base it "
        "only on these facts -- do not invent details:\n\n"
        f"Customer: {case_row['customer_id']}, Card: {case_row['card_id']}\n"
        f"Date: {str(case_row.get('opened_at', ''))[:10]}\n"
        f"Pattern: {case_summary['pattern']}\n"
        f"Fraud probability: {case_summary['fraud_probability']}\n"
        f"Evidence claims: {case_summary['evidence_claims']}\n"
        f"Trigger: {case_row.get('trigger_text', '')}\n"
    )
    result = await generate_structured(prompt, SARNarrativeOutput)
    return result.narrative
