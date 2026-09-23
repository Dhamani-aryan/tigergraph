## Task 12: LangGraph investigation flow + case write-back

**Files:**
- Create: `src/agent/state.py`
- Create: `src/agent/graph_flow.py`
- Create: `src/run/__init__.py` (empty)
- Create: `src/run/run_case.py`
- Test: `tests/test_run_case_hhg017.py` (live end-to-end test against the manual checkpoint case)

**Interfaces:**
- Consumes: everything from Tasks 3, 5, 6, 8.5, 9, 10, 11.
- Produces: `async run_single_case(tg: TigerGraphMCP, case_row: dict) -> AnswerFile`. Task 13 calls this per case-pack row.
- Flow is now: `gather_evidence` (deterministic, includes the Task 8.5 ring lookup) →
  `agentic_followup` (bounded real function-calling, spec step 2a) → `apply_followup`
  (executes at most one tool call if one was requested) → `assess` → stopping check →
  optionally `request_evidence`/`reassess` → `policy` → (this task's write-back, below).

- [ ] **Step 1: Write `src/agent/state.py`**

```python
from __future__ import annotations

from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    case_row: dict[str, Any]
    card_id: str
    evidence: list[dict[str, Any]]
    assessment: dict[str, Any]  # LLM output: pattern, probability, claims, similar_cases
    single_signal: bool
    shared_device: bool
    shared_region: bool
    shared_email: bool
    cluster_prior_fraud_rate: float  # from Task 8.5's connected-components pass
    _pending_followup: dict[str, Any]  # set by agentic_followup_node, consumed by apply_followup_node
    evidence_requests: list[dict[str, Any]]
    initial_policy_result: dict[str, Any]
    final_policy_result: dict[str, Any]
    tool_calls: int
    stop_reason: str
```

- [ ] **Step 2: Write `src/agent/graph_flow.py`** — implements the README's 8-step flow as plain async functions composed by LangGraph. Given the deterministic-evidence-gathering design (see plan Architecture), this uses `langgraph.graph.StateGraph` for the control flow (conditional loop-back for the evidence-request round) but each node is a straightforward async function, not an LLM-driven ReAct loop.

```python
from __future__ import annotations

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

    # reference_txn_id is required here, not optional -- Task 10's review found
    # that without it, card_window anchors on the card's own LATEST transaction
    # rather than the flagged one, silently excluding the exact transaction the
    # case is about whenever it isn't the card's most recent activity (confirmed
    # live: a 44-day-old flagged transaction was dropped entirely). Every
    # case-pack row's flagged_txn_id is exactly the reference this needs.
    window = await card_window(tg, card_id, hours=48, reference_txn_id=str(row["flagged_txn_id"]))
    evidence.append({"type": "card_window", "data": window})
    tool_calls += 1

    cards = await customer_cards(tg, row["customer_id"])
    evidence.append({"type": "customer_cards", "data": cards})
    tool_calls += 1

    neighbors = await device_neighbors(tg, str(row["flagged_txn_id"]))
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

    cluster_size = ring.get("cluster_prior_fraud_rate") is not None  # presence implies clustered
    cluster_rate = ring.get("cluster_prior_fraud_rate", 0.0) or 0.0
    coordinated = (
        cluster_rate >= CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD and bool(ring.get("ring_cluster_id"))
    )

    return {
        **state,
        "card_id": card_id,
        "evidence": evidence,
        "tool_calls": state.get("tool_calls", 0) + tool_calls,
        # shared_device/shared_region now come from the graph-algorithm cluster output
        # (Task 8.5) as well as the live neighbor check -- either signal is enough to
        # flag a shared origin, since the cluster catches multi-hop chains a single
        # device_neighbors lookup would miss.
        "shared_device": bool(neighbors) or coordinated,
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
        "tool call. If not, call exactly one tool."
    )
    result = await generate_with_tools(prompt, FOLLOWUP_TOOL_SCHEMAS, max_tool_calls=1)
    if result.tool_name is None:
        return state
    return {**state, "_pending_followup": {"name": result.tool_name, "arguments": result.tool_arguments}}


async def apply_followup_node(tg: TigerGraphMCP, state: InvestigationState) -> InvestigationState:
    pending = state.get("_pending_followup")
    if not pending:
        return state
    followup_result = await dispatch_followup_tool(tg, pending["name"], pending["arguments"])
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
        f"Evidence gathered: {state['evidence']}\n\n"
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
    window_txns = state["evidence"][0]["data"]
    flagged_amount = float(row.get("risk_score") and row.get("flagged_amount", 0) or 0)
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
    row = state["case_row"]
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
        exposure_usd=row.get("flagged_amount", 0.0) or 0.0,
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
    workflow = StateGraph(InvestigationState)
    workflow.add_node("gather_evidence", lambda s: gather_evidence_node(tg, s))
    workflow.add_node("agentic_followup", agentic_followup_node)
    workflow.add_node("apply_followup", lambda s: apply_followup_node(tg, s))
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
```

**Note:** `flagged_amount` isn't a column in `case_pack.csv` directly (the README's case table shows dollar amounts only inside `trigger_text` prose, e.g. `"$77.07"`). Before this task is "done", add a small `_parse_flagged_amount(trigger_text: str) -> float` helper (regex `\$([\d,]+\.\d{2})`) in `src/agent/graph_flow.py` and use it wherever `row.get('flagged_amount', ...)` appears above — replace those placeholder lookups with real parsing before running Step 5's test.

