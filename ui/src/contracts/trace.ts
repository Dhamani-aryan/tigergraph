/**
 * The execution trace (`runs/latest/traces/<case_id>.trace.json`): a
 * presentation projection of real backend execution. Unknown step names, node
 * types, roles and `via` values are accepted and rendered generically; the UI
 * reports them as diagnostics instead of failing.
 */
import { z } from "zod";

const record = z.record(z.string(), z.unknown());

export const TraceToolCallSchema = z
  .object({
    tool: z.string(),
    args: record.default({}),
    via: z.string(),
    result_count: z.number().optional(),
    duration_ms: z.number().optional(),
    error: z.string().optional(),
  })
  .passthrough();

export const TraceStepSchema = z
  .object({
    step: z.number(),
    node: z.string(),
    label: z.string(),
    started_ms: z.number().optional(),
    duration_ms: z.number().optional(),
    tool_calls: z.array(TraceToolCallSchema).default([]),
    summary: z.string().default(""),
  })
  .passthrough();

export const ProbabilityPointSchema = z.object({
  step: z.number(),
  label: z.string(),
  fraud_probability: z.number().min(0).max(1),
});

export const SignalTraceSchema = z.object({
  name: z.string(),
  fired: z.boolean(),
  detail: z.string().default(""),
  entity_ids: z.array(z.string()).default([]),
});

export const RuleTraceSchema = z.object({
  rule: z.string(),
  phase: z.string(),
  explanation: z.string().default(""),
});

export const RetrievedPriorCaseSchema = z.object({
  case_id: z.string(),
  outcome: z.string(),
  pattern: z.string(),
  score: z.number().optional(),
  used: z.boolean(),
});

export const RetrievedDocumentSchema = z.object({
  doc_id: z.string(),
  title: z.string(),
  section: z.string(),
  score: z.number().optional(),
});

export const GraphNodeSchema = z.object({
  id: z.string(),
  type: z.string(),
  label: z.string(),
  role: z.string(),
  discovered_at_step: z.number().optional(),
  attrs: record.default({}),
});

export const GraphEdgeSchema = z.object({
  source: z.string(),
  target: z.string(),
  type: z.string(),
  attrs: record.optional(),
});

export const TraceFileSchema = z
  .object({
    schema_version: z.string(),
    case_id: z.string().min(1),
    trigger: z.object({
      trigger_type: z.string(),
      trigger_text: z.string(),
      flagged_txn_id: z.string(),
      card_id: z.string(),
      customer_id: z.string(),
      risk_score: z.number().nullable().optional(),
      opened_at: z.string(),
    }),
    cutoff_ts: z.string(),
    steps: z.array(TraceStepSchema),
    probability_timeline: z.array(ProbabilityPointSchema),
    signals: z.array(SignalTraceSchema),
    rules_fired: z.array(RuleTraceSchema),
    retrieval: z.object({
      prior_cases: z.array(RetrievedPriorCaseSchema),
      documents: z.array(RetrievedDocumentSchema),
    }),
    subgraph: z.object({
      nodes: z.array(GraphNodeSchema),
      edges: z.array(GraphEdgeSchema),
    }),
    graph_write: z.object({
      written: z.boolean(),
      graph_case_id: z.string(),
      read_back_ok: z.boolean(),
      written_at: z.string().optional(),
    }),
    validation: z.object({
      passed: z.boolean(),
      errors: z.array(z.string()),
      warnings: z.array(z.string()),
    }),
    llm: z
      .object({ provider: z.string(), model: z.string(), tokens: z.number().optional() })
      .optional(),
  })
  .passthrough();

export type TraceToolCall = z.infer<typeof TraceToolCallSchema>;
export type TraceStep = z.infer<typeof TraceStepSchema>;
export type ProbabilityPoint = z.infer<typeof ProbabilityPointSchema>;
export type SignalTrace = z.infer<typeof SignalTraceSchema>;
export type RuleTrace = z.infer<typeof RuleTraceSchema>;
export type RetrievedPriorCase = z.infer<typeof RetrievedPriorCaseSchema>;
export type RetrievedDocument = z.infer<typeof RetrievedDocumentSchema>;
export type GraphNode = z.infer<typeof GraphNodeSchema>;
export type GraphEdge = z.infer<typeof GraphEdgeSchema>;
export type TraceFile = z.infer<typeof TraceFileSchema>;

/** Values the UI styles specifically; anything else renders generically. */
export const KNOWN_STEP_NODES = [
  "load_case",
  "gather_evidence",
  "agentic_followup",
  "apply_followup",
  "retrieve_knowledge",
  "assess",
  "policy",
  "initial_policy",
  "request_evidence",
  "reassess",
  "final_policy",
  "write_sar",
  "validate",
  "write_case",
  "read_back",
] as const;
export const KNOWN_NODE_TYPES = [
  "Customer",
  "Card",
  "Transaction",
  "Device",
  "Region",
  "Email",
  "Merchant",
  "ClosedCase",
  "FraudCase",
  "PolicyDoc",
] as const;
export const KNOWN_ROLES = ["subject", "flagged", "affected", "connected", "prior_case", "this_case", "context"] as const;
export const KNOWN_VIA = ["mcp", "direct", "fallback"] as const;
