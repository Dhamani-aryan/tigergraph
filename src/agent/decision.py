from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# One decision, used everywhere (README Sec 6)
# --------------------------------------------------------------------------
# Before this module, run_case.py called anything >= 0.70 fraud while the
# stopping check used 0.85 and ignored the two-independent-evidence rule, and
# the policy engine computed actions from its own reading of the same
# numbers -- which is how closed_fraud cases ended up carrying R7
# recurring-charge actions. Pattern, verdict, status, policy actions, SAR and
# affected transactions now all derive from the Decision returned here.

DECISIVE_FRAUD_PROBABILITY = 0.85
DECISIVE_LEGITIMATE_PROBABILITY = 0.15
MIN_INDEPENDENT_FAMILIES = 2

Verdict = Literal["fraud", "legitimate", "uncertain"]
Response = Literal["confirmed_fraud", "denies", "confirmed_legitimate", "disputes_recurring", "no_reply"]
SettledBy = Literal["verification_response", "probability_and_evidence", "none"]


class Decision(BaseModel):
    verdict: Verdict
    fraud_probability: float
    pattern: str
    pattern_description: str = ""
    decisive: bool
    settled_by: SettledBy
    response: Response | None = None
    supporting_families: list[str] = Field(default_factory=list)
    reason: str


def _clamp(p: float) -> float:
    return round(min(max(float(p), 0.0), 1.0), 4)


def resolve_decision(
    *,
    probability: float,
    suspicious_families: list[str],
    benign_families: list[str],
    response: Response | None = None,
    recurrence_strong: bool = False,
    llm_pattern: str = "none",
    llm_pattern_if_fraud: str = "",
    llm_pattern_description: str = "",
    deterministic_pattern: str | None = None,
) -> Decision:
    """`suspicious_families` / `benign_families` are the deterministic
    independent evidence families (features.compute_evidence_families), not
    the LLM's own list. `deterministic_pattern` is card_testing or
    out_of_region_use when the exact sequence / strict region signal fired;
    it names the pattern but never forces a fraud verdict."""
    p = _clamp(probability)

    def fraud_pattern() -> tuple[str, str]:
        if deterministic_pattern:
            return deterministic_pattern, ""
        for candidate in (llm_pattern, llm_pattern_if_fraud):
            if candidate and candidate != "none":
                return candidate, llm_pattern_description if candidate == "undocumented" else ""
        return "undocumented", llm_pattern_description

    def legit(prob: float, settled: SettledBy, reason: str) -> Decision:
        return Decision(
            verdict="legitimate", fraud_probability=min(prob, DECISIVE_LEGITIMATE_PROBABILITY), pattern="none",
            decisive=True, settled_by=settled, response=response, supporting_families=list(benign_families),
            reason=reason,
        )

    def fraud(prob: float, settled: SettledBy, reason: str) -> Decision:
        pattern, description = fraud_pattern()
        return Decision(
            verdict="fraud", fraud_probability=max(prob, DECISIVE_FRAUD_PROBABILITY), pattern=pattern,
            pattern_description=description, decisive=True, settled_by=settled, response=response,
            supporting_families=list(suspicious_families), reason=reason,
        )

    if response == "confirmed_legitimate":
        return legit(p, "verification_response", "Customer confirmed the transaction (R3).")
    if response == "disputes_recurring" or (response in ("denies", "confirmed_fraud") and recurrence_strong):
        return Decision(
            verdict="legitimate", fraud_probability=min(p, DECISIVE_LEGITIMATE_PROBABILITY), pattern="none",
            decisive=True, settled_by="verification_response", response="disputes_recurring",
            supporting_families=list(benign_families),
            reason="Disputed charge matches the card's own strong recurring pattern: disputed but legitimate (R7).",
        )
    if response in ("denies", "confirmed_fraud"):
        return fraud(p, "verification_response", "Customer denied the transaction (R2).")

    # no_reply or no response: the evidence has to meet Sec 6 on its own.
    if p >= DECISIVE_FRAUD_PROBABILITY and len(suspicious_families) >= MIN_INDEPENDENT_FAMILIES:
        return fraud(p, "probability_and_evidence",
                     f"Probability {p:.2f} >= 0.85 with {len(suspicious_families)} independent families "
                     f"({', '.join(suspicious_families)}).")
    if p <= DECISIVE_LEGITIMATE_PROBABILITY and len(benign_families) >= MIN_INDEPENDENT_FAMILIES:
        return legit(p, "probability_and_evidence",
                     f"Probability {p:.2f} <= 0.15 with {len(benign_families)} independent benign families "
                     f"({', '.join(benign_families)}).")

    if p >= DECISIVE_FRAUD_PROBABILITY:
        why = f"probability {p:.2f} >= 0.85 but only {len(suspicious_families)} independent suspicious family"
    elif p <= DECISIVE_LEGITIMATE_PROBABILITY:
        why = f"probability {p:.2f} <= 0.15 but only {len(benign_families)} independent benign family"
    else:
        why = f"probability {p:.2f} is between 0.15 and 0.85"
    if response == "no_reply":
        why += "; customer did not reply, which is not a denial"
    uncertain_pattern = deterministic_pattern or (llm_pattern if llm_pattern else "none")
    return Decision(
        verdict="uncertain", fraud_probability=p, pattern=uncertain_pattern,
        pattern_description=llm_pattern_description if uncertain_pattern == "undocumented" else "",
        decisive=False, settled_by="none", response=response,
        supporting_families=list(suspicious_families), reason=f"Not decisive under Sec 6: {why}.",
    )


def is_decisive(probability: float, suspicious_families: list[str], benign_families: list[str]) -> bool:
    """Stopping check without a verification response."""
    return resolve_decision(
        probability=probability, suspicious_families=suspicious_families, benign_families=benign_families,
    ).decisive


def resolve_status(verdict: str, final_action_names: list[str] | set[str]) -> str:
    if verdict == "fraud":
        return "closed_fraud"
    if verdict == "legitimate":
        return "closed_legitimate"
    return "escalated" if "ESCALATE_TO_ANALYST" in set(final_action_names) else "open"
