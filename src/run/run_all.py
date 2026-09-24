from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Reliability fix (2026-09-24): a small gap between cases so a case that
# just made several heavy LLM calls doesn't hand its still-warm Groq TPM
# window straight to the next case -- cheap insurance on top of llm.py's
# own Retry-After handling, not a substitute for it.
INTER_CASE_PAUSE_S = 5

import pandas as pd

from src.run.dataset_index import _default_data_dir, load_dataset_index
from src.run.run_case import run_single_case_with_context
from src.run.trace_writer import build_trace
from src.run.validate_outputs import validate_answer, validate_semantics
from src.tg_client import INVESTIGATION_ALLOWED_TOOLS, TigerGraphMCP


def _git_commit() -> str:
    """Best-effort short commit hash -- the UI's batch_summary contract
    (ui/src/contracts/summary.ts, from the frontend team) uses this for
    traceability. Never fatal: an unclean checkout or a missing git binary
    just yields ''."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _llm_info() -> dict[str, str]:
    backend = os.environ.get("LLM_BACKEND", "groq")
    if backend == "pi":
        # Provenance comes from the running Pi bridge's own `ready` frame (the
        # provider/model it actually resolved from Pi's authenticated catalog),
        # not from what .env asked for.
        from src.agent.pi_bridge import bridge_provenance

        info = bridge_provenance()
        return {"provider": info["provider"], "model": info["model"], "reasoning_effort": info.get("reasoning_effort", "")}
    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b") if backend == "groq" else "qwen3:4b-instruct"
    return {"provider": backend, "model": model}


def _load_case_pack(data_dir: Path) -> list[dict]:
    df = pd.read_csv(data_dir / "case_pack.csv")
    return df.to_dict(orient="records")


def _load_existing_summary(runs_dir: Path) -> tuple[list[dict], dict[str, str]]:
    path = runs_dir / "batch_summary.json"
    if not path.exists():
        return [], {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], {}
    return existing.get("cases", []), existing.get("errors", {})


async def run_all(
    cases_dir: str | Path = "cases",
    runs_dir: str | Path = "runs/latest",
    data_dir: str | Path | None = None,
    case_ids: list[str] | None = None,
    merge: bool = False,
) -> dict:
    """Task 14: runs every case in case_pack.csv through the same
    LangGraph flow as run_single_case, in one shared TigerGraph MCP session
    (restricted to INVESTIGATION_ALLOWED_TOOLS -- see tg_client.py), writing
    `cases/<case_id>.json` and a `runs/latest/batch_summary.json` (the
    aggregate the UI's Overview screen reads -- see docs/frontend-spec.md
    §3.2). One case's failure does not abort the batch: it's recorded in
    `errors` and the run continues, since a live-service hiccup on case 7
    shouldn't cost cases 8-20 their results.

    `case_ids`, when given, restricts the run to just those cases (e.g. to
    re-run ones that previously failed or need to be regenerated with fixed
    code) rather than all 20. `merge=True` reads the existing
    `runs/latest/batch_summary.json` first and replaces only the entries for
    the cases actually run this time, recomputing every aggregate from the
    full merged set -- so a partial rerun doesn't need to redo cases that
    already succeeded, and the summary never silently drops cases that
    exist on disk but weren't touched this run.

    Operational note (2026-09-23): a `TaskStop` on the WRAPPING shell
    command was observed live to NOT reliably kill the underlying python
    process -- a "stopped" run kept writing case files and hitting Groq
    concurrently with the next run, corrupting several outputs (a stale
    process using pre-fix code) and rate-limiting both. Confirm no earlier
    run's process is still alive (e.g. `Get-CimInstance Win32_Process`)
    before starting a rerun, not just that the shell task reports stopped.
    """
    base = Path(data_dir) if data_dir else _default_data_dir()
    full_cases = _load_case_pack(base)
    cases = full_cases
    if case_ids is not None:
        wanted = set(case_ids)
        cases = [c for c in cases if c["case_id"] in wanted]
    out_dir = Path(cases_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = load_dataset_index(base)
    runs_path = Path(runs_dir)
    traces_dir = runs_path / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    llm_info = _llm_info()
    llm_provider = llm_info["provider"]
    llm_model = llm_info["model"]

    summary_cases: list[dict] = []
    errors: dict[str, str] = {}
    if merge:
        existing_cases, errors = _load_existing_summary(runs_path)
        run_ids = {c["case_id"] for c in cases}
        summary_cases = [c for c in existing_cases if c["case_id"] not in run_ids]
        errors = {cid: msg for cid, msg in errors.items() if cid not in run_ids}

    async with TigerGraphMCP(allowed_tools=INVESTIGATION_ALLOWED_TOOLS) as tg:
        for i, row in enumerate(cases):
            if i > 0:
                await asyncio.sleep(INTER_CASE_PAUSE_S)
            case_id = row["case_id"]
            print(f"--- {case_id} ---", flush=True)
            try:
                answer, context = await run_single_case_with_context(tg, row)
            except Exception as e:  # noqa: BLE001 -- see docstring: one bad case must not kill the batch
                print(f"  FAILED: {e}", flush=True)
                errors[case_id] = str(e)
                continue

            violations = validate_answer(answer, index)
            if violations:
                print(f"  VALIDATION ISSUES ({len(violations)}):", flush=True)
                for v in violations:
                    print(f"    - {v}", flush=True)

            path = out_dir / f"{case_id}.json"
            path.write_text(answer.model_dump_json(indent=2), encoding="utf-8")
            print(
                f"  wrote {path} (verdict={answer.case.verdict}, "
                f"written_to_graph={answer.case.written_to_graph}, tool_calls={answer.tool_calls})",
                flush=True,
            )

            # Trace fix (2026-09-24): powers the UI's Investigation/Graph
            # tabs (ui/src/contracts/trace.ts) -- see trace_writer.py's own
            # module docstring. One bad trace must not cost the case its
            # already-written, already-valid answer file.
            semantic: list[str] = []
            try:
                trace = build_trace(
                    row, answer, context, validation_errors=violations,
                    llm_provider=llm_provider, llm_model=llm_model,
                )
                semantic = validate_semantics(answer, index, trace)
                trace["validation"]["semantic_errors"] = semantic
                trace_path = traces_dir / f"{case_id}.trace.json"
                trace_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
                print(f"  wrote {trace_path}", flush=True)
            except Exception as e:  # noqa: BLE001
                semantic = validate_semantics(answer, index)
                print(f"  TRACE FAILED (answer is still valid): {e}", flush=True)
            if semantic:
                print(f"  SEMANTIC ISSUES ({len(semantic)}):", flush=True)
                for v in semantic:
                    print(f"    - {v}", flush=True)
            final_state = context.get("final_state") or {}
            decision = final_state.get("decision") or {}

            summary_cases.append({
                "case_id": case_id,
                "trigger_type": row.get("trigger_type"),
                "customer_id": row.get("customer_id"),
                "card_id": row.get("card_id"),
                "opened_at": str(row.get("opened_at")),
                "verdict": answer.case.verdict,
                "fraud_probability": answer.case.fraud_probability,
                "pattern": answer.case.pattern,
                "status": answer.case.status,
                "exposure_usd": answer.case.exposure_usd,
                "sar_file": answer.sar.file,
                "initial_actions": [a.action for a in answer.next_best_actions.initial],
                "final_actions": [a.action for a in answer.next_best_actions.final],
                "actions_changed": [a.action for a in answer.next_best_actions.initial]
                != [a.action for a in answer.next_best_actions.final],
                "validation_passed": not violations,
                "semantic_validation_passed": not semantic,
                "settled_by": decision.get("settled_by"),
                "independent_evidence_families": (final_state.get("families") or {}).get("suspicious", []),
                "benign_evidence_families": (final_state.get("families") or {}).get("benign", []),
                "simulated_response": (final_state.get("simulation") or {}).get("response"),
                "written_to_graph": answer.case.written_to_graph,
                "tool_calls": answer.tool_calls,
                "tokens": answer.tokens,
                "latency_s": answer.latency_s,
            })

    verdict_counts: dict[str, int] = {}
    pattern_counts: dict[str, int] = {}
    for c in summary_cases:
        verdict_counts[c["verdict"]] = verdict_counts.get(c["verdict"], 0) + 1
        pattern_counts[c["pattern"]] = pattern_counts.get(c["pattern"], 0) + 1

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    batch_summary = {
        # Added 2026-09-24 to match the UI's contract (ui/src/contracts/
        # summary.ts): run_id/git_commit/llm were REQUIRED fields there but
        # this function never produced them, so BatchSummarySchema.safeParse
        # would fail on every real run -- non-fatal (the UI degrades to
        # `summary: undefined`), but it meant the whole Overview KPI strip
        # would silently vanish even though every individual case file was
        # fine. Found by reading the frontend's actual Zod schema, not
        # guessed.
        "run_id": generated_at,
        "git_commit": _git_commit(),
        "llm": llm_info,
        "generated_at": generated_at,
        "cases_total": len(full_cases),
        "cases_completed": len(summary_cases),
        "cases_failed": len(errors),
        "cases_valid": sum(1 for c in summary_cases if c["validation_passed"]),
        "cases_semantic_valid": sum(1 for c in summary_cases if c.get("semantic_validation_passed")),
        "cases_written_to_graph": sum(1 for c in summary_cases if c["written_to_graph"]),
        "verdicts": verdict_counts,
        "patterns": pattern_counts,
        "sar_filed": sum(1 for c in summary_cases if c["sar_file"]),
        "actions_changed": sum(1 for c in summary_cases if c["actions_changed"]),
        "total_exposure_usd": round(sum(c["exposure_usd"] for c in summary_cases), 2),
        "total_tool_calls": sum(c["tool_calls"] for c in summary_cases),
        "total_tokens": sum(c["tokens"] for c in summary_cases),
        "avg_latency_s": (
            round(sum(c["latency_s"] for c in summary_cases) / len(summary_cases), 1)
            if summary_cases else 0.0
        ),
        "errors": errors,
        "cases": summary_cases,
    }
    runs_path = Path(runs_dir)
    runs_path.mkdir(parents=True, exist_ok=True)
    (runs_path / "batch_summary.json").write_text(json.dumps(batch_summary, indent=2), encoding="utf-8")
    if os.environ.get("LLM_BACKEND", "groq") == "pi":
        from src.agent.pi_bridge import close_bridge

        close_bridge()

    print(
        f"\nBatch complete: {len(summary_cases)}/{len(full_cases)} produced, "
        f"{batch_summary['cases_valid']}/{len(summary_cases)} valid, {len(errors)} failed.",
        flush=True,
    )
    return batch_summary


def main() -> int:
    result = asyncio.run(run_all())
    return 0 if not result.get("errors") and result.get("cases_valid") == result.get("cases_completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
