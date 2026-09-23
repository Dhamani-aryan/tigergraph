from __future__ import annotations

import time
from datetime import datetime, timezone

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
    is_legit = verdict == "legitimate"

    # Answer-quality fix (2026-09-23): everything below this point used to
    # come from the flagged transaction ALONE (affected_txn_ids = [flagged],
    # first_suspicious_txn_id = flagged, exposure_usd = flagged amount,
    # connected_card_ids/connected_device_profiles = [] unconditionally) --
    # see docs/frontend-spec.md's UI contract and the README's own field
    # table for why this matters for scoring. `episode` (src.agent.episode,
    # computed in gather_evidence_node) is the actual fraud episode this
    # card shows, deterministically derived from card_window.
    episode = final_state.get("episode") or {}
    flagged_txn_id = str(case_row["flagged_txn_id"])
    affected_txn_ids = [] if is_legit else list(dict.fromkeys(episode.get("txn_ids") or [flagged_txn_id]))
    first_suspicious_txn_id = "" if is_legit else (episode.get("first_txn_id") or flagged_txn_id)
    exposure_usd = 0.0 if is_legit else round(
        float(episode.get("exposure_usd") or _flagged_amount(case_row)), 2
    )
    connected_card_ids = [] if is_legit else list(final_state.get("connected_card_ids") or [])
    connected_device_profiles = [] if is_legit else list(final_state.get("connected_device_profiles") or [])

    similar_prior_cases = _grounded_similar_cases(final_state, assessment.get("similar_prior_case_ids", []))
    evidence = _build_evidence(case_row, final_state, assessment, episode)
    summary = _build_summary(assessment, verdict, episode, connected_card_ids)
    pattern_description = assessment.get("pattern_description", "") if assessment["pattern"] == "undocumented" else ""

    graph_case_id = f"CASE-{case_row['case_id']}"
    written = await _write_case_to_graph(
        tg, graph_case_id, case_row, assessment, verdict, status, exposure_usd
    )

    case_record = CaseRecord(
        status=status,
        verdict=verdict,
        fraud_probability=assessment["fraud_probability"],
        pattern=assessment["pattern"],
        pattern_description=pattern_description,
        affected_txn_ids=affected_txn_ids,
        first_suspicious_txn_id=first_suspicious_txn_id,
        connected_card_ids=connected_card_ids,
        connected_device_profiles=connected_device_profiles,
        exposure_usd=exposure_usd,
        evidence=evidence,
        similar_prior_cases=similar_prior_cases,
        summary=summary,
        written_to_graph=written,
        graph_case_id=graph_case_id if written else "",
    )

    evidence_requests = [
        EvidenceRequestRecord(**er) for er in final_state.get("evidence_requests", [])
    ]

    next_best_actions = NextBestActionSet(
        initial=initial_actions,
        final=final_actions,
        what_changed=_what_changed(case_row, final_state, initial_actions, final_actions),
    )

    narrative = ""
    if sar_info["sar_file"]:
        narrative = await write_sar_narrative(case_row, assessment)

    sar = _build_sar(case_row, sar_info, narrative, episode, connected_card_ids, flagged_txn_id, exposure_usd)

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


def _grounded_similar_cases(final_state: dict, llm_ids: list[str]) -> list[str]:
    """Answer-quality fix (2026-09-23): the LLM's `similar_prior_case_ids`
    were passed straight into the answer file with no check that those IDs
    actually came back from a real closed-case lookup -- an LLM can invent a
    plausible-looking case ID. Filters to IDs that appear in this case's own
    `closed_cases` evidence (README rule: "Every ID in your answer files
    must exist in this dataset")."""
    evidence = final_state.get("evidence") or []
    closed = next((e["data"] for e in evidence if e["type"] == "closed_cases"), []) or []
    real_ids = {c.get("id") for c in closed if c.get("id")}
    return [cid for cid in llm_ids if cid in real_ids]


