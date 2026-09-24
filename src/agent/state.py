from __future__ import annotations

from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    case_row: dict[str, Any]
    card_id: str
    cutoff_ts: str  # case_row["opened_at"]; every graph lookup that can see other transactions is bounded to this
    evidence: list[dict[str, Any]]
    # Deterministic feature layer (src.agent.features), computed before any LLM call.
    behavior_profile: dict[str, Any]
    signals: dict[str, Any]  # card_testing / cnp_burst / out_of_region / recurrence / device_network results
    families_initial: dict[str, Any]  # evidence families before any customer statement is applied
    families: dict[str, Any]  # evidence families including the customer statement / simulated response
    customer_statement: str | None  # customer_report trigger: "denies" | "disputes_recurring"
    matched_prior_case: dict[str, Any]
    single_signal: bool  # at most one independent suspicious evidence family
    independent_evidence_count: int
    recurrence_tier: str  # none | candidate | strong
    assessment: dict[str, Any]  # LLM AssessmentOutput (internal contract)
    initial_assessment: dict[str, Any]  # snapshot of `assessment` right after assess_node
    simulation: dict[str, Any]  # simulator.SimulationResult
    initial_decision: dict[str, Any]  # decision.Decision before any response
    decision: dict[str, Any]  # decision.Decision: the single source for verdict/status/pattern/actions
    shared_device: bool  # direct, corroborated device evidence only
    shared_region: bool
    shared_email: bool
    cluster_prior_fraud_rate: float  # connected-components context only
    episode: dict[str, Any]  # src.agent.episode.build_episode
    is_new_device: bool
    is_proxy: bool
    out_of_region: bool  # strict out-of-region signal fired
    recurring_charge_detected: bool  # strong recurrence only
    device_profile_label: str
    connected_card_ids: list[str]  # directly corroborated cards only
    connected_device_profiles: list[str]
    _pending_followup: dict[str, Any]
    evidence_requests: list[dict[str, Any]]
    initial_policy_result: dict[str, Any]
    final_policy_result: dict[str, Any]
    tool_calls: int
    stop_reason: str