- [ ] **Step 3: Write `src/run/run_case.py`** — runs the graph, converts state into an `AnswerFile`, writes the case back to TigerGraph.

```python
from __future__ import annotations

import time

from src.agent.graph_flow import build_graph
from src.agent.llm import token_tracker
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
        exposure_usd=case_row.get("flagged_amount", 0.0) or 0.0 if verdict != "legitimate" else 0.0,
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

    sar = SAR(
        file=sar_info["sar_file"],
        reason=sar_info["sar_reason"],
        narrative="",  # filled by a follow-up LLM call if sar_info["sar_file"] is True
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
                        "exposure_usd": 0.0,
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
```

**Note:** this task's Python has several rough joins between the LangGraph state and the answer schema (verdict thresholds, SAR narrative left blank, `pattern_description` always empty) that are simplified first drafts, not final judged-quality output. Task 13's end-to-end test against the `HHG-017` manual checkpoint is where these get tightened — expect to revisit `run_case.py` after seeing real output next to the by-hand answer.

- [ ] **Step 4: Write `tests/test_run_case_hhg017.py`**

```python
import pytest

from src.run.run_case import run_single_case
from src.tg_client import TigerGraphMCP


@pytest.mark.asyncio
async def test_hhg017_end_to_end_produces_valid_answer():
    case_row = {
        "case_id": "HHG-017",
        "opened_at": "2016-11-12 00:46:24",
        "trigger_type": "risk_score",
        "trigger_text": "Real-time model scored transaction 3450629 ($100.09, online) at 0.57. Review and decide.",
        "flagged_txn_id": 3450629,
        "card_id": "C04570-K1",
        "customer_id": "C04570",
        "risk_score": 0.57,
        "flagged_amount": 100.09,
    }
    async with TigerGraphMCP() as tg:
        answer = await run_single_case(tg, case_row)

    assert answer.case_id == "HHG-017"
    assert answer.case.verdict in ("fraud", "legitimate", "uncertain")
    assert answer.next_best_actions.initial
    assert answer.tool_calls > 0
```

- [ ] **Step 5: Run it**

```bash
.venv\Scripts\pytest tests/test_run_case_hhg017.py -v -s
```

Expected: this is the real proof-of-life for the whole pipeline. It will likely fail on the first several attempts — GSQL query syntax errors from Task 10, LLM output that doesn't match `AssessmentOutput`, or a `KeyError` in `run_case.py`'s field-mapping. Iterate here rather than moving to Task 13. Compare the produced JSON (`print(answer.model_dump_json(indent=2))`) against `docs/manual-case-checkpoint.md`'s by-hand answer for HHG-017 — they don't need to match exactly, but the pattern/verdict/actions should be in the same neighborhood; if wildly different, the bug is more likely in evidence gathering or prompt wording than in the schema.

- [ ] **Step 6: Commit**

```bash
git add src/agent/state.py src/agent/graph_flow.py src/run/__init__.py src/run/run_case.py tests/test_run_case_hhg017.py
git commit -m "feat: LangGraph investigation flow and single-case runner"
```

---

