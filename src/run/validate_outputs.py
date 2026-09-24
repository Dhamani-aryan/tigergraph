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


SIMULATED_PREFIX = "Simulated assumption:"
BLOCK_OR_REPORT = {"BLOCK_CARD", "BLOCK_ALL_CARDS", "FILE_REPORT"}
CLOSE_CONFLICTS = {"BLOCK_CARD", "BLOCK_ALL_CARDS", "FILE_REPORT", "DECLINE_TRANSACTION"}


def validate_semantics(answer: AnswerFile, index: DatasetIndex, trace: dict | None = None) -> list[str]:
    """Semantic consistency (Task 14): a file can pass every structural check
    above and still say contradictory things -- closed_fraud with R7
    recurring-charge actions, a "CNP burst" citing in-person rows, an
    out-of-region claim on an online transaction. `trace` (the case's
    runs/.../traces/<case_id>.trace.json) enables the checks that need the
    deterministic feature audit; without it those checks are skipped and the
    caller reports the file as answer-only."""
    v: list[str] = []
    case = answer.case
    final = answer.next_best_actions.final
    final_names = {a.action for a in final}

    if case.status == "closed_fraud" and case.verdict != "fraud":
        v.append(f"status closed_fraud with verdict '{case.verdict}'")
    if case.status == "closed_legitimate" and case.verdict != "legitimate":
        v.append(f"status closed_legitimate with verdict '{case.verdict}'")
    if case.verdict == "uncertain" and case.status in ("closed_fraud", "closed_legitimate"):
        v.append(f"verdict uncertain with status '{case.status}'")
    if case.verdict == "legitimate":
        if case.pattern != "none":
            v.append(f"verdict legitimate with pattern '{case.pattern}' (expected none)")
        if case.affected_txn_ids or case.exposure_usd or answer.sar.file:
            v.append("verdict legitimate with affected transactions, exposure or a SAR")
        bad = sorted(final_names & BLOCK_OR_REPORT)
        if bad:
            v.append(f"verdict legitimate with final actions {bad}")
    if case.verdict == "fraud":
        if not case.affected_txn_ids:
            v.append("verdict fraud with empty affected_txn_ids")
        if not case.exposure_usd:
            v.append("verdict fraud with zero exposure_usd")

    for phase, actions in (("initial", answer.next_best_actions.initial), ("final", final)):
        names = {a.action for a in actions}
        if any(a.reason.startswith("R7") for a in actions) and BLOCK_OR_REPORT & names:
            v.append(f"{phase}: R7 (disputed but legitimate) path contains {sorted(BLOCK_OR_REPORT & names)}")
        if "FILE_REPORT" in names and "CREATE_CASE" not in names:
            v.append(f"{phase}: FILE_REPORT without CREATE_CASE")
        if "CLOSE_NO_FRAUD" in names and CLOSE_CONFLICTS & names:
            v.append(f"{phase}: CLOSE_NO_FRAUD combined with {sorted(CLOSE_CONFLICTS & names)}")
    if any(a.reason.startswith("R7") for a in final) and case.status == "closed_fraud":
        v.append("R7 path closed as fraud")

    for i, er in enumerate(answer.evidence_requests):
        if not er.assumed_response.startswith(SIMULATED_PREFIX):
            v.append(f"evidence_requests[{i}] simulated response not labeled '{SIMULATED_PREFIX}'")

    settled_by_response = bool(answer.evidence_requests) or any(
        ev.ref == "trigger:customer_report" for ev in case.evidence
    )
    if case.verdict in ("fraud", "legitimate") and not settled_by_response:
        p = case.fraud_probability
        if (case.verdict == "fraud" and p < 0.85) or (case.verdict == "legitimate" and p > 0.15):
            v.append(f"decisive verdict '{case.verdict}' at probability {p} without a settling verification response")

    flagged = index.case_flagged_txn.get(answer.case_id)
    flagged_channel = index.channel_of(flagged) if flagged else None
    for i, ev in enumerate(case.evidence):
        if ev.ref.startswith("signal:cnp_burst"):
            in_person = [e for e in ev.entity_ids if index.channel_of(e) == "in_person"]
            if in_person:
                v.append(f"evidence[{i}] CNP evidence cites in-person transaction(s) {in_person}")
    oor_claimed = case.pattern == "out_of_region_use" or any(
        ev.ref.startswith("signal:out_of_region") for ev in case.evidence
    )
    if oor_claimed and flagged_channel == "online":
        v.append("out-of-region claimed on an online flagged transaction")

    if trace is not None:
        profile = trace.get("behavior_profile") or {}
        if oor_claimed and (profile.get("flagged_region_prior_count") or 0) > 0:
            v.append(
                "out-of-region claimed but the flagged region already had "
                f"{profile['flagged_region_prior_count']} prior use(s)"
            )
        final_decision = (trace.get("decision") or {}).get("final") or {}
        if (final_decision.get("settled_by") == "probability_and_evidence"
                and len(final_decision.get("supporting_families") or []) < 2):
            v.append("decisive probability stop with fewer than two independent evidence families")
        network = (trace.get("signals_detail") or {}).get("device_network") or {}
        if case.connected_card_ids:
            corroborated = set(network.get("corroborated_card_ids") or [])
            if not network.get("corroborated") or network.get("generic_profile"):
                v.append("connected_card_ids present without direct corroborated device evidence (generic/collision profile)")
            elif set(case.connected_card_ids) - corroborated:
                v.append(f"connected_card_ids {sorted(set(case.connected_card_ids) - corroborated)} not directly corroborated")
    return v


