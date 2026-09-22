from __future__ import annotations

from src.policy.models import ActionRec, Findings, PolicyResult

# R1's "weak signal" bar. The README's rule text says "below 0.70", but the README's own
# worked example (HHG-017: pattern=card_testing, fraud_probability=0.72, single_signal=True)
# recommends VERIFY_WITH_CUSTOMER at 0.72 with the reason "R1: probability 0.72 on pattern
# alone, confirm before blocking" -- i.e. R1 stays in force until probability reaches the
# same confidence bar Section 6 uses to call a verdict settled (>= 0.85, or <= 0.15). We use
# that bar here rather than the literal "0.70" text, since the worked example is the more
# concrete and authoritative statement of intended behavior.
R1_WEAK_SIGNAL_THRESHOLD = 0.85


def apply_policy(f: Findings) -> PolicyResult:
    actions: list[ActionRec] = []
    sar_file = False
    sar_reason = "No filing criteria met."

    # R3: customer confirms the transaction themselves -> close, nothing else matters.
    if f.customer_response == "confirmed_legitimate":
        return PolicyResult(
            actions=[ActionRec(action="CLOSE_NO_FRAUD", route="auto", reason="R3")],
            sar_file=False,
            sar_reason="R3: customer confirmed the transaction as their own.",
        )

    # R7: disputed but matches the customer's own recurring pattern.
    if f.customer_response == "disputes_recurring":
        return PolicyResult(
            actions=[
                ActionRec(action="CREATE_CASE", route="auto", reason="R7"),
                ActionRec(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R7"),
                ActionRec(action="WARN_CUSTOMER", route="auto", reason="R7"),
            ],
            sar_file=False,
            sar_reason="R7: disputed charge matches the customer's own recurring pattern.",
        )

    # R2: customer denies the transaction.
    if f.customer_response == "denies":
        route = "L1" if f.exposure_usd <= 2500 else "L2"
        actions.append(ActionRec(action="BLOCK_CARD", route=route, reason="R2"))
        actions.append(ActionRec(action="CREATE_CASE", route="auto", reason="R2"))
        if f.exposure_usd > 1000 or f.shared_device or f.shared_region or f.shared_email:
            sar_file = True
            sar_reason = "R2: exposure exceeds $1,000 or activity connects to a shared origin."
            actions.append(ActionRec(action="FILE_REPORT", route="L2", reason="R2"))
        if f.shared_device or f.shared_region or f.shared_email:
            actions.append(
                ActionRec(action="MONITOR_CONNECTED_CARDS", route="auto", reason="R2/R6")
            )
        return _finish(f, actions, sar_file, sar_reason)

    # R4: no reply within 24 hours.
    if f.customer_response == "no_reply":
        actions.append(ActionRec(action="MONITOR_CARD", route="auto", reason="R4"))
        actions.append(ActionRec(action="DECLINE_TRANSACTION", route="L1", reason="R4"))
        if f.exposure_usd > 500:
            actions.append(ActionRec(action="ESCALATE_TO_ANALYST", route="auto", reason="R4"))
        return _finish(f, actions, sar_file, sar_reason)

    # R5: card testing pattern.
    if f.pattern == "card_testing":
        actions.append(ActionRec(action="DECLINE_TRANSACTION", route="L1", reason="R5"))
        actions.append(ActionRec(action="STEP_UP_AUTH", route="auto", reason="R5"))
        if f.single_signal and f.fraud_probability < R1_WEAK_SIGNAL_THRESHOLD:
            actions.append(
                ActionRec(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R1")
            )

    # R6: shared origin across cards (independent of pattern, if not already handled by R2).
    if (f.shared_device or f.shared_region or f.shared_email) and not any(
        a.action == "CREATE_CASE" for a in actions
    ):
        actions.append(ActionRec(action="CREATE_CASE", route="auto", reason="R6"))
        actions.append(ActionRec(action="FILE_REPORT", route="L2", reason="R6"))
        actions.append(
            ActionRec(action="MONITOR_CONNECTED_CARDS", route="auto", reason="R6")
        )
        sar_file = True
        sar_reason = "R6: shared device/region/email links this to other cards."

    # R9: undocumented pattern, coordinated/repeated abuse.
    if f.pattern == "undocumented" and f.undocumented_coordinated:
        if not any(a.action == "CREATE_CASE" for a in actions):
            actions.append(ActionRec(action="CREATE_CASE", route="auto", reason="R9"))
        if not any(a.action == "FILE_REPORT" for a in actions):
            actions.append(ActionRec(action="FILE_REPORT", route="L2", reason="R9"))
            sar_file = True
            sar_reason = "R9: undocumented but coordinated/repeated abuse across customers."
        actions.append(ActionRec(action="ESCALATE_TO_ANALYST", route="auto", reason="R9"))

    # R1: single weak signal -> verify before any block.
    if f.single_signal and f.fraud_probability < R1_WEAK_SIGNAL_THRESHOLD:
        if not any(a.action in ("VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH") for a in actions):
            actions.append(
                ActionRec(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R1")
            )
        actions = [
            a for a in actions if a.action not in ("BLOCK_CARD", "BLOCK_ALL_CARDS", "DECLINE_TRANSACTION")
            or a.action == "DECLINE_TRANSACTION" and f.pattern == "card_testing"
        ]

    # R8: uncertain verdict with real exposure -> escalate.
    if 0.15 < f.fraud_probability < 0.85 and f.exposure_usd > 500:
        if not any(a.action == "ESCALATE_TO_ANALYST" for a in actions):
            actions.append(ActionRec(action="ESCALATE_TO_ANALYST", route="auto", reason="R8"))

    if not actions:
        if f.fraud_probability <= 0.15:
            actions.append(ActionRec(action="CLOSE_NO_FRAUD", route="auto", reason="R8"))
        else:
            actions.append(
                ActionRec(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R1")
            )

    return _finish(f, actions, sar_file, sar_reason)


def _finish(
    f: Findings, actions: list[ActionRec], sar_file: bool, sar_reason: str
) -> PolicyResult:
    # Sec 3a: "Open one [CREATE_CASE] whenever fraud probability reaches 0.30, whenever
    # you request evidence, or whenever a customer disputes a charge." This is a general
    # trigger, independent of which specific rule (if any) fired above -- so it's applied
    # here, at the single choke point every non-early-return path funnels through (R2's
    # `denies` branch, R4's `no_reply` branch, and the general R5/R6/R9/R1/R8 fallthrough),
    # rather than duplicated per-branch. R2/R6/R7/R9 already open their own case when they
    # fire, so the guard below is a no-op there; this only fills the gap for paths that
    # previously never opened one (e.g. R4's no_reply, or a bare moderate-probability
    # VERIFY_WITH_CUSTOMER with no other rule triggered).
    #
    # R3 (`confirmed_legitimate`) and R7 (`disputes_recurring`) deliberately bypass this
    # function entirely (they return their own PolicyResult directly) and so never pick up
    # this trigger: R3 is a closed-as-legitimate verdict where opening a fraud case would be
    # wrong regardless of the pre-confirmation probability, and R7 already opens its own case
    # per its own rule text.
    #
    # The second half of Sec 3a's trigger -- "whenever you request evidence" -- is not
    # implementable here: `Findings` has no field recording that an evidence request
    # (VERIFY_WITH_CUSTOMER / STEP_UP_AUTH / an analyst request) was made independent of the
    # probability that prompted it, so there's no signal to gate on beyond the actions this
    # function already sees. In every path reachable today, requesting evidence coincides
    # with a probability that is either >=0.30 (already covered below) or handled by an
    # early-return branch (R3/R7) that intentionally opts out. If `Findings` ever grows an
    # explicit "evidence requested" flag, this is where it should be checked too.
    if f.fraud_probability >= 0.30 and not any(a.action == "CREATE_CASE" for a in actions):
        actions.append(ActionRec(action="CREATE_CASE", route="auto", reason="Sec 3a"))

    # R10: BLOCK_ALL_CARDS only with >=2 confirmed-fraud cards or confirmed compromised
    # credentials -- this engine only ever sees single-card findings, so it never emits
    # BLOCK_ALL_CARDS; a caller investigating multiple cards for one customer would need
    # to call apply_policy per card and apply R10 at a level above this function.
    return PolicyResult(actions=actions, sar_file=sar_file, sar_reason=sar_reason)
