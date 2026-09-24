from __future__ import annotations

from datetime import datetime, timedelta

from src.agent.episode import build_episode
from src.agent.features import (
    FAMILY_BEHAVIORAL,
    FAMILY_GEOGRAPHIC,
    FAMILY_IDENTITY,
    FAMILY_NETWORK,
    FAMILY_TEMPORAL,
    compute_behavior_profile,
    compute_evidence_families,
    detect_card_testing,
    detect_cnp_burst,
    detect_out_of_region,
    detect_recurring_charge,
    evaluate_device_network,
)

BASE = datetime(2016, 12, 1, 12, 0, 0)
CUTOFF = "2016-12-01 15:00:00"


def _ts(delta_hours: float) -> str:
    return (BASE + timedelta(hours=delta_hours)).strftime("%Y-%m-%d %H:%M:%S")


def txn(id_, hours, amount, channel="online", product="C", addr1=None, id_15=None, id_23=None, risk=0.1):
    return {
        "id": id_, "ts": _ts(hours), "TransactionAmt": amount, "channel": channel, "ProductCD": product,
        "addr1": addr1, "id_15": id_15, "id_23": id_23, "risk_score": risk,
    }


def history(n=30, channel="in_person", product="W", addr1="100.0", amount=40.0, step_hours=40):
    """`n` ordinary prior transactions, oldest first, the newest 72h before BASE
    (outside every 48h window)."""
    rows = []
    for i in range(n):
        hours = -72 - (n - 1 - i) * step_hours
        rows.append(txn(f"H{i}", hours, amount + (i % 5), channel=channel, product=product, addr1=addr1))
    return rows


def _all_signals(window, flagged, cutoff=CUTOFF):
    profile = compute_behavior_profile(window, flagged, cutoff)
    ct = detect_card_testing(window, flagged, cutoff)
    cnp = detect_cnp_burst(window, flagged, cutoff, profile.baseline_online_per_48h)
    region = detect_out_of_region(window, flagged, cutoff, profile)
    return profile, ct, cnp, region


# -- 1. In-person rows never enter CNP episodes ----------------------------
def test_in_person_rows_never_enter_cnp_episode():
    window = history(30, channel="online", product="C", addr1=None, step_hours=48) + [
        txn("P1", -3, 25.0, channel="in_person", product="W", addr1="100.0"),
        txn("O1", -2, 60.0),
        txn("P2", -1, 30.0, channel="in_person", product="W", addr1="100.0"),
        txn("F", 0, 70.0),
    ]
    _, ct, cnp, region = _all_signals(window, "F")
    assert cnp.online_txn_ids == ["O1", "F"]
    assert set(cnp.excluded_in_person_ids) == {"P1", "P2"}
    episode = build_episode(window, "F", CUTOFF, card_testing=ct, cnp=cnp, region=region)
    assert "P1" not in episode.txn_ids and "P2" not in episode.txn_ids


# -- 2. Online flagged + only in-person neighbours is not a CNP burst -------
def test_online_flagged_with_only_in_person_neighbours_is_not_a_burst():
    window = [
        txn("P1", -5, 20.0, channel="in_person", product="W", addr1="100.0"),
        txn("P2", -4, 20.0, channel="in_person", product="W", addr1="100.0"),
        txn("P3", -1, 20.0, channel="in_person", product="W", addr1="100.0"),
        txn("F", 0, 80.0),
    ]
    cnp = detect_cnp_burst(window, "F", CUTOFF)
    assert cnp.online_count == 1
    assert not cnp.documented_burst
    assert not cnp.temporal_signal


def test_in_person_flagged_never_fires_cnp():
    window = [txn("O1", -2, 50.0), txn("O2", -1, 50.0), txn("F", 0, 50.0, channel="in_person", product="W", addr1="1")]
    cnp = detect_cnp_burst(window, "F", CUTOFF)
    assert not cnp.flagged_online and not cnp.documented_burst and cnp.online_txn_ids == []


