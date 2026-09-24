from __future__ import annotations

import json
import re
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from src.agent.decision import is_decisive, resolve_decision
from src.agent.episode import build_episode
from src.agent.features import (
    CLOSE_PRIOR_CASE_MAX_DISTANCE,
    BehaviorProfile,
    CardTestingResult,
    CnpBurstResult,
    DeviceNetworkResult,
    EvidenceFamilies,
    RecurrenceResult,
    RegionSignal,
    compute_behavior_profile,
    compute_evidence_families,
    describe_findings,
    detect_card_testing,
    detect_cnp_burst,
    detect_out_of_region,
    detect_recurring_charge,
    evaluate_device_network,
)
from src.agent.llm import generate_structured, generate_with_tools
from src.agent.simulator import simulate_customer_validation
from src.agent.state import InvestigationState
from src.graph.queries import (
    FOLLOWUP_TOOL_SCHEMAS,
    card_window,
    closed_case_lookup,
    customer_cards,
    device_network,
    device_profile_label,
    dispatch_followup_tool,
    ring_context,
    ring_membership,
)
from src.graph.vector_search import build_similarity_query, retrieve_knowledge
from src.policy.engine import apply_policy
from src.policy.models import Findings
from src.tg_client import TigerGraphMCP

# Task 8.5 empirically confirmed that the dataset-wide confirmed-fraud rate
# among ClosedCase rows is ~83.83% (4,665/5,565) and that one ~3,565-card
# transitive supercluster sits at 0.847. Task 14 fix: cluster statistics are
# now CONTEXT ONLY -- they never set shared_region/shared_device, never fire
# R6, and are shown to the model together with the cluster size and the
# closed-case sample size behind the rate (see ring_context), because a
# 1-case cluster at 100% and a 3,565-card component formed through old,
# shared region/email collisions are not current fraud rings.
CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD = 0.95

# Every graph lookup is bounded by cutoff_ts (the case's opened_at), so a long
# backward lookback cannot leak the future; it gives the feature layer the
# card's full prior history.
CARD_WINDOW_LOOKBACK_HOURS = 24 * 400

# case_pack.csv has no `flagged_amount` column -- the README's case table only
# shows dollar amounts inside `trigger_text` prose (e.g. "$77.07").
_AMOUNT_RE = re.compile(r"\$([\d,]+\.\d{2})")

PatternName = Literal[
    "card_testing", "card_not_present_fraud", "card_not_present_new_device",
    "out_of_region_use", "account_takeover", "undocumented", "none",
]
FraudPatternName = Literal[
    "card_testing", "card_not_present_fraud", "card_not_present_new_device",
    "out_of_region_use", "account_takeover", "undocumented",
]


def _amount_from_trigger_text(trigger_text: str) -> float:
    match = _AMOUNT_RE.search(trigger_text or "")
    if not match:
        return 0.0
    return float(match.group(1).replace(",", ""))


def _flagged_amount(row: dict[str, Any]) -> float:
    """The dollar amount of the case's flagged transaction, from an explicit
    `flagged_amount` when a caller passes one, else parsed from trigger_text."""
    explicit = row.get("flagged_amount")
    if explicit:
        return float(explicit)
    return _amount_from_trigger_text(row.get("trigger_text", ""))


