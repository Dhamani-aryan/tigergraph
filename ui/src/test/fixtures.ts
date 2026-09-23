/** Test helpers built from the committed interface fixtures (ui/fixtures). */
import d1 from "../../fixtures/cases/DEMO-001.json";
import d2 from "../../fixtures/cases/DEMO-002.json";
import d3 from "../../fixtures/cases/DEMO-003.json";
import d4 from "../../fixtures/cases/DEMO-004.json";
import d5 from "../../fixtures/cases/DEMO-005.json";
import d6 from "../../fixtures/cases/DEMO-006.json";
import t1 from "../../fixtures/traces/DEMO-001.trace.json";
import t2 from "../../fixtures/traces/DEMO-002.trace.json";
import t4 from "../../fixtures/traces/DEMO-004.trace.json";
import t5 from "../../fixtures/traces/DEMO-005.trace.json";
import t6 from "../../fixtures/traces/DEMO-006.trace.json";
import summary from "../../fixtures/batch_summary.json";
import { DataClient, type FetchLike } from "@/data/client";

export const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

export const FIXTURE_ANSWERS: Record<string, unknown> = { "DEMO-001": d1, "DEMO-002": d2, "DEMO-003": d3, "DEMO-004": d4, "DEMO-005": d5, "DEMO-006": d6 };
export const FIXTURE_TRACES: Record<string, unknown> = { "DEMO-001": t1, "DEMO-002": t2, "DEMO-004": t4, "DEMO-005": t5, "DEMO-006": t6 };
export const FIXTURE_SUMMARY = summary as unknown;

export function fixtureManifest(extra: Record<string, unknown> = {}) {
  return {
    mode: "fixtures",
    generated_at: "2026-09-23T00:00:00Z",
    cases_found: 6,
    valid_cases: 6,
    traces_found: 4,
    summary_found: true,
    errors: [],
    warnings: [],
    case_files: Object.keys(FIXTURE_ANSWERS),
    trace_files: Object.keys(FIXTURE_TRACES),
    ...extra,
  };
}

/** A fetch that serves an in-memory `public/data` tree. Unknown paths 404. */
export function memoryFetch(files: Record<string, unknown>): FetchLike & { calls: string[] } {
  const calls: string[] = [];
  const fn = (async (url: string) => {
    calls.push(url);
    const key = url.replace(/^.*?data\//, "");
    if (!(key in files)) return { ok: false, status: 404, text: async () => "" };
    const v = files[key];
    return { ok: true, status: 200, text: async () => (typeof v === "string" ? v : JSON.stringify(v)) };
  }) as FetchLike & { calls: string[] };
  fn.calls = calls;
  return fn;
}

export function fixtureFiles(): Record<string, unknown> {
  const files: Record<string, unknown> = { "data-manifest.json": fixtureManifest(), "batch_summary.json": FIXTURE_SUMMARY };
  for (const [id, a] of Object.entries(FIXTURE_ANSWERS)) files[`cases/${id}.json`] = a;
  for (const [id, t] of Object.entries(FIXTURE_TRACES)) files[`traces/${id}.trace.json`] = t;
  return files;
}

export const fixtureClient = (files = fixtureFiles()) => new DataClient("./data", memoryFetch(files));

/**
 * Twenty contract-valid "official-shaped" answers (HHG-001…HHG-020) cloned from
 * the legitimate fixture, with a consistent batch summary. For mode tests only.
 */
export function officialFiles(): Record<string, unknown> {
  const base = FIXTURE_ANSWERS["DEMO-002"] as { case_id: string; case: { graph_case_id: string } };
  const baseRow = (FIXTURE_SUMMARY as { cases: Record<string, unknown>[] }).cases[1]!;
  const ids = Array.from({ length: 20 }, (_, i) => `HHG-${String(i + 1).padStart(3, "0")}`);
  const files: Record<string, unknown> = {};
  const rows = ids.map((id) => {
    const a = clone(base);
    a.case_id = id;
    a.case.graph_case_id = `CASE-${id}`;
    files[`cases/${id}.json`] = a;
    return { ...clone(baseRow), case_id: id };
  });
  const s = clone(FIXTURE_SUMMARY) as Record<string, unknown>;
  Object.assign(s, {
    run_id: "run-2026-09-24",
    git_commit: "abc1234",
    cases_total: 20,
    cases_valid: 20,
    cases_written_to_graph: 20,
    verdicts: { legitimate: 20 },
    patterns: { none: 20 },
    sar_filed: 0,
    actions_changed: 0,
    total_exposure_usd: 0,
    cases: rows,
  });
  files["batch_summary.json"] = s;
  files["data-manifest.json"] = {
    mode: "official",
    generated_at: "2026-09-24T00:00:00Z",
    cases_found: 20,
    valid_cases: 20,
    traces_found: 0,
    summary_found: true,
    errors: [],
    warnings: [],
    case_files: ids,
    trace_files: [],
  };
  return files;
}