# -- 3. >4 online txns: not truncated, not relabelled as the documented burst
def test_complete_cnp_set_larger_than_four_is_not_truncated_or_mislabelled():
    window = [txn(f"O{i}", -i * 5, 30.0 + i) for i in range(1, 6)] + [txn("F", 0, 90.0)]
    profile, ct, cnp, region = _all_signals(window, "F")
    assert cnp.online_count == 6
    assert len(cnp.online_txn_ids) == 6
    assert cnp.documented_burst is False
    assert cnp.high_volume_online is True
    # no baseline evidence (short history) -> not a temporal family either
    assert cnp.temporal_signal is False
    episode = build_episode(window, "F", CUTOFF, card_testing=ct, cnp=cnp, region=region)
    assert episode.txn_ids == ["F"]


def test_documented_burst_routine_for_card_baseline_is_not_temporal():
    # ~2 online per 48h for two months: a 3-transaction window is routine.
    window = history(60, channel="online", product="C", addr1=None, step_hours=24) + [
        txn("O1", -3, 40.0), txn("O2", -2, 41.0), txn("F", 0, 42.0),
    ]
    profile = compute_behavior_profile(window, "F", CUTOFF)
    cnp = detect_cnp_burst(window, "F", CUTOFF, profile.baseline_online_per_48h)
    assert cnp.documented_burst is True
    assert cnp.exceeds_baseline is False
    assert cnp.temporal_signal is False


# -- 4/5. Card testing -------------------------------------------------------
def test_exact_card_testing_sequence_fires_with_ids_and_timestamps():
    window = [
        txn("T1", -1.0, 1.10), txn("T2", -0.7, 2.40), txn("T3", -0.4, 0.95), txn("F", 0, 259.98),
    ]
    ct = detect_card_testing(window, "F", CUTOFF)
    assert ct.fired
    assert ct.txn_ids == ["T1", "T2", "T3", "F"]
    assert ct.timestamps[0] == _ts(-1.0)
    assert ct.purchase_over_100_cleared is True
    episode = build_episode(window, "F", CUTOFF, card_testing=ct)
    assert episode.detected_pattern == "card_testing"
    assert episode.exposure_usd == round(1.10 + 2.40 + 0.95 + 259.98, 2)


def test_card_testing_larger_purchase_must_be_online():
    window = [
        txn("T1", -1.0, 1.10), txn("T2", -0.7, 2.40), txn("T3", -0.4, 0.95),
        txn("F", 0, 259.98, channel="in_person", product="W", addr1="100.0"),
    ]
    assert not detect_card_testing(window, "F", CUTOFF).fired


def test_unrelated_card_testing_sequence_is_not_attached_to_the_case():
    window = [
        txn("T1", -900, 1.10), txn("T2", -899.8, 2.40), txn("T3", -899.6, 0.95), txn("T4", -899, 60.0),
        txn("F", 0, 100.09),
    ]
    ct = detect_card_testing(window, "F", CUTOFF)
    assert not ct.fired
    assert build_episode(window, "F", CUTOFF, card_testing=ct).txn_ids == ["F"]


def test_card_testing_small_auths_spread_over_more_than_an_hour_do_not_fire():
    window = [txn("T1", -3.0, 1.10), txn("T2", -1.0, 2.40), txn("T3", -0.4, 0.95), txn("F", 0, 60.0)]
    assert not detect_card_testing(window, "F", CUTOFF).fired


# -- 6/7/9. Strict out-of-region ---------------------------------------------
def test_online_transaction_never_fires_out_of_region():
    window = history(30) + [txn("HOME", -5, 30.0, channel="in_person", product="W", addr1="100.0"),
                            txn("F", 0, 90.0, addr1="999.0")]
    region = detect_out_of_region(window, "F", CUTOFF)
    assert not region.fired
    assert "card-present" in region.reason


