import asyncio

from src.schema.derive_entities import load_closed_case_multi_edges, load_device_profiles
from src.tg_client import TigerGraphMCP


async def main() -> None:
    async with TigerGraphMCP() as tg:
        n = await load_device_profiles(tg, "identity.csv")
        print(f"Loaded {n} device profile references")
        await load_closed_case_multi_edges(tg, "closed_cases_history.csv")
        print("Loaded ClosedCase INVOLVES/CONNECTED_TO edges")


if __name__ == "__main__":
    asyncio.run(main())
