import asyncio

from src.graph_algorithms.connected_components import (
    compute_cluster_fraud_rates,
    run_connected_components,
)
from src.tg_client import TigerGraphMCP


async def main() -> None:
    async with TigerGraphMCP() as tg:
        await run_connected_components(tg)
        await compute_cluster_fraud_rates(tg)


if __name__ == "__main__":
    asyncio.run(main())
