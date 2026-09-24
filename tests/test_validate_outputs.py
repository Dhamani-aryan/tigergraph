from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.run.dataset_index import DatasetIndex, load_dataset_index
from src.run.validate_outputs import required_route, validate_answer, validate_file
from src.agent.schemas import (
    SAR,
    ActionEntry,
    AnswerFile,
    CaseRecord,
    Evidence,
    NextBestActionSet,
)


@pytest.fixture()
def synthetic_data_dir(tmp_path: Path) -> Path:
    (tmp_path / "transactions.csv").write_text(
        "TransactionID,customer_id\n"
        "3450629,C04570\n"
        "3450436,C04570\n"
        "9999999,C99999\n",
        encoding="utf-8",
    )
    (tmp_path / "case_pack.csv").write_text(
        "case_id,opened_at,trigger_type,trigger_text,flagged_txn_id,card_id,customer_id,risk_score\n"
        "HHG-017,2016-11-12 00:46:24,risk_score,\"scored at 0.57\",3450629,C04570-K1,C04570,0.57\n",
        encoding="utf-8",
    )
    (tmp_path / "closed_cases_history.csv").write_text(
        "case_id,customer_id,card_id,opened_at,closed_at,outcome,pattern,first_fraud_txn_id,"
        "txn_ids,n_txns,exposure_usd,connected_card_ids,actions_taken,report_filed,analyst_notes\n"
        "CC-1383,C04570,C04570-K1,2016-08-01 00:00:00,2016-08-05 00:00:00,cleared,none,,,"
        "0,0,,none,false,cleared\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def index(synthetic_data_dir: Path) -> DatasetIndex:
    return load_dataset_index(synthetic_data_dir)


def _base_answer(**overrides) -> AnswerFile:
    case_kwargs = dict(
        status="closed_legitimate",
        verdict="legitimate",
        fraud_probability=0.1,
        pattern="none",
        pattern_description="",
        affected_txn_ids=[],
        first_suspicious_txn_id="",
        connected_card_ids=[],
        connected_device_profiles=[],
        exposure_usd=0.0,
        evidence=[],
        similar_prior_cases=[],
        summary="Reviewed and closed as legitimate.",
        written_to_graph=True,
        graph_case_id="CASE-HHG-017",
    )
    case_kwargs.update(overrides.pop("case", {}))
    sar_kwargs = dict(file=False, reason="No filing criteria met.", narrative="", subjects=[], total_amount_usd=0.0, activity_dates=[])
    sar_kwargs.update(overrides.pop("sar", {}))
    nba_kwargs = dict(
        initial=[ActionEntry(action="CLOSE_NO_FRAUD", route="auto", reason="R8")],
        final=[ActionEntry(action="CLOSE_NO_FRAUD", route="auto", reason="R8")],
        what_changed="nothing",
    )
    nba_kwargs.update(overrides.pop("next_best_actions", {}))
    return AnswerFile(
        case_id="HHG-017",
        case=CaseRecord(**case_kwargs),
        evidence_requests=[],
        next_best_actions=NextBestActionSet(**nba_kwargs),
        sar=SAR(**sar_kwargs),
        stop_reason="Decisive probability.",
        tool_calls=5,
        tokens=100,
        latency_s=1.0,
        **overrides,
    )


def test_required_route_matches_readme_table():
    assert required_route("ALLOW_TRANSACTION", 0) == "auto"
    assert required_route("DECLINE_TRANSACTION", 0) == "L1"
    assert required_route("BLOCK_CARD", 2500) == "L1"
    assert required_route("BLOCK_CARD", 2500.01) == "L2"
    assert required_route("BLOCK_ALL_CARDS", 0) == "L2"
    assert required_route("FILE_REPORT", 0) == "L2"
    assert required_route("NOT_A_REAL_ACTION", 0) is None


def test_valid_legitimate_case_passes(index: DatasetIndex):
    answer = _base_answer()
    assert validate_answer(answer, index) == []


def test_legitimate_with_nonempty_affected_txns_fails(index: DatasetIndex):
    answer = _base_answer(case={"affected_txn_ids": ["3450629"]})
    violations = validate_answer(answer, index)
    assert any("affected_txn_ids is non-empty" in v for v in violations)


def test_legitimate_with_nonzero_exposure_fails(index: DatasetIndex):
    answer = _base_answer(case={"exposure_usd": 50.0})
    violations = validate_answer(answer, index)
    assert any("exposure_usd" in v for v in violations)


def test_unknown_transaction_id_fails(index: DatasetIndex):
    answer = _base_answer(case={
        "verdict": "fraud", "status": "closed_fraud", "fraud_probability": 0.9,
        "affected_txn_ids": ["NOT-A-REAL-TXN"], "first_suspicious_txn_id": "NOT-A-REAL-TXN",
        "exposure_usd": 100.0,
    })
    violations = validate_answer(answer, index)
    assert any("NOT-A-REAL-TXN" in v for v in violations)


def test_known_transaction_id_passes(index: DatasetIndex):
    answer = _base_answer(case={
        "verdict": "fraud", "status": "closed_fraud", "fraud_probability": 0.9,
        "affected_txn_ids": ["3450629", "3450436"], "first_suspicious_txn_id": "3450436",
        "exposure_usd": 100.0,
    })
    violations = validate_answer(answer, index)
    assert violations == []


def test_unknown_card_id_in_connected_cards_fails(index: DatasetIndex):
    answer = _base_answer(case={
        "verdict": "fraud", "status": "closed_fraud", "fraud_probability": 0.9,
        "affected_txn_ids": ["3450629"], "first_suspicious_txn_id": "3450629",
        "exposure_usd": 100.0, "connected_card_ids": ["MADE-UP-CARD"],
    })
    violations = validate_answer(answer, index)
    assert any("MADE-UP-CARD" in v for v in violations)


def test_sar_file_true_without_file_report_action_fails(index: DatasetIndex):
    answer = _base_answer(
        case={"verdict": "fraud", "status": "closed_fraud", "fraud_probability": 0.9, "exposure_usd": 100.0},
        sar={"file": True, "reason": "R2", "narrative": "Full narrative here.", "subjects": ["C04570"],
             "total_amount_usd": 100.0, "activity_dates": ["2016-11-11", "2016-11-11"]},
    )
    violations = validate_answer(answer, index)
    assert any("sar.file=True" in v for v in violations)


def test_sar_file_true_with_file_report_action_passes(index: DatasetIndex):
    answer = _base_answer(
        case={"verdict": "fraud", "status": "closed_fraud", "fraud_probability": 0.9, "exposure_usd": 100.0},
        sar={"file": True, "reason": "R2", "narrative": "Full narrative here.", "subjects": ["C04570"],
             "total_amount_usd": 100.0, "activity_dates": ["2016-11-11", "2016-11-11"]},
        next_best_actions={
            "initial": [ActionEntry(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R1")],
            "final": [
                ActionEntry(action="BLOCK_CARD", route="L1", reason="R2"),
                ActionEntry(action="FILE_REPORT", route="L2", reason="R2"),
            ],
            "what_changed": "Customer denied.",
        },
    )
    violations = validate_answer(answer, index)
    assert violations == []


def test_wrong_route_for_block_card_fails(index: DatasetIndex):
    answer = _base_answer(
        case={"verdict": "fraud", "status": "closed_fraud", "fraud_probability": 0.9, "exposure_usd": 3000.0},
        next_best_actions={
            "initial": [ActionEntry(action="BLOCK_CARD", route="L1", reason="R2")],  # should be L2 above $2500
            "final": [ActionEntry(action="BLOCK_CARD", route="L1", reason="R2")],
            "what_changed": "nothing",
        },
    )
    violations = validate_answer(answer, index)
    assert any("expected 'L2'" in v for v in violations)


def test_unknown_action_name_fails(index: DatasetIndex):
    answer = _base_answer(
        next_best_actions={
            "initial": [ActionEntry(action="DO_SOMETHING_MADE_UP", route="auto", reason="???")],
            "final": [ActionEntry(action="DO_SOMETHING_MADE_UP", route="auto", reason="???")],
            "what_changed": "nothing",
        },
    )
    violations = validate_answer(answer, index)
    assert any("not a valid policy action" in v for v in violations)


def test_block_all_cards_without_r10_citation_fails(index: DatasetIndex):
    answer = _base_answer(
        case={"verdict": "fraud", "status": "closed_fraud", "fraud_probability": 0.95, "exposure_usd": 5000.0},
        next_best_actions={
            "initial": [ActionEntry(action="BLOCK_ALL_CARDS", route="L2", reason="seems bad")],
            "final": [ActionEntry(action="BLOCK_ALL_CARDS", route="L2", reason="seems bad")],
            "what_changed": "nothing",
        },
    )
    violations = validate_answer(answer, index)
    assert any("BLOCK_ALL_CARDS" in v and "R10" in v for v in violations)


def test_validate_file_reports_case_id_filename_mismatch(tmp_path: Path, index: DatasetIndex):
    answer = _base_answer()
    path = tmp_path / "HHG-999.json"
    path.write_text(answer.model_dump_json(), encoding="utf-8")
    violations = validate_file(path, index)
    assert any("does not match filename" in v for v in violations)


def test_validate_file_reports_schema_errors_on_malformed_json(tmp_path: Path, index: DatasetIndex):
    path = tmp_path / "HHG-017.json"
    path.write_text("{not valid json", encoding="utf-8")
    violations = validate_file(path, index)
    assert any("schema validation failed" in v for v in violations)


def test_dataset_index_knows_case_pack_card_and_customer(index: DatasetIndex):
    assert index.has_transaction("3450629")
    assert not index.has_transaction("0000000")
    assert index.has_card("C04570-K1")
    assert index.has_customer("C04570")
    assert index.has_closed_case("CC-1383")
    assert not index.has_closed_case("CC-9999")


# -- Task 14 semantic validation ---------------------------------------------
from src.agent.schemas import EvidenceRequestRecord  # noqa: E402
from src.run.validate_outputs import validate_semantics  # noqa: E402


def _sem_index() -> DatasetIndex:
    return DatasetIndex(
        transaction_ids=frozenset({"3450629", "3450436", "9999999"}),
        card_ids=frozenset({"C04570-K1", "C99999-K1"}), customer_ids=frozenset({"C04570"}),
        closed_case_ids=frozenset({"CC-1383"}),
        txn_channel={"3450629": "online", "3450436": "in_person", "9999999": "online"},
        case_flagged_txn={"HHG-017": "3450629"},
    )


def _fraud_answer(**case):
    base = dict(status="closed_fraud", verdict="fraud", fraud_probability=0.9, pattern="card_not_present_fraud",
                affected_txn_ids=["3450629"], first_suspicious_txn_id="3450629", exposure_usd=100.09)
    base.update(case)
    return _base_answer(case=base, next_best_actions=dict(
        initial=[ActionEntry(action="VERIFY_WITH_CUSTOMER", route="auto", reason="R1")],
        final=[ActionEntry(action="BLOCK_CARD", route="L1", reason="Sec 6"),
               ActionEntry(action="CREATE_CASE", route="auto", reason="Sec 3a")],
        what_changed="x"))


def test_semantic_valid_legitimate_passes():
    assert validate_semantics(_base_answer(), _sem_index()) == []


def test_semantic_valid_fraud_passes():
    assert validate_semantics(_fraud_answer(), _sem_index()) == []


def test_semantic_rejects_status_verdict_contradictions():
    idx = _sem_index()
    assert any("closed_fraud with verdict" in v for v in validate_semantics(_fraud_answer(verdict="uncertain"), idx))
    assert any("closed_legitimate with verdict" in v
               for v in validate_semantics(_base_answer(case={"verdict": "fraud"}), idx))
    assert any("uncertain with status" in v
               for v in validate_semantics(_base_answer(case={"verdict": "uncertain", "fraud_probability": 0.5}), idx))


def test_semantic_rejects_legitimate_with_pattern_or_block():
    a = _base_answer(case={"pattern": "card_not_present_fraud"}, next_best_actions=dict(
        initial=[ActionEntry(action="CLOSE_NO_FRAUD", route="auto", reason="R3")],
        final=[ActionEntry(action="CLOSE_NO_FRAUD", route="auto", reason="R3"),
               ActionEntry(action="BLOCK_CARD", route="L1", reason="x")],
        what_changed="x"))
    v = validate_semantics(a, _sem_index())
    assert any("pattern 'card_not_present_fraud'" in x for x in v)
    assert any("BLOCK_CARD" in x for x in v)
    assert any("CLOSE_NO_FRAUD combined" in x for x in v)


def test_semantic_rejects_fraud_without_episode():
    v = validate_semantics(_fraud_answer(affected_txn_ids=[], exposure_usd=0.0, first_suspicious_txn_id=""), _sem_index())
    assert any("empty affected_txn_ids" in x for x in v)
    assert any("zero exposure" in x for x in v)


def test_semantic_rejects_r7_with_block_and_report_without_case():
    a = _fraud_answer()
    a.next_best_actions.final = [
        ActionEntry(action="WARN_CUSTOMER", route="auto", reason="R7"),
        ActionEntry(action="BLOCK_CARD", route="L1", reason="x"),
        ActionEntry(action="FILE_REPORT", route="L2", reason="x"),
    ]
    v = validate_semantics(a, _sem_index())
    assert any("R7 (disputed but legitimate) path" in x for x in v)
    assert any("FILE_REPORT without CREATE_CASE" in x for x in v)
    assert any("R7 path closed as fraud" in x for x in v)


def test_semantic_rejects_decisive_verdict_without_threshold_or_response():
    v = validate_semantics(_fraud_answer(fraud_probability=0.72), _sem_index())
    assert any("without a settling verification response" in x for x in v)


def test_semantic_rejects_unlabeled_simulated_response():
    a = _fraud_answer()
    a.evidence_requests = [EvidenceRequestRecord(type="customer_validation", asked_after_step=3,
                                                 assumed_response="Customer states they did not make this.")]
    assert any("not labeled" in x for x in validate_semantics(a, _sem_index()))


def test_semantic_rejects_cnp_evidence_with_in_person_rows_and_online_out_of_region():
    a = _fraud_answer(pattern="out_of_region_use", evidence=[
        Evidence(claim="burst", source="graph", ref="signal:cnp_burst", entity_ids=["3450629", "3450436"]),
    ])
    v = validate_semantics(a, _sem_index())
    assert any("CNP evidence cites in-person" in x for x in v)
    assert any("out-of-region claimed on an online flagged transaction" in x for x in v)


def test_semantic_trace_checks_region_history_families_and_connected_cards():
    a = _fraud_answer(pattern="out_of_region_use", connected_card_ids=["C99999-K1"])
    trace = {
        "behavior_profile": {"flagged_region_prior_count": 10},
        "decision": {"final": {"settled_by": "probability_and_evidence", "supporting_families": ["identity_anomaly"]}},
        "signals_detail": {"device_network": {"corroborated": False, "generic_profile": True, "corroborated_card_ids": []}},
    }
    v = validate_semantics(a, _sem_index(), trace)
    assert any("already had 10 prior use" in x for x in v)
    assert any("fewer than two independent evidence families" in x for x in v)
    assert any("generic/collision profile" in x for x in v)
