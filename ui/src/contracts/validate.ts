/**
 * Validation shared by the browser data client and `scripts/sync-data.mjs`
 * (which bundles this module with esbuild). Pure functions only: no fetch, no
 * filesystem, no mutation of the input.
 */
import type { ZodError } from "zod";
import {
  AnswerDisplaySchema,
  AnswerFileSchema,
  answerSemanticIssues,
  type AnswerFile,
  type AnswerView,
} from "./answer";
import { BatchSummarySchema, type BatchSummary } from "./summary";
import {
  KNOWN_NODE_TYPES,
  KNOWN_ROLES,
  KNOWN_STEP_NODES,
  KNOWN_VIA,
  TraceFileSchema,
  type TraceFile,
} from "./trace";

export type Severity = "error" | "warning" | "info";
export type DiagnosticScope = "answer" | "trace" | "summary" | "manifest" | "crosscheck";

export interface Diagnostic {
  level: Severity;
  scope: DiagnosticScope;
  caseId?: string;
  path?: string;
  message: string;
}

export function zodIssues(error: ZodError, scope: DiagnosticScope, caseId?: string): Diagnostic[] {
  return error.issues.slice(0, 40).map((issue) => ({
    level: "error" as const,
    scope,
    caseId,
    path: issue.path.join(".") || "<root>",
    message: issue.message,
  }));
}

/* ------------------------------------------------------------------------ */

export interface AnswerValidation {
  /** Present when the file satisfies the frozen contract exactly. */
  strict?: AnswerFile;
  /** Present when the file can be rendered (strict or lenient). */
  view?: AnswerView;
  contractValid: boolean;
  diagnostics: Diagnostic[];
}

export function validateAnswer(raw: unknown, expectedCaseId?: string): AnswerValidation {
  const diagnostics: Diagnostic[] = [];
  const strict = AnswerFileSchema.safeParse(raw);
  const view = strict.success ? undefined : AnswerDisplaySchema.safeParse(raw);
  const caseId =
    (strict.success ? strict.data.case_id : view?.success ? view.data.case_id : undefined) ?? expectedCaseId;

  if (!strict.success) diagnostics.push(...zodIssues(strict.error, "answer", caseId));

  const rendered: AnswerView | undefined = strict.success
    ? (strict.data as AnswerView)
    : view?.success
      ? view.data
      : undefined;

  if (!rendered) {
    diagnostics.push({
      level: "error",
      scope: "answer",
      caseId,
      message: "answer file cannot be rendered: it lacks a case_id or a recognisable structure",
    });
    return { contractValid: false, diagnostics };
  }

  if (expectedCaseId && rendered.case_id !== expectedCaseId) {
    diagnostics.push({
      level: "error",
      scope: "answer",
      caseId: expectedCaseId,
      path: "case_id",
      message: `file ${expectedCaseId}.json declares case_id ${JSON.stringify(rendered.case_id)}`,
    });
  }

  const semantic = answerSemanticIssues(rendered);
  diagnostics.push(
    ...semantic.map((issue) => ({ ...issue, scope: "answer" as const, caseId: rendered.case_id })),
  );

  const contractValid =
    strict.success && !diagnostics.some((d) => d.level === "error");
  return {
    strict: strict.success ? strict.data : undefined,
    view: rendered,
    contractValid,
    diagnostics,
  };
}

/* ------------------------------------------------------------------------ */

export const GRAPH_RENDER_CAP = 250;

const PLACEHOLDER = /\b(mock|placeholder|lorem ipsum|todo|dummy|fake)\b/i;

export interface TraceValidation {
  trace?: TraceFile;
  diagnostics: Diagnostic[];
}

