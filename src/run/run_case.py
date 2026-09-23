from __future__ import annotations

import time

from src.agent.graph_flow import _flagged_amount, build_graph
from src.agent.llm import token_tracker
from src.agent.sar_writer import write_sar_narrative
from src.agent.schemas import (
    ActionEntry,
    AnswerFile,
    CaseRecord,
    Evidence,
    EvidenceRequestRecord,
    NextBestActionSet,
    SAR,
)
from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


async def run_single_case(tg: TigerGraphMCP, case_row: dict) -> AnswerFile:
    start = time.monotonic()
    token_tracker.reset()  # each case's `tokens` field should reflect only its own calls
    app = build_graph(tg)
    final_state = await app.ainvoke({"case_row": case_row})

    assessment = final_state["assessment"]
    initial_actions = [ActionEntry(**a) for a in final_state["initial_policy_result"]["actions"]]
    final_actions = [ActionEntry(**a) for a in final_state["final_policy_result"]["actions"]]
    sar_info = final_state["final_policy_result"]

    verdict = (
        "fraud" if assessment["fraud_probability"] >= 0.7
        else "legitimate" if assessment["fraud_probability"] <= 0.15
        else "uncertain"
    )
    status = (
        "closed_fraud" if verdict == "fraud"
        else "closed_legitimate" if verdict == "legitimate"
        else "escalated"
    )

    graph_case_id = f"CASE-{case_row['case_id']}"
    written = await _write_case_to_graph(
        tg, graph_case_id, case_row, assessment, final_actions, sar_info, verdict, status
    )

    exposure_usd = _flagged_amount(case_row) if verdict != "legitimate" else 0.0

    case_record = CaseRecord(
        status=status,
        verdict=verdict,
        fraud_probability=assessment["fraud_probability"],
        pattern=assessment["pattern"],
        pattern_description="",
        affected_txn_ids=[str(case_row["flagged_txn_id"])] if verdict != "legitimate" else [],
        first_suspicious_txn_id=str(case_row["flagged_txn_id"]) if verdict != "legitimate" else "",
        connected_card_ids=[],
        connected_device_profiles=[],
        exposure_usd=exposure_usd,
        evidence=[
            Evidence(claim=c, source="graph", ref=f"assessment", entity_ids=[])
            for c in assessment["evidence_claims"]
        ],
        similar_prior_cases=assessment.get("similar_prior_case_ids", []),
        summary=f"Pattern {assessment['pattern']} assessed at probability {assessment['fraud_probability']:.2f}.",
        written_to_graph=written,
        graph_case_id=graph_case_id if written else "",
    )

    evidence_requests = [
        EvidenceRequestRecord(**er) for er in final_state.get("evidence_requests", [])
    ]

    next_best_actions = NextBestActionSet(
        initial=initial_actions,
        final=final_actions,
        what_changed=(
            "nothing" if initial_actions == final_actions
            else "Simulated evidence response changed the recommended actions."
        ),
    )

    narrative = ""
    if sar_info["sar_file"]:
        narrative = await write_sar_narrative(case_row, assessment)

    sar = SAR(
        file=sar_info["sar_file"],
        reason=sar_info["sar_reason"],
        narrative=narrative,
        subjects=[case_row["customer_id"], case_row["card_id"]] if sar_info["sar_file"] else [],
        total_amount_usd=case_record.exposure_usd if sar_info["sar_file"] else 0.0,
        activity_dates=[] if not sar_info["sar_file"] else [str(case_row["opened_at"])[:10]] * 2,
    )

    return AnswerFile(
        case_id=case_row["case_id"],
        case=case_record,
        evidence_requests=evidence_requests,
        next_best_actions=next_best_actions,
        sar=sar,
        stop_reason=final_state["stop_reason"],
        tool_calls=final_state["tool_calls"],
        tokens=token_tracker.total,  # 0 on the ollama fallback backend, real usage on Groq
        latency_s=round(time.monotonic() - start, 1),
    )


async def _write_case_to_graph(
    tg: TigerGraphMCP, graph_case_id: str, case_row: dict, assessment: dict,
    final_actions: list[ActionEntry], sar_info: dict, verdict: str, status: str,
) -> bool:
    summary_text = (
        f"Case {graph_case_id} on card {case_row['card_id']}: pattern "
        f"{assessment['pattern']}, probability {assessment['fraud_probability']:.2f}. "
        f"{' '.join(assessment['evidence_claims'])}"
    )
    try:
        # Task 7 confirmed live against this server: raw tg.gsql("INSERT INTO
        # VERTEX ...") is rejected outright. Use tigergraph__add_nodes (REST++
        # batch upsert) instead -- named fields also removes the positional
        # field-order risk the original INSERT statement had (verdict/status
        # were once swapped there by mistake; a dict keyed by attribute name
        # can't have that specific bug).
        await tg.call(
            "tigergraph__add_nodes",
            {
                "vertex_type": "FraudCase",
                "vertex_id": "case_id",
                "vertices": [
                    {
                        "case_id": graph_case_id,
                        "customer_id": case_row["customer_id"],
                        "card_id": case_row["card_id"],
                        "status": status,
                        "verdict": verdict,
                        "fraud_probability": assessment["fraud_probability"],
                        "pattern": assessment["pattern"],
                        "exposure_usd": _flagged_amount(case_row),
                        "summary": summary_text,
                        "written_at": "now",
                    }
                ],
            },
        )
        # Embed and upsert immediately -- this is what makes case memory real within
        # the same 20-case batch run: a later case's retrieve_knowledge call (Task 10)
        # searches the `FraudCase` vertex type and will find this one, not just
        # pre-loaded ClosedCase history. See spec §6 step 8.
        from src.ingestion.embeddings import embed  # local import: keeps run_case.py
                                                       # decoupled from ingestion until
                                                       # the write path actually needs it
        vector = embed([summary_text])[0]
        await tg.upsert_vectors("FraudCase", "embedding", [{"vertex_id": graph_case_id, "vector": vector}])
        return True
    except Exception:  # noqa: BLE001
        return False