def test_previously_seen_region_never_fires_out_of_region():
    window = history(30) + [
        txn("OLD", -40 * 24, 20.0, channel="in_person", product="W", addr1="999.0"),
        txn("HOME", -5, 30.0, channel="in_person", product="W", addr1="100.0"),
        txn("F", 0, 90.0, channel="in_person", product="W", addr1="999.0"),
    ]
    region = detect_out_of_region(window, "F", CUTOFF)
    assert not region.fired
    assert region.flagged_region_prior_count == 1
    assert "not a region the cardholder has no history in" in region.reason


def test_established_new_region_with_home_activity_fires_out_of_region():
    window = history(30) + [
        txn("HOME", -5, 30.0, channel="in_person", product="W", addr1="100.0"),
        txn("F", 0, 90.0, channel="in_person", product="W", addr1="999.0"),
    ]
    region = detect_out_of_region(window, "F", CUTOFF)
    assert region.fired, region.reason
    assert region.home_region == "100.0"
    assert region.flagged_region_prior_count == 0
    assert "HOME" in region.evidence_ids


def test_out_of_region_requires_home_activity_within_48h():
    window = history(30) + [txn("F", 0, 90.0, channel="in_person", product="W", addr1="999.0")]
    region = detect_out_of_region(window, "F", CUTOFF)
    assert not region.fired
    assert "within 48h" in region.reason


def test_several_days_away_from_home_is_a_trip_not_out_of_region():
    window = history(30) + [
        txn("HOME", -80, 30.0, channel="in_person", product="W", addr1="100.0"),
        txn("AWAY1", -70, 30.0, channel="in_person", product="W", addr1="555.0"),
        txn("AWAY2", -45, 30.0, channel="in_person", product="W", addr1="555.0"),
        txn("AWAY3", -20, 30.0, channel="in_person", product="W", addr1="555.0"),
        txn("HOME2", -1, 30.0, channel="in_person", product="W", addr1="100.0"),
        txn("F", 0, 90.0, channel="in_person", product="W", addr1="999.0"),
    ]
    # home activity at -1h is interleaved, so no trip -> but AWAY is a previously seen
    # non-home region; the flagged region 999 is new and home continues -> fires.
    assert detect_out_of_region(window, "F", CUTOFF).fired
    trip = [r for r in window if r["id"] != "HOME2"]
    trip.append(txn("HOME2", 20, 30.0, channel="in_person", product="W", addr1="100.0"))  # after cutoff: invisible
    region = detect_out_of_region(trip, "F", CUTOFF)
    assert not region.fired


# -- 8. Missing region/channel never becomes a fraud signal -----------------
def test_missing_region_and_channel_do_not_fire_fraud_signals():
    window = history(30) + [
        txn("X1", -2, 50.0, channel=None, product=None, addr1=None),
        txn("F", 0, 90.0, channel="", product=None, addr1=None),
    ]
    profile, ct, cnp, region = _all_signals(window, "F")
    assert profile.flagged_channel is None
    assert not cnp.flagged_online and not cnp.documented_burst
    assert not region.fired
    assert profile.product_class == "unavailable"
    fams = compute_evidence_families(profile, ct, cnp, region, evaluate_device_network({}, "C1-K1", profile.flagged_ts, CUTOFF))
    assert FAMILY_GEOGRAPHIC not in fams.suspicious
    assert FAMILY_TEMPORAL not in fams.suspicious
    assert FAMILY_IDENTITY not in fams.suspicious


def test_short_history_makes_amount_and_product_unavailable_not_anomalous():
    window = [txn("H1", -100, 10.0), txn("F", 0, 900.0, product="R")]
    profile = compute_behavior_profile(window, "F", CUTOFF)
    assert profile.stable_history is False
    assert profile.amount_class == "unavailable"
    assert profile.product_class == "unavailable"