def _build_evidence(
    case_row: dict, final_state: dict, assessment: dict, episode: dict
) -> list[Evidence]:
    """Answer-quality fix (2026-09-23): every evidence entry used to be the
    LLM's free-text `evidence_claims` with `ref="assessment"` and
    `entity_ids=[]` -- no real query name, no real entity ids (README:
    each entry needs `ref` as "query name, document section, or request id"
    and `entity_ids` as "the IDs the claim rests on"). Builds one entry per
    deterministic finding that actually fired (with the real query call and
    entity ids), then appends the LLM's own synthesis claims grounded to the
    episode's transaction ids rather than left empty."""
    card_id = case_row["card_id"]
    flagged_id = str(case_row["flagged_txn_id"])
    episode_ids = episode.get("txn_ids") or [flagged_id]
    entries: list[Evidence] = []

    ev_by_type = {e["type"]: e["data"] for e in (final_state.get("evidence") or [])}

    detected_pattern = episode.get("detected_pattern")
    if detected_pattern == "card_testing":
        entries.append(Evidence(
            claim=(
                f"Card-testing sequence detected on card {card_id}: "
                f"{len(episode_ids) - 1} small online authorization(s) followed by a larger purchase."
            ),
            source="graph",
            ref=f"query:card_window(card_id={card_id})",
            entity_ids=episode_ids,
        ))
    elif detected_pattern == "cnp_burst":
        entries.append(Evidence(
            claim=(
                f"{len(episode_ids)} related online transaction(s) within 48 hours of the "
                f"flagged transaction on card {card_id}."
            ),
            source="graph",
            ref=f"query:card_window(card_id={card_id}, hours=48)",
            entity_ids=episode_ids,
        ))

    if final_state.get("is_new_device"):
        entries.append(Evidence(
            claim="The flagged transaction's device fingerprint is marked New for this account (id_15).",
            source="graph",
            ref=f"query:card_window(card_id={card_id})",
            entity_ids=[flagged_id],
        ))
    if final_state.get("is_proxy"):
        entries.append(Evidence(
            claim="The flagged transaction was made through an anonymizing/hidden IP proxy (id_23).",
            source="graph",
            ref=f"query:card_window(card_id={card_id})",
            entity_ids=[flagged_id],
        ))
    if final_state.get("out_of_region"):
        entries.append(Evidence(
            claim=(
                "The flagged transaction's billing region differs from this card's usual region, "
                "while activity in the usual region continues."
            ),
            source="graph",
            ref=f"query:card_window(card_id={card_id})",
            entity_ids=[flagged_id],
        ))

    connected = final_state.get("connected_card_ids") or []
    if connected:
        shown = ", ".join(connected[:5]) + ("..." if len(connected) > 5 else "")
        entries.append(Evidence(
            claim=f"The flagged transaction's device fingerprint is shared with {len(connected)} other card(s): {shown}.",
            source="graph",
            ref=f"query:device_neighbors(transaction_id={flagged_id})",
            entity_ids=connected,
        ))

    closed = ev_by_type.get("closed_cases") or []
    closed_ids = [c.get("id") for c in closed if c.get("id")]
    if closed_ids:
        entries.append(Evidence(
            claim=f"{len(closed_ids)} closed case(s) connect to this card, device, or region.",
            source="graph",
            ref=f"query:closed_case_lookup(card_id={card_id})",
            entity_ids=closed_ids[:10],
        ))

    ring = ev_by_type.get("ring_membership") or {}
    ring_rate = ring.get("cluster_prior_fraud_rate") or 0.0
    if ring.get("ring_cluster_id") and ring_rate > 0:
        entries.append(Evidence(
            claim=(
                f"Card belongs to cluster {ring['ring_cluster_id']} with a prior "
                f"confirmed-fraud rate of {ring_rate:.2f}."
            ),
            source="graph",
            ref=f"query:ring_membership(card_id={card_id})",
            entity_ids=[card_id],
        ))

    # LLM's own synthesis claims, still surfaced (they can name things the
    # deterministic checks above don't cover), grounded to the episode's own
    # transaction ids rather than left with entity_ids=[].
    for claim in assessment.get("evidence_claims", []) or []:
        entries.append(Evidence(claim=claim, source="graph", ref="assessment:llm_synthesis", entity_ids=episode_ids))

    if case_row.get("trigger_type") == "customer_report":
        entries.append(Evidence(
            claim=f"Customer reported this transaction as unrecognized: \"{case_row.get('trigger_text', '')}\"",
            source="customer",
            ref="trigger:customer_report",
            entity_ids=[flagged_id],
        ))
    elif final_state.get("evidence_requests"):
        entries.append(Evidence(
            claim=final_state["evidence_requests"][-1]["assumed_response"],
            source="customer",
            ref="evidence_request:1",
            entity_ids=[flagged_id],
        ))

    return entries


