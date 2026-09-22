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
