/**
 * Display derivations. These read backend fields and classify them for
 * presentation; they never compute a fraud outcome, policy decision, SAR
 * filing or graph-write result.
 */
import type { AnswerView, BatchCaseSummary, BatchSummary, Diagnostic, TraceFile } from "@/contracts";
import type { CaseEntry, DataBundle, TraceEntry } from "./client";

export type MemoryState = "verified" | "readback_failed" | "written_unverified" | "not_written" | "conflict" | "unknown";

/**
 * Graph-memory state. Success requires the answer's write flag AND a trace
 * reporting a successful independent read-back; anything less is shown as
 * unverified or failed.
 */
export function memoryState(answer?: AnswerView, trace?: TraceFile): MemoryState {
  if (!answer) return "unknown";
  if (!answer.case.written_to_graph) return trace?.graph_write.written ? "conflict" : "not_written";
  if (!trace) return "written_unverified";
  if (!trace.graph_write.written) return "conflict";
  return trace.graph_write.read_back_ok ? "verified" : "readback_failed";
}

export const MEMORY_LABEL: Record<MemoryState, string> = {
  verified: "Written · read back",
  readback_failed: "Read-back failed",
  written_unverified: "Written · read-back unverified",
  not_written: "Not written",
  conflict: "Write state conflicts",
  unknown: "Unknown",
};

export type BackendValidation = "passed" | "failed" | "not_reported";

/** The backend validator's verdict, from the trace or the batch summary only. */
export function backendValidation(trace?: TraceFile, row?: BatchCaseSummary): BackendValidation {
  if (trace) return trace.validation.passed ? "passed" : "failed";
  if (row) return row.validation_passed ? "passed" : "failed";
  return "not_reported";
}

export function actionsChanged(a: AnswerView): boolean {
  const { initial, final } = a.next_best_actions;
  return JSON.stringify(initial.map((x) => [x.action, x.route, x.reason])) !== JSON.stringify(final.map((x) => [x.action, x.route, x.reason]));
}

export interface ActionDiffItem {
  action: string;
  status: "kept" | "added" | "removed" | "rerouted";
  initialRoute?: string;
  finalRoute?: string;
}

/** Set-style diff of action names between the initial and final phases. */
export function diffActions(a: AnswerView): ActionDiffItem[] {
  const ini = new Map(a.next_best_actions.initial.map((x) => [x.action, x.route]));
  const fin = new Map(a.next_best_actions.final.map((x) => [x.action, x.route]));
  const items: ActionDiffItem[] = [];
  for (const [action, route] of fin) {
    if (!ini.has(action)) items.push({ action, status: "added", finalRoute: route });
    else if (ini.get(action) !== route) items.push({ action, status: "rerouted", initialRoute: ini.get(action), finalRoute: route });
    else items.push({ action, status: "kept", initialRoute: route, finalRoute: route });
  }
  for (const [action, route] of ini) if (!fin.has(action)) items.push({ action, status: "removed", initialRoute: route });
  return items;
}

/* ------------------------------------------------------------------ rows */

export interface CaseRow {
  caseId: string;
  entry: CaseEntry;
  answer?: AnswerView;
  summaryRow?: BatchCaseSummary;
  trace?: TraceFile;
  traceEntry?: TraceEntry;
  triggerType?: string;
  customerId?: string;
  cardId?: string;
  verdict?: string;
  probability?: number;
  pattern?: string;
  exposure?: number;
  initialFirst?: string;
  finalFirst?: string;
  changed?: boolean;
  sar?: boolean;
  memory: MemoryState;
  backend: BackendValidation;
  contractValid: boolean;
  errorCount: number;
  warningCount: number;
  searchText: string;
}