def _redact_baseline_cluster_rate(data: Any) -> Any:
    """Critical answer-quality fix (2026-09-24), found live by hand-checking
    the finished batch: EVERY evidence type that carries a Card's
    `cluster_prior_fraud_rate` (ring_membership, customer_cards,
    device_neighbors' shared_cards) was passing the RAW rate straight
    through into both the LLM prompt and the answer file's evidence list --
    completely bypassing `CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD` (0.95),
    the exact guard this module's own top-of-file comment documents as
    existing BECAUSE the dataset-wide baseline confirmed-fraud rate among
    closed cases is ~83.83%, and a single ~3,565-card supercluster sits at
    0.847 -- indistinguishable from that baseline, not a real ring.

    Confirmed live: 18 of the 20 batch cases carried a "cluster ... prior
    confirmed-fraud rate of 0.85" claim (17 of them citing that exact same
    supercluster), presented to the LLM with no indication it's noise --
    which is very plausibly why every single case in that run scored
    fraud_probability >= 0.55 and none came back "legitimate", despite the
    README's own expectation that about half the case pack should. A
    number below the coordinated threshold is not weak evidence of a ring;
    it is evidence AGAINST one (this card looks like every other card), so
    it's redacted here rather than shown with a caveat a small model might
    still latch onto.

    Applied to every evidence payload that can carry this field, recursively
    (device_neighbors nests it inside a list of per-card dicts), replacing
    a sub-threshold rate with `None` -- never deleting the key outright, so
    the LLM and run_case.py's evidence builder can still see that the field
    was checked and found to be baseline, not simply absent."""
    if isinstance(data, dict):
        out = dict(data)
        rate = out.get("cluster_prior_fraud_rate")
        if isinstance(rate, (int, float)) and rate < CLUSTER_FRAUD_RATE_COORDINATED_THRESHOLD:
            out["cluster_prior_fraud_rate"] = None
            out["ring_cluster_id"] = None
        return {k: _redact_baseline_cluster_rate(v) for k, v in out.items()}
    if isinstance(data, list):
        return [_redact_baseline_cluster_rate(v) for v in data]
    return data


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
    evidence: list[dict[str, Any]], max_items: int = 3, max_text_len: int = 150
) -> list[dict[str, Any]]:
    """Trim the deterministic evidence pack down to something that fits this
    Groq account's token budget.

    Confirmed live (original fix): passing `state['evidence']` verbatim into
    the assess/reassess prompts blew a single request to ~15,300 tokens
    against an 8,000 TPM cap (`413 Request too large for model openai/gpt-
    oss-120b ... Limit 8000, Requested 15327`) -- `device_neighbors` alone
    can carry up to 300 card dicts, and `retrieve_knowledge`'s hits carry
    full document/case text.

    Tightened again (2026-09-24), confirmed live during Task 14's batch
    run: the 8,000 limit is per-MINUTE, not per-call, and this pipeline
    makes several large calls per case (assess, sometimes reassess,
    sometimes the SAR narrative) -- the original max_items=8/max_text_len=
    300 kept any ONE call under budget but not several stacked inside the
    same 60s window, so cases were exhausting Groq's retry budget even with
    patient backoff (see llm.py). Every evidence type is still kept (the
    LLM still sees that each lookup ran and roughly what it found), but
    list-shaped payloads are capped tighter and string fields clipped
    shorter, since `total_count` already tells the model more exist without
    needing the full sample.
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
    """Internal assessment contract (the answer-file schema is unchanged)."""

    pattern: PatternName = Field(description="Best-fitting pattern for the observed activity; 'none' if nothing fits.")
    pattern_if_fraud: FraudPatternName = Field(
        description="The pattern this would be IF the verdict resolves to fraud. Pattern and verdict are separate decisions."
    )
    recommended_verdict: Literal["fraud", "legitimate", "uncertain"]
    fraud_probability: float = Field(ge=0.0, le=1.0)
    probability_rationale: str
    supporting_evidence_families: list[str] = Field(
        description="Only families that actually FIRED in the structured observations; never list a false signal."
    )
    contradicting_facts: list[str] = []
    benign_facts: list[str] = []
    independent_evidence_count: int = Field(ge=0)
    evidence_claims: list[str] = Field(description="Observed facts only, each traceable to a structured observation.")
    similar_prior_case_ids: list[str] = []
    pattern_description: str = ""


ASSESSMENT_RULES = """Rules for this assessment:
- risk_score is the alert trigger, not a verdict. In this benchmark, scores above 0.7 are often legitimate and some fraud scores low.
- A device marked New alone is not fraud: people buy new phones.
- A transaction that is merely nearby in time is not part of the same episode. Only the listed episode rows are the episode.
- Historical fraud on this card does not prove this transaction is fraud; historical cleared cases do not prove it is legitimate.
- Normal amount, established product and established region history are positive benign evidence.
- Missing or unavailable evidence increases uncertainty, not suspicion.
- Weigh cleared prior cases alongside confirmed-fraud prior cases. Do not infer this case's outcome from the closed-case base rate.
- Cluster (connected-component) statistics are contextual graph information, never proof of a ring.
- Do not target any particular verdict distribution across cases.
- Pattern and verdict are separate decisions: give the best-fitting pattern and, separately, pattern_if_fraud.
- Evidence claims and supporting_evidence_families must describe fired/observed facts. Never list a signal that did not fire as supporting evidence.
- Calibrate fraud_probability to the evidence. The bank decides fraud only at >= 0.85 with two independent evidence families, and legitimate only at <= 0.15 with two independent benign families; anything else is uncertain and goes to verification."""


def _dump(model: BaseModel | None, exclude: set[str] | None = None) -> dict[str, Any]:
    if model is None:
        return {}
    return model.model_dump(exclude=exclude or set())


def _profile(state: InvestigationState) -> BehaviorProfile:
    return BehaviorProfile(**state["behavior_profile"])


def _signals(state: InvestigationState) -> tuple[CardTestingResult, CnpBurstResult, RegionSignal, RecurrenceResult, DeviceNetworkResult]:
    s = state["signals"]
    return (
        CardTestingResult(**s["card_testing"]), CnpBurstResult(**s["cnp_burst"]),
        RegionSignal(**s["out_of_region"]), RecurrenceResult(**s["recurrence"]),
        DeviceNetworkResult(**s["device_network"]),
    )


def _customer_statement(row: dict[str, Any], recurrence: RecurrenceResult) -> str | None:
    """A customer_report trigger IS the customer's statement, already on
    file: a denial, unless the disputed charge matches a STRONG recurrence
    (R7). A candidate recurrence never changes it."""
    if row.get("trigger_type") != "customer_report":
        return None
    return "disputes_recurring" if recurrence.tier == "strong" else "denies"


def _families(
    state: InvestigationState, *, customer_statement: str | None, matched_prior_case: dict | None = None,
) -> EvidenceFamilies:
    profile = _profile(state)
    card_testing, cnp, region, _recurrence, network = _signals(state)
    statement = {"confirmed_legitimate": "confirms"}.get(customer_statement or "", customer_statement)
    if statement not in ("denies", "confirms", "disputes_recurring"):
        statement = None
    return compute_evidence_families(
        profile, card_testing, cnp, region, network,
        customer_statement=statement, matched_prior_case=matched_prior_case,
    )


async def gather_evidence_node(tg: TigerGraphMCP, state: InvestigationState) -> InvestigationState:
    row = state["case_row"]
    card_id = row["card_id"]
    evidence: list[dict[str, Any]] = []
    tool_calls = 0
    cutoff_ts = str(row["opened_at"])
    flagged_txn_id = str(row["flagged_txn_id"])

    window = await card_window(
        tg, card_id, hours=CARD_WINDOW_LOOKBACK_HOURS,
        reference_txn_id=flagged_txn_id, cutoff_ts=cutoff_ts,
    )
    evidence.append({"type": "card_window", "data": window})
    tool_calls += 1

    # Deterministic features, before any LLM call.
    profile = compute_behavior_profile(window, flagged_txn_id, cutoff_ts)
    card_testing = detect_card_testing(window, flagged_txn_id, cutoff_ts)
    cnp = detect_cnp_burst(window, flagged_txn_id, cutoff_ts, profile.baseline_online_per_48h)
    region = detect_out_of_region(window, flagged_txn_id, cutoff_ts, profile)
    recurrence = detect_recurring_charge(window, flagged_txn_id, cutoff_ts)
    episode = build_episode(window, flagged_txn_id, cutoff_ts, card_testing=card_testing, cnp=cnp, region=region)

    cards = await customer_cards(tg, row["customer_id"])
    evidence.append({"type": "customer_cards", "data": _redact_baseline_cluster_rate(cards)})
    tool_calls += 1

    device_label = await device_profile_label(tg, flagged_txn_id)
    evidence.append({"type": "device_profile_label", "data": device_label})
    tool_calls += 1

    device_raw: dict[str, Any] = {}
    if profile.flagged_channel == "online" and profile.flagged_found:
        device_raw = await device_network(tg, flagged_txn_id, profile.flagged_ts, cutoff_ts)
        tool_calls += 1
        if device_raw:
            device_raw["flagged_amount"] = profile.flagged_amount
    network = evaluate_device_network(device_raw, card_id, profile.flagged_ts, cutoff_ts)
    evidence.append({"type": "device_network", "data": device_raw})

    closed = await closed_case_lookup(tg, card_id=card_id)
    evidence.append({"type": "closed_cases", "data": closed})
    tool_calls += 1

    ring = await ring_membership(tg, card_id)
    evidence.append({"type": "ring_membership", "data": ring})
    tool_calls += 1
    ring_ctx: dict[str, Any] = {}
    if ring.get("ring_cluster_id"):
        ring_ctx = await ring_context(tg, ring["ring_cluster_id"])
        tool_calls += 1
    ring_ctx = {**ring_ctx, "cluster_prior_fraud_rate": ring.get("cluster_prior_fraud_rate")}
    evidence.append({"type": "ring_context", "data": ring_ctx})

    query_text = build_similarity_query(
        str(row.get("trigger_type", "")), profile, card_testing, cnp, region, network, recurrence,
    )
    knowledge = await retrieve_knowledge(tg, query_text, top_k=5)
    knowledge["query_text"] = query_text
    evidence.append({"type": "knowledge", "data": knowledge})
    tool_calls += 1

    statement = _customer_statement(row, recurrence)
    matched = _closest_prior_case(knowledge)
    signals = {
        "card_testing": _dump(card_testing),
        "cnp_burst": _dump(cnp),
        "out_of_region": _dump(region),
        "recurrence": _dump(recurrence),
        "device_network": _dump(network),
    }
    partial: InvestigationState = {**state, "behavior_profile": _dump(profile), "signals": signals}
    families_initial = _families(partial, customer_statement=None, matched_prior_case=matched)
    families = _families(partial, customer_statement=statement, matched_prior_case=matched)
    profile = describe_findings(profile, card_testing, cnp, region, recurrence, network, families)

    return {
        **state,
        "card_id": card_id,
        "cutoff_ts": cutoff_ts,
        "evidence": evidence,
        "tool_calls": state.get("tool_calls", 0) + tool_calls,
        "behavior_profile": _dump(profile),
        "signals": signals,
        "families_initial": _dump(families_initial),
        "families": _dump(families),
        "customer_statement": statement,
        "matched_prior_case": matched,
        "single_signal": families.single_signal,
        "independent_evidence_count": families.independent_evidence_count,
        # Direct corroborated device evidence only; cluster rates never set these.
        "shared_device": network.corroborated,
        "shared_region": False,
        "shared_email": False,
        "cluster_prior_fraud_rate": ring.get("cluster_prior_fraud_rate") or 0.0,
        "episode": episode.as_dict(),
        "is_new_device": bool(profile.is_new_device),
        "is_proxy": bool(profile.is_proxy),
        "out_of_region": region.fired,
        "recurring_charge_detected": recurrence.tier == "strong",
        "recurrence_tier": recurrence.tier,
        "device_profile_label": device_label,
        # Only directly corroborated cards -- never a generic profile collision
        # or a whole connected component.
        "connected_card_ids": [c for c in network.corroborated_card_ids if c != card_id] if network.corroborated else [],
        "connected_device_profiles": (
            [network.device_profile_label or device_label] if network.corroborated and (network.device_profile_label or device_label) else []
        ),
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


def _deterministic_pattern_override(state: InvestigationState) -> str | None:
    """Only two signals may pick the pattern NAME deterministically: an exact
    card-testing sequence containing the flagged transaction, and the strict
    out-of-region signal. CNP bursts, new devices, proxies, amount anomalies
    and shared origin are candidate observations for the assessment; they do
    not overwrite the model's pattern. Neither override forces a verdict."""
    signals = state.get("signals") or {}
    if (signals.get("card_testing") or {}).get("fired"):
        return "card_testing"
    if (signals.get("out_of_region") or {}).get("fired"):
        return "out_of_region_use"
    return None


