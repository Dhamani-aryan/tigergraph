import pytest

from src.tg_client import TigerGraphMCP


@pytest.mark.asyncio
async def test_gsql_show_returns_something():
    async with TigerGraphMCP() as tg:
        result = await tg.gsql("HELP")
        assert result is not None