def _load_answer(path: Path) -> tuple[AnswerFile | None, list[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, [f"could not read file: {e}"]
    try:
        return AnswerFile.model_validate_json(raw), []
    except Exception as e:  # noqa: BLE001 -- pydantic's ValidationError, reported verbatim
        return None, [f"schema validation failed: {e}"]


def validate_file(path: Path, index: DatasetIndex) -> list[str]:
    answer, errors = _load_answer(path)
    if answer is None:
        return errors
    violations = validate_answer(answer, index)
    if answer.case_id != path.stem:
        violations.append(f"case_id '{answer.case_id}' does not match filename '{path.name}'")
    return violations


def _load_trace(traces_dir: Path | None, case_id: str) -> dict | None:
    if traces_dir is None:
        return None
    path = traces_dir / f"{case_id}.trace.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def validate_all(cases_dir: str | Path) -> dict[str, list[str]]:
    cases_dir = Path(cases_dir)
    index = load_dataset_index()
    results: dict[str, list[str]] = {}
    for path in sorted(cases_dir.glob("HHG-*.json")):
        results[path.name] = validate_file(path, index)
    return results


def validate_all_semantic(cases_dir: str | Path, traces_dir: str | Path | None = None) -> dict[str, dict]:
    cases_dir = Path(cases_dir)
    tdir = Path(traces_dir) if traces_dir else None
    index = load_dataset_index()
    results: dict[str, dict] = {}
    for path in sorted(cases_dir.glob("HHG-*.json")):
        answer, errors = _load_answer(path)
        if answer is None:
            results[path.name] = {"violations": errors, "trace_checked": False}
            continue
        trace = _load_trace(tdir, answer.case_id)
        results[path.name] = {
            "violations": validate_semantics(answer, index, trace),
            "trace_checked": trace is not None,
        }
    return results


def main() -> int:
    args = sys.argv[1:]
    traces_dir: Path | None = None
    if "--traces" in args:
        i = args.index("--traces")
        traces_dir = Path(args[i + 1])
        args = args[:i] + args[i + 2:]
    cases_dir = Path(args[0]) if args else Path("cases")
    if not cases_dir.exists():
        print(f"No such directory: {cases_dir}")
        return 1

    print("== Structural validation ==")
    results = validate_all(cases_dir)
    total = len(results)
    failed = {name: v for name, v in results.items() if v}
    for name, violations in results.items():
        print(f"{name}: {'OK' if not violations else f'FAIL ({len(violations)})'}")
        for item in violations:
            print(f"    - {item}")
    print(f"Structural: {total - len(failed)}/{total} files passed.")

    mode = f"traces: {traces_dir}" if traces_dir else "no traces: trace-based checks skipped"
    print(f"\n== Semantic validation ({mode}) ==")
    sem = validate_all_semantic(cases_dir, traces_dir)
    sem_failed = {name: r for name, r in sem.items() if r["violations"]}
    for name, r in sem.items():
        n_bad = len(r["violations"])
        suffix = "" if r["trace_checked"] else " [answer-only]"
        print(f"{name}: {'OK' if not n_bad else f'FAIL ({n_bad})'}{suffix}")
        for item in r["violations"]:
            print(f"    - {item}")
    print(f"Semantic: {len(sem) - len(sem_failed)}/{len(sem)} files passed.")
    return 0 if not failed and not sem_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
