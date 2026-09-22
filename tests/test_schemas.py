import json

from src.agent.schemas import AnswerFile

README_EXAMPLE = {
    "case_id": "HHG-017",
    "case": {
        "status": "closed_fraud",
        "verdict": "fraud",
        "fraud_probability": 0.86,
        "pattern": "card_testing",
        "pattern_description": "",
        "affected_txn_ids": ["T0412877", "T0412878", "T0412879", "T0412883"],
        "first_suspicious_txn_id": "T0412877",
        "connected_card_ids": ["C00877-K1"],
        "connected_device_profiles": [
            "SAMSUNG SM-G892A Build/NRD90M | Android 7.0 | samsung browser 6.2 | 2220x1080"
        ],
        "exposure_usd": 268.43,
        "evidence": [
            {
                "claim": "Three online authorizations under $3 within 40 minutes, then a $259 purchase under a product code this card has never used",
                "source": "graph",
                "ref": "query:card_window(card_id=C00377-K1, hours=2)",
                "entity_ids": ["T0412877", "T0412878", "T0412879", "T0412883"],
            },
            {
                "claim": "Customer denied the purchases when asked",
                "source": "customer",
                "ref": "evidence_request:1",
                "entity_ids": [],
            },
        ],
        "similar_prior_cases": ["CC-0141"],
        "summary": "Textbook card testing.",
        "written_to_graph": True,
        "graph_case_id": "CASE-2016-1187",
    },
    "evidence_requests": [
        {
            "type": "customer_validation",
            "asked_after_step": 4,
            "assumed_response": "Customer states they did not make these purchases and still has the card",
        }
    ],
    "next_best_actions": {
        "initial": [
            {"action": "DECLINE_TRANSACTION", "route": "L1", "reason": "R5: testing sequence observed, purchase already cleared"},
            {"action": "VERIFY_WITH_CUSTOMER", "route": "auto", "reason": "R1: probability 0.72 on pattern alone, confirm before blocking"},
        ],
        "final": [
            {"action": "BLOCK_CARD", "route": "L1", "reason": "R2 and R5: customer denied; exposure $268 is under $2,500"},
            {"action": "CREATE_CASE", "route": "auto", "reason": "R2"},
            {"action": "FILE_REPORT", "route": "L2", "reason": "R2: shared device links this to another compromised card"},
            {"action": "MONITOR_CONNECTED_CARDS", "route": "auto", "reason": "Same device profile also used on C00877-K1"},
        ],
        "what_changed": "Customer denial raised probability from 0.72 to 0.86 and confirmed the block.",
    },
    "sar": {
        "file": True,
        "reason": "R2: confirmed unauthorized use linked by a shared device to a second compromised card",
        "narrative": "On 2016-11-14 ... Total unauthorized amount: $268.43.",
        "subjects": ["C00377", "C00377-K1", "C00877-K1"],
        "total_amount_usd": 268.43,
        "activity_dates": ["2016-11-14", "2016-11-14"],
    },
    "stop_reason": "Customer denial settled the verdict.",
    "tool_calls": 9,
    "tokens": 12480,
    "latency_s": 18.7,
}


def test_readme_example_parses():
    answer = AnswerFile.model_validate(README_EXAMPLE)
    assert answer.case_id == "HHG-017"
    assert answer.case.pattern == "card_testing"
    assert answer.next_best_actions.final[0].action == "BLOCK_CARD"
    assert answer.sar.file is True


def test_round_trip_json():
    answer = AnswerFile.model_validate(README_EXAMPLE)
    dumped = json.loads(answer.model_dump_json())
    reparsed = AnswerFile.model_validate(dumped)
    assert reparsed.case_id == answer.case_id
    assert reparsed.case.exposure_usd == answer.case.exposure_usd


def test_legitimate_verdict_shape():
    minimal = {
        **README_EXAMPLE,
        "case": {
            **README_EXAMPLE["case"],
            "verdict": "legitimate",
            "status": "closed_legitimate",
            "affected_txn_ids": [],
            "exposure_usd": 0,
        },
        "sar": {
            "file": False, "reason": "No fraud found.", "narrative": "",
            "subjects": [], "total_amount_usd": 0, "activity_dates": [],
        },
    }
    answer = AnswerFile.model_validate(minimal)
    assert answer.case.affected_txn_ids == []
    assert answer.sar.file is False
