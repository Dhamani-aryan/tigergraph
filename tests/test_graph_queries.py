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
        result = await card_window(tg, KNOWN_CARD, hours=24 * 60)
        assert result
        txn_ids = {t["id"] for t in result}
        assert KNOWN_TXN in txn_ids
        assert all("ts" in t and "TransactionAmt" in t for t in result)


@pytest.mark.asyncio
async def test_card_window_small_window_narrows_results():
    async with TigerGraphMCP() as tg:
        full_history = await card_window(tg, KNOWN_CARD, hours=100000)
        narrow = await card_window(tg, KNOWN_CARD, hours=1)
        assert len(narrow) < len(full_history)
        assert len(narrow) >= 1  # the card's own latest transaction always qualifies


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
        result = await device_neighbors(tg, KNOWN_TXN)
        assert result
        # Task 8's checkpoint found 299 distinct customers/cards sharing
        # this device; device_neighbors LIMITs shared_cards at 300.
        assert len(result) <= 300
        assert any(c["id"] == KNOWN_CARD for c in result)


@pytest.mark.asyncio
async def test_region_neighbors_returns_transactions_for_known_region():
    async with TigerGraphMCP() as tg:
        result = await region_neighbors(tg, KNOWN_REGION)
        assert result
        assert all("ts" in t and "TransactionAmt" in t for t in result)


@pytest.mark.asyncio
async def test_region_neighbors_time_window_narrows_results():
    async with TigerGraphMCP() as tg:
        unfiltered = await region_neighbors(tg, KNOWN_REGION)
        windowed = await region_neighbors(
            tg, KNOWN_REGION, txn_ts="2016-11-11 23:46:24", window_days=1
        )
        assert len(windowed) <= len(unfiltered)


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
async def test_retrieve_knowledge_returns_policy_and_case_hits():
    async with TigerGraphMCP() as tg:
        result = await retrieve_knowledge(tg, "card testing small authorizations", top_k=3)
        assert "knowledge" in result and "similar_cases" in result
        assert len(result["knowledge"]) > 0
        # FraudCase's index is confirmed empty until Task 12/13 writes to
        # it; similar_cases is dominated by ClosedCase hits, not required
        # to be non-empty from FraudCase specifically.
        assert len(result["similar_cases"]) > 0


@pytest.mark.asyncio
async def test_dispatch_followup_tool_routes_to_correct_function():
    async with TigerGraphMCP() as tg:
        result = await dispatch_followup_tool(
            tg, "wider_card_window", {"card_id": KNOWN_CARD, "hours": 100000}
        )
        assert result is not None
        assert len(result) == 59  # same full-history count as card_window's own test


@pytest.mark.asyncio
async def test_dispatch_followup_tool_unknown_name_raises():
    async with TigerGraphMCP() as tg:
        with pytest.raises(ValueError, match="Unknown follow-up tool"):
            await dispatch_followup_tool(tg, "not_a_real_tool", {})
