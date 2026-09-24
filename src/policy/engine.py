from __future__ import annotations

from src.policy.models import ActionRec, Findings, PolicyResult

# R1's literal threshold (README: "below 0.70"). The earlier 0.85 stretch was
# read from the worked example; R1 now uses the rule text, and card testing
# (R5) keeps asking for verification/step-up until the Sec 6 decisive
# threshold on its own path instead.
R1_WEAK_SIGNAL_THRESHOLD = 0.70
DECISIVE_FRAUD_PROBABILITY = 0.85
DECISIVE_LEGITIMATE_PROBABILITY = 0.15
CASE_OPEN_PROBABILITY = 0.30
BLOCK_ACTIONS = ("BLOCK_CARD", "BLOCK_ALL_CARDS")


def _block_route(exposure_usd: float) -> str:
    return "L1" if exposure_usd <= 2500 else "L2"


def _verdict(f: Findings) -> str:
    if f.verdict is not None:
        return f.verdict
    if f.fraud_probability >= DECISIVE_FRAUD_PROBABILITY and not f.single_signal:
        return "fraud"
    if f.fraud_probability <= DECISIVE_LEGITIMATE_PROBABILITY:
        return "legitimate"
    return "uncertain"


class _Plan:
    def __init__(self) -> None:
        self.actions: list[ActionRec] = []
        self.sar_file = False
        self.sar_reason = "No filing criteria met (Sec 3a)."

    def has(self, action: str) -> bool:
        return any(a.action == action for a in self.actions)

    def add(self, action: str, route: str, reason: str) -> None:
        if not self.has(action):
            self.actions.append(ActionRec(action=action, route=route, reason=reason))

    def drop(self, *names: str) -> None:
        self.actions = [a for a in self.actions if a.action not in names]

    def file(self, reason: str) -> None:
        self.add("FILE_REPORT", "L2", reason)
        self.sar_file = True
        self.sar_reason = reason


