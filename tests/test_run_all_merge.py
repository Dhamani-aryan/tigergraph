from __future__ import annotations

import json
from pathlib import Path

from src.run.run_all import _git_commit, _llm_info, _load_existing_summary


def test_load_existing_summary_missing_file_returns_empty(tmp_path: Path):
    cases, errors = _load_existing_summary(tmp_path)
    assert cases == []
    assert errors == {}


def test_load_existing_summary_reads_cases_and_errors(tmp_path: Path):
    (tmp_path / "batch_summary.json").write_text(
        json.dumps({"cases": [{"case_id": "HHG-001"}], "errors": {"HHG-002": "boom"}}),
        encoding="utf-8",
    )
    cases, errors = _load_existing_summary(tmp_path)
    assert cases == [{"case_id": "HHG-001"}]
    assert errors == {"HHG-002": "boom"}


def test_load_existing_summary_malformed_json_returns_empty(tmp_path: Path):
    (tmp_path / "batch_summary.json").write_text("{not valid", encoding="utf-8")
    cases, errors = _load_existing_summary(tmp_path)
    assert cases == []
    assert errors == {}


def test_git_commit_never_raises_and_returns_a_string():
    # Regression coverage for the UI contract fix: ui/src/contracts/
    # summary.ts requires run_id/git_commit/llm on batch_summary.json,
    # which run_all.py never produced -- BatchSummarySchema.safeParse
    # would fail on every real run. This just confirms the helper is
    # always safe to call (a git failure must not crash the batch).
    assert isinstance(_git_commit(), str)


def test_llm_info_reflects_env(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "groq")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    assert _llm_info() == {"provider": "groq", "model": "openai/gpt-oss-120b"}

    monkeypatch.setenv("LLM_BACKEND", "ollama")
    assert _llm_info() == {"provider": "ollama", "model": "qwen3:4b-instruct"}
