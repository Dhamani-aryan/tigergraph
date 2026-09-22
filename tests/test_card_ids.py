import pandas as pd

from src.schema.card_ids import build_card_id_map, card_id_for


def _case_pack_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"case_id": "HHG-003", "card_id": "C08623-K2", "customer_id": "C08623"},
            {"case_id": "HHG-007", "card_id": "C09933-K2", "customer_id": "C09933"},
        ]
    )


def _closed_cases_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "case_id": "CC-2649",
                "customer_id": "C03528",
                "card_id": "C03528-K1",
                "connected_card_ids": "C00255-K1|C01935-K1|C03551-K2",
            }
        ]
    )


def test_known_customers_use_referenced_suffix():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_map["C08623"] == "C08623-K2"
    assert card_map["C09933"] == "C09933-K2"
    assert card_map["C03528"] == "C03528-K1"


def test_connected_card_ids_are_parsed_into_the_map():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_map["C00255"] == "C00255-K1"
    assert card_map["C01935"] == "C01935-K1"
    assert card_map["C03551"] == "C03551-K2"


def test_unreferenced_customer_defaults_to_k1():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_id_for("C99999", card_map) == "C99999-K1"


def test_referenced_customer_uses_map_not_default():
    card_map = build_card_id_map(_case_pack_df(), _closed_cases_df())
    assert card_id_for("C08623", card_map) == "C08623-K2"
