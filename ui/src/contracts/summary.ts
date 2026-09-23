/** `runs/latest/batch_summary.json` — the backend's run-level summary. */
import { z } from "zod";
import { ACTIONS, PATTERNS, STATUSES, VERDICTS } from "./answer";

export const TRIGGER_TYPES = ["risk_score", "customer_report", "analyst_request"] as const;
export type TriggerType = (typeof TRIGGER_TYPES)[number];

export const BatchCaseSummarySchema = z.object({
  case_id: z.string().min(1),
  trigger_type: z.enum(TRIGGER_TYPES),
  customer_id: z.string(),
  card_id: z.string(),
  opened_at: z.string(),
  verdict: z.enum(VERDICTS),
  fraud_probability: z.number().min(0).max(1),
  pattern: z.enum(PATTERNS),
  status: z.enum(STATUSES),
  exposure_usd: z.number().min(0),
  sar_file: z.boolean(),
  initial_actions: z.array(z.enum(ACTIONS)),
  final_actions: z.array(z.enum(ACTIONS)),
  actions_changed: z.boolean(),
  validation_passed: z.boolean(),
  written_to_graph: z.boolean(),
  latency_s: z.number().min(0),
});

export const BatchSummarySchema = z.object({
  run_id: z.string().min(1),
  generated_at: z.string(),
  git_commit: z.string(),
  llm: z.object({ provider: z.string(), model: z.string() }),
  cases_total: z.number().int().min(0),
  cases_valid: z.number().int().min(0),
  cases_written_to_graph: z.number().int().min(0),
  verdicts: z.record(z.string(), z.number().int().min(0)),
  patterns: z.record(z.string(), z.number().int().min(0)),
  sar_filed: z.number().int().min(0),
  actions_changed: z.number().int().min(0),
  total_exposure_usd: z.number().min(0),
  total_tool_calls: z.number().int().min(0),
  total_tokens: z.number().int().min(0),
  avg_latency_s: z.number().min(0),
  cases: z.array(BatchCaseSummarySchema),
});

export type BatchCaseSummary = z.infer<typeof BatchCaseSummarySchema>;
export type BatchSummary = z.infer<typeof BatchSummarySchema>;
