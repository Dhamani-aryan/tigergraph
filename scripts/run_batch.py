"""Task 14: run the fraud-investigation pipeline over case_pack.csv.

Usage: run as a MODULE (`-m`), not as a bare script -- like every other
file in scripts/, this needs the repo root on sys.path for `from src...`
imports, which only `-m` gives you from the repo root:

    python -m scripts.run_batch                          # all 20 cases
    python -m scripts.run_batch --case-ids HHG-008,HHG-011 --merge
    python -m scripts.run_batch --cases-dir cases --runs-dir runs/latest

`--merge` reads the existing runs/latest/batch_summary.json first and
replaces only the entries for the cases actually run this time, so a
partial rerun (e.g. after a rate-limit failure, or after a bug fix that
only needs to be re-verified on a few cases) doesn't have to redo
everything and doesn't silently drop cases already on disk.

Requires GROQ_API_KEY (or LLM_BACKEND=ollama, or LLM_BACKEND=pi with
PI_PROVIDER/PI_MODEL and a Pi OpenAI Codex login -- see pi_bridge/README.md)
and TG_* in .env, and a
reachable TigerGraph workspace -- see src/run/run_all.py's own docstring
for exactly which MCP tools this needs (INVESTIGATION_ALLOWED_TOOLS).

Operational note: if you need to stop a run partway through, verify the
underlying python process actually exited (e.g. `Get-CimInstance
Win32_Process -Filter "Name='python.exe'"` on Windows) before starting
another -- a wrapping shell/task "stop" has been observed to NOT reliably
kill the child process, which then keeps running and competing for the
same TigerGraph connection and LLM rate limit as the new run.
"""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

load_dotenv()

from src.run.run_all import run_all  # noqa: E402 -- must follow load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case-ids", default=None,
        help="Comma-separated case_ids to run instead of all 20, e.g. HHG-008,HHG-011",
    )
    parser.add_argument(
        "--merge", action="store_true",
        help="Merge into the existing batch_summary.json instead of starting fresh.",
    )
    parser.add_argument("--cases-dir", default="cases")
    parser.add_argument("--runs-dir", default="runs/latest")
    parser.add_argument("--data-dir", default=None, help="Override the CSV directory (default: auto-detected).")
    args = parser.parse_args()

    case_ids = args.case_ids.split(",") if args.case_ids else None
    result = asyncio.run(
        run_all(
            cases_dir=args.cases_dir,
            runs_dir=args.runs_dir,
            data_dir=args.data_dir,
            case_ids=case_ids,
            merge=args.merge,
        )
    )
    print(
        f"DONE {result.get('cases_completed')}/{result.get('cases_total')} "
        f"valid={result.get('cases_valid')} failed={result.get('cases_failed')}"
    )
    ok = not result.get("errors") and result.get("cases_valid") == result.get("cases_completed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
