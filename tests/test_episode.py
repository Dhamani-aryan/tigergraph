from __future__ import annotations

from src.agent.episode import (
    build_episode,
    cluster_burst_around,
    detect_card_testing,
    detect_out_of_region,
    detect_recurring_charge,
)


def _txn(id_, ts, amount, channel="online", addr1="204.0"):
    return {"id": id_, "ts": ts, "TransactionAmt": amount, "channel": channel, "addr1": addr1}


def test_detect_card_testing_finds_three_small_auths_then_a_purchase():
    window = [
        _txn("T1", "2016-11-11 22:00:00", 1.10),
        _txn("T2", "2016-11-11 22:20:00", 2.40),
        _txn("T3", "2016-11-11 22:40:00", 0.95),
        _txn("T4", "2016-11-11 23:10:00", 259.98),
    ]
    result = detect_card_testing(window)
    assert result == ["T1", "T2", "T3", "T4"]


def test_detect_card_testing_returns_none_when_small_auths_spread_too_wide():
    window = [
        _txn("T1", "2016-11-11 20:00:00", 1.10),
        _txn("T2", "2016-11-11 22:20:00", 2.40),  # >1h after T1
        _txn("T3", "2016-11-11 22:40:00", 0.95),
        _txn("T4", "2016-11-11 23:10:00", 259.98),
    ]
    assert detect_card_testing(window) is None


def test_detect_card_testing_returns_none_without_a_followup_purchase():
    window = [
        _txn("T1", "2016-11-11 22:00:00", 1.10),
        _txn("T2", "2016-11-11 22:20:00", 2.40),
        _txn("T3", "2016-11-11 22:40:00", 0.95),
        # no larger purchase follows
    ]
    assert detect_card_testing(window) is None


def test_detect_card_testing_ignores_in_person_small_transactions():
    window = [
        _txn("T1", "2016-11-11 22:00:00", 1.10, channel="in_person"),
        _txn("T2", "2016-11-11 22:20:00", 2.40, channel="in_person"),
        _txn("T3", "2016-11-11 22:40:00", 0.95, channel="in_person"),
        _txn("T4", "2016-11-11 23:10:00", 259.98),
    ]
    assert detect_card_testing(window) is None


def test_cluster_burst_around_includes_flagged_and_nearby_online_txns():
    window = [
        _txn("T1", "2016-11-10 12:00:00", 50.0),
        _txn("T2", "2016-11-11 23:46:24", 100.09),  # flagged
        _txn("T3", "2016-11-12 10:00:00", 80.0),
        _txn("T4", "2016-12-25 08:00:00", 40.0),  # 44 days later, out of window
    ]
    result = cluster_burst_around(window, "T2")
    assert set(result) == {"T1", "T2", "T3"}
    assert "T4" not in result


def test_cluster_burst_around_caps_at_four_nearest():
    window = [_txn("T2", "2016-11-11 23:46:24", 100.0)] + [
        _txn(f"T{i}", f"2016-11-11 {20+i}:00:00", 10.0 * i) for i in range(1, 6)
    ]
    result = cluster_burst_around(window, "T2")
    assert len(result) <= 4
    assert "T2" in result


def test_cluster_burst_around_missing_flagged_txn_returns_just_that_id():
    result = cluster_burst_around([_txn("T1", "2016-11-11 10:00:00", 5.0)], "NOT_IN_WINDOW")
    assert result == ["NOT_IN_WINDOW"]


def test_detect_out_of_region_true_when_flagged_region_differs_with_home_activity():
    window = [
        _txn("HOME1", "2016-11-10 08:00:00", 20.0, addr1="100.0"),
        _txn("HOME2", "2016-11-11 08:00:00", 25.0, addr1="100.0"),
        _txn("FLAGGED", "2016-11-11 20:00:00", 90.0, addr1="999.0"),
    ]
    assert detect_out_of_region(window, "FLAGGED") is True


def test_detect_out_of_region_false_when_flagged_matches_home_region():
    window = [
        _txn("HOME1", "2016-11-10 08:00:00", 20.0, addr1="100.0"),
        _txn("FLAGGED", "2016-11-11 20:00:00", 90.0, addr1="100.0"),
    ]
    assert detect_out_of_region(window, "FLAGGED") is False


def test_detect_out_of_region_false_with_no_other_history():
    window = [_txn("FLAGGED", "2016-11-11 20:00:00", 90.0, addr1="999.0")]
    assert detect_out_of_region(window, "FLAGGED") is False


def test_detect_recurring_charge_true_for_same_amount_about_a_month_earlier():
    window = [
        _txn("PRIOR", "2016-10-12 09:00:00", 49.99),
        _txn("FLAGGED", "2016-11-11 09:00:00", 50.10),  # within $0.50, 30 days later
    ]
    assert detect_recurring_charge(window, "FLAGGED") is True


def test_detect_recurring_charge_false_when_amount_differs_too_much():
    window = [
        _txn("PRIOR", "2016-10-12 09:00:00", 20.00),
        _txn("FLAGGED", "2016-11-11 09:00:00", 50.10),
    ]
    assert detect_recurring_charge(window, "FLAGGED") is False


def test_detect_recurring_charge_false_when_gap_is_not_monthly():
    window = [
        _txn("PRIOR", "2016-11-05 09:00:00", 50.00),  # only 6 days earlier
        _txn("FLAGGED", "2016-11-11 09:00:00", 50.10),
    ]
    assert detect_recurring_charge(window, "FLAGGED") is False


def test_build_episode_prefers_card_testing_when_flagged_txn_is_in_it():
    window = [
        _txn("T1", "2016-11-11 22:00:00", 1.10),
        _txn("T2", "2016-11-11 22:20:00", 2.40),
        _txn("T3", "2016-11-11 22:40:00", 0.95),
        _txn("FLAGGED", "2016-11-11 23:10:00", 259.98),
    ]
    episode = build_episode(window, "FLAGGED")
    assert episode.detected_pattern == "card_testing"
    assert set(episode.txn_ids) == {"T1", "T2", "T3", "FLAGGED"}
    assert episode.first_txn_id == "T1"
    assert episode.exposure_usd == round(1.10 + 2.40 + 0.95 + 259.98, 2)


def test_build_episode_falls_back_to_burst_when_flagged_not_in_card_testing_sequence():
    # A card-testing sequence exists elsewhere on the card, but the FLAGGED
    # transaction (this case's actual alert) is unrelated to it -- the
    # episode must be built around the flagged transaction, not hijacked by
    # an unrelated sequence sharing the same card.
    window = [
        _txn("T1", "2016-09-01 22:00:00", 1.10),
        _txn("T2", "2016-09-01 22:20:00", 2.40),
        _txn("T3", "2016-09-01 22:40:00", 0.95),
        _txn("T4", "2016-09-01 23:10:00", 259.98),
        _txn("FLAGGED", "2016-11-11 23:46:24", 100.09),
    ]
    episode = build_episode(window, "FLAGGED")
    assert episode.detected_pattern is None
    assert episode.txn_ids == ["FLAGGED"]
    assert episode.first_txn_id == "FLAGGED"
    assert episode.exposure_usd == 100.09


def test_build_episode_single_txn_when_no_burst_and_no_card_testing():
    window = [_txn("FLAGGED", "2016-11-11 23:46:24", 100.09)]
    episode = build_episode(window, "FLAGGED")
    assert episode.txn_ids == ["FLAGGED"]
    assert episode.exposure_usd == 100.09
    assert episode.detected_pattern is None
