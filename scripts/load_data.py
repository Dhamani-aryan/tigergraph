import asyncio

from src.schema.loading_jobs import run_all_loading_jobs
from src.tg_client import TigerGraphMCP


async def main() -> None:
    async with TigerGraphMCP() as tg:
        await run_all_loading_jobs(tg, "transactions.csv", "closed_cases_history.csv", "case_pack.csv")


if __name__ == "__main__":
    asyncio.run(main())
