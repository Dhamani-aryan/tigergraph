import csv
from unittest.mock import AsyncMock

import pytest

from src.schema.derive_entities import (
    device_id_for,
    load_closed_case_multi_edges,
    load_device_profiles,
)


def test_device_id_for_is_deterministic_and_prefixed():
    d1 = device_id_for("SAMSUNG SM-G892A", "Android 7.0", "samsung browser 6.2", "2220x1080")
    d2 = device_id_for("SAMSUNG SM-G892A", "Android 7.0", "samsung browser 6.2", "2220x1080")
    assert d1 == d2
    assert d1.startswith("D")


def test_device_id_for_differs_on_any_field_change():
    base = device_id_for("A", "B", "C", "D")
    assert device_id_for("A", "B", "C", "E") != base
    assert device_id_for("X", "B", "C", "D") != base


@pytest.mark.asyncio
async def test_load_device_profiles_skips_rows_with_no_device_signal(tmp_path):
    csv_path = tmp_path / "identity.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["TransactionID"] + [f"id_{i:02d}" for i in range(1, 39)] + ["DeviceType", "DeviceInfo"]
        )
        # Row 1: has DeviceInfo -- should be loaded.
        row1 = ["3450629"] + [""] * 38 + ["mobile", "SAMSUNG SM-G892A"]
        # Row 2: blank DeviceInfo/id_30/id_31 -- nothing to link, should be skipped.
        row2 = ["3450630"] + [""] * 38 + ["", ""]
        writer.writerow(row1)
        writer.writerow(row2)

    tg = AsyncMock()
    inserted = await load_device_profiles(tg, str(csv_path))

    assert inserted == 1
    add_nodes_call = tg.call.await_args_list[0]
    assert add_nodes_call.args[0] == "tigergraph__add_nodes"
    assert add_nodes_call.args[1]["vertex_type"] == "DeviceProfile"
    assert add_nodes_call.args[1]["vertices"][0]["device_info"] == "SAMSUNG SM-G892A"

    add_edges_call = tg.call.await_args_list[1]
    assert add_edges_call.args[0] == "tigergraph__add_edges"
    assert add_edges_call.args[1]["edge_type"] == "FROM_DEVICE"
    edge = add_edges_call.args[1]["edges"][0]
    assert edge["source_type"] == "Transaction"
    assert edge["source_id"] == "3450629"
    assert edge["target_type"] == "DeviceProfile"


@pytest.mark.asyncio
async def test_load_closed_case_multi_edges_never_mixes_edge_types_in_one_batch(tmp_path):
    csv_path = tmp_path / "closed_cases_history.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["case_id", "txn_ids", "connected_card_ids"])
        writer.writerow(["HHG-017", "3450629|3450630", "C04570-K2"])
        writer.writerow(["CC-0002", "", "C00001-K1|C00002-K1"])

    tg = AsyncMock()
    await load_closed_case_multi_edges(tg, str(csv_path))

    for call in tg.call.await_args_list:
        tool_name, payload = call.args
        assert tool_name == "tigergraph__add_edges"
        edge_type = payload["edge_type"]
        assert edge_type in ("INVOLVES", "CONNECTED_TO")
        for edge in payload["edges"]:
            if edge_type == "INVOLVES":
                assert edge["source_type"] == "ClosedCase"
                assert edge["target_type"] == "Transaction"
            else:
                assert edge["source_type"] == "ClosedCase"
                assert edge["target_type"] == "Card"

    involves_calls = [c for c in tg.call.await_args_list if c.args[1]["edge_type"] == "INVOLVES"]
    connected_calls = [c for c in tg.call.await_args_list if c.args[1]["edge_type"] == "CONNECTED_TO"]
    all_involves_edges = [e for c in involves_calls for e in c.args[1]["edges"]]
    all_connected_edges = [e for c in connected_calls for e in c.args[1]["edges"]]
    assert len(all_involves_edges) == 2
    assert len(all_connected_edges) == 3
