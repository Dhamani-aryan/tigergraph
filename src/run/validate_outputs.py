from __future__ import annotations

import json
import sys
from pathlib import Path

from src.agent.schemas import AnswerFile
from src.run.dataset_index import DatasetIndex, load_dataset_index

VALID_ACTIONS = {
    "ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS",
    "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "BLOCK_CARD", "BLOCK_ALL_CARDS",
    "GENERATE_REPORT", "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD",
}
AUTO_ACTIONS = {
    "ALLOW_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER",
    "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "GENERATE_REPORT", "CREATE_CASE",
    "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD",
}
EXPOSURE_L1_L2_CUTOFF_USD = 2500.0


def required_route(action: str, exposure_usd: float) -> str | None:
    """README §2's approval-routing table, as a function. Returns None for
    an action name this table doesn't cover at all (caught separately as an
    "unknown action" violation, not a route mismatch)."""
    if action in AUTO_ACTIONS:
        return "auto"
    if action == "DECLINE_TRANSACTION":
        return "L1"
    if action == "BLOCK_CARD":
        return "L1" if exposure_usd <= EXPOSURE_L1_L2_CUTOFF_USD else "L2"
    if action in ("BLOCK_ALL_CARDS", "FILE_REPORT"):
        return "L2"
    return None


def validate_answer(answer: AnswerFile, index: DatasetIndex) -> list[str]:
    """Returns a list of violation strings; empty means the file passes
    every check here. This is deliberately not exhaustive (see
    docs/frontend-spec.md and the review of Aryan's fuller validator for
    what a more complete version would add) -- it covers the checks with
    the clearest, most objective right answer: dataset-ID existence,
    legitimate-verdict shape, SAR/action consistency, and approval routing.
    """
    violations: list[str] = []
    case = answer.case

    # -- ID existence (README: "Every ID in your answer files must exist in this dataset.") --
    for txn_id in case.affected_txn_ids:
        if not index.has_transaction(txn_id):
            violations.append(f"affected_txn_ids: '{txn_id}' is not a real transaction id")
    if case.first_suspicious_txn_id and not index.has_transaction(case.first_suspicious_txn_id):
        violations.append(f"first_suspicious_txn_id: '{case.first_suspicious_txn_id}' is not a real transaction id")
    for card_id in case.connected_card_ids:
        if not index.has_card(card_id):
            violations.append(f"connected_card_ids: '{card_id}' is not a real card id")
    for prior_id in case.similar_prior_cases:
        if not index.has_closed_case(prior_id):
            violations.append(f"similar_prior_cases: '{prior_id}' is not a real closed-case id")
    for i, ev in enumerate(case.evidence):
        for entity_id in ev.entity_ids:
            known = (
                index.has_transaction(entity_id) or index.has_card(entity_id)
                or index.has_customer(entity_id) or index.has_closed_case(entity_id)
            )
            if not known:
                violations.append(f"evidence[{i}].entity_ids: '{entity_id}' is not a known dataset id")

    # -- legitimate-verdict shape --
    if case.verdict == "legitimate":
        if case.affected_txn_ids:
            violations.append("verdict is 'legitimate' but affected_txn_ids is non-empty")
        if case.exposure_usd != 0:
            violations.append(f"verdict is 'legitimate' but exposure_usd is {case.exposure_usd}, expected 0")
        if answer.sar.file:
            violations.append("verdict is 'legitimate' but sar.file is true")

    # -- SAR / FILE_REPORT consistency --
    final_action_names = {a.action for a in answer.next_best_actions.final}
    files_report = "FILE_REPORT" in final_action_names
    if answer.sar.file != files_report:
        violations.append(
            f"sar.file={answer.sar.file} but FILE_REPORT {'is' if files_report else 'is not'} "
            f"in next_best_actions.final ({sorted(final_action_names)})"
        )
    if answer.sar.file and not answer.sar.narrative.strip():
        violations.append("sar.file is true but sar.narrative is empty")
    if not answer.sar.file and (answer.sar.subjects or answer.sar.total_amount_usd or answer.sar.activity_dates):
        violations.append("sar.file is false but subjects/total_amount_usd/activity_dates are non-empty")

    # -- action names + approval routing, both initial and final --
    for phase, actions in (("initial", answer.next_best_actions.initial), ("final", answer.next_best_actions.final)):
        for a in actions:
            if a.action not in VALID_ACTIONS:
                violations.append(f"next_best_actions.{phase}: '{a.action}' is not a valid policy action")
                continue
            expected = required_route(a.action, case.exposure_usd)
            if expected is not None and a.route != expected:
                violations.append(
                    f"next_best_actions.{phase}: '{a.action}' has route '{a.route}', expected '{expected}' "
                    f"(exposure_usd={case.exposure_usd})"
                )

    # -- R10: BLOCK_ALL_CARDS only ever appears with a strong stated reason --
    for a in answer.next_best_actions.final:
        if a.action == "BLOCK_ALL_CARDS" and "R10" not in a.reason:
            violations.append("next_best_actions.final: BLOCK_ALL_CARDS present without citing R10 in its reason")

    # -- case_id / filename correspond (checked by the caller passing the right pair) --
    return violations


def validate_file(path: Path, index: DatasetIndex) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"could not read file: {e}"]
    try:
        answer = AnswerFile.model_validate_json(raw)
    except Exception as e:  # noqa: BLE001 -- pydantic's ValidationError, reported verbatim
        return [f"schema validation failed: {e}"]
    violations = validate_answer(answer, index)
    if answer.case_id != path.stem:
        violations.append(f"case_id '{answer.case_id}' does not match filename '{path.name}'")
    return violations


def validate_all(cases_dir: str | Path) -> dict[str, list[str]]:
    cases_dir = Path(cases_dir)
    index = load_dataset_index()
    results: dict[str, list[str]] = {}
    for path in sorted(cases_dir.glob("HHG-*.json")):
        results[path.name] = validate_file(path, index)
    return results


def main() -> int:
    cases_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("cases")
    if not cases_dir.exists():
        print(f"No such directory: {cases_dir}")
        return 1
    results = validate_all(cases_dir)
    total = len(results)
    failed = {name: v for name, v in results.items() if v}
    for name, violations in results.items():
        status = "OK" if not violations else f"FAIL ({len(violations)})"
        print(f"{name}: {status}")
        for v in violations:
            print(f"    - {v}")
    print(f"\n{total - len(failed)}/{total} files passed.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
