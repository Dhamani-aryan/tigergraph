# Findings from running this script against the live TigerGraph Savanna workspace
# (Task 1, Step 7). Full schemas are in docs/tigergraph-mcp-tools.json (69 tools
# total). Confirmed exact input parameter names for the tools Tasks 2, 3, 8-11
# will call — use these names, not the plausible guesses in the plan text:
#
# tigergraph__gsql
#   - command (required, str): GSQL command to execute.
#   - graph_name (optional, str): defaults to the active connection's graph.
#   - profile (optional, str): connection profile name.
#
# tigergraph__create_graph
#   - graph_name (required, str)
#   - vertex_types (required, list): vertex type definitions for this graph.
#   - edge_types (optional, list): edge type definitions for this graph.
#   - profile (optional, str)
#
# tigergraph__create_loading_job
#   - job_name (required, str)
#   - files (required, list): each item needs 'file_alias' and
#     'node_mappings' and/or 'edge_mappings', e.g.
#     [{"file_alias": "f1", "node_mappings": [...]}]
#   - run_job (optional, bool): run immediately after creation.
#   - drop_after_run (optional, bool): only applies if run_job=True.
#   - graph_name (optional, str), profile (optional, str)
#
# tigergraph__upsert_vectors
#   - vertex_type (required, str)
#   - vector_attribute (required, str)
#   - vectors (required, list): each with vertex_id, vector, optional attributes.
#   - graph_name (optional, str), profile (optional, str)
#
# tigergraph__search_top_k_similarity
#   - vertex_type (required, str)
#   - vector_attribute (required, str)
#   - query_vector (required, list[float])
#   - top_k (optional, int)
#   - ef (optional, int): HNSW exploration factor, higher = more accurate/slower.
#   - return_vectors (optional, bool): can be large, defaults False-ish.
#   - graph_name (optional, str), profile (optional, str)
#
# tigergraph__run_installed_query
#   - query_name (required, str)
#   - params (optional, dict): query parameters.
#   - graph_name (optional, str), profile (optional, str)
#
# tigergraph__install_query
#   - query_text (required, str): GSQL query text to install.
#   - graph_name (optional, str), profile (optional, str)
#
# Connectivity note: TG_RESTPP_PORT=TG_GS_PORT=443 (Savanna workspace routes
# everything through HTTPS on the workspace hostname) is CONFIRMED correct —
# the server authenticated (token obtained), returned a real TigerGraph
# version, and gsql/show_graph_details calls returned genuine GSQL-engine
# responses ("Graph 'FraudInvestigation' does not exist." / a permission
# message), not a network/TLS/port error. The 'FraudInvestigation' graph does
# not exist yet on the server (expected — Task 2 creates it), and this
# Savanna user lacks the global READ_SCHEMA privilege needed for
# list_graphs/get_global_schema (expected for a scoped workspace user;
# graph-scoped operations like gsql/create_graph are unaffected).

import asyncio
import json
from pathlib import Path

from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client


async def main() -> None:
    env_dict = dotenv_values(Path(".env").resolve())
    server_params = StdioServerParameters(
        command="tigergraph-mcp",
        args=["-vv"],
        env={**get_default_environment(), **env_dict},
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            dumped = [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                }
                for t in tools.tools
            ]
            out_path = Path("docs/tigergraph-mcp-tools.json")
            out_path.write_text(json.dumps(dumped, indent=2))
            print(f"Wrote {len(dumped)} tool schemas to {out_path}")

            # Sanity check: confirm the server can actually reach the TG instance.
            result = await session.call_tool("tigergraph__list_graphs", arguments={})
            for content in result.content:
                if hasattr(content, "text"):
                    print("list_graphs ->", content.text)


if __name__ == "__main__":
    asyncio.run(main())
