from __future__ import annotations

import pandas as pd


def build_card_id_map(
    case_pack_df: pd.DataFrame, closed_cases_df: pd.DataFrame
) -> dict[str, str]:
    """Map customer_id -> card_id, trusting the -K suffix given in the reference
    files. card1 is 1:1 with customer_id across the whole transactions.csv (verified
    empirically), so there is no second distinguishable card to derive from raw
    transaction data; the -K suffix is per-customer labeling assigned in the
    reference data, not evidence of multiple physical cards.
    """
    card_map: dict[str, str] = {}

    for _, row in case_pack_df.iterrows():
        card_map[row["customer_id"]] = row["card_id"]

    for _, row in closed_cases_df.iterrows():
        card_map[row["customer_id"]] = row["card_id"]
        connected = row.get("connected_card_ids")
        if isinstance(connected, str) and connected:
            for card_id in connected.split("|"):
                customer_id = card_id.split("-K")[0]
                card_map.setdefault(customer_id, card_id)

    return card_map


def card_id_for(customer_id: str, card_map: dict[str, str]) -> str:
    return card_map.get(customer_id, f"{customer_id}-K1")
