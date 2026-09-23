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
    episode: dict[str, Any]  # from src.agent.episode.build_episode: txn_ids, first_txn_id, exposure_usd, detected_pattern
    is_new_device: bool  # real id_15 == "New" on the flagged transaction (NOT shared_device -- see episode.py)
    is_proxy: bool  # real id_23 proxy flag on the flagged transaction
    out_of_region: bool  # from src.agent.episode.detect_out_of_region
    recurring_charge_detected: bool  # from src.agent.episode.detect_recurring_charge, R7
    device_profile_label: str  # "DEVICE_INFO | OS | BROWSER | SCREEN" for the flagged transaction's device
    connected_card_ids: list[str]  # other cards sharing the flagged transaction's device (gated, see gather_evidence_node)
    connected_device_profiles: list[str]
    _pending_followup: dict[str, Any]  # set by agentic_followup_node, consumed by apply_followup_node
    evidence_requests: list[dict[str, Any]]
    initial_policy_result: dict[str, Any]
    final_policy_result: dict[str, Any]
    tool_calls: int
    stop_reason: str