def _observations(state: InvestigationState) -> dict[str, Any]:
    """Structured observations with provenance for the prompt (all
    deterministic; nothing here came from an LLM)."""
    profile = state.get("behavior_profile") or {}
    signals = state.get("signals") or {}
    families = state.get("families") or {}
    ev = {e["type"]: e["data"] for e in state.get("evidence") or []}
    knowledge = ev.get("knowledge") or {}
    network = signals.get("device_network") or {}
    closed = ev.get("closed_cases") or []
    ring_ctx = ev.get("ring_context") or {}
    followups = {t: d for t, d in ev.items() if t.startswith("followup:")}
    return {
        "behavior_profile (source: card_window, strictly-prior baseline)": {
            k: profile.get(k) for k in (
                "flagged_amount", "flagged_channel", "flagged_product", "flagged_region", "history_count",
                "history_span_days", "stable_history", "amount_median", "amount_ratio", "amount_percentile",
                "amount_class", "product_prior_count", "product_prior_share", "product_class",
                "flagged_region_prior_count", "flagged_region_prior_in_person_count", "region_class",
                "home_region", "home_region_count", "home_region_share", "online_txn_ids_48h",
                "card_present_txn_ids_48h", "device_status", "is_new_device", "proxy_type", "is_proxy",
                "baseline_online_per_48h", "suspicious_findings", "benign_findings", "unavailable_findings",
            )
        },
        "alert_context_only": {"risk_score": profile.get("risk_score_context_only")},
        "card_testing (source: card_window; exact R5 sequence)": {
            k: (signals.get("card_testing") or {}).get(k) for k in ("fired", "reason", "txn_ids", "amounts", "timestamps")
        },
        "cnp_burst (source: card_window; online rows only)": {
            k: (signals.get("cnp_burst") or {}).get(k) for k in (
                "flagged_online", "online_count", "online_txn_ids", "documented_burst", "high_volume_online",
                "baseline_online_per_48h", "exceeds_baseline", "reason",
            )
        },
        "out_of_region (source: card_window; strict card-present rule)": {
            k: (signals.get("out_of_region") or {}).get(k) for k in (
                "fired", "reason", "flagged_region", "home_region", "flagged_region_prior_count", "trip_candidate",
            )
        },
        "recurrence (source: card_window; merchant is proxied by channel+ProductCD+amount)": {
            k: (signals.get("recurrence") or {}).get(k) for k in (
                "tier", "reason", "monthly_match_ids", "band_share", "collision_rate", "proxy_note",
            )
        },
        "device_network (source: device_network query, 48h window, cutoff-bounded)": {
            k: network.get(k) for k in (
                "available", "total_distinct_cards", "other_card_count", "generic_profile", "other_cards_48h",
                "meaningful_match", "corroborated", "corroboration_basis", "corroborated_card_ids", "reason",
            )
        },
        "evidence_families (deterministic; risk score never counts)": {
            "suspicious": families.get("suspicious"), "benign": families.get("benign"),
            "strong_suspicious": families.get("strong_suspicious"), "single_signal": families.get("single_signal"),
            "detail": families.get("detail"),
        },
        "episode (rule-based)": {k: (state.get("episode") or {}).get(k) for k in ("txn_ids", "exposure_usd", "basis")},
        "graph_context_only (connected components; not evidence)": {
            "ring_cluster_id": ring_ctx.get("ring_cluster_id"), "cluster_size_cards": ring_ctx.get("n_cards"),
            "closed_case_sample_size": ring_ctx.get("n_closed_cases"),
            "cluster_prior_fraud_rate": ring_ctx.get("cluster_prior_fraud_rate"),
        },
        "same_card_closed_cases (history on this card; does not prove this transaction)": [
            {"id": c.get("id"), "outcome": c.get("outcome"), "pattern": c.get("pattern"),
             "notes": _clip_strings(c.get("analyst_notes"), 160)}
            for c in closed[:5]
        ],
        "similar_closed_cases (balanced: up to 3 confirmed_fraud + 3 cleared)": [
            {"id": c.get("id"), "outcome": c.get("outcome"), "pattern": c.get("pattern"),
             "distance": c.get("distance"), "notes": _clip_strings(c.get("analyst_notes"), 160)}
            for c in knowledge.get("similar_cases") or []
        ],
        "policy_and_pattern_documents": [
            {"id": d.get("id"), "section": d.get("section"), "text": _clip_strings(d.get("text"), 220)}
            for d in (knowledge.get("knowledge") or [])[:5]
        ],
        "followup_lookups": _summarize_evidence_for_prompt(
            [{"type": t, "data": d} for t, d in followups.items()]
        ) if followups else [],
    }


