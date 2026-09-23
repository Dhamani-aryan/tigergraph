from __future__ import annotations

from typing import Literal

SimulatedType = Literal["customer_validation", "step_up_auth", "analyst_info"]


def simulate_evidence_response(
    request_type: SimulatedType,
    *,
    flagged_amount: float,
    customer_median_amount: float,
    is_new_device: bool,
    fraud_probability: float,
) -> str:
    """Simulate the response a customer/analyst would plausibly give, grounded in the
    actual transaction data rather than invented freely. State the assumption plainly --
    callers must log this string verbatim into evidence_requests.assumed_response.
    """
    amount_ratio = flagged_amount / customer_median_amount if customer_median_amount else 999
    looks_anomalous = amount_ratio > 3 or is_new_device or fraud_probability > 0.6

    if request_type == "customer_validation":
        if looks_anomalous:
            return (
                f"Customer states they did not make this ${flagged_amount:.2f} purchase "
                f"and still has the card. (Simulated: amount is {amount_ratio:.1f}x their "
                f"typical transaction{' from a device new to this account' if is_new_device else ''}.)"
            )
        return (
            f"Customer confirms they made this ${flagged_amount:.2f} purchase. "
            f"(Simulated: amount is in line with their typical spending pattern.)"
        )

    if request_type == "step_up_auth":
        if looks_anomalous:
            return "Step-up authentication failed / was not completed. (Simulated: anomalous activity pattern.)"
        return "Step-up authentication completed successfully. (Simulated: activity fits customer's normal pattern.)"

    return "Analyst confirms no additional context beyond the graph evidence is available. (Simulated.)"