export function validateTrace(raw: unknown, expectedCaseId?: string): TraceValidation {
  const parsed = TraceFileSchema.safeParse(raw);
  if (!parsed.success) {
    return {
      diagnostics: [
        {
          level: "error",
          scope: "trace",
          caseId: expectedCaseId,
          message: "trace file does not satisfy the trace contract; the answer is shown without it",
        },
        ...zodIssues(parsed.error, "trace", expectedCaseId),
      ],
    };
  }
  const t = parsed.data;
  const d: Diagnostic[] = [];
  const add = (level: Severity, message: string, path?: string) =>
    d.push({ level, scope: "trace", caseId: t.case_id, path, message });

  if (expectedCaseId && t.case_id !== expectedCaseId) {
    add("error", `trace file for ${expectedCaseId} declares case_id ${JSON.stringify(t.case_id)}`, "case_id");
  }
  const knownSteps = new Set<string>(KNOWN_STEP_NODES);
  t.steps.forEach((s, i) => {
    if (!knownSteps.has(s.node)) add("info", `unrecognised step node "${s.node}" rendered generically`, `steps[${i}].node`);
    s.tool_calls.forEach((call, j) => {
      if (!(KNOWN_VIA as readonly string[]).includes(call.via)) {
        add("warning", `tool ${call.tool} reports unknown invocation path via="${call.via}"`, `steps[${i}].tool_calls[${j}].via`);
      }
    });
    if (PLACEHOLDER.test(s.summary)) add("warning", `step ${s.step} summary looks like placeholder text`, `steps[${i}].summary`);
  });

  const nodeIds = new Set<string>();
  const types = new Set<string>(KNOWN_NODE_TYPES);
  const roles = new Set<string>(KNOWN_ROLES);
  const unknownTypes = new Set<string>();
  const unknownRoles = new Set<string>();
  for (const n of t.subgraph.nodes) {
    if (nodeIds.has(n.id)) add("warning", `duplicate graph node id ${n.id}`, "subgraph.nodes");
    nodeIds.add(n.id);
    if (!types.has(n.type)) unknownTypes.add(n.type);
    if (!roles.has(n.role)) unknownRoles.add(n.role);
    const ts = n.attrs["ts"] ?? n.attrs["timestamp"];
    if (typeof ts === "string" && t.cutoff_ts && ts > t.cutoff_ts && n.role !== "this_case") {
      add("error", `graph node ${n.id} is timestamped ${ts}, after the case cutoff ${t.cutoff_ts}`, "subgraph.nodes");
    }
  }
  if (unknownTypes.size) add("info", `unrecognised node type(s) rendered generically: ${[...unknownTypes].join(", ")}`);
  if (unknownRoles.size) add("info", `unrecognised node role(s) rendered as context: ${[...unknownRoles].join(", ")}`);
  const dangling = t.subgraph.edges.filter((e) => !nodeIds.has(e.source) || !nodeIds.has(e.target)).length;
  if (dangling) add("warning", `${dangling} edge(s) reference nodes missing from the subgraph and are not drawn`);
  if (t.subgraph.nodes.length > GRAPH_RENDER_CAP) {
    add("info", `subgraph has ${t.subgraph.nodes.length} nodes; the view renders the first ${GRAPH_RENDER_CAP} by priority`);
  }
  if (t.graph_write.written && !t.graph_write.read_back_ok) {
    add("warning", "graph write reported but read-back did not succeed; memory is treated as unverified");
  }
  if (!t.validation.passed) add("warning", "backend validation did not pass for this case");
  return { trace: t, diagnostics: d };
}

/** Cross-checks between one answer and its trace. Nothing is repaired. */
export function crossCheckAnswerTrace(a: AnswerView, t: TraceFile): Diagnostic[] {
  const d: Diagnostic[] = [];
  const add = (level: Severity, message: string, path?: string) =>
    d.push({ level, scope: "crosscheck", caseId: a.case_id, path, message });

  if (a.case_id !== t.case_id) add("error", `answer case_id ${a.case_id} ≠ trace case_id ${t.case_id}`);
  if (a.case.written_to_graph !== t.graph_write.written) {
    add("error", `answer written_to_graph=${String(a.case.written_to_graph)} but trace graph_write.written=${String(t.graph_write.written)}`);
  }
  if (a.case.graph_case_id && t.graph_write.graph_case_id && a.case.graph_case_id !== t.graph_write.graph_case_id) {
    add("error", `graph_case_id differs: answer ${a.case.graph_case_id}, trace ${t.graph_write.graph_case_id}`);
  }
  const last = t.probability_timeline[t.probability_timeline.length - 1];
  if (last && Math.abs(last.fraud_probability - a.case.fraud_probability) > 0.005) {
    add("warning", `final timeline probability ${last.fraud_probability} ≠ answer probability ${a.case.fraud_probability}`);
  }
  const traced = t.steps.reduce((sum, s) => sum + s.tool_calls.length, 0);
  if (Number.isFinite(a.tool_calls) && traced > 0 && traced !== a.tool_calls) {
    add("info", `answer reports ${a.tool_calls} tool calls; the trace lists ${traced}`);
  }
  const retrieved = new Set(t.retrieval.prior_cases.map((p) => p.case_id));
  const unretrieved = a.case.similar_prior_cases.filter((id) => !retrieved.has(id));
  if (unretrieved.length) {
    add("warning", `similar_prior_cases not present in trace retrieval: ${unretrieved.join(", ")}`, "case.similar_prior_cases");
  }
  const cited = new Set(a.case.similar_prior_cases);
  const usedNotCited = t.retrieval.prior_cases.filter((p) => p.used && !cited.has(p.case_id)).map((p) => p.case_id);
  if (usedNotCited.length) add("info", `trace marks ${usedNotCited.join(", ")} as used but the answer does not cite them`);
  if (t.trigger.flagged_txn_id && a.case.verdict === "fraud" && a.case.affected_txn_ids.length === 1 && a.case.affected_txn_ids[0] === t.trigger.flagged_txn_id) {
    add("info", "affected episode consists only of the trigger transaction");
  }
  return d;
}

