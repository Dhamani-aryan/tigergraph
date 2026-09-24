from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.agent.features import (
    BehaviorProfile,
    CardTestingResult,
    CnpBurstResult,
    DeviceNetworkResult,
    EvidenceFamilies,
    RegionSignal,
)

# --------------------------------------------------------------------------
# Non-circular simulated customer response (README Sec 5)
# --------------------------------------------------------------------------
# The previous simulator took the model's own probability as an input
# and denied whenever it exceeded 0.6, so the model's suspicion came back to
# it as a "customer denial" (0.48 -> 0.94 and similar in the diagnostic run),
# and it compared every amount to a hardcoded $100 "median".
#
# This version sees only the deterministic BehaviorProfile and detector
# results, which are computed before any LLM call. It never receives the
# LLM probability, verdict or pattern, any batch-level distribution, or the
# case id. Every response text starts with "Simulated assumption:" and states
# the rule and the metrics that produced it.

SimulatedResponse = Literal["confirmed_legitimate", "denies", "no_reply"]
SIMULATED_PREFIX = "Simulated assumption:"


class SimulationResult(BaseModel):
    response: SimulatedResponse
    text: str
    rule: str


def _metrics(profile: BehaviorProfile) -> str:
    text = (
        f"history {profile.history_count} prior txns; amount {profile.amount_ratio}x median "
        f"${profile.amount_median} (percentile {profile.amount_percentile}, {profile.amount_class}); "
        f"ProductCD {profile.flagged_product} {profile.product_class} ({profile.product_prior_count} prior); "
        f"channel {profile.flagged_channel}"
    )
    if profile.flagged_channel == "in_person":
        text += f"; region {profile.flagged_region} {profile.region_class}"
    if profile.flagged_channel == "online":
        text += f"; device {profile.device_status or 'unknown'}"
    return text


def simulate_customer_validation(
    profile: BehaviorProfile,
    families: EvidenceFamilies,
    card_testing: CardTestingResult,
    cnp: CnpBurstResult,
    region: RegionSignal,
    network: DeviceNetworkResult,
) -> SimulationResult:
    amount = f"${profile.flagged_amount:.2f}" if profile.flagged_amount is not None else "the flagged amount"
    metrics = _metrics(profile)

    behaviorally_anomalous_burst = "behaviorally_anomalous_cnp_burst" in families.strong_suspicious
    confirm_conditions = {
        "stable history (>=20 prior)": profile.stable_history,
        "normal amount": profile.amount_class == "normal",
        "established ProductCD": profile.product_class == "established",
        "established region (in-person)": (
            profile.flagged_channel != "in_person" or profile.region_class == "established"
        ),
        "no anonymizing proxy": not profile.is_proxy,
        "no exact card testing": not card_testing.fired,
        "no strict out-of-region": not region.fired,
        "no behaviorally anomalous CNP burst": not behaviorally_anomalous_burst,
        "no direct shared-origin corroboration": not network.corroborated,
    }
    if all(confirm_conditions.values()):
        rule = "confirm -- every behavioral feature is consistent with the card's own history"
        note = " A device marked New does not block confirmation on its own." if profile.is_new_device else ""
        return SimulationResult(
            response="confirmed_legitimate",
            rule=rule,
            text=(
                f"{SIMULATED_PREFIX} customer confirms they made this {amount} purchase. Rule: {rule}.{note} "
                f"Metrics: {metrics}."
            ),
        )

    if len(families.suspicious) >= 2 and families.strong_suspicious:
        rule = (
            f"deny -- {len(families.suspicious)} independent suspicious families "
            f"({', '.join(families.suspicious)}) including strong signal(s) {', '.join(families.strong_suspicious)}"
        )
        return SimulationResult(
            response="denies",
            rule=rule,
            text=(
                f"{SIMULATED_PREFIX} customer states they did not make this {amount} purchase and still has the "
                f"card. Rule: {rule}. Metrics: {metrics}."
            ),
        )

    failed = [name for name, ok in confirm_conditions.items() if not ok]
    n = len(families.suspicious)
    rule = (
        "no reply -- profile is ambiguous: not fully consistent with history (" + ", ".join(failed) + ") "
        f"but only {n} suspicious {'family' if n == 1 else 'families'}"
        + (f" ({', '.join(families.suspicious)})" if families.suspicious else "")
        + ("" if families.strong_suspicious else " and no strong signal")
    )
    return SimulationResult(
        response="no_reply",
        rule=rule,
        text=f"{SIMULATED_PREFIX} customer did not reply within 24 hours. Rule: {rule}. Metrics: {metrics}.",
    )
