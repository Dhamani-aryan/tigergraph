import pytest

from src.graph.queries import (
    card_window,
    closed_case_lookup,
    customer_cards,
    device_neighbors,
    dispatch_followup_tool,
    region_neighbors,
    ring_membership,
)
from src.graph.vector_search import retrieve_knowledge
from src.tg_client import TigerGraphMCP

# Known-good live fixtures (from Task 8's manual checkpoint on HHG-017 and
# Task 8.5's ring spot check -- see docs/manual-case-checkpoint.md and
# task-8.5-report.md):
KNOWN_CARD = "C04570-K1"
KNOWN_CUSTOMER = "C04570"
KNOWN_TXN = "3450629"  # HHG-017's flagged transaction, on KNOWN_CARD
KNOWN_PRIOR_CASE = "CC-1383"  # cleared closed case on KNOWN_CARD
KNOWN_REGION = "204.0"  # billing region shared by KNOWN_TXN and several others
KNOWN_DEVICE = "Dec9ef04aa023"  # HHG-017's device fingerprint (299-customer collision)
KNOWN_RING_CARD = "C03528-K1"  # 24-member ring per Task 8.5's spot check

# Temporal cutoff fix (2026-09-23): card_window/device_neighbors/region_neighbors
# now require a `cutoff_ts`. Every existing test below that isn't specifically
# testing the cutoff itself passes this sentinel -- a date after every
# timestamp in the dataset (transactions.csv ends 2016-12-31 23:58:54) -- so it
# behaves exactly like "no cutoff", preserving each test's original intent.
NO_EFFECTIVE_CUTOFF = "2099-01-01 00:00:00"
# HHG-017's actual case-open time: 1 hour after KNOWN_TXN (2016-11-11 23:46:24).
HHG_017_OPENED_AT = "2016-11-12 00:46:24"


@pytest.mark.asyncio
async def test_card_window_returns_known_transaction():
    # NOTE: `hours` windows around the card's OWN most recent transaction
    # (see card_window's docstring -- the interface has no reference
    # timestamp to window around otherwise). C04570-K1's most recent
    # transaction is 2016-12-25, well after the KNOWN_TXN flagged
    # transaction (2016-11-11) -- confirmed live, this card has real
    # activity after the case's flagged transaction. So a *small* window
    # (e.g. 24h) legitimately does NOT include KNOWN_TXN; use a window wide
    # enough to span from 2016-12-25 back through 2016-11-11 instead of
    # assuming the flagged transaction is always the most recent one.
    async with TigerGraphMCP() as tg:
        result = await card_window(tg, KNOWN_CARD, hours=24 * 60, cutoff_ts=NO_EFFECTIVE_CUTOFF)
        assert result
        txn_ids = {t["id"] for t in result}
        assert KNOWN_TXN in txn_ids
        assert all("ts" in t and "TransactionAmt" in t for t in result)


@pytest.mark.asyncio
async def test_card_window_small_window_narrows_results():
    async with TigerGraphMCP() as tg:
        full_history = await card_window(tg, KNOWN_CARD, hours=100000, cutoff_ts=NO_EFFECTIVE_CUTOFF)
        narrow = await card_window(tg, KNOWN_CARD, hours=1, cutoff_ts=NO_EFFECTIVE_CUTOFF)
        assert len(narrow) < len(full_history)
        assert len(narrow) >= 1  # the card's own latest transaction always qualifies


@pytest.mark.asyncio
async def test_card_window_without_reference_excludes_flagged_txn_at_hours_48():
    # Reproduces task-10-review.md's Important finding directly: Task 12's
    # actual planned call site (gather_evidence_node) calls
    # card_window(tg, card_id, hours=48) with NO reference timestamp, and
    # for this exact fixture that silently excludes the flagged transaction
    # (KNOWN_TXN, 2016-11-11) because KNOWN_CARD has real activity 44 days
    # later (2016-12-25) that the old "anchor on latest" behavior windows
    # around instead. This test pins that fallback behavior down explicitly
    # so a future change can't silently "fix" it back to including the
    # flagged transaction without reference_txn_id -- the fix is the new
    # parameter below, not a change to the no-reference fallback.
    async with TigerGraphMCP() as tg:
        result = await card_window(tg, KNOWN_CARD, hours=48, cutoff_ts=NO_EFFECTIVE_CUTOFF)
        txn_ids = {t["id"] for t in result}
        assert KNOWN_TXN not in txn_ids


