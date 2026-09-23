from __future__ import annotations

import json
from pathlib import Path

from src.run.run_all import _load_existing_summary


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
