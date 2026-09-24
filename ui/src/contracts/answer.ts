/**
 * The frozen challenge answer-file contract (`cases/<case_id>.json`).
 *
 * Field names, nesting and vocabularies mirror the dataset README's Answer
 * Format section and the backend models (`src/agent/schemas.py`, and the
 * reference `domain/answer_models.py`). No frontend-only field is added here.
 *
 * Two schemas are exported:
 *  - `AnswerFileSchema` is strict: unknown keys and unknown enum values fail.
 *    Passing it is what "contract valid" means in this UI.
 *  - `AnswerDisplaySchema` is lenient: vocabularies relax to strings and
 *    unknown keys pass through, so an invalid official file can still be shown
 *    (with diagnostics) instead of crashing the page.
 *
 * Browser-side validation is diagnostic only; it never replaces the backend
 * validator and never repairs a file.
 */
import { z } from "zod";

export const PATTERNS = [
  "card_testing",
  "card_not_present_fraud",
  "card_not_present_new_device",
  "out_of_region_use",
  "account_takeover",
  "undocumented",
  "none",
] as const;

export const VERDICTS = ["fraud", "legitimate", "uncertain"] as const;
export const STATUSES = ["open", "closed_fraud", "closed_legitimate", "escalated"] as const;
export const ROUTES = ["auto", "L1", "L2"] as const;
export const EVIDENCE_SOURCES = ["graph", "document", "customer", "external"] as const;
export const EVIDENCE_REQUEST_TYPES = ["customer_validation", "step_up_auth", "analyst_info"] as const;

/** The fourteen policy actions, in policy order. */
export const ACTIONS = [
  "ALLOW_TRANSACTION",
  "DECLINE_TRANSACTION",
  "MONITOR_CARD",
  "MONITOR_CONNECTED_CARDS",
  "WARN_CUSTOMER",
  "VERIFY_WITH_CUSTOMER",
  "STEP_UP_AUTH",
  "BLOCK_CARD",
  "BLOCK_ALL_CARDS",
  "GENERATE_REPORT",
  "CREATE_CASE",
  "FILE_REPORT",
  "ESCALATE_TO_ANALYST",
  "CLOSE_NO_FRAUD",
] as const;

export type Pattern = (typeof PATTERNS)[number];
export type Verdict = (typeof VERDICTS)[number];
export type Status = (typeof STATUSES)[number];
export type Route = (typeof ROUTES)[number];
export type Action = (typeof ACTIONS)[number];
export type EvidenceSource = (typeof EVIDENCE_SOURCES)[number];
export type EvidenceRequestType = (typeof EVIDENCE_REQUEST_TYPES)[number];

/** IDs are strings. A numeric JSON value is a contract error, never coerced. */
const id = z.string();

export const EvidenceSchema = z
  .object({
    claim: z.string().min(1),
    source: z.enum(EVIDENCE_SOURCES),
    ref: z.string().min(1),
    entity_ids: z.array(id),
  })
  .strict();

export const ActionEntrySchema = z
  .object({
    action: z.enum(ACTIONS),
    route: z.enum(ROUTES),
    reason: z.string().min(1),
  })
  .strict();

export const EvidenceRequestSchema = z
  .object({
    type: z.enum(EVIDENCE_REQUEST_TYPES),
    asked_after_step: z.number().int().min(0),
    assumed_response: z.string().min(1),
  })
  .strict();

export const CaseRecordSchema = z
  .object({
    status: z.enum(STATUSES),
    verdict: z.enum(VERDICTS),
    fraud_probability: z.number().min(0).max(1),
    pattern: z.enum(PATTERNS),
    pattern_description: z.string(),
    affected_txn_ids: z.array(id),
    first_suspicious_txn_id: z.string(),
    connected_card_ids: z.array(id),
    connected_device_profiles: z.array(z.string()),
    exposure_usd: z.number().min(0),
    evidence: z.array(EvidenceSchema),
    similar_prior_cases: z.array(id),
    summary: z.string().min(1),
    written_to_graph: z.boolean(),
    graph_case_id: z.string(),
  })
  .strict();

export const SarSchema = z
  .object({
    file: z.boolean(),
    reason: z.string().min(1),
    narrative: z.string(),
    subjects: z.array(z.string()),
    total_amount_usd: z.number().min(0),
    activity_dates: z.array(z.string()),
  })
  .strict();

export const AnswerFileSchema = z
  .object({
    case_id: z.string().min(1),
    case: CaseRecordSchema,
    evidence_requests: z.array(EvidenceRequestSchema),
    next_best_actions: z
      .object({
        initial: z.array(ActionEntrySchema).min(1),
        final: z.array(ActionEntrySchema).min(1),
        what_changed: z.string().min(1),
      })
      .strict(),
    sar: SarSchema,
    stop_reason: z.string().min(1),
    tool_calls: z.number().int().min(0),
    tokens: z.number().int().min(0),
    latency_s: z.number().min(0),
  })
  .strict();

