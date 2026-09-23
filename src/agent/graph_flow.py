from __future__ import annotations

import re
import time
from typing import Any

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from src.agent.llm import generate_structured, generate_with_tools
from src.agent.simulator import simulate_evidence_response
from src.agent.state import InvestigationState
from src.graph.queries import (
    FOLLOWUP_TOOL_SCHEMAS,
    card_window,
    closed_case_lookup,
    customer_cards,
    device_neighbors,
    dispatch_followup_tool,
    region_neighbors,
    ring_membership,
)
from src.graph.vector_search import retrieve_knowledge
from src.policy.engine import apply_policy
from src.policy.models import Findings
from src.tg_client import TigerGraphMCP

# Task 8.5 empirically confirmed (live, independently verified twice) that the
# dataset-wide baseline confirmed-fraud rate among ClosedCase rows is ~83.83%
# (4,665/5,565) -- analysts only open a case when there's real cause, so most
# closed cases confirm fraud REGARDLESS of whether the card is in a genuine
# coordinated ring. A 0.5 threshold is therefore nearly meaningless: it clears
# for almost any cluster with closed-case representation, including a verified
# 3,565-card supercluster (26% of all cards) sitting at 0.847 -- indistinguishable
# from baseline noise, not a real ring signal. 0.95 is chosen to sit clearly
# above that baseline, so only clusters with a materially higher confirmed-fraud
# concentration than "cases get investigated at all" trip this flag.
CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD = 0.95
# Aspirational, not applied: a minimum-sample-size floor would further guard
# against a small cluster hitting rate=1.0 off a single closed case (observed
# live during Task 8.5's review on several small clusters) -- but Card's schema
# (Task 4) and Task 8.5's cluster_fraud_rate query only persist the RATIO, not
# the underlying case count, onto each Card. Adding that would mean reopening
# Task 8.5 (already complete and reviewed) for a new schema attribute, which
# isn't warranted given the 0.5->0.95 fix already addresses the dominant,
# confirmed problem. Documented here as a known limitation, not silently
# dropped -- worth doing with more time (blog post "what we'd improve").
CLUSTER_MIN_SIZE_FOR_COORDINATED = 3  # not currently wired into any check, see note above

# Task 12 review finding (confirmed live against HHG-017/C04570-K1's own flagged
# transaction): `device_neighbors` can return a large, generic-fingerprint collision
# (299 distinct cards here -- a common Windows/Chrome/1920x1080 profile, per Task 8's
# manual checkpoint) that is noise, not a real ring signal -- the exact same failure
# mode `coordinated`'s 0.5->0.95 threshold above exists to filter out for the sibling
# `shared_region` signal, but `shared_device` had no analogous guard at all (a bare
# `bool(neighbors)`), so it counted a 299-way collision as identically strong evidence
# to a real 2-3 card ring. Mirrors Task 8.5's own `SHARES_ORIGIN` edge-building
# precedent, which already caps/excludes buckets over 20 members from contributing an
# edge at all ("cap=20", task-8.5-report.md) -- reused verbatim here rather than
# inventing a new number, since this dataset's genuine small-scale device sharing and
# its large fingerprint-collision noise are separated by orders of magnitude (a real
# ring: single digits to low tens; this dataset's known collisions: hundreds), so the
# exact cutoff between ~20 and ~300 isn't sensitive for this data.
DEVICE_NEIGHBORS_COLLISION_CAP = 20

# case_pack.csv has no `flagged_amount` column -- the README's case table only
# shows dollar amounts inside `trigger_text` prose (e.g. "$77.07"). This regex
# pulls the first dollar amount out of that prose. Confirmed against every
# trigger_text style seen in case_pack.csv (risk_score/chargeback/manual_review
# triggers), which all quote the flagged amount as "$<amount>" with exactly two
# decimal digits.
_AMOUNT_RE = re.compile(r"\$([\d,]+\.\d{2})")


def _amount_from_trigger_text(trigger_text: str) -> float:
    match = _AMOUNT_RE.search(trigger_text or "")
    if not match:
        return 0.0
    return float(match.group(1).replace(",", ""))


