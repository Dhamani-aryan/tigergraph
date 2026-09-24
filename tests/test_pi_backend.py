"""LLM_BACKEND=pi adapter tests. Everything here runs against
tests/fixtures/fake_pi_bridge.py (same JSONL protocol as pi_bridge/bridge.mjs,
scripted replies, no network) -- except the last test, which starts the real
Node bridge only far enough to check its startup-error path."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

import src.agent.llm as llm_module
from src.agent import pi_bridge
from src.agent.pi_bridge import PiBridge, PiBridgeError

FAKE = Path(__file__).parent / "fixtures" / "fake_pi_bridge.py"


class _PatternSchema(BaseModel):
    pattern: str


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "wider_card_window",
            "description": "Re-run the card window with a longer lookback.",
            "parameters": {
                "type": "object",
                "properties": {"card_id": {"type": "string"}, "hours": {"type": "number"}},
                "required": ["card_id", "hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wider_region_check",
            "description": "Check the billing region.",
            "parameters": {"type": "object", "properties": {"addr1": {"type": "string"}}, "required": ["addr1"]},
        },
    },
]


@pytest.fixture
def fake_bridge(tmp_path, monkeypatch):
    """Returns make(script, mode) -> (PiBridge, log_path); installs it as the
    process-wide bridge and switches llm.py to the pi backend."""
    made: list[PiBridge] = []

    def make(script=None, mode="normal", request_timeout_s=None, startup_timeout_s=None):
        script_path = tmp_path / f"script{len(made)}.json"
        script_path.write_text(json.dumps(script or []), encoding="utf-8")
        log_path = tmp_path / f"requests{len(made)}.jsonl"
        bridge = PiBridge(
            command=[sys.executable, str(FAKE)],
            env={"FAKE_PI_MODE": mode, "FAKE_PI_SCRIPT": str(script_path), "FAKE_PI_LOG": str(log_path)},
            request_timeout_s=request_timeout_s,
            startup_timeout_s=startup_timeout_s,
        )
        made.append(bridge)
        pi_bridge.set_bridge(bridge)
        return bridge, log_path

    monkeypatch.setattr(llm_module, "LLM_BACKEND", "pi")
    llm_module.token_tracker.reset()
    yield make
    pi_bridge.set_bridge(None)
    for b in made:
        b.close()
    llm_module.token_tracker.reset()


def _requests(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


# -- backend selection -------------------------------------------------------------

@pytest.mark.asyncio
async def test_groq_backend_still_uses_groq(monkeypatch):
    monkeypatch.setattr(llm_module, "LLM_BACKEND", "groq")
    calls = []

    class _Msg:
        content = '{"pattern": "card_testing"}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def fake_groq(messages, **kwargs):
        calls.append(kwargs)
        return _Resp()

    monkeypatch.setattr(llm_module, "_groq_chat", fake_groq)
    monkeypatch.setattr(llm_module, "_pi_chat", lambda *a, **k: pytest.fail("pi used on groq backend"))
    result = await llm_module.generate_structured("Classify.", _PatternSchema)
    assert result.pattern == "card_testing"
    assert calls == [{"response_format": {"type": "json_object"}}]


@pytest.mark.asyncio
async def test_ollama_backend_still_uses_ollama(monkeypatch):
    monkeypatch.setattr(llm_module, "LLM_BACKEND", "ollama")
    monkeypatch.setattr(llm_module.ollama, "chat", lambda **kw: {"message": {"content": '{"pattern": "ato"}'}})
    monkeypatch.setattr(llm_module, "_pi_chat", lambda *a, **k: pytest.fail("pi used on ollama backend"))
    result = await llm_module.generate_structured("Classify.", _PatternSchema)
    assert result.pattern == "ato"
    tool_result = await llm_module.generate_with_tools("x", TOOLS)
    assert tool_result.tool_name is None


# -- structured output ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_pi_structured_success(fake_bridge):
    _, log = fake_bridge([{"text": '{"pattern": "card_testing"}'}])
    result = await llm_module.generate_structured("Classify the pattern.", _PatternSchema)
    assert result.pattern == "card_testing"
    reqs = _requests(log)
    assert len(reqs) == 1
    assert reqs[0]["op"] == "complete"
    assert "JSON" in reqs[0]["system"]
    assert [m["role"] for m in reqs[0]["messages"]] == ["user"]
    assert '"pattern"' in reqs[0]["messages"][0]["content"]
    assert "tools" not in reqs[0]


@pytest.mark.asyncio
async def test_pi_structured_strips_json_fence_only(fake_bridge):
    fake_bridge([{"text": '```json\n{"pattern": "bust_out"}\n```'}])
    result = await llm_module.generate_structured("Classify.", _PatternSchema)
    assert result.pattern == "bust_out"


@pytest.mark.asyncio
async def test_pi_structured_schema_correction(fake_bridge):
    _, log = fake_bridge([
        {"text": '{"fraud_pattern": "card_testing"}'},  # wrong key
        {"text": '{"pattern": "card_testing"}'},
    ])
    result = await llm_module.generate_structured("Classify.", _PatternSchema, max_retries=2)
    assert result.pattern == "card_testing"
    reqs = _requests(log)
    assert len(reqs) == 2
    second = reqs[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == '{"fraud_pattern": "card_testing"}'
    assert "['pattern']" in second[2]["content"]


@pytest.mark.asyncio
async def test_pi_structured_gives_up_after_retries(fake_bridge):
    fake_bridge([{"text": "not json"}] * 3)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        await llm_module.generate_structured("Classify.", _PatternSchema, max_retries=2)


# -- tool round ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pi_one_valid_tool_call(fake_bridge):
    _, log = fake_bridge([{"text": "", "tool_calls": [{"name": "wider_card_window", "arguments": {"card_id": "x", "hours": 168}}]}])
    result = await llm_module.generate_with_tools("ambiguous?", TOOLS)
    assert result.tool_name == "wider_card_window"
    assert result.tool_arguments == {"card_id": "x", "hours": 168}
    req = _requests(log)[0]
    # OpenAI-style function schemas translated to Pi tool definitions.
    assert req["tools"] == [
        {"name": t["function"]["name"], "description": t["function"]["description"], "parameters": t["function"]["parameters"]}
        for t in TOOLS
    ]
    assert req["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_pi_no_tool_response(fake_bridge):
    fake_bridge([{"text": "Evidence is sufficient."}])
    result = await llm_module.generate_with_tools("ambiguous?", TOOLS)
    assert result.tool_name is None
    assert result.final_text == "Evidence is sufficient."


@pytest.mark.asyncio
async def test_pi_undeclared_tool_is_rejected(fake_bridge):
    fake_bridge([{"text": "", "tool_calls": [{"name": "bash", "arguments": {"cmd": "rm -rf /"}}]}])
    with pytest.raises(RuntimeError, match="undeclared tool 'bash'"):
        await llm_module.generate_with_tools("ambiguous?", TOOLS)


# -- usage accounting --------------------------------------------------------------

@pytest.mark.asyncio
async def test_pi_usage_accounting_sums_every_call(fake_bridge):
    fake_bridge([
        {"text": '{"fraud_pattern": "x"}', "usage": {"input": 100, "output": 20, "cache_read": 30, "cache_write": 0, "total": 150}},
        {"text": '{"pattern": "x"}', "usage": {"input": 200, "output": 40, "cache_read": 0, "cache_write": 0, "total": 240}},
        {"text": "no tool", "usage": {"input": 50, "output": 10, "cache_read": 0, "cache_write": 0, "total": 60}},
    ])
    await llm_module.generate_structured("Classify.", _PatternSchema)
    await llm_module.generate_with_tools("ambiguous?", TOOLS)
    # Rejected first attempt still counts: those tokens were really spent.
    assert llm_module.token_tracker.total == 150 + 240 + 60


# -- failure handling -------------------------------------------------------------

def test_pi_startup_error_is_reported(fake_bridge):
    bridge, _ = fake_bridge(mode="startup_error")
    with pytest.raises(PiBridgeError) as exc:
        bridge.start()
    assert exc.value.kind == "auth"
    assert "No Pi credential" in str(exc.value)


def test_pi_startup_missing_executable():
    bridge = PiBridge(command=["definitely-not-a-real-node-binary-xyz"])
    with pytest.raises(PiBridgeError) as exc:
        bridge.start()
    assert exc.value.kind == "startup"


def test_pi_bridge_exits_before_ready(fake_bridge):
    bridge, _ = fake_bridge(mode="no_ready")
    with pytest.raises(PiBridgeError) as exc:
        bridge.start()
    assert exc.value.kind == "exited"


def test_pi_malformed_startup_frame(fake_bridge):
    bridge, _ = fake_bridge(mode="garbage_ready")
    with pytest.raises(PiBridgeError) as exc:
        bridge.start()
    assert exc.value.kind == "malformed"


def test_pi_malformed_response(fake_bridge):
    bridge, _ = fake_bridge(mode="garbage")
    with pytest.raises(PiBridgeError) as exc:
        bridge.complete("sys", [{"role": "user", "content": "hi"}])
    assert exc.value.kind == "malformed"
    assert not bridge._alive()  # a desynced bridge is killed, not reused


def test_pi_request_timeout_kills_bridge(fake_bridge, monkeypatch):
    bridge, _ = fake_bridge(mode="timeout", request_timeout_s=0.5)
    monkeypatch.setattr(pi_bridge, "PiBridge", PiBridge)
    # Python deadline = request timeout + 15s grace; shrink the grace for the test.
    original = bridge._read_frame
    bridge._read_frame = lambda timeout_s, phase: original(1.0 if phase.startswith("request") else timeout_s, phase)
    with pytest.raises(PiBridgeError) as exc:
        bridge.complete("sys", [{"role": "user", "content": "hi"}])
    assert exc.value.kind == "timeout"
    assert not bridge._alive()


def test_pi_unexpected_exit_mid_request(fake_bridge):
    bridge, _ = fake_bridge(mode="exit_mid")
    with pytest.raises(PiBridgeError) as exc:
        bridge.complete("sys", [{"role": "user", "content": "hi"}])
    assert exc.value.kind == "exited"


@pytest.mark.asyncio
async def test_pi_transient_exit_retries_on_fresh_bridge(fake_bridge, monkeypatch):
    """After the bridge dies, the next attempt must start a new process."""
    bridge, _ = fake_bridge(mode="exit_mid")
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: None)
    starts = []
    real_start = bridge.start

    def counting_start():
        if not bridge._alive():
            starts.append(1)
        return real_start()

    bridge.start = counting_start
    with pytest.raises(PiBridgeError) as exc:
        await llm_module.generate_structured("Classify.", _PatternSchema, max_retries=0)
    assert exc.value.kind == "exited"
    assert len(starts) == llm_module.PI_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_pi_rate_limit_retries_short_wait_then_succeeds(fake_bridge, monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: sleeps.append(s))
    fake_bridge([
        {"error": {"kind": "rate_limit", "message": "429 too many requests", "retry_after_s": 2}},
        {"text": '{"pattern": "ok"}'},
    ])
    result = await llm_module.generate_structured("Classify.", _PatternSchema)
    assert result.pattern == "ok"
    assert sleeps == [2]


@pytest.mark.asyncio
async def test_pi_long_usage_limit_fails_fast(fake_bridge, monkeypatch):
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: pytest.fail("should not wait hours"))
    fake_bridge([{"error": {"kind": "rate_limit", "message": "You have hit your ChatGPT usage limit. Try again in ~90 min.", "retry_after_s": 5400}}])
    with pytest.raises(PiBridgeError) as exc:
        await llm_module.generate_structured("Classify.", _PatternSchema)
    assert exc.value.kind == "rate_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["auth", "model", "provider"])
async def test_pi_non_transient_errors_fail_fast(fake_bridge, monkeypatch, kind):
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: pytest.fail("non-transient error retried"))
    _, log = fake_bridge([{"error": {"kind": kind, "message": f"{kind} broke"}}])
    with pytest.raises(PiBridgeError) as exc:
        await llm_module.generate_structured("Classify.", _PatternSchema)
    assert exc.value.kind == kind
    assert len(_requests(log)) == 1


# -- statelessness + provenance -------------------------------------------------------

@pytest.mark.asyncio
async def test_pi_no_cross_request_state_leakage(fake_bridge):
    bridge, log = fake_bridge([
        {"text": '{"pattern": "first"}'},
        {"text": '{"pattern": "second"}'},
    ])
    await llm_module.generate_structured("CASE-A secret-ish prompt", _PatternSchema)
    await llm_module.generate_structured("CASE-B prompt", _PatternSchema)
    reqs = _requests(log)
    assert len(reqs) == 2
    assert len(reqs[1]["messages"]) == 1  # only its own prompt, nothing carried over
    assert "CASE-A" not in json.dumps(reqs[1])
    assert "session" not in json.dumps(reqs[1]).lower()
    assert reqs[0]["id"] != reqs[1]["id"]


def test_pi_provenance_reports_actual_provider_and_model(fake_bridge, monkeypatch):
    fake_bridge()
    monkeypatch.setenv("LLM_BACKEND", "pi")
    monkeypatch.setenv("PI_MODEL", "something-else-requested")
    from src.run.run_all import _llm_info

    assert _llm_info() == {"provider": "pi/openai-codex", "model": "fake-codex-model", "reasoning_effort": "low"}


def test_llm_info_groq_unchanged(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "groq")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    from src.run.run_all import _llm_info

    assert _llm_info() == {"provider": "groq", "model": "openai/gpt-oss-120b"}


# -- real Node bridge (no network): protocol-only stdout on a startup error ---------

def _node_ok() -> str | None:
    node = os.environ.get("PI_NODE") or shutil.which("node")
    bridge_dir = Path(__file__).resolve().parents[1] / "pi_bridge"
    if not node or not (bridge_dir / "node_modules").exists():
        return None
    try:
        version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except OSError:
        return None
    major, minor = (int(x) for x in version.lstrip("v").split(".")[:2])
    return node if (major, minor) >= (22, 19) else None


@pytest.mark.skipif(_node_ok() is None, reason="needs Node >= 22.19 and `npm ci` in pi_bridge/")
def test_real_bridge_unknown_provider_startup_error():
    bridge = PiBridge(
        command=[_node_ok(), str(pi_bridge.BRIDGE_SCRIPT), "serve"],
        env={"PI_PROVIDER": "no-such-provider", "PI_MODEL": "x"},
        startup_timeout_s=60,
    )
    with pytest.raises(PiBridgeError) as exc:
        bridge.start()
    assert exc.value.kind == "provider"
    bridge.close()


@pytest.mark.skipif(
    _node_ok() is None or os.environ.get("PI_LIVE_TESTS") != "1",
    reason="opt-in (PI_LIVE_TESTS=1): needs Node >= 22.19 and a Pi openai-codex login; makes no model request",
)
def test_real_bridge_shutdown_exits_cleanly():
    """Regression: a `shutdown` request used to destroy stdin without closing
    readline, so serve() never settled and Node exited 13 with 'Detected
    unsettled top-level await'."""
    proc = subprocess.run(
        [_node_ok(), str(pi_bridge.BRIDGE_SCRIPT), "serve"],
        input='{"id": 1, "op": "ping"}\n{"id": 2, "op": "shutdown"}\n',
        capture_output=True, text=True, timeout=90,
    )
    frames = [json.loads(line) for line in proc.stdout.splitlines()]
    assert frames[0]["event"] == "ready"
    assert frames[-1] == {"id": 2, "ok": True, "result": {"shutdown": True}}
    assert proc.returncode == 0
    assert "unsettled top-level await" not in proc.stderr