export type Evidence = z.infer<typeof EvidenceSchema>;
export type ActionEntry = z.infer<typeof ActionEntrySchema>;
export type EvidenceRequest = z.infer<typeof EvidenceRequestSchema>;
export type AnswerFile = z.infer<typeof AnswerFileSchema>;

/* ------------------------------------------------------------------------ */
/* Lenient display projection                                                */
/* ------------------------------------------------------------------------ */

const str = z.string().catch("");
const strList = z.array(z.string()).catch([]);
const num = z.number().catch(Number.NaN);

const DisplayActionSchema = z
  .object({ action: z.string().catch("UNKNOWN"), route: str, reason: str })
  .passthrough();

/**
 * Accepts anything shaped roughly like an answer. Fields that are the wrong
 * type become empty/NaN placeholders, which components render as "—" and which
 * are always accompanied by a strict-contract diagnostic.
 */
export const AnswerDisplaySchema = z
  .object({
    case_id: z.string().min(1),
    case: z
      .object({
        status: str,
        verdict: str,
        fraud_probability: num,
        pattern: str,
        pattern_description: str,
        affected_txn_ids: strList,
        first_suspicious_txn_id: str,
        connected_card_ids: strList,
        connected_device_profiles: strList,
        exposure_usd: num,
        evidence: z
          .array(
            z
              .object({ claim: str, source: str, ref: str, entity_ids: strList })
              .passthrough(),
          )
          .catch([]),
        similar_prior_cases: strList,
        summary: str,
        written_to_graph: z.boolean().catch(false),
        graph_case_id: str,
      })
      .passthrough(),
    evidence_requests: z
      .array(
        z
          .object({ type: str, asked_after_step: num, assumed_response: str })
          .passthrough(),
      )
      .catch([]),
    next_best_actions: z
      .object({
        initial: z.array(DisplayActionSchema).catch([]),
        final: z.array(DisplayActionSchema).catch([]),
        what_changed: str,
      })
      .passthrough()
      .catch({ initial: [], final: [], what_changed: "" }),
    sar: z
      .object({
        file: z.boolean().catch(false),
        reason: str,
        narrative: str,
        subjects: strList,
        total_amount_usd: num,
        activity_dates: strList,
      })
      .passthrough()
      .catch({ file: false, reason: "", narrative: "", subjects: [], total_amount_usd: Number.NaN, activity_dates: [] }),
    stop_reason: str,
    tool_calls: num,
    tokens: num,
    latency_s: num,
  })
  .passthrough();

/** What components render. Strict answers satisfy it structurally. */
export type AnswerView = z.infer<typeof AnswerDisplaySchema>;
export type ActionView = AnswerView["next_best_actions"]["final"][number];

/* ------------------------------------------------------------------------ */
/* Semantic rules                                                            */
/* ------------------------------------------------------------------------ */

export interface RuleIssue {
  level: "error" | "warning";
  path: string;
  message: string;
}

const SENTENCE_END = /[.!?](?:\s|$)/g;

/** Same counting rule as the reference validator's `count_sentences`. */
export function countSentences(text: string): number {
  const stripped = text.trim();
  if (!stripped) return 0;
  let count = (stripped.match(SENTENCE_END) ?? []).length;
  if (!/[.!?](?:\s|$)/.test(stripped.slice(-2))) count += 1;
  return count;
}

const AUTO_ACTIONS = new Set<string>([
  "ALLOW_TRANSACTION",
  "MONITOR_CARD",
  "MONITOR_CONNECTED_CARDS",
  "WARN_CUSTOMER",
  "VERIFY_WITH_CUSTOMER",
  "STEP_UP_AUTH",
  "GENERATE_REPORT",
  "CREATE_CASE",
  "ESCALATE_TO_ANALYST",
  "CLOSE_NO_FRAUD",
]);

/** The approval route the README policy table assigns. Display/diagnostic only. */
export function expectedRoute(action: string, exposureUsd: number): Route | null {
  if (AUTO_ACTIONS.has(action)) return "auto";
  if (action === "BLOCK_ALL_CARDS" || action === "FILE_REPORT") return "L2";
  if (action === "DECLINE_TRANSACTION") return "L1";
  if (action === "BLOCK_CARD") return exposureUsd > 2500 ? "L2" : "L1";
  return null;
}

const sameActions = (a: ActionView[], b: ActionView[]) =>
  a.length === b.length &&
  a.every((x, i) => x.action === b[i]?.action && x.route === b[i]?.route && x.reason === b[i]?.reason);

/**
 * Cross-field rules an answer can check about itself. Errors mirror the
 * contract the spec freezes; warnings mirror stricter reference-validator rules
 * (sentence counts, route table) that the backend owns.
 */
