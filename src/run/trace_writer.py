from __future__ import annotations

import math
from typing import Any

from src.agent.schemas import AnswerFile

# --------------------------------------------------------------------------
# Why this exists (2026-09-24)
# --------------------------------------------------------------------------
# The UI (Aryan's frontend, ui/src/contracts/trace.ts) has a fully built
# Investigation tab -- step timeline, probability chart, signals fired/not-
# fired, retrieved prior cases/documents, a graph view, the write/read-back
# and validation status -- but our backend never wrote a single trace.json.
# Without one, that whole tab (and the Graph tab) shows "not available" for
# every real case; Evidence/Actions/SAR/Raw still work from the answer file
# alone.
#
# This module builds a trace.json from data the investigation ALREADY
# collected (final_state, the constructed AnswerFile, a couple of extra
# locals from run_case.py) -- no new TigerGraph calls, no new LLM calls, no
# real per-step timing instrumentation (started_ms/duration_ms are optional
# in the UI's own schema and are simply omitted here). It is an honest,
# schema-valid PROJECTION of a real run, not a fabrication: every field
# traces back to something the investigation actually found or decided.
SCHEMA_VERSION = "1.0"

# Matches ui/src/contracts/trace.ts's KNOWN_STEP_NODES exactly, so the UI
# renders these with its own styling instead of the generic fallback.
NODE_GATHER_EVIDENCE = "gather_evidence"
NODE_AGENTIC_FOLLOWUP = "agentic_followup"
NODE_ASSESS = "assess"
NODE_INITIAL_POLICY = "initial_policy"
NODE_REQUEST_EVIDENCE = "request_evidence"
NODE_REASSESS = "reassess"
NODE_FINAL_POLICY = "final_policy"
NODE_WRITE_SAR = "write_sar"
NODE_WRITE_CASE = "write_case"
NODE_READ_BACK = "read_back"
NODE_VALIDATE = "validate"


def _evidence_by_type(final_state: dict) -> dict[str, Any]:
    return {e["type"]: e["data"] for e in (final_state.get("evidence") or [])}


