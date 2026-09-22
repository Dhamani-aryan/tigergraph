import asyncio

from src.schema.build_schema import add_vector_attributes, apply_schema
from src.tg_client import TigerGraphMCP


async def main() -> None:
    async with TigerGraphMCP() as tg:
        await apply_schema(tg, "transactions.csv", "identity.csv")
        await add_vector_attributes(tg)


if __name__ == "__main__":
    asyncio.run(main())