def _assessment_prompt(state: InvestigationState) -> str:
    row = state["case_row"]
    trigger = row.get("trigger_type", "")
    statement = ""
    if trigger == "customer_report":
        statement = (
            "\nThe trigger text is the customer's own statement, already on file (a real statement, not simulated). "
            f"Deterministic recurrence tier for the disputed charge: {state.get('recurrence_tier')}."
        )
    elif trigger == "analyst_request":
        statement = "\nThe analyst request is context, not proof: validate its claim against the device_network observations."
    return (
        f"{ASSESSMENT_RULES}\n\n"
        f"CASE {row.get('case_id', '')}: trigger_type={trigger}; flagged transaction {row.get('flagged_txn_id')} on card "
        f"{row.get('card_id')}.\nTrigger text: {row.get('trigger_text', '')}{statement}\n\n"
        "STRUCTURED OBSERVATIONS (deterministic, with provenance):\n"
        f"{json.dumps(_observations(state), default=str)}\n\n"
        "Assess this case. Give the best-fitting pattern (or none), pattern_if_fraud, recommended_verdict, a calibrated "
        "fraud_probability in [0, 1] with probability_rationale, the evidence families that actually fired and support "
        "your view, contradicting facts, benign facts, and short evidence_claims that restate observed facts. Cite "
        "similar_prior_case_ids only from the closed cases listed above that you actually relied on. If (and only if) "
        "pattern or pattern_if_fraud is undocumented, fill pattern_description with two or three sentences."
    )