def _flagged_amount(row: dict[str, Any]) -> float:
    """The dollar amount of the case's flagged transaction.

    `flagged_amount` isn't a real case_pack.csv column (see module docstring
    above) -- real case-pack rows only have it inside `trigger_text` prose, so
    the normal path parses it out of there. Some callers (e.g. this task's own
    HHG-017 fixture test) pass `flagged_amount` explicitly in the row dict;
    honor that when present rather than re-deriving it, so an explicit,
    known-correct value is never silently overridden by a regex guess.
    """
    explicit = row.get("flagged_amount")
    if explicit:
        return float(explicit)
    return _amount_from_trigger_text(row.get("trigger_text", ""))


def _clip_strings(value: Any, max_text_len: int) -> Any:
    """Recursively clip long string values inside dicts/lists so a single
    verbose field (e.g. a closed case's `analyst_notes` or a knowledge doc
    excerpt) can't dominate the token budget."""
    if isinstance(value, str):
        return value if len(value) <= max_text_len else value[:max_text_len] + "...(truncated)"
    if isinstance(value, dict):
        return {k: _clip_strings(v, max_text_len) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip_strings(v, max_text_len) for v in value]
    return value


def _summarize_evidence_for_prompt(
    evidence: list[dict[str, Any]], max_items: int = 8, max_text_len: int = 300
) -> list[dict[str, Any]]:
    """Trim the deterministic evidence pack down to something that fits this
    Groq account's token budget.

    Confirmed live: passing `state['evidence']` verbatim into the assess/
    reassess prompts blew a single request to ~15,300 tokens against an 8,000
    TPM cap (`413 Request too large for model openai/gpt-oss-120b ... Limit
    8000, Requested 15327`) -- `device_neighbors` alone can carry up to 300
    card dicts (confirmed against this exact HHG-017/C04570-K1 fixture in
    `docs/manual-case-checkpoint.md`: 621 shared transactions / up to 300
    shared cards), and `retrieve_knowledge`'s hits carry full document/case
    text. Every evidence type is kept (the LLM still sees that each lookup
    ran and roughly what it found), but any list-shaped payload is capped to
    `max_items` entries with an explicit `total_count` so the model knows
    more exist rather than silently seeing a partial list as the whole
    picture, and long string fields are clipped to `max_text_len` characters.
    """
    summary: list[dict[str, Any]] = []
    for item in evidence:
        data = item["data"]
        entry: dict[str, Any] = {"type": item["type"]}
        if isinstance(data, list):
            entry["total_count"] = len(data)
            entry["sample"] = _clip_strings(data[:max_items], max_text_len)
        elif isinstance(data, dict):
            trimmed: dict[str, Any] = {}
            for key, value in data.items():
                if isinstance(value, list):
                    trimmed[f"{key}_total_count"] = len(value)
                    trimmed[key] = _clip_strings(value[:max_items], max_text_len)
                else:
                    trimmed[key] = _clip_strings(value, max_text_len)
            entry["data"] = trimmed
        else:
            entry["data"] = _clip_strings(data, max_text_len)
        summary.append(entry)
    return summary


class AssessmentOutput(BaseModel):
    pattern: str
    fraud_probability: float
    evidence_claims: list[str]
    similar_prior_case_ids: list[str] = []


