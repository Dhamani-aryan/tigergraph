from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Pattern = Literal[
    "card_testing",
    "card_not_present_fraud",
    "card_not_present_new_device",
    "out_of_region_use",
    "account_takeover",
    "undocumented",
    "none",
]
Verdict = Literal["fraud", "legitimate", "uncertain"]
Status = Literal["open", "closed_fraud", "closed_legitimate", "escalated"]
EvidenceSource = Literal["graph", "document", "customer", "external"]
Route = Literal["auto", "L1", "L2"]
EvidenceRequestType = Literal["customer_validation", "step_up_auth", "analyst_info"]


class Evidence(BaseModel):
    claim: str
    source: EvidenceSource
    ref: str
    entity_ids: list[str] = []


class CaseRecord(BaseModel):
    status: Status
    verdict: Verdict
    fraud_probability: float
    pattern: Pattern
    pattern_description: str = ""
    affected_txn_ids: list[str] = []
    first_suspicious_txn_id: str = ""
    connected_card_ids: list[str] = []
    connected_device_profiles: list[str] = []
    exposure_usd: float = 0.0
    evidence: list[Evidence] = []
    similar_prior_cases: list[str] = []
    summary: str
    written_to_graph: bool
    graph_case_id: str = ""


class SAR(BaseModel):
    file: bool
    reason: str
    narrative: str = ""
    subjects: list[str] = []
    total_amount_usd: float = 0.0
    activity_dates: list[str] = []


class ActionEntry(BaseModel):
    action: str
    route: Route
    reason: str


class NextBestActionSet(BaseModel):
    initial: list[ActionEntry]
    final: list[ActionEntry]
    what_changed: str


class EvidenceRequestRecord(BaseModel):
    type: EvidenceRequestType
    asked_after_step: int
    assumed_response: str


class AnswerFile(BaseModel):
    case_id: str
    case: CaseRecord
    evidence_requests: list[EvidenceRequestRecord] = []
    next_best_actions: NextBestActionSet
    sar: SAR
    stop_reason: str
    tool_calls: int
    tokens: int
    latency_s: float