def _closest_prior_case(knowledge: dict[str, Any]) -> dict[str, Any] | None:
    """A prior ClosedCase is an evidence family only when it is materially
    similar: the closest retrieved hit at cosine distance <= 
    CLOSE_PRIOR_CASE_MAX_DISTANCE. Chosen deterministically from retrieval,
    never from which case the LLM decided to cite, so the model cannot add
    an evidence family (and so steer the simulator or Sec 6) by citation.
    Merely existing on the same card never counts."""
    close = [
        h for h in knowledge.get("similar_cases") or []
        if h.get("type") == "ClosedCase" and h.get("distance") is not None
        and h["distance"] <= CLOSE_PRIOR_CASE_MAX_DISTANCE
    ]
    if not close:
        return None
    best = min(close, key=lambda h: (h["distance"], str(h.get("id"))))
    return {k: best.get(k) for k in ("id", "outcome", "pattern", "distance")}


async def assess_node(state: InvestigationState) -> InvestigationState:
    result = await generate_structured(_assessment_prompt(state), AssessmentOutput)
    dumped = result.model_dump()
    # initial_assessment is recorded once, before reassess can overwrite it.
    return {**state, "assessment": dumped, "initial_assessment": dict(dumped)}


def stopping_check(state: InvestigationState) -> str:
    # A customer_report's trigger is the customer's statement already on file:
    # the same question is not asked again.
    if state["case_row"].get("trigger_type") == "customer_report":
        return "stop"
    families = state.get("families") or {}
    if is_decisive(
        state["assessment"]["fraud_probability"], families.get("suspicious") or [], families.get("benign") or [],
    ):
        return "stop"
    if state.get("evidence_requests"):
        return "stop"
    return "request_evidence"