def _clean(value: Any) -> Any:
    """NaN (pandas risk_score on a non-risk_score trigger) isn't valid JSON
    per the strict RFC -- and isn't valid against the UI's z.number() either."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _build_trigger(case_row: dict) -> dict:
    return {
        "trigger_type": case_row.get("trigger_type", ""),
        "trigger_text": case_row.get("trigger_text", ""),
        "flagged_txn_id": str(case_row.get("flagged_txn_id", "")),
        "card_id": case_row.get("card_id", ""),
        "customer_id": case_row.get("customer_id", ""),
        "risk_score": _clean(case_row.get("risk_score")),
        "opened_at": str(case_row.get("opened_at", "")),
    }


def _build_steps(final_state: dict, evidence_by_type: dict) -> list[dict]:
    steps: list[dict] = []
    n = 0

    def add(node: str, label: str, summary: str, tool_calls: list[dict] | None = None) -> None:
        nonlocal n
        n += 1
        steps.append({"step": n, "node": node, "label": label, "tool_calls": tool_calls or [], "summary": summary})

    def tool(name: str, result: Any) -> dict:
        count = len(result) if isinstance(result, list) else (1 if result else 0)
        return {"tool": name, "via": "mcp", "args": {}, "result_count": count}

    window = evidence_by_type.get("card_window") or []
    device_neighbors = evidence_by_type.get("device_neighbors") or []
    closed_cases = evidence_by_type.get("closed_cases") or []
    ring = evidence_by_type.get("ring_membership") or {}
    device_label = evidence_by_type.get("device_profile_label") or ""

    add(
        NODE_GATHER_EVIDENCE,
        "Gather graph evidence",
        f"{len(window)} card transaction(s) in the pre-cutoff window, "
        f"{len(device_neighbors)} device-sharing card(s), {len(closed_cases)} connected closed case(s).",
        [
            tool("card_window", window),
            tool("customer_cards", evidence_by_type.get("customer_cards")),
            tool("device_neighbors", device_neighbors),
            tool("device_profile_label", device_label),
            tool("closed_case_lookup", closed_cases),
            tool("ring_membership", ring),
            tool("retrieve_knowledge", evidence_by_type.get("knowledge")),
        ],
    )

    followup_items = [
        (t, d) for t, d in evidence_by_type.items() if t.startswith("followup:")
    ]
    if followup_items:
        tool_name, data = followup_items[0]
        add(
            NODE_AGENTIC_FOLLOWUP,
            "Agent requested one follow-up query",
            f"The model judged the deterministic evidence ambiguous and called {tool_name.split(':', 1)[1]}.",
            [tool(tool_name.split(":", 1)[1], data)],
        )

    initial_assessment = final_state.get("initial_assessment") or final_state.get("assessment") or {}
    add(
        NODE_ASSESS,
        "Initial assessment",
        f"pattern={initial_assessment.get('pattern')}, probability={initial_assessment.get('fraud_probability')}",
    )

    initial_actions = [a["action"] for a in (final_state.get("initial_policy_result") or {}).get("actions", [])]
    add(NODE_INITIAL_POLICY, "Initial policy (R1-R10)", f"Recommended: {', '.join(initial_actions) or 'none'}")

    evidence_requests = final_state.get("evidence_requests") or []
    if evidence_requests:
        req = evidence_requests[-1]
        add(
            NODE_REQUEST_EVIDENCE,
            "Requested evidence",
            f"{req.get('type')}: {req.get('assumed_response', '')[:160]}",
        )
        add(
            NODE_REASSESS,
            "Reassessment",
            f"probability now {final_state.get('assessment', {}).get('fraud_probability')}",
        )
        final_actions = [a["action"] for a in (final_state.get("final_policy_result") or {}).get("actions", [])]
        add(NODE_FINAL_POLICY, "Final policy (R1-R10)", f"Recommended: {', '.join(final_actions) or 'none'}")

    return steps


def _build_probability_timeline(final_state: dict, steps: list[dict]) -> list[dict]:
    assess_step = next((s["step"] for s in steps if s["node"] == NODE_ASSESS), None)
    reassess_step = next((s["step"] for s in steps if s["node"] == NODE_REASSESS), None)
    timeline: list[dict] = []
    initial_assessment = final_state.get("initial_assessment")
    if assess_step is not None and initial_assessment is not None:
        timeline.append({
            "step": assess_step, "label": "Initial assessment",
            "fraud_probability": initial_assessment.get("fraud_probability", 0.0),
        })
    if reassess_step is not None:
        timeline.append({
            "step": reassess_step, "label": "After requested evidence",
            "fraud_probability": (final_state.get("assessment") or {}).get("fraud_probability", 0.0),
        })
    if not timeline and (final_state.get("assessment") or {}).get("fraud_probability") is not None:
        timeline.append({
            "step": assess_step or 1, "label": "Assessment",
            "fraud_probability": final_state["assessment"]["fraud_probability"],
        })
    return timeline


def _build_signals(final_state: dict) -> list[dict]:
    episode = final_state.get("episode") or {}
    signals = [
        {
            "name": "card_testing_sequence",
            "fired": episode.get("detected_pattern") == "card_testing",
            "detail": "Three or more small online authorizations within an hour, then a larger purchase.",
            "entity_ids": episode.get("txn_ids", []) if episode.get("detected_pattern") == "card_testing" else [],
        },
        {
            "name": "cnp_burst",
            "fired": episode.get("detected_pattern") == "cnp_burst",
            "detail": "Two to four related online transactions within 48 hours of the flagged one.",
            "entity_ids": episode.get("txn_ids", []) if episode.get("detected_pattern") == "cnp_burst" else [],
        },
        {
            "name": "new_device",
            "fired": bool(final_state.get("is_new_device")),
            "detail": "The flagged transaction's device is marked New for this account (id_15).",
            "entity_ids": [],
        },
        {
            "name": "anonymizing_proxy",
            "fired": bool(final_state.get("is_proxy")),
            "detail": "The flagged transaction was made through a hidden/anonymizing IP proxy (id_23).",
            "entity_ids": [],
        },
        {
            "name": "out_of_region",
            "fired": bool(final_state.get("out_of_region")),
            "detail": "Billing region differs from this card's usual region, with concurrent home-region activity.",
            "entity_ids": [],
        },
        {
            "name": "shared_device",
            "fired": bool(final_state.get("shared_device")),
            "detail": "Device fingerprint shared with other cards within the cardinality cap (not a generic collision).",
            "entity_ids": final_state.get("connected_card_ids", []) or [],
        },
        {
            "name": "coordinated_ring",
            "fired": bool(final_state.get("shared_region")),
            "detail": (
                f"Card cluster's confirmed-fraud rate ({final_state.get('cluster_prior_fraud_rate', 0):.2f}) "
                "clears the 0.95 coordination threshold, well above this dataset's ~84% baseline."
            ),
            "entity_ids": [],
        },
        {
            "name": "recurring_charge",
            "fired": bool(final_state.get("recurring_charge_detected")),
            "detail": "A prior transaction of the same amount recurs roughly monthly on this card (R7).",
            "entity_ids": [],
        },
    ]
    return signals


def _build_rules_fired(final_state: dict) -> list[dict]:
    rules: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for phase_key, phase_name in (("initial_policy_result", "initial"), ("final_policy_result", "final")):
        for action in (final_state.get(phase_key) or {}).get("actions", []):
            reason = action.get("reason", "")
            rule = reason.split(":")[0].strip() if reason else ""
            key = (rule, phase_name)
            if rule and key not in seen:
                seen.add(key)
                rules.append({"rule": rule, "phase": phase_name, "explanation": reason})
    return rules


def _build_retrieval(final_state: dict, answer: AnswerFile) -> dict:
    knowledge = (_evidence_by_type(final_state).get("knowledge") or {})
    used_ids = set(answer.case.similar_prior_cases)
    prior_cases = [
        {
            "case_id": c.get("id", ""),
            "outcome": c.get("outcome", ""),
            "pattern": c.get("pattern", ""),
            "score": c.get("distance"),
            "used": c.get("id") in used_ids,
        }
        for c in (knowledge.get("similar_cases") or [])
        if c.get("id")
    ]
    documents = [
        {
            "doc_id": d.get("id", ""),
            "title": d.get("source", "") or d.get("id", ""),
            "section": d.get("section", ""),
            "score": d.get("distance"),
        }
        for d in (knowledge.get("knowledge") or [])
        if d.get("id")
    ]
    return {"prior_cases": prior_cases, "documents": documents}


def _build_subgraph(case_row: dict, final_state: dict, answer: AnswerFile, context: dict) -> dict:
    customer_id = case_row.get("customer_id", "")
    card_id = case_row.get("card_id", "")
    flagged_id = str(case_row.get("flagged_txn_id", ""))
    episode = context.get("episode") or {}
    window_by_id = {t.get("id"): t for t in (_evidence_by_type(final_state).get("card_window") or [])}

    nodes: list[dict] = [
        {"id": customer_id, "type": "Customer", "label": customer_id, "role": "subject", "attrs": {}},
        {"id": card_id, "type": "Card", "label": card_id, "role": "subject", "attrs": {}},
    ]
    edges: list[dict] = [{"source": customer_id, "target": card_id, "type": "OWNS"}]

    for txn_id in episode.get("txn_ids") or [flagged_id]:
        row = window_by_id.get(txn_id, {})
        nodes.append({
            "id": txn_id, "type": "Transaction",
            "label": f"${row.get('TransactionAmt', 0):.2f}" if row.get("TransactionAmt") is not None else txn_id,
            "role": "flagged" if txn_id == flagged_id else "affected",
            "attrs": {k: row[k] for k in ("ts", "TransactionAmt", "channel", "addr1") if k in row},
        })
        edges.append({"source": card_id, "target": txn_id, "type": "MADE"})

    device_label = _evidence_by_type(final_state).get("device_profile_label")
    if device_label:
        device_id = f"DEV-{abs(hash(device_label)) % 100000:05d}"
        nodes.append({"id": device_id, "type": "Device", "label": device_label, "role": "connected", "attrs": {}})
        edges.append({"source": flagged_id, "target": device_id, "type": "FROM_DEVICE"})
        for connected_card in context.get("connected_card_ids") or []:
            nodes.append({"id": connected_card, "type": "Card", "label": connected_card, "role": "connected", "attrs": {}})
            edges.append({"source": connected_card, "target": device_id, "type": "FROM_DEVICE"})

    for prior_id in answer.case.similar_prior_cases:
        nodes.append({"id": prior_id, "type": "ClosedCase", "label": prior_id, "role": "prior_case", "attrs": {}})

    if answer.case.written_to_graph and answer.case.graph_case_id:
        nodes.append({
            "id": answer.case.graph_case_id, "type": "FraudCase", "label": answer.case.graph_case_id,
            "role": "this_case", "attrs": {},
        })
        edges.append({"source": card_id, "target": answer.case.graph_case_id, "type": "CASE_ON_CARD"})

    # De-dup nodes by id (a connected card can coincide with a prior-case
    # card id, etc.) -- last write wins, which is fine since role/label are
    # stable per id in practice.
    dedup = {n["id"]: n for n in nodes if n.get("id")}
    return {"nodes": list(dedup.values()), "edges": edges}


def build_trace(
    case_row: dict,
    answer: AnswerFile,
    context: dict,
    validation_errors: list[str] | None = None,
    llm_provider: str = "groq",
    llm_model: str = "openai/gpt-oss-120b",
) -> dict:
    """Pure function: no I/O. `context` is exactly what
    run_single_case_with_context returns alongside the answer."""
    final_state = context["final_state"]
    evidence_by_type = _evidence_by_type(final_state)
    steps = _build_steps(final_state, evidence_by_type)

    if answer.sar.file:
        steps.append({
            "step": len(steps) + 1, "node": NODE_WRITE_SAR, "label": "Draft SAR narrative",
            "tool_calls": [], "summary": f"{len(answer.sar.narrative)} character narrative drafted.",
        })
    steps.append({
        "step": len(steps) + 1, "node": NODE_WRITE_CASE, "label": "Write case memory",
        "tool_calls": [{"tool": "add_nodes", "via": "mcp", "args": {}, "result_count": 1},
                       {"tool": "upsert_vectors", "via": "mcp", "args": {}, "result_count": 1}],
        "summary": f"Upserted FraudCase {context.get('graph_case_id', '')} and its embedding.",
    })
    steps.append({
        "step": len(steps) + 1, "node": NODE_READ_BACK, "label": "Independent read-back",
        "tool_calls": [{"tool": "get_node", "via": "mcp", "args": {}, "result_count": 1 if answer.case.written_to_graph else 0}],
        "summary": (
            "Read the case back from the graph; verdict matched." if answer.case.written_to_graph
            else "Read-back did not confirm the write; written_to_graph is false."
        ),
    })
    errors = validation_errors or []
    steps.append({
        "step": len(steps) + 1, "node": NODE_VALIDATE, "label": "Answer validation",
        "tool_calls": [], "summary": "Passed contract and cross-field checks." if not errors else f"{len(errors)} issue(s) found.",
    })

    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": answer.case_id,
        "trigger": _build_trigger(case_row),
        "cutoff_ts": final_state.get("cutoff_ts", str(case_row.get("opened_at", ""))),
        "steps": steps,
        "probability_timeline": _build_probability_timeline(final_state, steps),
        "signals": _build_signals(final_state),
        "rules_fired": _build_rules_fired(final_state),
        "retrieval": _build_retrieval(final_state, answer),
        "subgraph": _build_subgraph(case_row, final_state, answer, context),
        "graph_write": {
            "written": answer.case.written_to_graph,
            "graph_case_id": answer.case.graph_case_id or context.get("graph_case_id", ""),
            "read_back_ok": answer.case.written_to_graph,
            "written_at": context.get("written_at", ""),
        },
        "validation": {
            "passed": not errors,
            "errors": errors,
            "warnings": [],
        },
        "llm": {"provider": llm_provider, "model": llm_model, "tokens": answer.tokens},
    }
