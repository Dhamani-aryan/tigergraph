from __future__ import annotations

import json
import re
import shutil
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


def _extract_json_envelope(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of the tigergraph-mcp JSON result envelope.

    Every observed tigergraph-mcp tool response (success or failure) wraps
    its structured result in a ```json ... ``` fenced block containing keys
    like "success", "operation", "data"/"error"/"error_code" -- usually
    followed by a human-readable markdown rendering of the same data. Try
    the fenced block first, then fall back to parsing the whole text as
    JSON, so callers can inspect `success`/`error` regardless of whether the
    tool call failed.
    """
    match = _JSON_FENCE_RE.search(text)
    candidate = match.group(1) if match else text
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


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


# Answer-quality / hardening fix (2026-09-23): the investigation runtime
# (graph_flow.py / run_case.py) never needs schema-mutating or bulk-loading
# tools -- those only run once, during setup (src/schema/*). The LLM itself
# can't reach any MCP tool at all regardless (it only ever sees the 3
# hardcoded FOLLOWUP_TOOL_SCHEMAS Python functions via generate_with_tools,
# never this client object -- see src/graph/queries.py's dispatch_
# followup_tool), so this isn't closing a path the model could exploit; it's
# reducing the blast radius of a bug or a bad argument in OUR OWN code
# during a live investigation run, the same defense-in-depth idea reviewed
# in PROJECT_COMPARISON_AND_INTEGRATION_RECOMMENDATION.md's MCP-safety row.
# tigergraph-mcp's own `--allowed-tools` flag (confirmed present via
# `tigergraph-mcp --help`) restricts the SERVER PROCESS itself to exactly
# this list -- every tool investigation.py's whole call path actually uses:
# card_window/device_neighbors/region_neighbors/closed_case_lookup/
# ring_membership/device_profile_label (run_installed_query, plus one-time
# gsql installs), retrieve_knowledge (search_top_k_similarity), and the
# case write-back + read-back receipt (add_nodes, upsert_vectors, get_node).
INVESTIGATION_ALLOWED_TOOLS = (
    "tigergraph__gsql,"
    "tigergraph__run_installed_query,"
    "tigergraph__search_top_k_similarity,"
    "tigergraph__upsert_vectors,"
    "tigergraph__add_nodes,"
    "tigergraph__get_node"
)


class TigerGraphMCP:
    """Persistent MCP client session against a running tigergraph-mcp server.

    Usage:
        async with TigerGraphMCP() as tg:
            result = await tg.gsql("SHOW VERTEX TYPE Customer")

        # Investigation runtime (run_case.py/run_all.py): restrict the server
        # process to only the tools an investigation actually needs.
        async with TigerGraphMCP(allowed_tools=INVESTIGATION_ALLOWED_TOOLS) as tg:
            ...
    """

    def __init__(self, env_path: str = ".env", allowed_tools: str | None = None) -> None:
        self._env_path = env_path
        self._allowed_tools = allowed_tools
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "TigerGraphMCP":
        env_dict = dotenv_values(Path(self._env_path).resolve())
        args = ["-vv"]
        if self._allowed_tools:
            args += ["--allowed-tools", self._allowed_tools]
        server_params = StdioServerParameters(
            command=_resolve_tigergraph_mcp_command(),
            args=args,
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
        joined = "\n".join(texts)
        envelope = _extract_json_envelope(joined) if joined else None

        # The tigergraph-mcp server does NOT reliably set the MCP protocol's
        # `is_error` flag on failures (observed False even for a rejected
        # GSQL command and a 404 on a missing installed query) -- it instead
        # reports failure inside the JSON envelope via `"success": false`.
        # Check both so a real server failure is never silently swallowed as
        # a successful result, which matters most for tasks that batch many
        # sequential calls without inspecting each one individually.
        tool_failed = result.is_error or (envelope is not None and envelope.get("success") is False)
        if tool_failed:
            error_detail = envelope.get("error") or envelope.get("summary") if envelope else None
            raise RuntimeError(
                f"TigerGraph MCP tool '{tool_name}' failed: {error_detail or joined or '<no error text returned>'}"
            )

        if not texts:
            return None
        if envelope is not None:
            return envelope
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
