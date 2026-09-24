from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

CustomerResponse = Literal[
    "confirmed_fraud", "denies", "confirmed_legitimate", "disputes_recurring", "no_reply"
]


class Findings(BaseModel):
    pattern: str  # one of the README's pattern enum values, or "none"
    fraud_probability: float
    single_signal: bool  # at most one independent suspicious evidence family (risk score never counts)
    # The resolved decision (src.agent.decision). None keeps the pre-decision
    # behaviour for callers that only have a probability.
    verdict: Literal["fraud", "legitimate", "uncertain"] | None = None
    # Direct, corroborated shared origin only (features.evaluate_device_network)
    # -- never a generic fingerprint collision or a connected-component rate.
    shared_device: bool = False
    shared_region: bool = False
    shared_email: bool = False
    exposure_usd: float = 0.0
    customer_response: CustomerResponse | None = None
    undocumented_coordinated: bool = False  # R9: undocumented pattern, coordinated/repeated abuse
    card_testing: bool = False  # exact R5 sequence containing the flagged transaction
    card_testing_purchase_over_100: bool = False
    recurrence_strong: bool = False
    evidence_conflict: bool = False


class ActionRec(BaseModel):
    action: str
    route: Literal["auto", "L1", "L2"]
    reason: str


class PolicyResult(BaseModel):
    actions: list[ActionRec]
    sar_file: bool
    sar_reason: str
