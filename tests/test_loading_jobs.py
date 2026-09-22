from unittest.mock import AsyncMock

import pytest

from src.schema.loading_jobs import backfill_stub_card_customer_ids, customer_id_from_card_id


def test_customer_id_from_card_id_basic():
    assert customer_id_from_card_id("C08623-K2") == "C08623"
    assert customer_id_from_card_id("C02575-K1") == "C02575"


def test_customer_id_from_card_id_handles_double_digit_suffix():
    # The "-K<n>" convention doesn't cap n at a single digit; the split must
    # not assume exactly one digit follows "-K".
    assert customer_id_from_card_id("C00001-K12") == "C00001"


def test_customer_id_from_card_id_matches_card_ids_module_convention():
    # card_ids.py's build_card_id_map derives a customer_id from a
    # connected_card_ids entry via `card_id.split("-K")[0]` -- this function
    # must stay byte-for-byte consistent with that convention, since it's
    # patching vertices created from the very same card_id strings.
    from src.schema.card_ids import build_card_id_map
    import pandas as pd

    closed_cases_df = pd.DataFrame(
        [
            {
                "case_id": "CC-0001",
                "customer_id": "C99999",
                "card_id": "C99999-K1",
                "connected_card_ids": "C01234-K3",
            }
        ]
    )
    card_map = build_card_id_map(pd.DataFrame(columns=["card_id", "customer_id"]), closed_cases_df)
    assert customer_id_from_card_id(card_map["C01234"]) == "C01234"


@pytest.mark.asyncio
async def test_backfill_stub_card_customer_ids_patches_only_blank_ones():
    tg = AsyncMock()
    tg.call.return_value = {
        "data": {
            "vertices": [
                {"v_id": "C08623-K2", "v_type": "Card", "attributes": {"customer_id": "C08623"}},
                {"v_id": "C02575-K1", "v_type": "Card", "attributes": {"customer_id": ""}},
                {"v_id": "C09999-K3", "v_type": "Card", "attributes": {}},  # missing key entirely
            ]
        }
    }

    patched = await backfill_stub_card_customer_ids(tg)

    assert patched == ["C02575-K1", "C09999-K3"]
    assert tg.call.await_count == 3  # get_nodes, add_nodes, add_edges

    get_nodes_call, add_nodes_call, add_edges_call = tg.call.await_args_list
    assert get_nodes_call.args[0] == "tigergraph__get_nodes"

    assert add_nodes_call.args[0] == "tigergraph__add_nodes"
    added_vertices = add_nodes_call.args[1]["vertices"]
    assert {"card_id": "C02575-K1", "customer_id": "C02575"} in added_vertices
    assert {"card_id": "C09999-K3", "customer_id": "C09999"} in added_vertices
    assert len(added_vertices) == 2  # the already-populated C08623-K2 is untouched

    assert add_edges_call.args[0] == "tigergraph__add_edges"
    added_edges = add_edges_call.args[1]["edges"]
    assert {
        "source_type": "Customer",
        "source_id": "C02575",
        "target_type": "Card",
        "target_id": "C02575-K1",
    } in added_edges
    assert len(added_edges) == 2


@pytest.mark.asyncio
async def test_backfill_stub_card_customer_ids_is_a_noop_when_nothing_blank():
    tg = AsyncMock()
    tg.call.return_value = {
        "data": {
            "vertices": [
                {"v_id": "C08623-K2", "v_type": "Card", "attributes": {"customer_id": "C08623"}},
            ]
        }
    }

    patched = await backfill_stub_card_customer_ids(tg)

    assert patched == []
    # Only the read (get_nodes) call happens -- no write calls when there's
    # nothing to fix, which is what makes re-running this safe/idempotent
    # against an already-correct graph.
    tg.call.assert_awaited_once()
    assert tg.call.await_args.args[0] == "tigergraph__get_nodes"
