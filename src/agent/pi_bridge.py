"""Python side of the LLM_BACKEND=pi adapter.

Pi (https://github.com/earendil-works/pi) is not an OpenAI-compatible HTTP
server, so this talks to a persistent Node child process
(`pi_bridge/bridge.mjs serve`) over newline-delimited JSON: one request per
stdin line, one response per stdout line. The bridge uses pi-ai's
`openai-codex` provider (ChatGPT Plus/Pro via Pi's own OAuth credential
store, ~/.pi/agent/auth.json) -- no token ever passes through this process,
the repo, or `.env`.

The bridge is stateless per request: every call sends its complete message
list, and the bridge sets no session id / prompt-cache key, so nothing from
one call (or case) can leak into the next. It also never executes tools --
tool calls come back here as data for `generate_with_tools` to vet.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

BRIDGE_SCRIPT = Path(__file__).resolve().parents[2] / "pi_bridge" / "bridge.mjs"


class PiBridgeError(RuntimeError):
    """Any failure talking to Pi. `kind` is one of: auth, provider, model,
    timeout, rate_limit, malformed, startup, exited."""

    def __init__(self, kind: str, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(f"[pi:{kind}] {message}")
        self.kind = kind
        self.retry_after_s = retry_after_s


@dataclass
class PiToolCall:
    name: str
    arguments: dict


@dataclass
class PiCompletion:
    text: str
    tool_calls: list[PiToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    stop_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return int(self.usage.get("total") or 0)


def _node_executable() -> str:
    """PI_NODE lets you point at a Node >= 22.19 that isn't first on PATH."""
    return os.environ.get("PI_NODE") or shutil.which("node") or "node"


