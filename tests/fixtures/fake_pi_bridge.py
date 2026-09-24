"""Stand-in for pi_bridge/bridge.mjs in tests: same JSONL protocol, scripted
replies, no network. Controlled by env vars:

FAKE_PI_MODE      normal | startup_error | no_ready | garbage_ready | timeout | garbage | exit_mid
FAKE_PI_SCRIPT    path to a JSON list; each `complete` request pops the next entry
                  ({"text":..., "tool_calls":[...], "usage":{...}} or {"error": {...}})
FAKE_PI_LOG       path; every received request is appended as one JSON line
"""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_PI_MODE", "normal")
script_path = os.environ.get("FAKE_PI_SCRIPT")
log_path = os.environ.get("FAKE_PI_LOG")
script = json.load(open(script_path, encoding="utf-8")) if script_path else []


def out(frame):
    sys.stdout.write(json.dumps(frame) + "\n")
    sys.stdout.flush()


if mode == "startup_error":
    out({"event": "startup_error", "error": {"kind": "auth", "message": "No Pi credential for 'openai-codex'"}})
    sys.exit(2)
if mode == "no_ready":
    sys.exit(3)
if mode == "garbage_ready":
    sys.stdout.write("not json at all\n")
    sys.stdout.flush()
    time.sleep(5)
    sys.exit(0)

out({"event": "ready", "provider": "openai-codex", "model": "fake-codex-model", "api": "openai-codex-responses",
     "reasoning_effort": "low", "pi_ai_version": "0.87.1"})
sys.stderr.write("[fake-pi] ready\n")

for line in sys.stdin:
    req = json.loads(line)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(req) + "\n")
    if req["op"] == "shutdown":
        out({"id": req["id"], "ok": True, "result": {"shutdown": True}})
        break
    if mode == "timeout":
        time.sleep(30)
        continue
    if mode == "garbage":
        sys.stdout.write("{this is not json\n")
        sys.stdout.flush()
        continue
    if mode == "exit_mid":
        sys.exit(9)
    entry = script.pop(0) if script else {"text": "{}"}
    if "error" in entry:
        out({"id": req["id"], "ok": False, "error": entry["error"]})
        continue
    out({"id": req["id"], "ok": True, "result": {
        "text": entry.get("text", ""),
        "tool_calls": entry.get("tool_calls", []),
        "stop_reason": "toolUse" if entry.get("tool_calls") else "stop",
        "usage": entry.get("usage", {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0, "total": 15}),
        "provider": "openai-codex",
        "model": "fake-codex-model",
    }})
