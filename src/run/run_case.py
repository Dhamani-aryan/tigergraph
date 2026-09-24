from __future__ import annotations

import time
from datetime import datetime, timezone

from src.agent.graph_flow import _flagged_amount, build_graph
from src.agent.decision import resolve_status
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
    """Back-compat wrapper (existing tests/callers use this exact signature)
    -- discards the trace context. Use run_single_case_with_context when
    you also need runs/latest/traces/<case_id>.trace.json (see run_all.py)."""
    answer, _ctx = await run_single_case_with_context(tg, case_row)
    return answer


async def run_single_case_with_context(tg: TigerGraphMCP, case_row: dict) -> tuple[AnswerFile, dict]:
    """Same investigation as run_single_case, but also returns a `context`
    dict (final_state plus the few extra values -- written_at -- that
    only exist as locals in this function) for src.run.trace_writer to
    build a trace file from, without re-running the investigation."""
    start = time.monotonic()
    token_tracker.reset()  # each case's `tokens` field should reflect only its own calls
    app = build_graph(tg)
    final_state = await app.ainvoke({"case_row": case_row})

    assessment = final_state["assessment"]
    initial_actions = [ActionEntry(**a) for a in final_state["initial_policy_result"]["actions"]]
    final_actions = [ActionEntry(**a) for a in final_state["final_policy_result"]["actions"]]
    sar_info = final_state["final_policy_result"]

    # Single source of truth (src.agent.decision): verdict, probability,
    # pattern, status, affected transactions and SAR all come from the same
    # resolved decision the policy engine used. There is no separate
    # probability threshold here any more (the old >= 0.70 fraud rule).
    decision = final_state["decision"]
    verdict = decision["verdict"]
    status = resolve_status(verdict, [a.action for a in final_actions])
    is_legit = verdict == "legitimate"

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
    summary = _build_summary(decision, assessment, verdict, episode, connected_card_ids)
    pattern = decision["pattern"]
    pattern_description = decision.get("pattern_description", "") if pattern == "undocumented" else ""
    resolved = {**assessment, "pattern": pattern, "fraud_probability": decision["fraud_probability"]}

    graph_case_id = f"CASE-{case_row['case_id']}"
    written, written_at = await _write_case_to_graph(
        tg, graph_case_id, case_row, resolved, verdict, status, exposure_usd
    )

    case_record = CaseRecord(
        status=status,
        verdict=verdict,
        fraud_probability=decision["fraud_probability"],
        pattern=pattern,
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
        narrative = await write_sar_narrative(
            case_row, resolved, episode=episode, connected_card_ids=connected_card_ids, exposure_usd=exposure_usd
        )

    sar = _build_sar(case_row, sar_info, narrative, episode, connected_card_ids, flagged_txn_id, exposure_usd)

    answer = AnswerFile(
        case_id=case_row["case_id"],
        case=case_record,
        evidence_requests=evidence_requests,
        next_best_actions=next_best_actions,
        sar=sar,
        stop_reason=final_state["stop_reason"],
        # Consistency fix (2026-09-24), caught by cross-checking a trace
        # against its own answer file (ui/src/contracts/validate.ts's
        # crossCheckAnswerTrace): final_state["tool_calls"] only counts
        # calls made INSIDE the LangGraph flow (gather_evidence + the
        # bounded followup) -- the graph write and its independent
        # read-back happen afterward, in this function, and were never
        # counted at all. +3 (add_nodes, upsert_vectors, get_node) matches
        # what _write_case_to_graph always attempts.
        tool_calls=final_state["tool_calls"] + 3,
        tokens=token_tracker.total,  # 0 on the ollama fallback backend, real usage on Groq
        latency_s=round(time.monotonic() - start, 1),
    )
    context = {
        "final_state": final_state,
        "written_at": written_at,
        "graph_case_id": graph_case_id,
        "episode": episode,
        "connected_card_ids": connected_card_ids,
    }
    return answer, context


def _grounded_similar_cases(final_state: dict, llm_ids: list[str]) -> list[str]:
    """Answer-quality fix (2026-09-23): the LLM's `similar_prior_case_ids`
    were passed straight into the answer file with no check that those IDs
    actually came back from a real lookup -- an LLM can invent a
    plausible-looking case ID. Filters to IDs that appear in either of the
    TWO sources this case's own evidence draws closed cases from:
    `closed_cases` (the graph-traversal lookup by card/device/region, always
    real ClosedCase ids) and `knowledge.similar_cases` (retrieve_knowledge's
    vector search, which searches ClosedCase AND FraudCase together and
    returns both in one list -- see vector_search.py's own_case_hits).

    Bug fix (found live on HHG-001's actual batch output, caught by
    validate_outputs.py before it was fixed here): the README field is
    explicitly "Closed-case IDs from closed_cases_history.csv" only -- a
    FraudCase id (this pipeline's own writes, format "CASE-HHG-XXX") must
    NEVER be accepted here, including a case citing ITS OWN prior write of
    the same case_id (observed live: "CASE-HHG-001" cited as a "similar
    prior case" for HHG-001 itself, from a stale FraudCase vector entry).
    `retrieve_knowledge`'s hits already carry `type` (ClosedCase/FraudCase,
    via vector_search.py's `_unwrap_hits`), so this filters on that rather
    than trusting every id in `similar_cases` alike.
    """
    evidence = final_state.get("evidence") or []
    closed = next((e["data"] for e in evidence if e["type"] == "closed_cases"), []) or []
    knowledge = next((e["data"] for e in evidence if e["type"] == "knowledge"), {}) or {}
    real_ids = {c.get("id") for c in closed if c.get("id")}
    real_ids |= {
        c.get("id") for c in (knowledge.get("similar_cases") or [])
        if c.get("id") and c.get("type") == "ClosedCase"
    }
    return [cid for cid in llm_ids if cid in real_ids]


def _build_evidence(
    case_row: dict, final_state: dict, assessment: dict, episode: dict
) -> list[Evidence]:
    """One entry per deterministic observation, with the query/signal that
    produced it and the entity ids it rests on. Only fired or observed facts
    are stated; a signal that did not fire is never listed as supporting
    evidence. `signal:*` refs are the detector outputs the semantic
    validator checks (e.g. a CNP entry may only cite online rows)."""
    card_id = case_row["card_id"]
    flagged_id = str(case_row["flagged_txn_id"])
    window_ref = f"query:card_window(card_id={card_id}, reference_txn_id={flagged_id})"
    profile = final_state.get("behavior_profile") or {}
    signals = final_state.get("signals") or {}
    ev_by_type = {e["type"]: e["data"] for e in (final_state.get("evidence") or [])}
    entries: list[Evidence] = []

    if profile.get("flagged_found"):
        bits = [f"{profile.get('history_count')} prior transactions"]
        if profile.get("amount_ratio") is not None:
            bits.append(
                f"amount ${profile.get('flagged_amount'):.2f} is {profile.get('amount_ratio')}x the prior median "
                f"${profile.get('amount_median')} (percentile {profile.get('amount_percentile')}, {profile.get('amount_class')})"
            )
        if profile.get("flagged_product"):
            bits.append(
                f"ProductCD {profile['flagged_product']} used {profile.get('product_prior_count')} times before "
                f"({profile.get('product_class')})"
            )
        if profile.get("flagged_channel") == "in_person" and profile.get("flagged_region"):
            bits.append(
                f"region {profile['flagged_region']} used {profile.get('flagged_region_prior_in_person_count')} times "
                f"in person before ({profile.get('region_class')})"
            )
        entries.append(Evidence(
            claim=f"Behavior profile ({profile.get('flagged_channel') or 'unknown'} channel): " + "; ".join(bits) + ".",
            source="graph", ref=f"{window_ref}; features:behavior_profile", entity_ids=[flagged_id],
        ))

    card_testing = signals.get("card_testing") or {}
    if card_testing.get("fired"):
        entries.append(Evidence(
            claim=f"Card-testing sequence: {card_testing.get('reason')}",
            source="graph", ref="signal:card_testing", entity_ids=list(card_testing.get("txn_ids") or []),
        ))

    cnp = signals.get("cnp_burst") or {}
    if cnp.get("flagged_online") and (cnp.get("online_count") or 0) >= 2:
        entries.append(Evidence(
            claim=f"Online activity around the flagged transaction: {cnp.get('reason')}.",
            source="graph", ref="signal:cnp_burst", entity_ids=list(cnp.get("online_txn_ids") or []),
        ))

    if profile.get("flagged_channel") == "online":
        if profile.get("is_new_device"):
            entries.append(Evidence(
                claim="The flagged transaction's device is marked New for this account (id_15); on its own this is not proof.",
                source="graph", ref=window_ref, entity_ids=[flagged_id],
            ))
        elif profile.get("is_new_device") is False:
            entries.append(Evidence(
                claim="The flagged transaction's device is already Found for this account (id_15).",
                source="graph", ref=window_ref, entity_ids=[flagged_id],
            ))
        if profile.get("is_proxy"):
            entries.append(Evidence(
                claim=f"The flagged transaction came through an anonymizing proxy ({profile.get('proxy_type')}, id_23).",
                source="graph", ref=window_ref, entity_ids=[flagged_id],
            ))

    region = signals.get("out_of_region") or {}
    if region.get("fired"):
        entries.append(Evidence(
            claim=f"Out-of-region card-present use: {region.get('reason')}.",
            source="graph", ref="signal:out_of_region", entity_ids=list(region.get("evidence_ids") or [flagged_id]),
        ))

    recurrence = signals.get("recurrence") or {}
    if recurrence.get("tier") in ("candidate", "strong"):
        entries.append(Evidence(
            claim=f"Recurrence check ({recurrence.get('tier')}): {recurrence.get('reason')}. {recurrence.get('proxy_note')}",
            source="graph", ref="signal:recurrence",
            entity_ids=[flagged_id, *(recurrence.get("monthly_match_ids") or [])],
        ))

    network = signals.get("device_network") or {}
    if network.get("corroborated"):
        entries.append(Evidence(
            claim=f"Direct shared-device corroboration: {network.get('reason')}.",
            source="graph", ref=f"signal:device_network(transaction_id={flagged_id})",
            entity_ids=[
                *(network.get("corroborated_card_ids") or []), *(network.get("corroborating_txn_ids") or []),
                *(network.get("confirmed_fraud_case_ids") or []),
            ],
        ))
    elif network.get("available") and (network.get("other_card_count") or 0) > 0:
        entries.append(Evidence(
            claim=f"Device profile context (not corroboration): {network.get('reason')}.",
            source="graph", ref=f"context:device_network(transaction_id={flagged_id})", entity_ids=[],
        ))

    closed = ev_by_type.get("closed_cases") or []
    if closed:
        outcomes = ", ".join(f"{c.get('id')} {c.get('outcome')}" for c in closed[:5] if c.get("id"))
        entries.append(Evidence(
            claim=f"Closed-case history on this card (context; does not decide this transaction): {outcomes}.",
            source="graph", ref=f"query:closed_case_lookup(card_id={card_id})",
            entity_ids=[c["id"] for c in closed[:10] if c.get("id")],
        ))

    ring = ev_by_type.get("ring_context") or {}
    if ring.get("ring_cluster_id"):
        rate = ring.get("cluster_prior_fraud_rate")
        entries.append(Evidence(
            claim=(
                f"Connected component {ring['ring_cluster_id']}: {ring.get('n_cards')} cards, "
                f"{ring.get('n_closed_cases')} closed case(s) behind a prior confirmed-fraud rate of "
                f"{rate if rate is not None else 'n/a'} -- contextual graph information, not evidence of a ring."
            ),
            source="graph", ref=f"context:ring_membership(card_id={card_id})", entity_ids=[card_id],
        ))

    for claim in assessment.get("evidence_claims", []) or []:
        entries.append(Evidence(claim=claim, source="graph", ref="assessment:llm_synthesis", entity_ids=[flagged_id]))

    if case_row.get("trigger_type") == "customer_report":
        entries.append(Evidence(
            claim=f"Customer's own report on file: \"{case_row.get('trigger_text', '')}\"",
            source="customer", ref="trigger:customer_report", entity_ids=[flagged_id],
        ))
    elif final_state.get("evidence_requests"):
        entries.append(Evidence(
            claim=final_state["evidence_requests"][-1]["assumed_response"],
            source="customer", ref="evidence_request:1", entity_ids=[flagged_id],
        ))

    return entries


def _build_summary(
    decision: dict, assessment: dict, verdict: str, episode: dict, connected_card_ids: list[str]
) -> str:
    prob = decision["fraud_probability"]
    if verdict == "legitimate":
        base = f"Closed as legitimate at probability {prob:.2f}. {decision.get('reason', '')}"
        detail = " ".join((assessment.get("benign_facts") or [])[:2])
    elif verdict == "fraud":
        n = len(episode.get("txn_ids") or [])
        base = (
            f"{decision['pattern'].replace('_', ' ').title()} at probability {prob:.2f}, spanning {n} "
            f"transaction{'s' if n != 1 else ''} totaling ${episode.get('exposure_usd', 0.0):.2f}. "
            f"{decision.get('reason', '')}"
        )
        detail = " ".join((assessment.get("evidence_claims") or [])[:2])
    else:
        base = f"Uncertain at probability {prob:.2f}. {decision.get('reason', '')}"
        detail = " ".join((assessment.get("evidence_claims") or [])[:2])
    connected_note = f" Directly corroborated with {len(connected_card_ids)} other card(s)." if connected_card_ids else ""
    return f"{base} {detail}{connected_note}".strip()[:900]


def _what_changed(
    case_row: dict, final_state: dict, initial_actions: list[ActionEntry], final_actions: list[ActionEntry]
) -> str:
    if [a.action for a in initial_actions] == [a.action for a in final_actions]:
        return "nothing"
    decision = final_state.get("decision") or {}
    initial = final_state.get("initial_decision") or {}
    probs = ""
    if initial.get("fraud_probability") is not None and decision.get("fraud_probability") is not None:
        probs = f" Probability {initial['fraud_probability']:.2f} -> {decision['fraud_probability']:.2f}."
    if case_row.get("trigger_type") == "customer_report":
        if decision.get("response") == "disputes_recurring" or final_state.get("recurring_charge_detected"):
            return (
                "The customer's dispute matched a strong recurring charge on this card (R7), so the recommendation "
                "moved to verify-and-warn instead of a block." + probs
            )
        return (
            "The customer's own report, already on file at the time the case opened, established "
            "non-recognition of the charge (R2), which changed the recommendation." + probs
        )
    if final_state.get("evidence_requests"):
        response = (final_state.get("simulation") or {}).get("response", "")
        return f"Simulated evidence response ({response}) changed the recommended actions." + probs
    return "The recommendation changed as additional graph evidence was incorporated." + probs


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
) -> tuple[bool, str]:
    """Returns (written, written_at) -- written_at is exposed so the trace
    writer can report it even when the write later fails the read-back
    check (still useful for diagnosing WHEN the write was attempted)."""
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
        return False, written_at

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
        ok = data.get("v_id") == graph_case_id and attrs.get("verdict") == verdict
        return ok, written_at
    except Exception:  # noqa: BLE001
        return False, written_at