@pytest.mark.asyncio
async def test_card_window_with_reference_txn_id_anchors_on_reference_not_latest():
    # The actual fix: passing reference_txn_id=KNOWN_TXN must anchor the
    # +/-hours window on ITS timestamp (2016-11-11 23:46:24), not on the
    # card's unrelated most-recent transaction (2016-12-25). cutoff_ts is the
    # no-op sentinel here -- this test is about the reference anchor, not the
    # cutoff (see test_card_window_cutoff_ts_clips_window_below for that).
    async with TigerGraphMCP() as tg:
        result = await card_window(
            tg, KNOWN_CARD, hours=48, reference_txn_id=KNOWN_TXN, cutoff_ts=NO_EFFECTIVE_CUTOFF
        )
        txn_ids = {t["id"] for t in result}
        assert KNOWN_TXN in txn_ids
        # The two neighboring transactions from the manual checkpoint
        # (docs/manual-case-checkpoint.md: 3450436 @ 22:36:50 and 3450503 @
        # 22:58:57, both ~1-1.5h before KNOWN_TXN on the same evening) fall
        # inside a +/-48h window around it.
        assert "3450436" in txn_ids
        assert "3450503" in txn_ids
        # The unrelated 2016-12-25 transaction that the old "anchor on
        # latest" behavior centered on is 44 days away -- well outside a
        # 48h window around the reference -- and must NOT appear.
        assert "3573010" not in txn_ids


@pytest.mark.asyncio
async def test_card_window_unknown_reference_txn_id_falls_back_to_latest_anchor():
    async with TigerGraphMCP() as tg:
        fallback = await card_window(
            tg, KNOWN_CARD, hours=1, reference_txn_id="not-a-real-txn-id", cutoff_ts=NO_EFFECTIVE_CUTOFF
        )
        latest_only = await card_window(tg, KNOWN_CARD, hours=1, cutoff_ts=NO_EFFECTIVE_CUTOFF)
        assert {t["id"] for t in fallback} == {t["id"] for t in latest_only}


@pytest.mark.asyncio
async def test_card_window_cutoff_ts_never_returns_anything_after_cutoff():
    # The safety property the fix guarantees, checked directly against live
    # data rather than a hand-picked count: with reference_txn_id=KNOWN_TXN
    # and hours=48 (the real gather_evidence_node call shape), the nominal
    # window would extend to 2016-11-13 23:46:24 -- but no row in the result
    # may have a `ts` after HHG_017_OPENED_AT (2016-11-12 00:46:24, HHG-017's
    # real case-open time, 1h after KNOWN_TXN) when that's passed as
    # cutoff_ts. This dataset's own known-good fixture (KNOWN_CARD's next
    # transaction after KNOWN_TXN is 44 days later, per the manual checkpoint)
    # happens to have no activity in the 1h-48h gap either, so this doesn't
    # assert the result set shrinks -- only that the invariant holds, which
    # is the actual leakage guarantee.
    # test_device_neighbors_cutoff_ts_never_includes_post_cutoff_activity and
    # test_region_neighbors_cutoff_ts_never_returns_anything_after_cutoff
    # below cover the same invariant on fixtures with real after-cutoff
    # neighbor activity (the 299-card device collision, the 500-cap region).
    async with TigerGraphMCP() as tg:
        capped = await card_window(
            tg, KNOWN_CARD, hours=48, reference_txn_id=KNOWN_TXN, cutoff_ts=HHG_017_OPENED_AT
        )
        assert KNOWN_TXN in {t["id"] for t in capped}
        assert all(t["ts"] <= HHG_017_OPENED_AT for t in capped)


@pytest.mark.asyncio
async def test_card_window_full_history_has_59_transactions():
    async with TigerGraphMCP() as tg:
        # hours=100000 -- wide enough to not exclude anything -- confirms
        # the underlying MADE traversal itself returns the full,
        # independently-verified 59-transaction history for this card
        # (Task 8's manual checkpoint), separate from the Python-side
        # windowing logic tested above.
        result = await card_window(tg, KNOWN_CARD, hours=100000)
        assert len(result) == 59


