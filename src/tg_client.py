from __future__ import annotations

import json
import shutil
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client


def _resolve_tigergraph_mcp_command() -> str:
    """Resolve the tigergraph-mcp executable to an absolute path.

    On Windows, the mcp library locates the "command" via shutil.which
    against the *current process's* real PATH -- not the custom `env` dict
    passed to StdioServerParameters (that dict only becomes the child
    process's environment after it has already been found). If the venv
    hosting tigergraph-mcp isn't activated (its Scripts dir isn't on PATH),
    that lookup silently returns the bare string back and Windows'
    CreateProcess then raises FileNotFoundError: [WinError 2]. Checking next
    to the running interpreter first (i.e. this venv's Scripts/bin dir)
    makes the wrapper work regardless of whether venv shell activation
    happened, since every later task depends on this class connecting
    reliably.
    """
    venv_bin_dir = Path(sys.executable).parent
    for name in ("tigergraph-mcp.exe", "tigergraph-mcp"):
        candidate = venv_bin_dir / name
        if candidate.exists():
            return str(candidate)
    return shutil.which("tigergraph-mcp") or "tigergraph-mcp"


class TigerGraphMCP:
    """Persistent MCP client session against a running tigergraph-mcp server.

    Usage:
        async with TigerGraphMCP() as tg:
            result = await tg.gsql("SHOW VERTEX TYPE Customer")
    """

    def __init__(self, env_path: str = ".env") -> None:
        self._env_path = env_path
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "TigerGraphMCP":
        env_dict = dotenv_values(Path(self._env_path).resolve())
        server_params = StdioServerParameters(
            command=_resolve_tigergraph_mcp_command(),
            args=["-vv"],
            env={**get_default_environment(), **env_dict},
        )
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(server_params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self.session = None

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        assert self.session is not None, "use 'async with TigerGraphMCP() as tg:'"
        result = await self.session.call_tool(tool_name, arguments=arguments)
        texts = [c.text for c in result.content if hasattr(c, "text")]
        if not texts:
            return None
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined

    async def gsql(self, command: str) -> Any:
        return await self.call("tigergraph__gsql", {"command": command})

    async def upsert_vectors(
        self, vertex_type: str, vector_attribute: str, vectors: list[dict[str, Any]]
    ) -> Any:
        # Real schema (docs/tigergraph-mcp-tools.json) requires vector_attribute
        # in addition to vertex_type/vectors; the plan brief's assumed signature
        # omitted it. Each item in `vectors` follows the VectorData shape:
        # {"vertex_id": ..., "vector": [...], "attributes": {...}}.
        return await self.call(
            "tigergraph__upsert_vectors",
            {
                "vertex_type": vertex_type,
                "vector_attribute": vector_attribute,
                "vectors": vectors,
            },
        )

    async def search_top_k_similarity(
        self,
        vertex_type: str,
        vector_attribute: str,
        query_vector: list[float],
        top_k: int = 5,
    ) -> Any:
        # Real schema requires vector_attribute (not in the brief's assumed
        # signature) and names the result-count param `top_k`, not `k`.
        return await self.call(
            "tigergraph__search_top_k_similarity",
            {
                "vertex_type": vertex_type,
                "vector_attribute": vector_attribute,
                "query_vector": query_vector,
                "top_k": top_k,
            },
        )

    async def run_installed_query(self, query_name: str, params: dict[str, Any]) -> Any:
        return await self.call(
            "tigergraph__run_installed_query",
            {"query_name": query_name, "params": params},
        )
