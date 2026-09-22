from __future__ import annotations

from src.graph_algorithms.connected_components import _group_touched_cards


def test_group_touched_cards_groups_by_label():
    touched = [
        {"v_id": "C002", "attributes": {"Cards.@minLabel": 5}},
        {"v_id": "C001", "attributes": {"Cards.@minLabel": 5}},
        {"v_id": "C003", "attributes": {"Cards.@minLabel": 9}},
    ]
    groups = _group_touched_cards(touched)
    assert groups == {
        "RING-C001": ["C001", "C002"],
        "RING-C003": ["C003"],
    }


def test_group_touched_cards_ring_id_is_lexicographically_smallest_member():
    touched = [
        {"v_id": "C999", "attributes": {"Cards.@minLabel": 1}},
        {"v_id": "C100", "attributes": {"Cards.@minLabel": 1}},
        {"v_id": "C500", "attributes": {"Cards.@minLabel": 1}},
    ]
    groups = _group_touched_cards(touched)
    assert list(groups.keys()) == ["RING-C100"]
    assert groups["RING-C100"] == ["C100", "C500", "C999"]


def test_group_touched_cards_empty_input():
    assert _group_touched_cards([]) == {}


def test_group_touched_cards_is_deterministic_regardless_of_input_order():
    touched_a = [
        {"v_id": "C003", "attributes": {"Cards.@minLabel": 42}},
        {"v_id": "C001", "attributes": {"Cards.@minLabel": 42}},
    ]
    touched_b = list(reversed(touched_a))
    assert _group_touched_cards(touched_a) == _group_touched_cards(touched_b)