@pytest.mark.asyncio
async def test_customer_cards_returns_known_card():
    async with TigerGraphMCP() as tg:
        result = await customer_cards(tg, KNOWN_CUSTOMER)
        assert result
        assert any(c["id"] == KNOWN_CARD for c in result)


@pytest.mark.asyncio
async def test_device_neighbors_matches_known_collision_count():
    async with TigerGraphMCP() as tg:
        result = await device_neighbors(tg, KNOWN_TXN, cutoff_ts=NO_EFFECTIVE_CUTOFF)
        assert result
        # Task 8's checkpoint found 299 distinct customers/cards sharing
        # this device; device_neighbors LIMITs shared_cards at 300.
        assert len(result) <= 300
        assert any(c["id"] == KNOWN_CARD for c in result)


@pytest.mark.asyncio
async def test_device_neighbors_cutoff_ts_never_includes_post_cutoff_activity():
    # Leakage fix: this device fingerprint is a known 299-customer generic
    # collision spanning the whole 6-month dataset (Task 8's checkpoint), so
    # restricting to on/before HHG-017's case-open time must not silently
    # keep counting cards whose ONLY shared-device activity is later than
    # that. Checked as a subset relationship (every capped card is also in
    # the uncapped set), which holds regardless of exactly how many cards
    # the cutoff removes.
    async with TigerGraphMCP() as tg:
        capped = await device_neighbors(tg, KNOWN_TXN, cutoff_ts=HHG_017_OPENED_AT)
        uncapped = await device_neighbors(tg, KNOWN_TXN, cutoff_ts=NO_EFFECTIVE_CUTOFF)
        capped_ids = {c["id"] for c in capped}
        uncapped_ids = {c["id"] for c in uncapped}
        assert KNOWN_CARD in capped_ids  # the flagged transaction's own card must survive
        assert capped_ids <= uncapped_ids
        assert len(capped_ids) <= len(uncapped_ids)


@pytest.mark.asyncio
async def test_region_neighbors_returns_transactions_for_known_region():
    async with TigerGraphMCP() as tg:
        result = await region_neighbors(tg, KNOWN_REGION, cutoff_ts=NO_EFFECTIVE_CUTOFF)
        assert result
        assert all("ts" in t and "TransactionAmt" in t for t in result)


@pytest.mark.asyncio
async def test_region_neighbors_time_window_narrows_results():
    async with TigerGraphMCP() as tg:
        unfiltered = await region_neighbors(tg, KNOWN_REGION, cutoff_ts=NO_EFFECTIVE_CUTOFF)
        windowed = await region_neighbors(
            tg, KNOWN_REGION, cutoff_ts=NO_EFFECTIVE_CUTOFF,
            txn_ts="2016-11-11 23:46:24", window_days=1,
        )
        assert len(windowed) <= len(unfiltered)


@pytest.mark.asyncio
async def test_region_neighbors_cutoff_ts_never_returns_anything_after_cutoff():
    # Leakage fix, and the previously-documented "LIMIT truncates before the
    # time filter" defect: KNOWN_REGION is known to hit the 500-row cap
    # unfiltered (confirmed live pre-fix), so the cutoff must be applied
    # inside the same GSQL WHERE clause as the LIMIT, not after it -- checked
    # directly rather than by count, since the cap can make counts alone
    # misleading.
    async with TigerGraphMCP() as tg:
        capped = await region_neighbors(tg, KNOWN_REGION, cutoff_ts=HHG_017_OPENED_AT)
        assert capped
        assert all(t["ts"] <= HHG_017_OPENED_AT for t in capped)


@pytest.mark.asyncio
async def test_closed_case_lookup_by_card_returns_known_prior_case():
    async with TigerGraphMCP() as tg:
        result = await closed_case_lookup(tg, card_id=KNOWN_CARD)
        assert result
        case_ids = {c["id"] for c in result}
        assert KNOWN_PRIOR_CASE in case_ids


