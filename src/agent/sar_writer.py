from __future__ import annotations

from pydantic import BaseModel

from src.agent.llm import generate_structured


class SARNarrativeOutput(BaseModel):
    narrative: str


async def write_sar_narrative(
    case_row: dict,
    case_summary: dict,
    episode: dict | None = None,
    connected_card_ids: list[str] | None = None,
    exposure_usd: float | None = None,
) -> str:
    """Answer-quality fix (2026-09-23): previously only ever saw `opened_at`
    (the case-open date, not the activity date) and had no idea how many
    transactions/how much money were actually involved or which other cards
    connect to this one -- so the narrative's own "when"/"how far it goes"
    could silently disagree with the structured `sar.activity_dates`/
    `sar.total_amount_usd`/`sar.subjects` fields built separately in
    run_case.py. Passing the same episode/connected-cards/exposure facts in
    here keeps the free-text narrative consistent with those fields."""
    episode = episode or {}
    connected_card_ids = connected_card_ids or []
    activity_span = (
        f"{episode.get('first_date')} to {episode.get('last_date')}"
        if episode.get("first_date") else str(case_row.get("opened_at", ""))[:10]
    )
    prompt = (
        "Write a suspicious activity report narrative, six to twelve sentences, covering "
        "who (customer, cards, merchants, devices), what happened, when (dates), where "
        "(locations, channels), how it was carried out, and why it is suspicious. Base it "
        "only on these facts -- do not invent details:\n\n"
        f"Customer: {case_row['customer_id']}, Card: {case_row['card_id']}\n"
        f"Connected cards: {connected_card_ids if connected_card_ids else 'none'}\n"
        f"Activity dates: {activity_span}\n"
        f"Transactions in this episode: {episode.get('txn_ids', [case_row.get('flagged_txn_id')])}\n"
        f"Total exposure: ${exposure_usd if exposure_usd is not None else episode.get('exposure_usd', 0.0)}\n"
        f"Pattern: {case_summary['pattern']}\n"
        f"Fraud probability: {case_summary['fraud_probability']}\n"
        f"Evidence claims: {case_summary['evidence_claims']}\n"
        f"Trigger: {case_row.get('trigger_text', '')}\n"
    )
    result = await generate_structured(prompt, SARNarrativeOutput)
    return result.narrative
