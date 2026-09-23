from __future__ import annotations

from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    case_row: dict[str, Any]
    card_id: str
    cutoff_ts: str  # case_row["opened_at"]; every graph lookup that can see other transactions is bounded to this
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