async def evidence_request_node(state: InvestigationState) -> InvestigationState:
    """Simulated customer validation from the deterministic profile only --
    the LLM's probability, verdict and pattern are not inputs."""
    card_testing, cnp, region, _recurrence, network = _signals(state)
    families = EvidenceFamilies(**state["families_initial"])
    sim = simulate_customer_validation(_profile(state), families, card_testing, cnp, region, network)
    request = {"type": "customer_validation", "asked_after_step": 3, "assumed_response": sim.text}
    return {**state, "evidence_requests": [request], "simulation": sim.model_dump()}


async def reassess_node(state: InvestigationState) -> InvestigationState:
    sim = state.get("simulation") or {}
    prompt = (
        f"{ASSESSMENT_RULES}\n\n"
        f"Your earlier assessment: {json.dumps(state['assessment'], default=str)}\n"
        f"Evidence request result ({sim.get('response')}): {state['evidence_requests'][-1]['assumed_response']}\n"
        "This response is a documented simulation assumption derived from the card's measured profile (stated in the "
        "text), not an independent real statement. A no_reply is not a denial. Update fraud_probability, "
        "recommended_verdict and the other fields. Keep the same pattern unless the response clearly changes it."
    )
    result = await generate_structured(prompt, AssessmentOutput)
    return {**state, "assessment": result.model_dump()}


def _resolve(state: InvestigationState, assessment: dict[str, Any], families: dict[str, Any], response: str | None):
    return resolve_decision(
        probability=assessment["fraud_probability"],
        suspicious_families=families.get("suspicious") or [],
        benign_families=families.get("benign") or [],
        response=response,
        recurrence_strong=state.get("recurrence_tier") == "strong",
        llm_pattern=assessment.get("pattern") or "none",
        llm_pattern_if_fraud=assessment.get("pattern_if_fraud") or "",
        llm_pattern_description=assessment.get("pattern_description") or "",
        deterministic_pattern=_deterministic_pattern_override(state),
    )


