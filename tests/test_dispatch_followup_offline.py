from __future__ import annotations

import pytest

from src.graph.queries import dispatch_followup_tool


class _ExplodingTg:
    """A `tg` that raises if any TigerGraph call is attempted -- proves the
    empty-addr1 guard short-circuits before ever reaching the network."""

    def __getattr__(self, name):
        raise AssertionError(f"dispatch_followup_tool must not touch tg.{name} for a blank addr1")


@pytest.mark.asyncio
async def test_wider_region_check_blank_addr1_returns_empty_without_calling_tg():
    # Regression test: HHG-011/HHG-013 both hit a hard TigerGraph engine
    # error ("invalid vertex id", SYS-0005) live because a genuinely blank
    # addr1 on the flagged transaction was passed straight through to a
    # VERTEX<BillingRegion> query parameter, crashing the whole case.
    result = await dispatch_followup_tool(
        _ExplodingTg(), "wider_region_check", {"addr1": ""}, cutoff_ts="2099-01-01 00:00:00"
    )
    assert result == []


@pytest.mark.asyncio
async def test_closed_case_lookup_by_region_blank_addr1_returns_empty_without_calling_tg():
    result = await dispatch_followup_tool(
        _ExplodingTg(), "closed_case_lookup_by_region", {"addr1": ""}, cutoff_ts="2099-01-01 00:00:00"
    )
    assert result == []


@pytest.mark.asyncio
async def test_wider_region_check_whitespace_only_addr1_treated_as_blank():
    result = await dispatch_followup_tool(
        _ExplodingTg(), "wider_region_check", {"addr1": "   "}, cutoff_ts="2099-01-01 00:00:00"
    )
    assert result == []


@pytest.mark.asyncio
async def test_wider_region_check_missing_addr1_key_treated_as_blank():
    result = await dispatch_followup_tool(
        _ExplodingTg(), "wider_region_check", {}, cutoff_ts="2099-01-01 00:00:00"
    )
    assert result == []