def apply_policy(f: Findings) -> PolicyResult:
    verdict = _verdict(f)
    shared = f.shared_device or f.shared_region or f.shared_email
    plan = _Plan()

    # R3: customer confirms -> close, nothing else.
    if f.customer_response == "confirmed_legitimate":
        return PolicyResult(
            actions=[ActionRec(action="CLOSE_NO_FRAUD", route="auto", reason="R3: customer confirmed the transaction")],
            sar_file=False,
            sar_reason="R3: customer confirmed the transaction as their own; no filing.",
        )

    # R7: disputed but matches the card's own STRONG recurring pattern. Never
    # a block or a report, never closed as fraud.
    if f.customer_response == "disputes_recurring":
        return PolicyResult(
            actions=[
                ActionRec(action="CREATE_CASE", route="auto", reason="R7: customer disputed a charge (Sec 3a)"),
                ActionRec(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R7: confirm the recurring charge with the customer"),
                ActionRec(action="WARN_CUSTOMER", route="auto", reason="R7: recurring-charge reminder"),
            ],
            sar_file=False,
            sar_reason="R7: disputed charge matches the customer's own recurring pattern; disputed but legitimate.",
        )

    # Sec 6 decisive legitimate without a response.
    if verdict == "legitimate":
        return PolicyResult(
            actions=[ActionRec(
                action="CLOSE_NO_FRAUD", route="auto",
                reason="Sec 6: probability <= 0.15 with two independent benign evidence families",
            )],
            sar_file=False,
            sar_reason="Closed as legitimate; no filing (Sec 3a).",
        )

    denied = f.customer_response in ("denies", "confirmed_fraud")

    # R5: exact card-testing sequence (the pattern name is only ever set to
    # card_testing by that deterministic sequence).
    card_testing = f.card_testing or f.pattern == "card_testing"
    if card_testing:
        plan.add("DECLINE_TRANSACTION", "L1", "R5: card-testing sequence observed")
        if not denied and verdict != "fraud":
            plan.add("STEP_UP_AUTH", "auto", "R5: step-up before further activity; not yet decisive (< 0.85)")
            plan.add("VERIFY_WITH_CUSTOMER", "auto",
                     f"R5/R1: probability {f.fraud_probability:.2f} on the pattern alone, confirm before blocking")
        if f.card_testing_purchase_over_100 and (denied or verdict == "fraud"):
            plan.add("BLOCK_CARD", _block_route(f.exposure_usd), "R5: a purchase over $100 already cleared")

    # R2: customer denies.
    if denied:
        plan.add("BLOCK_CARD", _block_route(f.exposure_usd), "R2: customer denies the transaction")
        plan.add("CREATE_CASE", "auto", "R2")
        if f.exposure_usd > 1000 or shared:
            plan.file("R2: denied transaction with exposure over $1,000 or a corroborated shared origin")
        if shared:
            plan.add("MONITOR_CONNECTED_CARDS", "auto", "R2/R6: cards sharing the corroborated origin")

    # Sec 6 decisive fraud without a response (>= 0.85 and >= 2 independent families).
    elif verdict == "fraud":
        plan.add("BLOCK_CARD", _block_route(f.exposure_usd),
                 "Sec 6: fraud probability >= 0.85 on two or more independent evidence families (not a single signal, R1)")
        plan.add("CREATE_CASE", "auto", "Sec 3a")
        if f.exposure_usd > 1000 or shared:
            plan.file("Sec 3a: strongly suspected fraud with exposure over $1,000 or a corroborated shared origin")

    # R4: no reply within 24 hours.
    if f.customer_response == "no_reply":
        plan.add("MONITOR_CARD", "auto", "R4: no reply within 24 hours")
        plan.add("DECLINE_TRANSACTION", "L1", "R4: decline pending authorizations")
        plan.add("CREATE_CASE", "auto", "Sec 3a: evidence was requested")
        if f.exposure_usd > 500:
            plan.add("ESCALATE_TO_ANALYST", "auto", "R4: no reply and exposure over $500")

    # R6: shared origin, only on direct corroborated device evidence.
    if shared:
        plan.add("CREATE_CASE", "auto", "R6: shared origin across cards")
        plan.add("MONITOR_CONNECTED_CARDS", "auto", "R6: every card sharing the corroborated origin")
        if verdict == "fraud" or denied:
            plan.file("R6: fraud established and corroborated across cards by a shared origin")
        else:
            plan.add("ESCALATE_TO_ANALYST", "auto", "R6/R8: shared origin corroborated but this card's fraud is not established")

    # R9: undocumented, coordinated abuse.
    if f.pattern == "undocumented" and f.undocumented_coordinated:
        plan.add("CREATE_CASE", "auto", "R9")
        if verdict == "fraud" or denied:
            plan.file("R9: undocumented but coordinated abuse across customers")
        plan.add("ESCALATE_TO_ANALYST", "auto", "R9")

    if verdict == "uncertain" and f.customer_response is None:
        # Verify before deciding (R1 for a weak single signal; Sec 5 otherwise).
        if not (plan.has("VERIFY_WITH_CUSTOMER") or plan.has("STEP_UP_AUTH")):
            reason = (
                f"R1: single signal at probability {f.fraud_probability:.2f} < 0.70, verify before any block"
                if f.single_signal and f.fraud_probability < R1_WEAK_SIGNAL_THRESHOLD
                else f"Sec 5/6: probability {f.fraud_probability:.2f} not decisive, verify before deciding"
            )
            plan.add("VERIFY_WITH_CUSTOMER", "auto", reason)

    # R1: a single weak signal never blocks.
    if f.single_signal and f.fraud_probability < R1_WEAK_SIGNAL_THRESHOLD and not denied:
        plan.drop(*BLOCK_ACTIONS)
        if not card_testing and f.customer_response is None:
            plan.drop("DECLINE_TRANSACTION")

    # An uncertain verdict never blocks the card without a denial.
    if verdict == "uncertain" and not denied:
        plan.drop(*BLOCK_ACTIONS)

    # R8: uncertain and exposed, or conflicting evidence.
    if verdict == "uncertain" and (f.exposure_usd > 500 or f.evidence_conflict):
        plan.add("ESCALATE_TO_ANALYST", "auto",
                 "R8: uncertain verdict with exposure over $500" if f.exposure_usd > 500 else "R8: evidence conflicts")

    # Sec 3a: a case opens at probability >= 0.30, on any evidence request, or on a dispute.
    if (f.fraud_probability >= CASE_OPEN_PROBABILITY or plan.has("VERIFY_WITH_CUSTOMER")
            or plan.has("STEP_UP_AUTH") or denied):
        plan.add("CREATE_CASE", "auto", "Sec 3a")

    # A report always has a case behind it, and the case comes first.
    if plan.has("FILE_REPORT") and plan.actions[0].action != "CREATE_CASE":
        idx = next(i for i, a in enumerate(plan.actions) if a.action == "FILE_REPORT")
        case_idx = next((i for i, a in enumerate(plan.actions) if a.action == "CREATE_CASE"), None)
        if case_idx is None or case_idx > idx:
            case = plan.actions.pop(case_idx) if case_idx is not None else ActionRec(
                action="CREATE_CASE", route="auto", reason="Sec 3a: a report always has a case behind it")
            plan.actions.insert(idx, case)

    if not plan.actions:
        plan.add("VERIFY_WITH_CUSTOMER", "auto", "Sec 5: verify before deciding")

    # R10: BLOCK_ALL_CARDS needs two confirmed-fraud cards or confirmed
    # compromised credentials -- this engine only sees one card, so it never
    # emits it.
    return PolicyResult(actions=plan.actions, sar_file=plan.sar_file, sar_reason=plan.sar_reason)