export function buildRows(bundle: DataBundle): CaseRow[] {
  const byId = new Map((bundle.summary?.cases ?? []).map((c) => [c.case_id, c]));
  return bundle.cases.map((entry) => {
    const answer = entry.answer?.view;
    const summaryRow = byId.get(entry.caseId);
    const traceEntry = bundle.traces.get(entry.caseId);
    const trace = traceEntry?.validation?.trace;
    const diags = caseDiagnostics(bundle, entry.caseId);
    const ids = [
      entry.caseId,
      summaryRow?.card_id,
      summaryRow?.customer_id,
      trace?.trigger.card_id,
      trace?.trigger.customer_id,
      ...(answer?.sar.subjects ?? []),
      ...(answer?.case.connected_card_ids ?? []),
      ...(answer?.case.evidence.flatMap((e) => e.entity_ids) ?? []),
    ];
    return {
      caseId: entry.caseId,
      entry,
      answer,
      summaryRow,
      trace,
      traceEntry,
      triggerType: summaryRow?.trigger_type ?? trace?.trigger.trigger_type,
      customerId: summaryRow?.customer_id ?? trace?.trigger.customer_id,
      cardId: summaryRow?.card_id ?? trace?.trigger.card_id,
      verdict: answer?.case.verdict,
      probability: answer?.case.fraud_probability,
      pattern: answer?.case.pattern,
      exposure: answer?.case.exposure_usd,
      initialFirst: answer?.next_best_actions.initial[0]?.action,
      finalFirst: answer?.next_best_actions.final[0]?.action,
      changed: answer ? actionsChanged(answer) : undefined,
      sar: answer?.sar.file,
      memory: memoryState(answer, trace),
      backend: backendValidation(trace, summaryRow),
      contractValid: entry.answer?.contractValid ?? false,
      errorCount: diags.filter((d) => d.level === "error").length,
      warningCount: diags.filter((d) => d.level === "warning").length,
      searchText: ids.filter(Boolean).join(" ").toLowerCase(),
    };
  });
}

export function caseDiagnostics(bundle: DataBundle, caseId: string): Diagnostic[] {
  return bundle.diagnostics.filter((d) => d.caseId === caseId);
}

/* -------------------------------------------------------------- overview */

export interface OverviewStats {
  source: "batch_summary" | "derived";
  casesTotal: number;
  verdicts: Record<string, number>;
  patterns: Record<string, number>;
  sarFiled: number;
  totalExposure: number;
  actionsChanged: number;
  /** Undefined when not derivable honestly. */
  totalToolCalls?: number;
  totalTokens?: number;
  avgLatency?: number;
  casesValid?: number;
  contractValid: number;
  writtenToGraph: number;
  readBackVerified: number;
  llm?: { provider: string; model: string };
  runId?: string;
  gitCommit?: string;
  generatedAt?: string;
}

const tally = (values: (string | undefined)[]) =>
  values.reduce<Record<string, number>>((acc, v) => {
    const key = v || "unknown";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});

/**
 * Uses batch_summary.json when present and valid. Otherwise derives a
 * temporary overview from the answer files alone; totals that answer files
 * report themselves (tool_calls, tokens, latency) are summed and labeled as
 * derived, and backend validation is left unknown.
 */