def _build_summary(assessment: dict, verdict: str, episode: dict, connected_card_ids: list[str]) -> str:
    """Answer-quality fix (2026-09-23): was a single generic templated
    sentence regardless of what was actually found. README wants "two to
    six sentences an analyst could read.\""""
    pattern = assessment["pattern"]
    prob = assessment["fraud_probability"]
    if verdict == "legitimate":
        base = f"Reviewed and closed as legitimate activity (pattern: {pattern}, probability {prob:.2f})."
    else:
        n = len(episode.get("txn_ids") or [])
        base = (
            f"{pattern.replace('_', ' ').title()} identified at probability {prob:.2f}, "
            f"spanning {n} transaction{'s' if n != 1 else ''} totaling ${episode.get('exposure_usd', 0.0):.2f}."
        )
    claims = assessment.get("evidence_claims") or []
    detail = " ".join(claims[:3])
    connected_note = f" Connects to {len(connected_card_ids)} other card(s)." if connected_card_ids else ""
    summary = f"{base} {detail}{connected_note}".strip()
    return summary[:900]  # a summary, not the SAR narrative -- README: "Keep summary short"


def _what_changed(
    case_row: dict, final_state: dict, initial_actions: list[ActionEntry], final_actions: list[ActionEntry]
) -> str:
    if initial_actions == final_actions:
        return "nothing"
    if case_row.get("trigger_type") == "customer_report":
        if final_state.get("recurring_charge_detected"):
            return (
                "The customer's own report matched a charge recurring monthly on this card (R7), "
                "which changed the recommendation away from a block."
            )
        return (
            "The customer's own report, already on file at the time the case opened, established "
            "non-recognition of the charge (R2), which changed the recommendation."
        )
    if final_state.get("evidence_requests"):
        return "Simulated evidence response changed the recommended actions."
    return "The recommendation changed as additional graph evidence was incorporated."


def _build_sar(
    case_row: dict,
    sar_info: dict,
    narrative: str,
    episode: dict,
    connected_card_ids: list[str],
    flagged_txn_id: str,
    exposure_usd: float,
) -> SAR:
    if not sar_info["sar_file"]:
        return SAR(file=False, reason=sar_info["sar_reason"], narrative="", subjects=[], total_amount_usd=0.0, activity_dates=[])

    # Answer-quality fix (2026-09-23): activity_dates used to always be
    # [opened_at, opened_at] -- the case-OPEN date, not the actual activity
    # dates. episode.first_date/last_date (src.agent.episode) come from the
    # real `ts` of the transactions in the episode.
    first_date = episode.get("first_date") or str(case_row.get("opened_at", ""))[:10]
    last_date = episode.get("last_date") or first_date
    subjects = [case_row["customer_id"], case_row["card_id"], *connected_card_ids]

    return SAR(
        file=True,
        reason=sar_info["sar_reason"],
        narrative=narrative,
        subjects=list(dict.fromkeys(subjects)),
        total_amount_usd=exposure_usd,
        activity_dates=[first_date, last_date],
    )


async def _write_case_to_graph(
    tg: TigerGraphMCP, graph_case_id: str, case_row: dict, assessment: dict,
    verdict: str, status: str, exposure_usd: float,
) -> bool:
    summary_text = (
        f"Case {graph_case_id} on card {case_row['card_id']}: pattern "
        f"{assessment['pattern']}, probability {assessment['fraud_probability']:.2f}. "
        f"{' '.join(assessment['evidence_claims'])}"
    )
    written_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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
                        "exposure_usd": exposure_usd,
                        "summary": summary_text,
                        # Answer-quality fix (2026-09-23): was the literal
                        # string "now" -- a real timestamp, matching every
                        # other `ts`/`opened_at`/`closed_at` field's format.
                        "written_at": written_at,
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
    except Exception:  # noqa: BLE001
        # Fail LOUD to the caller's log, but still report written_to_graph=False
        # rather than raising -- a graph outage shouldn't crash the whole batch
        # run for the other 19 cases. The read-back below is the real signal;
        # this except only guards the write calls themselves.
        return False

    # Reliability fix (2026-09-23): a successful `add_nodes` response is not
    # proof the case is actually readable back out of the graph (the old
    # code treated `written_to_graph=True` as soon as the write calls
    # returned without raising). Read the vertex back independently and
    # confirm it carries the values just written, matching the pattern
    # Aryan's review recommended (a receipt, not a response).
    try:
        readback = await tg.call("tigergraph__get_node", {"vertex_type": "FraudCase", "vertex_id": graph_case_id})
        data = readback.get("data", {})
        attrs = data.get("attributes", {})
        # FraudCase has no `primary_id_as_attribute` (confirmed live -- see
        # src/schema/build_schema.py), so `case_id` itself is only readable
        # as the vertex's own `v_id`, not inside `attributes`.
        return data.get("v_id") == graph_case_id and attrs.get("verdict") == verdict
    except Exception:  # noqa: BLE001
        return False