/* ------------------------------------------------------------------------ */

export interface SummaryValidation {
  summary?: BatchSummary;
  diagnostics: Diagnostic[];
}

export function validateSummary(raw: unknown): SummaryValidation {
  const parsed = BatchSummarySchema.safeParse(raw);
  if (!parsed.success) {
    return {
      diagnostics: [
        { level: "error", scope: "summary", message: "batch_summary.json does not satisfy the summary contract" },
        ...zodIssues(parsed.error, "summary"),
      ],
    };
  }
  const s = parsed.data;
  const d: Diagnostic[] = [];
  const add = (level: Severity, message: string) => d.push({ level, scope: "summary", message });
  if (s.cases_total !== s.cases.length) add("error", `cases_total=${s.cases_total} but ${s.cases.length} case rows are listed`);
  const count = (pred: (c: BatchSummary["cases"][number]) => boolean) => s.cases.filter(pred).length;
  if (count((c) => c.validation_passed) !== s.cases_valid) add("error", "cases_valid disagrees with the per-case validation_passed flags");
  if (count((c) => c.written_to_graph) !== s.cases_written_to_graph) add("error", "cases_written_to_graph disagrees with the per-case flags");
  if (count((c) => c.sar_file) !== s.sar_filed) add("error", "sar_filed disagrees with the per-case sar_file flags");
  if (count((c) => c.actions_changed) !== s.actions_changed) add("error", "actions_changed disagrees with the per-case flags");
  const exposure = s.cases.reduce((sum, c) => sum + c.exposure_usd, 0);
  if (Math.abs(exposure - s.total_exposure_usd) > 0.02) add("error", `total_exposure_usd=${s.total_exposure_usd} but case rows sum to ${exposure.toFixed(2)}`);
  const verdictTotal = Object.values(s.verdicts).reduce((x, y) => x + y, 0);
  if (verdictTotal !== s.cases.length) add("warning", "verdict distribution does not add up to the number of cases");
  const ids = s.cases.map((c) => c.case_id);
  if (new Set(ids).size !== ids.length) add("error", "batch summary lists a case more than once");
  return { summary: s, diagnostics: d };
}

/** Summary rows versus the answer files they describe. */
export function crossCheckSummaryAnswers(s: BatchSummary, answers: AnswerView[]): Diagnostic[] {
  const d: Diagnostic[] = [];
  const byId = new Map(answers.map((a) => [a.case_id, a]));
  for (const row of s.cases) {
    const a = byId.get(row.case_id);
    const add = (level: Severity, message: string) => d.push({ level, scope: "crosscheck", caseId: row.case_id, message });
    if (!a) {
      add("warning", "listed in batch_summary.json but no answer file was synchronized");
      continue;
    }
    if (row.verdict !== a.case.verdict) add("error", `summary verdict ${row.verdict} ≠ answer verdict ${a.case.verdict}`);
    if (Math.abs(row.fraud_probability - a.case.fraud_probability) > 0.005) add("error", "summary probability ≠ answer probability");
    if (row.pattern !== a.case.pattern) add("error", `summary pattern ${row.pattern} ≠ answer pattern ${a.case.pattern}`);
    if (row.status !== a.case.status) add("error", `summary status ${row.status} ≠ answer status ${a.case.status}`);
    if (Math.abs(row.exposure_usd - a.case.exposure_usd) > 0.01) add("error", "summary exposure ≠ answer exposure");
    if (row.sar_file !== a.sar.file) add("error", "summary sar_file ≠ answer sar.file");
    if (row.written_to_graph !== a.case.written_to_graph) add("error", "summary written_to_graph ≠ answer written_to_graph");
    const fin = a.next_best_actions.final.map((x) => x.action).join(",");
    if (row.final_actions.join(",") !== fin) add("error", "summary final_actions ≠ answer final actions");
    const ini = a.next_best_actions.initial.map((x) => x.action).join(",");
    if (row.initial_actions.join(",") !== ini) add("error", "summary initial_actions ≠ answer initial actions");
  }
  for (const a of answers) {
    if (!s.cases.some((c) => c.case_id === a.case_id)) {
      d.push({ level: "warning", scope: "crosscheck", caseId: a.case_id, message: "answer file is not listed in batch_summary.json" });
    }
  }
  return d;
}

/* ------------------------------------------------------------------------ */

export const OFFICIAL_CASE_IDS = Array.from({ length: 20 }, (_, i) => `HHG-${String(i + 1).padStart(3, "0")}`);

/** Filenames the sync script accepts: `HHG-001.json`, `DEMO-004.json` … */
export const CASE_FILE_RE = /^([A-Z][A-Z0-9]*-\d{3})\.json$/;
export const TRACE_FILE_RE = /^([A-Z][A-Z0-9]*-\d{3})\.trace\.json$/;