export function overviewStats(bundle: DataBundle, rows: CaseRow[]): OverviewStats {
  const readBackVerified = rows.filter((r) => r.memory === "verified").length;
  const contractValid = rows.filter((r) => r.contractValid).length;
  const s: BatchSummary | undefined = bundle.summary;
  if (s) {
    return {
      source: "batch_summary",
      casesTotal: s.cases_total,
      verdicts: s.verdicts,
      patterns: s.patterns,
      sarFiled: s.sar_filed,
      totalExposure: s.total_exposure_usd,
      actionsChanged: s.actions_changed,
      totalToolCalls: s.total_tool_calls,
      totalTokens: s.total_tokens,
      avgLatency: s.avg_latency_s,
      casesValid: s.cases_valid,
      contractValid,
      writtenToGraph: s.cases_written_to_graph,
      readBackVerified,
      llm: s.llm,
      runId: s.run_id,
      gitCommit: s.git_commit,
      generatedAt: s.generated_at,
    };
  }
  const answers = rows.flatMap((r) => (r.answer ? [r.answer] : []));
  const finite = (xs: number[]) => xs.every((x) => Number.isFinite(x));
  const tools = answers.map((a) => a.tool_calls);
  const tokens = answers.map((a) => a.tokens);
  const lat = answers.map((a) => a.latency_s);
  const sum = (xs: number[]) => xs.reduce((x, y) => x + y, 0);
  return {
    source: "derived",
    casesTotal: rows.length,
    verdicts: tally(answers.map((a) => a.case.verdict)),
    patterns: tally(answers.map((a) => a.case.pattern)),
    sarFiled: answers.filter((a) => a.sar.file).length,
    totalExposure: sum(answers.map((a) => (Number.isFinite(a.case.exposure_usd) ? a.case.exposure_usd : 0))),
    actionsChanged: answers.filter(actionsChanged).length,
    totalToolCalls: answers.length && finite(tools) ? sum(tools) : undefined,
    totalTokens: answers.length && finite(tokens) ? sum(tokens) : undefined,
    avgLatency: answers.length && finite(lat) ? sum(lat) / answers.length : undefined,
    casesValid: undefined,
    contractValid,
    writtenToGraph: answers.filter((a) => a.case.written_to_graph).length,
    readBackVerified,
  };
}

/* --------------------------------------------------------------- filters */

export interface RowFilters {
  query: string;
  verdict: string;
  pattern: string;
  trigger: string;
  sar: "" | "filed" | "not_filed";
  changed: "" | "changed" | "unchanged";
}

export const EMPTY_FILTERS: RowFilters = { query: "", verdict: "", pattern: "", trigger: "", sar: "", changed: "" };

export function filterRows(rows: CaseRow[], f: RowFilters): CaseRow[] {
  const q = f.query.trim().toLowerCase();
  return rows.filter(
    (r) =>
      (!q || r.searchText.includes(q)) &&
      (!f.verdict || r.verdict === f.verdict) &&
      (!f.pattern || r.pattern === f.pattern) &&
      (!f.trigger || r.triggerType === f.trigger) &&
      (!f.sar || (f.sar === "filed" ? r.sar === true : r.sar === false)) &&
      (!f.changed || (f.changed === "changed" ? r.changed === true : r.changed === false)),
  );
}

export type SortKey = "caseId" | "trigger" | "verdict" | "probability" | "pattern" | "exposure" | "sar" | "memory" | "validation";

const MEMORY_ORDER: MemoryState[] = ["verified", "written_unverified", "readback_failed", "conflict", "not_written", "unknown"];

export function sortRows(rows: CaseRow[], key: SortKey, dir: "asc" | "desc"): CaseRow[] {
  const val = (r: CaseRow): string | number => {
    switch (key) {
      case "caseId":
        return r.caseId;
      case "trigger":
        return r.triggerType ?? "~";
      case "verdict":
        return r.verdict ?? "~";
      case "probability":
        return Number.isFinite(r.probability) ? (r.probability as number) : -1;
      case "pattern":
        return r.pattern ?? "~";
      case "exposure":
        return Number.isFinite(r.exposure) ? (r.exposure as number) : -1;
      case "sar":
        return r.sar ? 1 : 0;
      case "memory":
        return MEMORY_ORDER.indexOf(r.memory);
      case "validation":
        return (r.contractValid ? 0 : 2) + (r.backend === "passed" ? 0 : r.backend === "failed" ? 1 : 0.5);
    }
  };
  const sorted = [...rows].sort((a, b) => {
    const x = val(a);
    const y = val(b);
    const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
    return cmp || a.caseId.localeCompare(b.caseId);
  });
  return dir === "asc" ? sorted : sorted.reverse();
}
