## Task 5: Policy engine (pure Python, no TigerGraph, no LLM)

The single most-scored piece of logic in the system (25% next-best-action + feeds into 25% accuracy), and the only piece that's fully deterministic and unit-testable in isolation — build and lock this down before anything that depends on it.

**Files:**
- Create: `src/policy/__init__.py` (empty)
- Create: `src/policy/models.py`
- Create: `src/policy/engine.py`
- Test: `tests/test_policy_engine.py`

**Interfaces:**
- Produces: `Findings` (pydantic model: `pattern`, `fraud_probability`, `single_signal: bool`, `shared_device: bool`, `shared_region: bool`, `shared_email: bool`, `exposure_usd: float`, `customer_response: Literal["confirmed_fraud","denies","confirmed_legitimate","disputes_recurring","no_reply",None]`, `undocumented_coordinated: bool`), `ActionRec` (`action: str`, `route: Literal["auto","L1","L2"]`, `reason: str`), `PolicyResult` (`actions: list[ActionRec]`, `sar_file: bool`, `sar_reason: str`). `apply_policy(findings: Findings) -> PolicyResult`. Task 12's `graph_flow.py` is the sole caller.

- [ ] **Step 1: Write `src/policy/models.py`**

```python
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
```

- [ ] **Step 2: Write the failing tests** — table-driven, one test per policy rule plus the README's own worked example.

`tests/test_policy_engine.py`:

