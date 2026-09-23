## Task 2: Persistent MCP client wrapper

**Files:**
- Create: `src/__init__.py` (empty)
- Create: `src/tg_client.py`
- Test: `tests/test_tg_client.py`

**Interfaces:**
- Produces: `class TigerGraphMCP` — async context manager with `.call(tool_name, arguments) -> Any` (JSON-decoded if possible, else raw text) and typed convenience methods `.gsql(command)`, `.upsert_vectors(...)`, `.search_top_k_similarity(...)`, `.run_installed_query(...)`. All later tasks that touch TigerGraph go through this class, never a raw MCP session of their own.
- Consumes: `.env` (via `python-dotenv`), the `tigergraph-mcp` CLI on PATH (installed in Task 1).

- [ ] **Step 1: Write `src/tg_client.py`**

```python
from __future__ import annotations

import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client


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
            command="tigergraph-mcp",
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

    async def upsert_vectors(self, vertex_type: str, vectors: list[dict[str, Any]]) -> Any:
        return await self.call(
            "tigergraph__upsert_vectors",
            {"vertex_type": vertex_type, "vectors": vectors},
        )

    async def search_top_k_similarity(
        self, vertex_type: str, query_vector: list[float], k: int = 5
    ) -> Any:
        return await self.call(
            "tigergraph__search_top_k_similarity",
            {"vertex_type": vertex_type, "query_vector": query_vector, "k": k},
        )

    async def run_installed_query(self, query_name: str, params: dict[str, Any]) -> Any:
        return await self.call(
            "tigergraph__run_installed_query",
            {"query_name": query_name, "params": params},
        )
```

**Note:** if Task 1 Step 7 found different parameter names in the real schema (e.g. `query_name` vs `queryName`, or `vector` vs `query_vector`), edit the method bodies above to match before moving on — everything downstream calls through these methods, so fixing it once here is cheaper than fixing every call site later.

- [ ] **Step 2: Write the connectivity test**

`tests/test_tg_client.py`:

```python
import pytest

from src.tg_client import TigerGraphMCP


@pytest.mark.asyncio
async def test_gsql_show_returns_something():
    async with TigerGraphMCP() as tg:
        result = await tg.gsql("HELP")
        assert result is not None
```

- [ ] **Step 3: Run it against the live Savanna workspace**

```bash
.venv\Scripts\pytest tests/test_tg_client.py -v
```

Expected: PASS. This requires the real `.env` credentials from Task 1 — there is no offline/mocked mode, since the whole point is to verify live connectivity before building anything on top.

- [ ] **Step 4: Commit**

```bash
git add src/__init__.py src/tg_client.py tests/test_tg_client.py
git commit -m "feat: persistent TigerGraph MCP client wrapper"
```

---