@pytest.mark.asyncio
async def test_closed_case_lookup_by_device_returns_cases():
    async with TigerGraphMCP() as tg:
        result = await closed_case_lookup(tg, device_id=KNOWN_DEVICE)
        assert result


@pytest.mark.asyncio
async def test_closed_case_lookup_by_region_returns_cases():
    async with TigerGraphMCP() as tg:
        result = await closed_case_lookup(tg, addr1=KNOWN_REGION)
        assert result is not None


@pytest.mark.asyncio
async def test_closed_case_lookup_with_no_args_returns_empty():
    async with TigerGraphMCP() as tg:
        result = await closed_case_lookup(tg)
        assert result == []


@pytest.mark.asyncio
async def test_ring_membership_returns_cluster_from_known_ring():
    async with TigerGraphMCP() as tg:
        result = await ring_membership(tg, KNOWN_RING_CARD)
        assert result.get("ring_cluster_id") == "RING-C00001-K1"
        assert result.get("cluster_prior_fraud_rate") is not None


@pytest.mark.asyncio
async def test_ring_membership_singleton_card_has_own_ring():
    async with TigerGraphMCP() as tg:
        result = await ring_membership(tg, KNOWN_CARD)
        assert result.get("ring_cluster_id")


@pytest.mark.asyncio
async def test_retrieve_knowledge_returns_structured_candidates_live():
    # Task 14: structured TigerGraph retrieval (no embeddings, no vector search).
    shape = {"trigger_type": "risk_score", "channel": "online", "product": "C", "amount": 100.0,
             "amount_class": "normal", "is_new_device": True, "episode_size": 1, "card_testing": False,
             "network_corroborated": False, "candidate_patterns": ["card_not_present_new_device"]}
    async with TigerGraphMCP() as tg:
        result = await retrieve_knowledge(tg, shape)
        assert result["vector_search_used"] is False
        assert result["closed_case_catalog_size"] == 5565
        cands = result["closed_case_candidates"]
        assert cands["confirmed_fraud"] and cands["cleared"]
        assert all(c["id"].startswith("CC-") for c in cands["confirmed_fraud"] + cands["cleared"])
        assert any(d["id"] == "pattern-cnp-new-device" for d in result["knowledge_candidates"])


@pytest.mark.asyncio
async def test_dispatch_followup_tool_routes_to_correct_function():
    async with TigerGraphMCP() as tg:
        result = await dispatch_followup_tool(
            tg, "wider_card_window", {"card_id": KNOWN_CARD, "hours": 100000},
            cutoff_ts=NO_EFFECTIVE_CUTOFF,
        )
        assert result is not None
        assert len(result) == 59  # same full-history count as card_window's own test


@pytest.mark.asyncio
async def test_dispatch_followup_tool_wider_card_window_passes_through_reference_txn_id():
    async with TigerGraphMCP() as tg:
        result = await dispatch_followup_tool(
            tg,
            "wider_card_window",
            {"card_id": KNOWN_CARD, "hours": 48, "reference_txn_id": KNOWN_TXN},
            cutoff_ts=NO_EFFECTIVE_CUTOFF,
        )
        txn_ids = {t["id"] for t in result}
        assert KNOWN_TXN in txn_ids


@pytest.mark.asyncio
async def test_dispatch_followup_tool_wider_card_window_respects_cutoff_ts():
    # A follow-up call is still part of the same investigation -- it must
    # not be able to see past the case's own cutoff either.
    async with TigerGraphMCP() as tg:
        result = await dispatch_followup_tool(
            tg,
            "wider_card_window",
            {"card_id": KNOWN_CARD, "hours": 100000, "reference_txn_id": KNOWN_TXN},
            cutoff_ts=HHG_017_OPENED_AT,
        )
        assert all(t["ts"] <= HHG_017_OPENED_AT for t in result)


@pytest.mark.asyncio
async def test_dispatch_followup_tool_unknown_name_raises():
    async with TigerGraphMCP() as tg:
        with pytest.raises(ValueError, match="Unknown follow-up tool"):
            await dispatch_followup_tool(tg, "not_a_real_tool", {}, cutoff_ts=NO_EFFECTIVE_CUTOFF)