def test_profile_baseline_uses_only_strictly_prior_transactions():
    window = history(30, amount=10.0) + [txn("F", 0, 50.0, channel="in_person", product="W", addr1="100.0"),
                                         txn("AFTER", 1, 5000.0, channel="in_person", product="W", addr1="100.0")]
    profile = compute_behavior_profile(window, "F", CUTOFF)
    assert profile.history_count == 30
    assert profile.amount_median == 12.0
    assert profile.amount_class == "extreme"


# -- 10/11. Recurrence -------------------------------------------------------
def _recurring_history(n, amount=20.0):
    return [txn(f"R{i}", -24 * (120 - i * 2), amount + i * 3.1) for i in range(n)]


def test_amount_only_monthly_coincidence_is_not_strong_recurrence():
    # A same-amount charge 30 days earlier, but on a different ProductCD.
    window = _recurring_history(40) + [
        txn("PRIOR", -30 * 24, 49.99, product="H"),
        txn("F", 0, 50.10, product="C"),
    ]
    assert detect_recurring_charge(window, "F", CUTOFF).tier == "none"


def test_collision_prone_amount_band_is_only_a_candidate():
    window = [txn(f"B{i}", -24 * (100 - i * 2.5), 50.0) for i in range(30)]
    window += [txn("PRIOR", -30 * 24, 50.00), txn("F", 0, 50.10)]
    res = detect_recurring_charge(window, "F", CUTOFF)
    assert res.tier == "candidate"


def test_rare_monthly_same_channel_product_amount_match_is_strong():
    window = _recurring_history(40) + [txn("PRIOR", -30 * 24, 12.99), txn("F", 0, 12.99)]
    res = detect_recurring_charge(window, "F", CUTOFF)
    assert res.tier == "strong", res.reason
    assert res.monthly_match_ids == ["PRIOR"]
    assert "no merchant-name column" in res.proxy_note


# -- 12-15. Direct device evidence -------------------------------------------
def _device(total, txns, fraud_cases=None, flagged_amount=99.92):
    return {
        "device_profile_id": "Dabc", "device_profile_label": "X | Android | Chrome | 1080x1920",
        "total_distinct_cards": total, "txns": txns, "fraud_cases": fraud_cases or [], "flagged_amount": flagged_amount,
    }


def _dtx(txn_id, card, hours, amount, risk=0.2):
    return {"txn_id": txn_id, "card_id": card, "customer_id": card.split("-")[0], "ts": _ts(hours), "amount": amount, "risk_score": risk}


def test_subject_card_excluded_from_device_neighbour_count():
    data = _device(1, [_dtx("F", "C1-K1", 0, 99.92)])
    res = evaluate_device_network(data, "C1-K1", _ts(0), CUTOFF)
    assert res.other_card_count == 0
    assert res.other_cards_48h == []
    assert not res.meaningful_match and not res.corroborated


def test_generic_profile_with_more_than_twenty_cards_is_context_only():
    txns = [_dtx(f"T{i}", f"C{i}-K1", -1, 99.9, risk=0.95) for i in range(2, 6)]
    res = evaluate_device_network(_device(44, txns), "C1-K1", _ts(0), CUTOFF)
    assert res.generic_profile
    assert not res.meaningful_match and not res.corroborated
    assert res.corroborated_card_ids == []


def test_old_device_sharing_outside_48h_is_not_r6_evidence():
    txns = [_dtx("T2", "C2-K1", -24 * 10, 99.92, 0.9), _dtx("T3", "C3-K1", -24 * 9, 99.95, 0.9)]
    res = evaluate_device_network(_device(3, txns), "C1-K1", _ts(0), CUTOFF)
    assert not res.meaningful_match and not res.corroborated


def test_low_risk_neighbours_on_a_small_profile_are_only_a_candidate():
    txns = [_dtx("T2", "C2-K1", -5, 20.0), _dtx("T3", "C3-K1", -30, 300.0)]
    res = evaluate_device_network(_device(3, txns), "C1-K1", _ts(0), CUTOFF)
    assert res.meaningful_match and not res.corroborated


