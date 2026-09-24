# Pi bridge (`LLM_BACKEND=pi`)

Runs the fraud agent's LLM calls on a ChatGPT Plus/Pro subscription through
[Pi](https://github.com/earendil-works/pi)'s `openai-codex` OAuth provider.
This is not an OpenAI API key and not an OpenAI-compatible HTTP server, so the
Python code talks to a persistent Node child process over JSONL:

```
src/agent/llm.py -> src/agent/pi_bridge.py -> node pi_bridge/bridge.mjs serve
  -> ModelRuntime (Pi's auth.json credential store) -> pi-ai openai-codex provider
```

Packages are pinned to `@earendil-works/pi-ai` and
`@earendil-works/pi-coding-agent` 0.87.1 (via `package-lock.json`). pi-ai makes
the requests. pi-coding-agent supplies only `ModelRuntime`, its public
`Models` collection backed by Pi's own `~/.pi/agent/auth.json`, which handles
locked token refresh. The bridge never executes tools. It declares them to the
model and hands any tool call back to Python.

## Setup

Requires Node >= 22.19. If `node` on PATH is older, set `PI_NODE` in `.env`.

```bash
cd pi_bridge && npm ci && cd ..
node pi_bridge/bridge.mjs login     # opens OpenAI's OAuth page; credential goes to ~/.pi/agent/auth.json
node pi_bridge/bridge.mjs models    # which openai-codex models this login can use
```

Then in `.env`, set `LLM_BACKEND=pi`, `PI_PROVIDER=openai-codex` and
`PI_MODEL=<a non-Spark id from the list>`. `GROQ_API_KEY` is not needed.

## Protocol

stdin gets one JSON request per line, and stdout returns one JSON response per
line. Logs go to stderr.

- Startup: `{"event":"ready","provider","model",...}` or `{"event":"startup_error","error":{kind,message}}`
- `{"id","op":"complete","system","messages":[{role,content}],"tools"?,"tool_choice"?,"timeout_ms"}`
  returns `{"id","ok":true,"result":{text,tool_calls:[{name,arguments}],usage:{input,output,cache_read,cache_write,reasoning,total},provider,model}}`
- Errors: `{"id","ok":false,"error":{kind,message,retry_after_s?}}`. `kind` is one of
  `auth | provider | model | timeout | rate_limit | malformed`.
- `ping` and `shutdown` are also supported.

Each request carries its full message list. No session id or prompt-cache key
is sent, so nothing carries over between calls or cases.
