import pytest

from src.tg_client import TigerGraphMCP


@pytest.mark.asyncio
async def test_gsql_show_returns_something():
    async with TigerGraphMCP() as tg:
        result = await tg.gsql("HELP")
        assert result is not None


@pytest.mark.asyncio
async def test_gsql_invalid_command_raises():
    async with TigerGraphMCP() as tg:
        with pytest.raises(RuntimeError, match="tigergraph__gsql"):
            await tg.gsql("NOT VALID GSQL AT ALL")