export function answerSemanticIssues(a: AnswerView): RuleIssue[] {
  const issues: RuleIssue[] = [];
  const err = (path: string, message: string) => issues.push({ level: "error", path, message });
  const warn = (path: string, message: string) => issues.push({ level: "warning", path, message });
  const c = a.case;
  const finalActions = a.next_best_actions.final.map((x) => x.action);

  if (!(c.fraud_probability >= 0 && c.fraud_probability <= 1)) {
    err("case.fraud_probability", "fraud_probability must be between 0 and 1");
  }
  if (c.verdict === "legitimate") {
    if (c.affected_txn_ids.length > 0) err("case.affected_txn_ids", "a legitimate verdict must have no affected transactions");
    if (c.exposure_usd !== 0) err("case.exposure_usd", "a legitimate verdict must have zero exposure");
    if (a.sar.file) err("sar.file", "a legitimate verdict must not file a SAR");
  }
  if (c.first_suspicious_txn_id && !c.affected_txn_ids.includes(c.first_suspicious_txn_id)) {
    err("case.first_suspicious_txn_id", "first_suspicious_txn_id must appear in affected_txn_ids");
  }
  if (new Set(c.affected_txn_ids).size !== c.affected_txn_ids.length) {
    err("case.affected_txn_ids", "affected_txn_ids repeats a transaction");
  }
  if (c.written_to_graph && !c.graph_case_id) {
    err("case.graph_case_id", "written_to_graph=true requires a graph_case_id");
  }
  if (!c.written_to_graph && c.graph_case_id) {
    warn("case.graph_case_id", "graph_case_id is set although written_to_graph is false");
  }
  if (c.pattern === "undocumented" && !c.pattern_description.trim()) {
    err("case.pattern_description", "pattern 'undocumented' requires a pattern_description");
  }
  if (c.pattern !== "undocumented" && c.pattern_description.trim()) {
    warn("case.pattern_description", `pattern '${c.pattern}' should carry an empty pattern_description`);
  }
  const summarySentences = countSentences(c.summary);
  if (summarySentences < 2 || summarySentences > 6) {
    warn("case.summary", `summary should be 2–6 sentences (found ${summarySentences})`);
  }
  if (c.evidence.length === 0) warn("case.evidence", "no evidence items were supplied");

  const filesReport = finalActions.includes("FILE_REPORT");
  if (filesReport !== a.sar.file) {
    err("sar.file", `sar.file=${String(a.sar.file)} disagrees with FILE_REPORT in final actions (${String(filesReport)})`);
  }
  if (a.sar.file) {
    if (a.sar.subjects.length === 0) err("sar.subjects", "a filed SAR must name its subjects");
    if (!(a.sar.total_amount_usd > 0)) err("sar.total_amount_usd", "a filed SAR must carry a positive total_amount_usd");
    if (a.sar.activity_dates.length !== 2) {
      err("sar.activity_dates", "a filed SAR must carry exactly two activity dates");
    } else {
      const [first, last] = a.sar.activity_dates as [string, string];
      if (![first, last].every((d) => /^\d{4}-\d{2}-\d{2}$/.test(d))) warn("sar.activity_dates", "activity dates should be YYYY-MM-DD");
      else if (first > last) err("sar.activity_dates", "activity dates must run first then last");
    }
    if (!a.sar.narrative.trim()) err("sar.narrative", "a filed SAR must carry a narrative");
    else {
      const n = countSentences(a.sar.narrative);
      if (n < 6 || n > 12) warn("sar.narrative", `SAR narrative should be 6–12 sentences (found ${n})`);
    }
    if (!finalActions.includes("CREATE_CASE")) warn("next_best_actions.final", "a filed SAR normally has CREATE_CASE in final actions");
  } else {
    if (a.sar.narrative || a.sar.subjects.length || a.sar.activity_dates.length || a.sar.total_amount_usd) {
      warn("sar", "an unfiled SAR should have an empty narrative, subjects, dates and zero amount");
    }
  }

  if (a.evidence_requests.length === 0) {
    if (!sameActions(a.next_best_actions.initial, a.next_best_actions.final)) {
      err("next_best_actions", "no evidence was requested, so final actions must equal initial actions");
    }
    if (a.next_best_actions.what_changed.trim().toLowerCase() !== "nothing") {
      err("next_best_actions.what_changed", "no evidence was requested, so what_changed must be 'nothing'");
    }
  }

  (["initial", "final"] as const).forEach((phase) => {
    a.next_best_actions[phase].forEach((entry, i) => {
      const expected = expectedRoute(entry.action, c.exposure_usd);
      if (expected && entry.route !== expected) {
        warn(
          `next_best_actions.${phase}[${i}].route`,
          `${entry.action} is routed ${entry.route}; the policy table expects ${expected}`,
        );
      }
    });
  });

  return issues;
}