async def gather_evidence_node(tg: TigerGraphMCP, state: InvestigationState) -> InvestigationState:
    row = state["case_row"]
    card_id = row["card_id"]
    evidence: list[dict[str, Any]] = []
    tool_calls = 0

    # Temporal cutoff fix (2026-09-23): every graph lookup below that can see
    # OTHER transactions (card_window, device_neighbors, region_neighbors) is
    # now bounded to `opened_at` -- the moment the bank actually opened this
    # case. Confirmed live on the real case pack: without this, card_window's
    # ±48h window and the unbounded device/region lookups could see activity
    # AFTER the case opened (up to 61 extra transactions on HHG-018), which
    # is future information no analyst had at investigation time.
    # closed_case_lookup needs no cutoff: every closed case in this dataset
    # closes before any case-pack case opens (verified against the CSVs).
    cutoff_ts = str(row["opened_at"])

    # reference_txn_id is required here, not optional -- Task 10's review found
    # that without it, card_window anchors on the card's own LATEST transaction
    # rather than the flagged one, silently excluding the exact transaction the
    # case is about whenever it isn't the card's most recent activity (confirmed
    # live: a 44-day-old flagged transaction was dropped entirely). Every
    # case-pack row's flagged_txn_id is exactly the reference this needs.
    window = await card_window(
        tg, card_id, hours=48, reference_txn_id=str(row["flagged_txn_id"]), cutoff_ts=cutoff_ts
    )
    evidence.append({"type": "card_window", "data": window})
    tool_calls += 1

    cards = await customer_cards(tg, row["customer_id"])
    evidence.append({"type": "customer_cards", "data": cards})
    tool_calls += 1

    neighbors = await device_neighbors(tg, str(row["flagged_txn_id"]), cutoff_ts=cutoff_ts)
    evidence.append({"type": "device_neighbors", "data": neighbors})
    tool_calls += 1

    closed = await closed_case_lookup(tg, card_id=card_id)
    evidence.append({"type": "closed_cases", "data": closed})
    tool_calls += 1

    ring = await ring_membership(tg, card_id)
    evidence.append({"type": "ring_membership", "data": ring})
    tool_calls += 1

    knowledge = await retrieve_knowledge(
        tg, f"fraud investigation {row.get('trigger_text', '')}", top_k=5
    )
    evidence.append({"type": "knowledge", "data": knowledge})
    tool_calls += 1

    cluster_rate = ring.get("cluster_prior_fraud_rate", 0.0) or 0.0
    coordinated = (
        cluster_rate >= CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD and bool(ring.get("ring_cluster_id"))
    )

    # Distinct-card count, not raw row count (device_neighbors' own SharedCards
    # SELECT could in principle repeat a card, though it hasn't been observed to in
    # practice) -- gated the same way `coordinated` gates shared_region, so a
    # large collision (this dataset's confirmed 299-card fingerprint-collision
    # false positive) doesn't count as identically strong evidence to a real,
    # small-scale shared device.
    distinct_neighbor_cards = len({n.get("id") for n in neighbors if n.get("id")})
    device_signal_is_meaningful = 0 < distinct_neighbor_cards <= DEVICE_NEIGHBORS_COLLISION_CAP

    return {
        **state,
        "card_id": card_id,
        "cutoff_ts": cutoff_ts,
        "evidence": evidence,
        "tool_calls": state.get("tool_calls", 0) + tool_calls,
        # shared_device/shared_region now come from the graph-algorithm cluster output
        # (Task 8.5) as well as the live neighbor check -- either signal is enough to
        # flag a shared origin, since the cluster catches multi-hop chains a single
        # device_neighbors lookup would miss. Both signals are now gated against the
        # same class of false positive (a large, generic collision that isn't a real
        # ring) -- device_signal_is_meaningful for shared_device, coordinated's own
        # cluster_prior_fraud_rate threshold for shared_region.
        "shared_device": device_signal_is_meaningful or coordinated,
        "shared_region": coordinated,
        "shared_email": False,
        "single_signal": row.get("trigger_type") == "risk_score" and not evidence[3]["data"] and not coordinated,
        "cluster_prior_fraud_rate": cluster_rate,
    }


async def agentic_followup_node(state: InvestigationState) -> InvestigationState:
    """Spec step 2a: one bounded, real function-calling round. The LLM sees a summary
    of the deterministic evidence and may request exactly one additional targeted
    query if it judges the evidence ambiguous -- this is the genuinely agentic piece
    of the flow (see plan Architecture note); everything before and after this node
    is deterministic Python."""
    row = state["case_row"]
    evidence_summary = {e["type"]: bool(e["data"]) for e in state["evidence"]}
    prompt = (
        f"Case trigger: {row.get('trigger_text', '')}\n"
        f"Evidence gathered so far (type -> has_results): {evidence_summary}\n\n"
        "Is this evidence sufficient to assess the case, or would one more targeted "
        "lookup meaningfully change your confidence? If sufficient, respond with no "
        "tool call. If not, call exactly one tool. You do not need to know the real "
        "card/region ids -- they will be filled in automatically for this case; just "
        "choose the tool and any free parameters it takes (e.g. hours)."
    )
    result = await generate_with_tools(prompt, FOLLOWUP_TOOL_SCHEMAS, max_tool_calls=1)
    if result.tool_name is None:
        return state
    return {**state, "_pending_followup": {"name": result.tool_name, "arguments": result.tool_arguments}}


