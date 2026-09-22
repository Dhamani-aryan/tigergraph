from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

CustomerResponse = Literal[
    "confirmed_fraud", "denies", "confirmed_legitimate", "disputes_recurring", "no_reply"
]


class Findings(BaseModel):
    pattern: str  # one of the README's pattern enum values, or "none"
    fraud_probability: float
    single_signal: bool  # True if the case rests on one signal (e.g. risk score alone)
    shared_device: bool = False
    shared_region: bool = False
    shared_email: bool = False
    exposure_usd: float = 0.0
    customer_response: CustomerResponse | None = None
    undocumented_coordinated: bool = False  # R9: undocumented pattern, coordinated/repeated abuse


class ActionRec(BaseModel):
    action: str
    route: Literal["auto", "L1", "L2"]
    reason: str


class PolicyResult(BaseModel):
    actions: list[ActionRec]
    sar_file: bool
    sar_reason: str
