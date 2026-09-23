## Task 1: Environment setup, MCP connectivity, and tool schema discovery

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.env` (gitignored — real Savanna credentials)
- Create: `scripts/discover_mcp_tools.py`
- Create: `docs/tigergraph-mcp-tools.json` (generated artifact, committed — reference for later tasks)

**Interfaces:**
- Produces: a committed dump of every `tigergraph-mcp` tool's exact name and input JSON schema, which Tasks 2–11 read from instead of guessing parameter names.

- [ ] **Step 1: Create the venv and install dependencies**

```bash
"C:\Users\naman\AppData\Local\Programs\Python\Python310\python.exe" -m venv .venv
.venv\Scripts\pip install --upgrade pip
```

- [ ] **Step 2: Write `requirements.txt`**

```
tigergraph-mcp
mcp>=1.2.0
python-dotenv
langgraph>=0.2.0
openai>=1.40.0
ollama
pydantic>=2.0
pandas
pypdf
requests
streamlit
pytest
pytest-asyncio
tenacity
```

Install: `.venv\Scripts\pip install -r requirements.txt`

(`openai` is the client used to talk to Groq, whose API is OpenAI-compatible — see Step 3b. `ollama` stays as the fallback LLM backend and is still required for `nomic-embed-text` embeddings either way. `tenacity` is for rate-limit retry/backoff on the Groq calls in Task 11.)

- [ ] **Step 3: Write `.env.example`**

```
TG_HOST=https://your-workspace.i.tgcloud.io
TG_GRAPHNAME=FraudInvestigation
TG_USERNAME=tigergraph
TG_PASSWORD=changeme
TG_RESTPP_PORT=9000
TG_GS_PORT=14240

LLM_BACKEND=groq
GROQ_API_KEY=your-groq-key-here
GROQ_MODEL=llama-3.3-70b-versatile
```

Copy to `.env` and fill in the real Savanna workspace hostname/credentials from the workspace you created (`https://savanna.tgcloud.io`, "Explore with Your Own Data").

- [ ] **Step 3b: Get a free Groq API key** (do this yourself — account creation isn't something to automate): go to `https://console.groq.com`, sign up (no credit card required for the free tier), create an API key, paste it into `.env` as `GROQ_API_KEY`. While there, check the current model list at `https://console.groq.com/docs/models` and confirm `llama-3.3-70b-versatile` (or whatever the current strongest general-purpose model is called — model names on free platforms change) is available and supports tool/function calling; update `GROQ_MODEL` in `.env` if the name has changed since this plan was written. Also note the free tier's requests-per-minute limit from your account dashboard — Task 11's retry/backoff logic needs to know roughly what it's working around.

- [ ] **Step 4: Confirm Ollama models are present** (still needed for embeddings, and as the LLM fallback)

```bash
ollama list
```

Expected: `qwen3:4b-instruct` and `nomic-embed-text` both listed (already pulled per earlier session check). If either is missing, `ollama pull qwen3:4b-instruct` / `ollama pull nomic-embed-text`.

- [ ] **Step 5: Write the tool-discovery script**

`scripts/discover_mcp_tools.py`:

```python
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
                    "inputSchema": t.inputSchema,
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
```

- [ ] **Step 6: Run it and verify connectivity**

```bash
.venv\Scripts\python scripts\discover_mcp_tools.py
```

Expected: prints "Wrote N tool schemas to docs/tigergraph-mcp-tools.json" (N should be around 69 per the tigergraph-mcp README) and a `list_graphs` result that doesn't error. If it errors, the `.env` credentials/host are wrong — fix before continuing; nothing downstream works without this.

- [ ] **Step 7: Open `docs/tigergraph-mcp-tools.json` and note the exact input schemas for these tools** (write the findings as a comment block at the top of `scripts/discover_mcp_tools.py` for later tasks to reference): `tigergraph__gsql`, `tigergraph__create_graph`, `tigergraph__create_loading_job`, `tigergraph__upsert_vectors`, `tigergraph__search_top_k_similarity`, `tigergraph__run_installed_query`, `tigergraph__install_query`. Tasks 2, 3, 8–11 below use plausible parameter names (`command`, `vertex_type`, `vectors`, etc.) based on the tigergraph-mcp README's tool-name table — **confirm each against the actual dumped schema before writing the call, and adjust parameter names to match if they differ.**

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .env.example scripts/discover_mcp_tools.py docs/tigergraph-mcp-tools.json
git commit -m "feat: environment setup and MCP tool schema discovery"
```

(`.env` itself stays untracked per `.gitignore`.)

---