def _flagged_txn_addr1(state: InvestigationState) -> str | None:
    """Billing region (`addr1`) of the case's own flagged transaction, read back
    out of `gather_evidence_node`'s `card_window` result (evidence[0]) rather than
    asked of the LLM -- see `_resolve_followup_arguments`."""
    row = state["case_row"]
    evidence = state.get("evidence") or []
    if not evidence:
        return None
    window_txns = evidence[0].get("data") or []
    flagged_txn_id = str(row.get("flagged_txn_id"))
    for txn in window_txns:
        if str(txn.get("id")) == flagged_txn_id:
            return txn.get("addr1")
    return None


def _resolve_followup_arguments(
    state: InvestigationState, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Override LLM-supplied identifier arguments with the real, deterministically-
    known values before dispatch.

    `agentic_followup_node`'s prompt deliberately withholds real card/region ids
    from the LLM (giving it the full evidence pack there risks the same token-
    budget blowup fixed in `assess_node` -- see `_summarize_evidence_for_prompt`),
    so it cannot reliably fill in identifier-shaped tool arguments itself.
    Confirmed live: it called `wider_card_window(card_id="unknown", hours=...)`,
    which TigerGraph's `run_installed_query` rejects outright ("Failed to convert
    user vertex id for parameter input_card") since "unknown" isn't a real Card
    vertex id. Every identifier field below is something this process already
    knows for certain (the case's own card_id, its flagged transaction's billing
    region) -- only genuinely free parameters the LLM is actually choosing (e.g.
    `hours` for a wider window) are left as it supplied them.
    """
    row = state["case_row"]
    card_id = state.get("card_id") or row.get("card_id")
    resolved = dict(arguments)
    if name == "wider_card_window":
        resolved["card_id"] = card_id
        resolved.setdefault("reference_txn_id", str(row.get("flagged_txn_id")))
    elif name in ("wider_region_check", "closed_case_lookup_by_region"):
        addr1 = _flagged_txn_addr1(state)
        if addr1 is not None:
            resolved["addr1"] = addr1
    return resolved


async def apply_followup_node(tg: TigerGraphMCP, state: InvestigationState) -> InvestigationState:
    pending = state.get("_pending_followup")
    if not pending:
        return state
    arguments = _resolve_followup_arguments(state, pending["name"], pending["arguments"])
    # Same cutoff as the deterministic first pass (see gather_evidence_node)
    # -- a follow-up lookup is still part of this investigation, so it must
    # not be able to see anything past the case's own opened_at either.
    cutoff_ts = state.get("cutoff_ts") or str(state["case_row"]["opened_at"])
    followup_result = await dispatch_followup_tool(tg, pending["name"], arguments, cutoff_ts=cutoff_ts)
    evidence = [*state["evidence"], {"type": f"followup:{pending['name']}", "data": followup_result}]
    return {
        **state,
        "evidence": evidence,
        "tool_calls": state.get("tool_calls", 0) + 1,
    }


async def assess_node(state: InvestigationState) -> InvestigationState:
    row = state["case_row"]
    prompt = (
        f"Case trigger: {row.get('trigger_text', '')}\n"
        f"Evidence gathered: {_summarize_evidence_for_prompt(state['evidence'])}\n\n"
        "Based on this evidence, classify the fraud pattern (one of: card_testing, "
        "card_not_present_fraud, card_not_present_new_device, out_of_region_use, "
        "account_takeover, undocumented, none), estimate fraud_probability (0-1), "
        "list evidence_claims (short strings), and similar_prior_case_ids if any closed "
        "case narratives clearly match."
    )
    result = await generate_structured(prompt, AssessmentOutput)
    return {**state, "assessment": result.model_dump()}


def stopping_check(state: InvestigationState) -> str:
    prob = state["assessment"]["fraud_probability"]
    if prob >= 0.85 or prob <= 0.15:
        return "stop"
    if state.get("evidence_requests"):
        return "stop"  # already asked once; don't loop indefinitely with a small model
    return "request_evidence"


async def evidence_request_node(state: InvestigationState) -> InvestigationState:
    row = state["case_row"]
    flagged_amount = _flagged_amount(row)
    response = simulate_evidence_response(
        "customer_validation",
        flagged_amount=flagged_amount or 100.0,
        customer_median_amount=100.0,
        is_new_device=state.get("shared_device", False),
        fraud_probability=state["assessment"]["fraud_probability"],
    )
    request = {
        "type": "customer_validation",
        "asked_after_step": 3,
        "assumed_response": response,
    }
    return {**state, "evidence_requests": [request]}


async def reassess_node(state: InvestigationState) -> InvestigationState:
    response_text = state["evidence_requests"][-1]["assumed_response"]
    prompt = (
        f"Original assessment: {state['assessment']}\n"
        f"Customer response: {response_text}\n\n"
        "Update the fraud_probability and evidence_claims given this new information. "
        "Keep the same pattern unless the response clearly changes it."
    )
    result = await generate_structured(prompt, AssessmentOutput)
    return {**state, "assessment": result.model_dump()}


def _findings_from_state(state: InvestigationState, customer_response: str | None) -> Findings:
    assessment = state["assessment"]
    row = state["case_row"]
    return Findings(
        pattern=assessment["pattern"],
        fraud_probability=assessment["fraud_probability"],
        single_signal=state.get("single_signal", False),
        shared_device=state.get("shared_device", False),
        shared_region=state.get("shared_region", False),
        shared_email=state.get("shared_email", False),
        exposure_usd=_flagged_amount(row),
        customer_response=customer_response,
        undocumented_coordinated=(
            assessment["pattern"] == "undocumented"
            and (state.get("shared_device", False) or state.get("cluster_prior_fraud_rate", 0.0) >= CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD)
        ),
    )


async def policy_node(state: InvestigationState) -> InvestigationState:
    initial_findings = _findings_from_state(state, customer_response=None)
    initial_result = apply_policy(initial_findings)

    if state.get("evidence_requests"):
        raw_response = state["evidence_requests"][-1]["assumed_response"]
        customer_response = "denies" if "did not make" in raw_response else (
            "confirmed_legitimate" if "confirms they made" in raw_response else "no_reply"
        )
        final_findings = _findings_from_state(state, customer_response=customer_response)
        final_result = apply_policy(final_findings)
    else:
        final_result = initial_result

    return {
        **state,
        "initial_policy_result": initial_result.model_dump(),
        "final_policy_result": final_result.model_dump(),
        "stop_reason": (
            "Customer response settled the verdict." if state.get("evidence_requests")
            else "Fraud probability reached a decisive threshold with sufficient evidence."
        ),
    }


def build_graph(tg: TigerGraphMCP):
    # Plain lambdas here would return an un-awaited coroutine (LangGraph detects
    # whether a node is async by inspecting the callable itself, not its return
    # value) -- confirmed live: `lambda s: gather_evidence_node(tg, s)` raised
    # `InvalidUpdateError: Expected dict, got <coroutine object ...>`. Real
    # `async def` wrapper closures fix this since LangGraph correctly detects
    # them as coroutine functions and awaits them.
    async def _gather_evidence(s: InvestigationState) -> InvestigationState:
        return await gather_evidence_node(tg, s)

    async def _apply_followup(s: InvestigationState) -> InvestigationState:
        return await apply_followup_node(tg, s)

    workflow = StateGraph(InvestigationState)
    workflow.add_node("gather_evidence", _gather_evidence)
    workflow.add_node("agentic_followup", agentic_followup_node)
    workflow.add_node("apply_followup", _apply_followup)
    workflow.add_node("assess", assess_node)
    workflow.add_node("request_evidence", evidence_request_node)
    workflow.add_node("reassess", reassess_node)
    workflow.add_node("policy", policy_node)

    workflow.set_entry_point("gather_evidence")
    workflow.add_edge("gather_evidence", "agentic_followup")
    workflow.add_edge("agentic_followup", "apply_followup")  # apply_followup_node is a no-op if no tool was requested
    workflow.add_edge("apply_followup", "assess")
    workflow.add_conditional_edges(
        "assess", stopping_check, {"stop": "policy", "request_evidence": "request_evidence"}
    )
    workflow.add_edge("request_evidence", "reassess")
    workflow.add_edge("reassess", "policy")
    workflow.add_edge("policy", END)

    return workflow.compile()