def test_direct_coordinated_device_activity_is_corroborated_network_evidence():
    txns = [_dtx("T2", "C2-K1", -40, 100.06, 0.71), _dtx("T3", "C3-K1", -3, 100.00, 0.91)]
    res = evaluate_device_network(_device(3, txns), "C1-K1", _ts(0), CUTOFF)
    assert res.corroborated
    assert res.corroborated_card_ids == ["C2-K1", "C3-K1"]
    assert set(res.corroborating_txn_ids) == {"T2", "T3"}


def test_confirmed_fraud_on_other_card_same_device_is_corroborated():
    txns = [_dtx("T2", "C2-K1", -5, 20.0)]
    fraud = [{"case_id": "CC-1", "outcome": "confirmed_fraud", "txn_id": "T9", "txn_ts": _ts(-24 * 10), "card_id": "C2-K1"}]
    res = evaluate_device_network(_device(2, txns, fraud), "C1-K1", _ts(0), CUTOFF)
    assert res.corroborated and res.confirmed_fraud_case_ids == ["CC-1"]


def test_post_cutoff_device_activity_is_ignored():
    txns = [_dtx("T2", "C2-K1", 5, 99.92, 0.9), _dtx("T3", "C3-K1", 6, 99.92, 0.9)]
    res = evaluate_device_network(_device(3, txns), "C1-K1", _ts(0), CUTOFF)
    assert not res.meaningful_match


# -- 21. The risk score is never an evidence family --------------------------
def test_risk_score_is_not_an_independent_evidence_family():
    window = history(30) + [txn("F", 0, 41.0, channel="in_person", product="W", addr1="100.0", risk=0.99)]
    profile, ct, cnp, region = _all_signals(window, "F")
    fams = compute_evidence_families(profile, ct, cnp, region, evaluate_device_network({}, "C1-K1", profile.flagged_ts, CUTOFF))
    assert fams.suspicious == []
    assert fams.single_signal is True
    assert profile.risk_score_context_only == 0.99


def test_new_device_and_proxy_count_as_one_identity_family():
    window = history(30, channel="online", product="C", addr1=None, step_hours=48) + [
        txn("F", 0, 41.0, id_15="New", id_23="IP_PROXY:ANONYMOUS")
    ]
    profile, ct, cnp, region = _all_signals(window, "F")
    fams = compute_evidence_families(profile, ct, cnp, region, evaluate_device_network({}, "C1-K1", profile.flagged_ts, CUTOFF))
    assert fams.suspicious == [FAMILY_IDENTITY]
    assert fams.single_signal is True


def test_extreme_amount_plus_new_device_is_two_families_and_strong():
    window = history(30, channel="online", product="C", addr1=None, step_hours=48, amount=20.0) + [
        txn("F", 0, 900.0, id_15="New")
    ]
    profile, ct, cnp, region = _all_signals(window, "F")
    net = evaluate_device_network({}, "C1-K1", profile.flagged_ts, CUTOFF)
    fams = compute_evidence_families(profile, ct, cnp, region, net)
    assert set(fams.suspicious) == {FAMILY_BEHAVIORAL, FAMILY_IDENTITY}
    assert "extreme_amount_with_identity_anomaly" in fams.strong_suspicious


def test_corroborated_network_is_a_family():
    window = [txn("F", 0, 99.92)]
    profile, ct, cnp, region = _all_signals(window, "F")
    net = evaluate_device_network(
        _device(3, [_dtx("T2", "C2-K1", -40, 100.06, 0.71), _dtx("T3", "C3-K1", -3, 100.0, 0.91)]),
        "C1-K1", profile.flagged_ts, CUTOFF,
    )
    fams = compute_evidence_families(profile, ct, cnp, region, net)
    assert FAMILY_NETWORK in fams.suspicious
