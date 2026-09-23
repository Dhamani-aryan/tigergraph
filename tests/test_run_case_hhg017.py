import sys

import pytest

from src.run.run_case import run_single_case
from src.tg_client import TigerGraphMCP


@pytest.mark.asyncio
async def test_hhg017_end_to_end_produces_valid_answer():
    case_row = {
        "case_id": "HHG-017",
        "opened_at": "2016-11-12 00:46:24",
        "trigger_type": "risk_score",
        "trigger_text": "Real-time model scored transaction 3450629 ($100.09, online) at 0.57. Review and decide.",
        "flagged_txn_id": 3450629,
        "card_id": "C04570-K1",
        "customer_id": "C04570",
        "risk_score": 0.57,
    }
    async with TigerGraphMCP() as tg:
        answer = await run_single_case(tg, case_row)

    # The LLM's free-text evidence claims/summary can contain non-cp1252
    # characters (confirmed live: a Unicode non-breaking hyphen U+2011 in an
    # evidence claim crashed a plain `print()` on Windows' cp1252 console
    # with `UnicodeEncodeError: 'charmap' codec can't encode character`).
    # Write raw UTF-8 bytes directly so this diagnostic dump can't crash the
    # test regardless of the console's active code page.
    sys.stdout.buffer.write(answer.model_dump_json(indent=2).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")

    assert answer.case_id == "HHG-017"
    assert answer.case.verdict in ("fraud", "legitimate", "uncertain")
    assert answer.next_best_actions.initial
    assert answer.tool_calls > 0