class PiBridge:
    def __init__(
        self,
        command: list[str] | None = None,
        startup_timeout_s: float | None = None,
        request_timeout_s: float | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command or [_node_executable(), str(BRIDGE_SCRIPT), "serve"]
        self.startup_timeout_s = startup_timeout_s or float(os.environ.get("PI_STARTUP_TIMEOUT_S", "60"))
        self.request_timeout_s = request_timeout_s or float(os.environ.get("PI_REQUEST_TIMEOUT_S", "180"))
        self.env = env
        self.info: dict = {}
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._next_id = 0
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------------------
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> dict:
        if self._alive():
            return self.info
        env = {**os.environ, **(self.env or {})}
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,  # bridge logs pass straight through to our stderr
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise PiBridgeError("startup", f"could not launch Pi bridge {self.command[0]!r}: {exc}") from exc
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self._proc, self._lines), daemon=True).start()
        frame = self._read_frame(self.startup_timeout_s, phase="startup")
        if frame.get("event") == "startup_error":
            err = frame.get("error") or {}
            self._kill()
            raise PiBridgeError(err.get("kind") or "startup", err.get("message") or "bridge failed to start")
        if frame.get("event") != "ready":
            self._kill()
            raise PiBridgeError("malformed", f"expected a 'ready' frame from the Pi bridge, got {frame!r}")
        self.info = {k: v for k, v in frame.items() if k != "event"}
        return self.info

    @staticmethod
    def _pump(proc: subprocess.Popen, lines: queue.Queue) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)  # EOF sentinel: the bridge exited

    def _read_frame(self, timeout_s: float, phase: str) -> dict:
        try:
            line = self._lines.get(timeout=timeout_s)
        except queue.Empty:
            self._kill()
            raise PiBridgeError("timeout", f"Pi bridge {phase} timed out after {timeout_s:.0f}s") from None
        if line is None:
            code = self._proc.poll() if self._proc else None
            self._proc = None
            raise PiBridgeError("exited", f"Pi bridge exited unexpectedly during {phase} (exit code {code})")
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            self._kill()
            raise PiBridgeError("malformed", f"Pi bridge wrote non-JSON to stdout during {phase}: {line[:200]!r}") from None
        if not isinstance(frame, dict):
            self._kill()
            raise PiBridgeError("malformed", f"Pi bridge frame is not a JSON object: {line[:200]!r}")
        return frame

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 -- best effort
            pass

    def close(self) -> None:
        """Clean shutdown: ask the bridge to exit, then make sure it did."""
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write(json.dumps({"id": "shutdown", "op": "shutdown"}) + "\n")
                proc.stdin.flush()
                proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._kill()

    # -- requests -------------------------------------------------------------------
    def request(self, payload: dict, timeout_s: float | None = None) -> dict:
        with self._lock:
            self.start()
            self._next_id += 1
            req_id = self._next_id
            timeout = timeout_s or self.request_timeout_s
            body = {**payload, "id": req_id, "timeout_ms": int(timeout * 1000)}
            try:
                assert self._proc is not None and self._proc.stdin is not None
                self._proc.stdin.write(json.dumps(body) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self._kill()
                raise PiBridgeError("exited", f"could not write to Pi bridge: {exc}") from exc
            # Python-side deadline sits a little past the bridge's own abort, so the
            # bridge normally reports the timeout itself; if it doesn't, we kill it.
            frame = self._read_frame(timeout + 15, phase=f"request {req_id}")
            if frame.get("id") != req_id:
                self._kill()
                raise PiBridgeError("malformed", f"Pi bridge response id {frame.get('id')!r} != request id {req_id}")
            if not frame.get("ok"):
                err = frame.get("error") or {}
                raise PiBridgeError(
                    err.get("kind") or "provider",
                    err.get("message") or "unknown Pi error",
                    retry_after_s=err.get("retry_after_s"),
                )
            result = frame.get("result")
            if not isinstance(result, dict):
                raise PiBridgeError("malformed", f"Pi bridge result is not an object: {frame!r}")
            return result

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> PiCompletion:
        payload: dict = {"op": "complete", "system": system, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        result = self.request(payload)
        text = result.get("text")
        raw_calls = result.get("tool_calls")
        usage = result.get("usage")
        if not isinstance(text, str) or not isinstance(raw_calls, list) or not isinstance(usage, dict):
            raise PiBridgeError("malformed", f"Pi completion is missing text/tool_calls/usage: {result!r}")
        calls = []
        for call in raw_calls:
            if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                raise PiBridgeError("malformed", f"Pi tool call is malformed: {call!r}")
            args = call.get("arguments") or {}
            if not isinstance(args, dict):
                raise PiBridgeError("malformed", f"Pi tool call arguments are not an object: {call!r}")
            calls.append(PiToolCall(name=call["name"], arguments=args))
        return PiCompletion(
            text=text,
            tool_calls=calls,
            usage=usage,
            provider=str(result.get("provider") or ""),
            model=str(result.get("model") or ""),
            stop_reason=str(result.get("stop_reason") or ""),
        )


_bridge: PiBridge | None = None


def get_bridge() -> PiBridge:
    """One persistent bridge per Python process (i.e. per batch run)."""
    global _bridge
    if _bridge is None:
        _bridge = PiBridge()
        atexit.register(close_bridge)
    return _bridge


def set_bridge(bridge: PiBridge | None) -> None:
    """Test hook: swap in a bridge backed by a fake command."""
    global _bridge
    if _bridge is not None and _bridge is not bridge:
        _bridge.close()
    _bridge = bridge


def close_bridge() -> None:
    global _bridge
    if _bridge is not None:
        _bridge.close()
        _bridge = None


def bridge_provenance() -> dict:
    """Actual provider/model the running bridge reported in its `ready` frame
    (starts the bridge if needed)."""
    info = get_bridge().start()
    return {
        "provider": f"pi/{info.get('provider', '')}",
        "model": str(info.get("model", "")),
        "pi_ai_version": str(info.get("pi_ai_version", "")),
        "reasoning_effort": str(info.get("reasoning_effort", "")),
    }


if __name__ == "__main__":  # manual check: python -m src.agent.pi_bridge
    from dotenv import load_dotenv

    load_dotenv()
    b = get_bridge()
    print(json.dumps(b.start(), indent=2), file=sys.stderr)
    out = b.complete(
        "Respond with ONLY a single JSON object. No prose, no markdown fences.",
        [{"role": "user", "content": 'Return {"answer": "ok"} exactly.'}],
    )
    print(json.dumps({"text": out.text, "usage": out.usage, "provider": out.provider, "model": out.model}))
    close_bridge()