def _findings(state: InvestigationState, decision, families: dict[str, Any]) -> Findings:
    signals = state.get("signals") or {}
    card_testing = signals.get("card_testing") or {}
    episode = state.get("episode") or {}
    shared = bool(state.get("shared_device"))
    return Findings(
        pattern=decision.pattern,
        fraud_probability=decision.fraud_probability,
        single_signal=len(families.get("suspicious") or []) <= 1,
        verdict=decision.verdict,
        shared_device=shared,
        exposure_usd=0.0 if decision.verdict == "legitimate" else float(episode.get("exposure_usd") or 0.0),
        customer_response=decision.response,
        undocumented_coordinated=decision.pattern == "undocumented" and shared,
        card_testing=bool(card_testing.get("fired")),
        card_testing_purchase_over_100=bool(card_testing.get("purchase_over_100_cleared")),
        recurrence_strong=state.get("recurrence_tier") == "strong",
        evidence_conflict=bool(families.get("suspicious")) and bool(families.get("benign")) and decision.verdict == "uncertain",
    )


async def policy_node(state: InvestigationState) -> InvestigationState:
    row = state["case_row"]
    initial_assessment = state.get("initial_assessment") or state["assessment"]
    families_initial = state.get("families_initial") or {}
    initial_decision = _resolve(state, initial_assessment, families_initial, None)
    initial_result = apply_policy(_findings(state, initial_decision, families_initial))

    families = state.get("families") or families_initial
    if row.get("trigger_type") == "customer_report":
        response = state.get("customer_statement")
        stop_reason = (
            "The customer's own dispute matches a strong recurring charge on this card (R7): disputed but "
            "legitimate; the same question is not asked again." if response == "disputes_recurring"
            else "The customer's own report, already on file, states they did not make the transaction (R2); "
            "no simulated follow-up since the customer already answered."
        )
    elif state.get("evidence_requests"):
        response = (state.get("simulation") or {}).get("response")
        # The simulated response is the customer_statement family for the final decision.
        final_families = _families(
            state, customer_statement=response,
            matched_prior_case=state.get("matched_prior_case"),
        )
        families = _dump(final_families)
        stop_reason = ""
    else:
        response = None
        stop_reason = ""

    decision = _resolve(state, state["assessment"], families, response)
    if state.get("evidence_requests"):
        stop_reason = {
            "confirmed_legitimate": "Simulated customer confirmation settled the question (R3).",
            "denies": "Simulated customer denial settled the question (R2).",
            "disputes_recurring": (
                "Simulated denial, but the charge matches a strong recurring pattern on this card: "
                "disputed but legitimate (R7)."
            ),
        }.get(decision.response or "", "")
    if not stop_reason:
        if decision.decisive:
            stop_reason = f"Stopped under Sec 6: {decision.reason}"
        elif response == "no_reply":
            stop_reason = (
                "Customer did not reply within 24 hours (simulated); the evidence alone is not decisive, so the "
                f"case stays open under R4 pending further evidence. {decision.reason}"
            )
        else:
            stop_reason = f"Further automated steps are unlikely to change the decision. {decision.reason}"
    final_result = apply_policy(_findings(state, decision, families))

    return {
        **state,
        "families": families,
        "single_signal": len(families.get("suspicious") or []) <= 1,
        "independent_evidence_count": len(families.get("suspicious") or []),
        "initial_decision": initial_decision.model_dump(),
        "decision": decision.model_dump(),
        "initial_policy_result": initial_result.model_dump(),
        "final_policy_result": final_result.model_dump(),
        "stop_reason": stop_reason,
    }


def build_graph(tg: TigerGraphMCP):
    # Real `async def` wrapper closures (not lambdas) so LangGraph awaits them.
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
    workflow.add_edge("agentic_followup", "apply_followup")  # no-op if no tool was requested
    workflow.add_edge("apply_followup", "assess")
    workflow.add_conditional_edges(
        "assess", stopping_check, {"stop": "policy", "request_evidence": "request_evidence"}
    )
    workflow.add_edge("request_evidence", "reassess")
    workflow.add_edge("reassess", "policy")
    workflow.add_edge("policy", END)

    return workflow.compile()