```python
from src.policy.engine import apply_policy
from src.policy.models import Findings


def _actions(result):
    return [a.action for a in result.actions]


def test_r1_weak_single_signal_verifies_before_block():
    findings = Findings(pattern="none", fraud_probability=0.55, single_signal=True)
    result = apply_policy(findings)
    assert "VERIFY_WITH_CUSTOMER" in _actions(result) or "STEP_UP_AUTH" in _actions(result)
    assert "BLOCK_CARD" not in _actions(result)


def test_r2_customer_denies_blocks_and_creates_case():
    findings = Findings(
        pattern="card_not_present_fraud",
        fraud_probability=0.85,
        single_signal=False,
        exposure_usd=1200.0,
        customer_response="denies",
    )
    result = apply_policy(findings)
    assert "BLOCK_CARD" in _actions(result)
    assert "CREATE_CASE" in _actions(result)
    assert result.sar_file is True  # exposure > $1000


def test_r2_denies_low_exposure_no_shared_signal_no_report():
    findings = Findings(
        pattern="card_not_present_fraud",
        fraud_probability=0.8,
        single_signal=False,
        exposure_usd=200.0,
        customer_response="denies",
    )
    result = apply_policy(findings)
    assert "BLOCK_CARD" in _actions(result)
    assert result.sar_file is False


def test_r3_customer_confirms_closes_no_fraud():
    findings = Findings(
        pattern="none", fraud_probability=0.4, single_signal=True,
        customer_response="confirmed_legitimate",
    )
    result = apply_policy(findings)
    assert _actions(result) == ["CLOSE_NO_FRAUD"]


def test_r4_no_reply_monitors_and_declines_pending():
    findings = Findings(
        pattern="card_not_present_fraud", fraud_probability=0.6, single_signal=True,
        exposure_usd=600.0, customer_response="no_reply",
    )
    result = apply_policy(findings)
    assert "MONITOR_CARD" in _actions(result)
    assert "DECLINE_TRANSACTION" in _actions(result)
    assert "ESCALATE_TO_ANALYST" in _actions(result)  # exposure > $500


def test_r5_card_testing_declines_and_steps_up():
    findings = Findings(pattern="card_testing", fraud_probability=0.72, single_signal=False)
    result = apply_policy(findings)
    assert "DECLINE_TRANSACTION" in _actions(result)
    assert "STEP_UP_AUTH" in _actions(result)


def test_r6_shared_origin_creates_case_and_monitors_connected():
    findings = Findings(
        pattern="account_takeover", fraud_probability=0.9, single_signal=False,
        shared_device=True, exposure_usd=800.0,
    )
    result = apply_policy(findings)
    assert "CREATE_CASE" in _actions(result)
    assert "FILE_REPORT" in _actions(result)
    assert "MONITOR_CONNECTED_CARDS" in _actions(result)


def test_r7_disputed_but_recurring_does_not_block():
    findings = Findings(
        pattern="none", fraud_probability=0.3, single_signal=False,
        customer_response="disputes_recurring",
    )
    result = apply_policy(findings)
    assert "BLOCK_CARD" not in _actions(result)
    assert "VERIFY_WITH_CUSTOMER" in _actions(result)
    assert "WARN_CUSTOMER" in _actions(result)
    assert "CREATE_CASE" in _actions(result)


def test_r8_uncertain_and_exposed_escalates():
    findings = Findings(
        pattern="none", fraud_probability=0.5, single_signal=False, exposure_usd=900.0
    )
    result = apply_policy(findings)
    assert "ESCALATE_TO_ANALYST" in _actions(result)


def test_r9_undocumented_coordinated_creates_case_files_report_and_escalates():
    findings = Findings(
        pattern="undocumented", fraud_probability=0.8, single_signal=False,
        undocumented_coordinated=True, exposure_usd=1500.0,
    )
    result = apply_policy(findings)
    for expected in ("CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST"):
        assert expected in _actions(result)


def test_r10_block_all_cards_requires_two_confirmed_or_compromised_credentials():
    findings = Findings(
        pattern="account_takeover", fraud_probability=0.95, single_signal=False,
        exposure_usd=3000.0, customer_response="denies",
    )
    result = apply_policy(findings)
    assert "BLOCK_ALL_CARDS" not in _actions(result)  # only one card's evidence here


def test_approval_routes_match_policy_table():
    findings = Findings(
        pattern="card_not_present_fraud", fraud_probability=0.9, single_signal=False,
        exposure_usd=3000.0, customer_response="denies",
    )
    result = apply_policy(findings)
    routes = {a.action: a.route for a in result.actions}
    assert routes["BLOCK_CARD"] == "L2"  # exposure > $2,500
    assert routes["CREATE_CASE"] == "auto"
    if "FILE_REPORT" in routes:
        assert routes["FILE_REPORT"] == "L2"


def test_worked_example_from_readme():
    """README example: HHG-017, card testing, prob 0.72 -> denies -> prob 0.86."""
    initial = Findings(pattern="card_testing", fraud_probability=0.72, single_signal=True)
    initial_result = apply_policy(initial)
    assert "VERIFY_WITH_CUSTOMER" in _actions(initial_result)
    assert "DECLINE_TRANSACTION" in _actions(initial_result)

    final = Findings(
        pattern="card_testing", fraud_probability=0.86, single_signal=False,
        shared_device=True, exposure_usd=268.43, customer_response="denies",
    )
    final_result = apply_policy(final)
    final_actions = _actions(final_result)
    assert "BLOCK_CARD" in final_actions
    assert "CREATE_CASE" in final_actions
    assert "FILE_REPORT" in final_actions
    assert "MONITOR_CONNECTED_CARDS" in final_actions
    assert final_result.sar_file is True
```

- [ ] **Step 3: Run to verify failure**

```bash
.venv\Scripts\pytest tests/test_policy_engine.py -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Implement `src/policy/engine.py`**

```python
from __future__ import annotations

from src.policy.models import ActionRec, Findings, PolicyResult


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
        if f.single_signal and f.fraud_probability < 0.70:
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
    if f.single_signal and f.fraud_probability < 0.70:
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
    # R10: BLOCK_ALL_CARDS only with >=2 confirmed-fraud cards or confirmed compromised
    # credentials -- this engine only ever sees single-card findings, so it never emits
    # BLOCK_ALL_CARDS; a caller investigating multiple cards for one customer would need
    # to call apply_policy per card and apply R10 at a level above this function.
    return PolicyResult(actions=actions, sar_file=sar_file, sar_reason=sar_reason)
```

- [ ] **Step 5: Run to verify passing**

```bash
.venv\Scripts\pytest tests/test_policy_engine.py -v
```

Expected: all tests pass. If any fail, the rule logic (not the test) is usually what's wrong — trace the specific README rule text again and fix `engine.py`; do not weaken a test to make it pass.

- [ ] **Step 6: Commit**

```bash
git add src/policy tests/test_policy_engine.py
git commit -m "feat: deterministic fraud policy engine (rules R1-R10)"
```

---

