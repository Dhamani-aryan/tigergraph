#!/usr/bin/env node
/**
 * Generates the frontend's INTERFACE FIXTURES under `ui/fixtures/`.
 *
 * These files exist only to exercise the UI when official backend output has
 * not been synchronized. Every case ID starts with `DEMO-`, every entity ID
 * carries a `DEMO`/`9xxxxxx` marker, and every value is invented. They are not
 * investigation outcomes and must never be copied into the backend's `cases/`
 * or `runs/` directories.
 *
 * Usage: node scripts/generate-fixtures.mjs
 */
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures");
const FIXTURE_NOTE =
  "INTERFACE FIXTURE — invented values for UI development; not an investigation outcome.";

/* --------------------------------------------------------------- helpers */

const mcp = (tool, args, result_count, duration_ms, extra = {}) => ({
  tool,
  args,
  via: "mcp",
  result_count,
  duration_ms,
  ...extra,
});
const q = (name, params) => ({ graph: "FraudInvestigation", query_name: name, params });
const node = (id, type, label, role, discovered_at_step, attrs = {}) => ({
  id,
  type,
  label,
  role,
  discovered_at_step,
  attrs,
});
const edge = (source, target, type, attrs) => (attrs ? { source, target, type, attrs } : { source, target, type });

function answer(o) {
  return {
    case_id: o.case_id,
    case: {
      status: o.status,
      verdict: o.verdict,
      fraud_probability: o.p,
      pattern: o.pattern,
      pattern_description: o.pattern_description ?? "",
      affected_txn_ids: o.affected ?? [],
      first_suspicious_txn_id: o.first ?? "",
      connected_card_ids: o.connected_cards ?? [],
      connected_device_profiles: o.devices ?? [],
      exposure_usd: o.exposure ?? 0,
      evidence: o.evidence,
      similar_prior_cases: o.prior ?? [],
      summary: o.summary,
      written_to_graph: o.written,
      graph_case_id: o.written ? `CASE-${o.case_id}` : "",
    },
    evidence_requests: o.requests ?? [],
    next_best_actions: {
      initial: o.initial,
      final: o.final ?? o.initial,
      what_changed: o.what_changed ?? "nothing",
    },
    sar: o.sar ?? {
      file: false,
      reason: o.sar_reason,
      narrative: "",
      subjects: [],
      total_amount_usd: 0,
      activity_dates: [],
    },
    stop_reason: o.stop_reason,
    tool_calls: o.tool_calls,
    tokens: o.tokens,
    latency_s: o.latency_s,
  };
}

const traceBase = (o) => ({
  schema_version: "1.0",
  case_id: o.case_id,
  fixture_note: FIXTURE_NOTE,
  trigger: o.trigger,
  cutoff_ts: o.cutoff_ts,
  steps: o.steps,
  probability_timeline: o.timeline,
  signals: o.signals,
  rules_fired: o.rules,
  retrieval: o.retrieval,
  subgraph: o.subgraph,
  graph_write: o.graph_write,
  validation: o.validation,
  llm: { provider: "fixture", model: "none (interface fixture)", tokens: o.tokens },
});

/* ------------------------------------------------------------- DEMO-001 */
// Fraud, customer denial after an evidence request, actions change, filed SAR,
// shared-device ring, verified graph write + read-back.

const d1Device = "iOS 16.4 | Mobile Safari 16.4 | 390x844 | proxy:anonymous";
const d1 = answer({
  case_id: "DEMO-001",
  status: "closed_fraud",
  verdict: "fraud",
  p: 0.93,
  pattern: "card_not_present_new_device",
  affected: ["9100401", "9100417", "9100452", "0090460"],
  first: "9100401",
  connected_cards: ["DEMO-C2044-K1", "DEMO-C3310-K2"],
  devices: [d1Device],
  exposure: 612.46,
  evidence: [
    {
      claim:
        "Four online authorizations totalling $612.46 hit card DEMO-C1001-K1 within 38 minutes, all from a device first seen on this account that evening.",
      source: "graph",
      ref: "query:get_transaction_window_v1(card_id=DEMO-C1001-K1, hours_before=24)",
      entity_ids: ["9100401", "9100417", "9100452", "0090460", "DEMO-C1001-K1"],
    },
    {
      claim:
        "The same device profile authorized transactions on two other customers' cards in the same window; one of those cards has a confirmed-fraud closed case.",
      source: "graph",
      ref: "query:find_shared_origin_activity_v1(device_profile)",
      entity_ids: ["DEMO-DEV-7F21", "DEMO-C2044-K1", "DEMO-C3310-K2"],
    },
    {
      claim:
        "Policy R6 applies when several cards show fraud from the same device profile: open a case, file a report and monitor every connected card.",
      source: "document",
      ref: "fraud_policy.md#R6",
      entity_ids: [],
    },
    {
      claim: "Simulated customer response: the cardholder denies all four transactions and still holds the card.",
      source: "customer",
      ref: "evidence_request:1",
      entity_ids: ["DEMO-C1001"],
    },
  ],
  prior: ["DEMO-CC-0412", "DEMO-CC-0098"],
  summary:
    "Four card-not-present authorizations totalling $612.46 posted to DEMO-C1001-K1 within 38 minutes from a device new to the account. The same device touched two other customers' cards in the same window. After a simulated denial from the cardholder the probability rose to 0.93. The case was opened, a SAR filed and connected cards placed under monitoring.",
  written: true,
  requests: [
    {
      type: "customer_validation",
      asked_after_step: 5,
      assumed_response:
        "Simulated: the customer states they did not make these four purchases and still has the physical card.",
    },
  ],
  initial: [
    { action: "VERIFY_WITH_CUSTOMER", route: "auto", reason: "R1: probability 0.72 rests largely on the new-device signal; confirm before blocking" },
    { action: "STEP_UP_AUTH", route: "auto", reason: "R1: challenge further online use while verification is pending" },
    { action: "CREATE_CASE", route: "auto", reason: "Sec 3a: fraud probability reached 0.30 and evidence was requested" },
  ],
  final: [
    { action: "BLOCK_CARD", route: "L1", reason: "R2: customer denied the transactions; exposure $612.46 is at or below $2,500" },
    { action: "CREATE_CASE", route: "auto", reason: "R2: a denial always opens a case" },
    { action: "FILE_REPORT", route: "L2", reason: "R2/R6: activity connects to a device shared with other customers' cards" },
    { action: "MONITOR_CONNECTED_CARDS", route: "auto", reason: "R6: monitor DEMO-C2044-K1 and DEMO-C3310-K2, which share the device" },
  ],
  what_changed:
    "The simulated denial removed the need to verify, raised the probability from 0.72 to 0.93 and moved the recommendation to a block with a report and connected-card monitoring.",
  sar: {
    file: true,
    reason: "R2/R6: the customer denied the activity and it connects to a device profile shared with two other customers' cards.",
    narrative:
      "Between 2016-11-28 and 2016-11-29 the institution identified four card-not-present authorizations on card DEMO-C1001-K1, held by customer DEMO-C1001, totalling $612.46. " +
      "The authorizations were placed within a 38-minute window from a mobile device profile that had never previously been associated with this account and that routed its traffic through an anonymizing proxy. " +
      "Graph analysis showed that the same device profile authorized transactions on cards DEMO-C2044-K1 and DEMO-C3310-K2, which belong to two unrelated customers, during the same evening. " +
      "One of those connected cards is the subject of a previously closed case that was confirmed as fraud before this investigation opened. " +
      "When contacted through the simulated verification step, the cardholder stated that they did not make any of the four purchases and that the physical card remained in their possession. " +
      "The pattern is consistent with card-not-present fraud using compromised card credentials on a new device, and the shared device indicates that the activity is likely coordinated across several accounts rather than isolated. " +
      "The first suspicious transaction was 9100401 and the last was 0090460; all four were approved by the issuer before the case was opened. " +
      "The institution has blocked the card pending reissue, opened an internal case and placed the two connected cards under enhanced monitoring. " +
      "No evidence dated after the case cutoff was used to reach this conclusion. " +
      "The total amount reported reflects only the four authorizations inside the affected episode and excludes earlier legitimate spending on the account. " +
      "This report is filed because the denied activity exceeds the policy threshold for shared-origin fraud and involves more than one customer. " +
      "Supporting graph query references and retrieved prior cases are retained in the case record for examiner review.",
    subjects: ["DEMO-C1001", "DEMO-C1001-K1", "DEMO-C2044-K1", "DEMO-C3310-K2"],
    total_amount_usd: 612.46,
    activity_dates: ["2016-11-28", "2016-11-29"],
  },
  stop_reason: "The customer denial settled the verdict above 0.85 and no further evidence could change the final actions.",
  tool_calls: 14,
  tokens: 11842,
  latency_s: 21.4,
});

const d1Trace = traceBase({
  case_id: "DEMO-001",
  tokens: 11842,
  trigger: {
    trigger_type: "risk_score",
    trigger_text: "Real-time model scored transaction 0090460 ($249.99, online) at 0.88. Review and decide.",
    flagged_txn_id: "0090460",
    card_id: "DEMO-C1001-K1",
    customer_id: "DEMO-C1001",
    risk_score: 0.88,
    opened_at: "2016-11-29 00:12:40",
  },
  cutoff_ts: "2016-11-29 00:12:40",
  steps: [
    {
      step: 1,
      node: "gather_evidence",
      label: "Gather case context",
      started_ms: 0,
      duration_ms: 2310,
      summary: "Loaded the trigger, card baseline and 24-hour transaction window up to the cutoff.",
      tool_calls: [
        mcp("tigergraph__run_installed_query", q("get_case_context_v1", { case_id: "DEMO-001" }), 1, 1180),
        mcp("tigergraph__run_installed_query", q("get_card_baseline_v1", { card_id: "DEMO-C1001-K1", as_of: "2016-11-29 00:12:40" }), 212, 410),
        mcp("tigergraph__run_installed_query", q("get_transaction_window_v1", { card_id: "DEMO-C1001-K1", hours_before: 24 }), 7, 390),
      ],
    },
    {
      step: 2,
      node: "gather_evidence",
      label: "Shared-origin and region scan",
      started_ms: 2310,
      duration_ms: 1460,
      summary: "Found the device profile on two other customers' cards; billing region consistent with history.",
      tool_calls: [
        mcp("tigergraph__run_installed_query", q("find_shared_origin_activity_v1", { device_profile: d1Device, window_hours: 6 }), 3, 520),
        mcp("tigergraph__run_installed_query", q("find_region_anomalies_v1", { card_id: "DEMO-C1001-K1" }), 0, 470),
        mcp("tigergraph__run_installed_query", q("wcc_component_summary_v1", { card_id: "DEMO-C1001-K1" }), 5, 470),
      ],
    },
    {
      step: 3,
      node: "retrieve_knowledge",
      label: "GraphRAG retrieval",
      started_ms: 3770,
      duration_ms: 1210,
      summary: "Retrieved four closed cases and five policy/regulatory passages by TigerGraph vector search.",
      tool_calls: [
        mcp("tigergraph__search_top_k_similarity", { vertex_type: "ClosedCase", k: 4, as_of: "2016-11-29 00:12:40" }, 4, 640),
        mcp("tigergraph__search_top_k_similarity", { vertex_type: "PolicyChunk", k: 5 }, 5, 570),
      ],
    },
    {
      step: 4,
      node: "agentic_followup",
      label: "Agent follow-up: device neighbours",
      started_ms: 4980,
      duration_ms: 880,
      summary: "The model requested one follow-up tool to size the device neighbourhood (3 cards, below the collision cap).",
      tool_calls: [
        mcp("tigergraph__run_installed_query", { ...q("device_neighbors_v1", { device_id: "DEMO-DEV-7F21" }), authorization: "Bearer demo-fixture-value-should-be-redacted" }, 3, 880),
      ],
    },
    {
      step: 5,
      node: "assess",
      label: "Initial assessment",
      started_ms: 5860,
      duration_ms: 4120,
      summary: "Structured LLM assessment: card_not_present_new_device at 0.72, resting mostly on the new-device signal.",
      tool_calls: [],
    },
    {
      step: 6,
      node: "policy",
      label: "Initial policy",
      started_ms: 9980,
      duration_ms: 12,
      summary: "Deterministic rules R1 and Sec 3a produced VERIFY_WITH_CUSTOMER, STEP_UP_AUTH and CREATE_CASE.",
      tool_calls: [],
    },
    {
      step: 7,
      node: "request_evidence",
      label: "Request customer validation",
      started_ms: 9992,
      duration_ms: 35,
      summary: "Asked the customer to confirm the four transactions. Response is simulated: customer denies.",
      tool_calls: [],
    },
    {
      step: 8,
      node: "reassess",
      label: "Reassessment after denial",
      started_ms: 10027,
      duration_ms: 3890,
      summary: "Probability raised to 0.93 with the denial as independent customer evidence.",
      tool_calls: [],
    },
    {
      step: 9,
      node: "policy",
      label: "Final policy",
      started_ms: 13917,
      duration_ms: 10,
      summary: "R2 (denial) and R6 (shared device) produced BLOCK_CARD, CREATE_CASE, FILE_REPORT and MONITOR_CONNECTED_CARDS.",
      tool_calls: [],
    },
    {
      step: 10,
      node: "write_sar",
      label: "Draft SAR narrative",
      started_ms: 13927,
      duration_ms: 5020,
      summary: "Generated a 12-sentence narrative grounded in the affected episode dates.",
      tool_calls: [],
    },
    {
      step: 11,
      node: "write_case",
      label: "Write case memory",
      started_ms: 18947,
      duration_ms: 1510,
      summary: "Upserted FraudCase CASE-DEMO-001 and its embedding.",
      tool_calls: [
        mcp("tigergraph__add_nodes", { vertex_type: "FraudCase", vertex_id: "CASE-DEMO-001" }, 1, 610),
        { tool: "tigergraph__upsert_vectors", args: { vertex_type: "FraudCase", attribute: "embedding", dims: 1536 }, via: "fallback", result_count: 1, duration_ms: 900 },
      ],
    },
    {
      step: 12,
      node: "read_back",
      label: "Independent read-back",
      started_ms: 20457,
      duration_ms: 540,
      summary: "Read CASE-DEMO-001 back from the graph; verdict, status and probability matched.",
      tool_calls: [mcp("tigergraph__get_node", { vertex_type: "FraudCase", vertex_id: "CASE-DEMO-001" }, 1, 540)],
    },
    {
      step: 13,
      node: "validate",
      label: "Answer validation",
      started_ms: 20997,
      duration_ms: 403,
      summary: "Answer passed the contract and cross-field rules.",
      tool_calls: [],
    },
  ],
  timeline: [
    { step: 1, label: "Context", fraud_probability: 0.41 },
    { step: 2, label: "Shared origin", fraud_probability: 0.58 },
    { step: 3, label: "Retrieval", fraud_probability: 0.64 },
    { step: 5, label: "Assess", fraud_probability: 0.72 },
    { step: 8, label: "Reassess", fraud_probability: 0.93 },
  ],
  signals: [
    { name: "new_device_online", fired: true, detail: "device first seen on this account 41 minutes before the trigger", entity_ids: ["DEMO-DEV-7F21"] },
    { name: "shared_device", fired: true, detail: "device used by 3 cards across 3 customers in 6 hours (cap 20)", entity_ids: ["DEMO-DEV-7F21", "DEMO-C2044-K1", "DEMO-C3310-K2"] },
    { name: "velocity_burst", fired: true, detail: "4 authorizations in 38 minutes versus a 7-day median of 1 per day", entity_ids: ["9100401", "9100417", "9100452", "0090460"] },
    { name: "proxy_flag", fired: true, detail: "identity record marks the session as anonymous proxy", entity_ids: ["0090460"] },
    { name: "amount_outlier", fired: false, detail: "amounts within 1.1 robust deviations of the card median", entity_ids: [] },
    { name: "out_of_region", fired: false, detail: "billing region matches 97% of the card's history", entity_ids: ["DEMO-REG-204"] },
    { name: "concurrent_home_region_activity", fired: false, detail: "no in-person activity in the home region during the episode", entity_ids: [] },
  ],
  rules: [
    { rule: "R1", phase: "initial", explanation: "Single dominant signal below the 0.85 settled bar: verify before any block." },
    { rule: "Sec 3a", phase: "initial", explanation: "Probability reached 0.30 and evidence was requested, so a case is opened." },
    { rule: "R2", phase: "final", explanation: "Customer denied the transactions: block (L1 at ≤ $2,500) and open a case." },
    { rule: "R6", phase: "final", explanation: "Shared device across cards: file a report and monitor connected cards." },
  ],
  retrieval: {
    prior_cases: [
      { case_id: "DEMO-CC-0412", outcome: "confirmed_fraud", pattern: "card_not_present_new_device", score: 0.812, used: true },
      { case_id: "DEMO-CC-0098", outcome: "confirmed_fraud", pattern: "card_not_present_fraud", score: 0.744, used: true },
      { case_id: "DEMO-CC-1377", outcome: "confirmed_fraud", pattern: "card_testing", score: 0.521, used: false },
      { case_id: "DEMO-CC-0251", outcome: "cleared", pattern: "card_not_present_new_device", score: 0.498, used: false },
    ],
    documents: [
      { doc_id: "policy:R6", title: "Shared origin", section: "Fraud Policy §R6", score: 0.781 },
      { doc_id: "policy:R2", title: "Customer denies", section: "Fraud Policy §R2", score: 0.702 },
      { doc_id: "policy:R1", title: "Single weak signal", section: "Fraud Policy §R1", score: 0.655 },
      { doc_id: "pattern:card_not_present_new_device", title: "Card not present, new device", section: "Fraud Patterns", score: 0.633 },
      { doc_id: "reg:sar-narrative", title: "SAR narrative guidance", section: "Regulatory §Narrative", score: 0.512 },
    ],
  },
  subgraph: {
    nodes: [
      node("DEMO-C1001", "Customer", "DEMO-C1001", "subject", 1, { home_region: "DEMO-REG-204", tenure_days: 812 }),
      node("DEMO-C1001-K1", "Card", "DEMO-C1001-K1", "subject", 1, { network: "visa", type: "debit" }),
      node("9100388", "Transaction", "9100388 · $41.20", "context", 1, { ts: "2016-11-27 18:22:05", amount_usd: 41.2, channel: "in_person" }),
      node("9100395", "Transaction", "9100395 · $12.99", "context", 1, { ts: "2016-11-28 09:10:44", amount_usd: 12.99, channel: "online" }),
      node("9100401", "Transaction", "9100401 · $89.50", "affected", 1, { ts: "2016-11-28 23:34:02", amount_usd: 89.5, channel: "online", sequence: 1 }),
      node("9100417", "Transaction", "9100417 · $148.02", "affected", 1, { ts: "2016-11-28 23:47:19", amount_usd: 148.02, channel: "online", sequence: 2 }),
      node("9100452", "Transaction", "9100452 · $124.95", "affected", 1, { ts: "2016-11-29 00:03:51", amount_usd: 124.95, channel: "online", sequence: 3 }),
      node("0090460", "Transaction", "0090460 · $249.99", "flagged", 1, { ts: "2016-11-29 00:12:40", amount_usd: 249.99, channel: "online", sequence: 4, risk_score: 0.88 }),
      node("DEMO-DEV-7F21", "Device", "iOS 16.4 · Safari · proxy", "connected", 2, { profile: d1Device, first_seen: "2016-11-28 23:31:10", id_15: "New", id_23: "IP_PROXY:ANONYMOUS" }),
      node("DEMO-REG-204", "Region", "Region 204", "context", 2, { country: "87" }),
      node("DEMO-EM-gmail", "Email", "gmail.com", "context", 2, { domain: "gmail.com" }),
      node("DEMO-C2044-K1", "Card", "DEMO-C2044-K1", "connected", 2, { ring_cluster_id: "WCC-DEMO-17" }),
      node("DEMO-C2044", "Customer", "DEMO-C2044", "connected", 2, {}),
      node("9100433", "Transaction", "9100433 · $99.00", "connected", 2, { ts: "2016-11-28 23:52:12", amount_usd: 99.0, channel: "online" }),
      node("DEMO-C3310-K2", "Card", "DEMO-C3310-K2", "connected", 2, { ring_cluster_id: "WCC-DEMO-17" }),
      node("DEMO-C3310", "Customer", "DEMO-C3310", "connected", 2, {}),
      node("9100444", "Transaction", "9100444 · $175.10", "connected", 2, { ts: "2016-11-28 23:58:37", amount_usd: 175.1, channel: "online" }),
      node("DEMO-M-551", "Merchant", "Merchant W-551", "context", 1, { product_cd: "W" }),
      node("DEMO-M-872", "Merchant", "Merchant C-872", "context", 1, { product_cd: "C" }),
      node("DEMO-CC-0412", "ClosedCase", "DEMO-CC-0412", "prior_case", 3, { outcome: "confirmed_fraud", closed_at: "2016-10-02 11:20:00" }),
      node("DEMO-CC-0098", "ClosedCase", "DEMO-CC-0098", "prior_case", 3, { outcome: "confirmed_fraud", closed_at: "2016-08-19 16:45:00" }),
      node("DEMO-CC-0251", "ClosedCase", "DEMO-CC-0251", "context", 3, { outcome: "cleared", closed_at: "2016-09-01 10:02:00" }),
      node("CASE-DEMO-001", "FraudCase", "CASE-DEMO-001", "this_case", 11, { verdict: "fraud", written_at: "2016-11-29 00:13:02" }),
    ],
    edges: [
      edge("DEMO-C1001", "DEMO-C1001-K1", "OWNS"),
      edge("DEMO-C1001-K1", "9100388", "MADE"),
      edge("DEMO-C1001-K1", "9100395", "MADE"),
      edge("DEMO-C1001-K1", "9100401", "MADE"),
      edge("DEMO-C1001-K1", "9100417", "MADE"),
      edge("DEMO-C1001-K1", "9100452", "MADE"),
      edge("DEMO-C1001-K1", "0090460", "MADE"),
      edge("9100401", "9100417", "NEXT_IN_EPISODE", { gap_s: 797 }),
      edge("9100417", "9100452", "NEXT_IN_EPISODE", { gap_s: 992 }),
      edge("9100452", "0090460", "NEXT_IN_EPISODE", { gap_s: 529 }),
      edge("9100401", "DEMO-DEV-7F21", "USED_DEVICE"),
      edge("9100417", "DEMO-DEV-7F21", "USED_DEVICE"),
      edge("9100452", "DEMO-DEV-7F21", "USED_DEVICE"),
      edge("0090460", "DEMO-DEV-7F21", "USED_DEVICE"),
      edge("9100433", "DEMO-DEV-7F21", "USED_DEVICE"),
      edge("9100444", "DEMO-DEV-7F21", "USED_DEVICE"),
      edge("DEMO-C2044", "DEMO-C2044-K1", "OWNS"),
      edge("DEMO-C3310", "DEMO-C3310-K2", "OWNS"),
      edge("DEMO-C2044-K1", "9100433", "MADE"),
      edge("DEMO-C3310-K2", "9100444", "MADE"),
      edge("9100388", "DEMO-REG-204", "BILLED_IN"),
      edge("0090460", "DEMO-REG-204", "BILLED_IN"),
      edge("DEMO-C1001", "DEMO-EM-gmail", "HAS_EMAIL"),
      edge("9100401", "DEMO-M-551", "AT_MERCHANT"),
      edge("9100417", "DEMO-M-551", "AT_MERCHANT"),
      edge("9100452", "DEMO-M-872", "AT_MERCHANT"),
      edge("0090460", "DEMO-M-872", "AT_MERCHANT"),
      edge("DEMO-CC-0412", "DEMO-C2044-K1", "ABOUT_CARD"),
      edge("DEMO-CC-0098", "DEMO-M-551", "SIMILAR_TO"),
      edge("DEMO-CC-0251", "DEMO-M-872", "SIMILAR_TO"),
      edge("CASE-DEMO-001", "DEMO-C1001-K1", "ABOUT_CARD"),
      edge("CASE-DEMO-001", "DEMO-CC-0412", "SIMILAR_TO"),
      edge("CASE-DEMO-001", "DEMO-CC-0098", "SIMILAR_TO"),
    ],
  },
  graph_write: { written: true, graph_case_id: "CASE-DEMO-001", read_back_ok: true, written_at: "2016-11-29 00:13:02" },
  validation: { passed: true, errors: [], warnings: [] },
});

/* ------------------------------------------------------------- DEMO-002 */
// Legitimate, zero exposure, no SAR, no evidence request, empty retrieval.

const d2 = answer({
  case_id: "DEMO-002",
  status: "closed_legitimate",
  verdict: "legitimate",
  p: 0.08,
  pattern: "none",
  evidence: [
    {
      claim: "The disputed $64.00 charge matches a monthly merchant the card has paid on the same day for eleven consecutive months.",
      source: "graph",
      ref: "query:get_card_baseline_v1(card_id=DEMO-C4410-K1)",
      entity_ids: ["9203311", "DEMO-C4410-K1"],
    },
    {
      claim: "Device, billing region and email all match the card's established history.",
      source: "graph",
      ref: "query:find_shared_origin_activity_v1(card_id=DEMO-C4410-K1)",
      entity_ids: ["DEMO-DEV-11AA"],
    },
  ],
  summary:
    "The customer queried a $64.00 charge that matches an eleven-month recurring payment on the same card. Device, region and email are all consistent with the card's history. No fraud signal fired, so the case was closed as legitimate.",
  written: true,
  initial: [{ action: "CLOSE_NO_FRAUD", route: "auto", reason: "R8: probability 0.08 is at or below 0.15 and no signal fired" }],
  sar_reason: "Legitimate verdict: no filing criteria met.",
  stop_reason: "Probability settled below 0.15 with consistent history; no evidence request could change the outcome.",
  tool_calls: 5,
  tokens: 4210,
  latency_s: 8.7,
});

const d2Trace = traceBase({
  case_id: "DEMO-002",
  tokens: 4210,
  trigger: {
    trigger_type: "customer_report",
    trigger_text: "Customer asked about a $64.00 charge from 2016-10-14 they did not recognise.",
    flagged_txn_id: "9203311",
    card_id: "DEMO-C4410-K1",
    customer_id: "DEMO-C4410",
    risk_score: 0.31,
    opened_at: "2016-10-15 09:02:00",
  },
  cutoff_ts: "2016-10-15 09:02:00",
  steps: [
    {
      step: 1,
      node: "gather_evidence",
      label: "Gather case context",
      started_ms: 0,
      duration_ms: 1820,
      summary: "Loaded card baseline and a 30-day window; the charge recurs monthly.",
      tool_calls: [
        mcp("tigergraph__run_installed_query", q("get_case_context_v1", { case_id: "DEMO-002" }), 1, 910),
        { tool: "get_card_baseline_v1", args: { card_id: "DEMO-C4410-K1" }, via: "direct", result_count: 143, duration_ms: 520 },
        mcp("tigergraph__run_installed_query", q("find_shared_origin_activity_v1", { card_id: "DEMO-C4410-K1" }), 0, 390),
      ],
    },
    {
      step: 2,
      node: "retrieve_knowledge",
      label: "GraphRAG retrieval",
      started_ms: 1820,
      duration_ms: 720,
      summary: "No closed case or policy passage cleared the retrieval threshold.",
      tool_calls: [
        mcp("tigergraph__search_top_k_similarity", { vertex_type: "ClosedCase", k: 4 }, 0, 380),
        mcp("tigergraph__search_top_k_similarity", { vertex_type: "PolicyChunk", k: 5 }, 0, 340),
      ],
    },
    { step: 3, node: "assess", label: "Assessment", started_ms: 2540, duration_ms: 3200, summary: "Structured assessment: no pattern, probability 0.08.", tool_calls: [] },
    { step: 4, node: "policy", label: "Policy", started_ms: 5740, duration_ms: 8, summary: "R8: CLOSE_NO_FRAUD.", tool_calls: [] },
    { step: 5, node: "write_case", label: "Write case memory", started_ms: 5748, duration_ms: 1400, summary: "Upserted FraudCase CASE-DEMO-002.", tool_calls: [] },
    { step: 6, node: "read_back", label: "Independent read-back", started_ms: 7148, duration_ms: 610, summary: "Read-back matched.", tool_calls: [] },
  ],
  timeline: [
    { step: 1, label: "Context", fraud_probability: 0.14 },
    { step: 3, label: "Assess", fraud_probability: 0.08 },
  ],
  signals: [
    { name: "recurring_merchant", fired: false, detail: "charge matches an 11-month recurring payment (exculpatory)", entity_ids: ["9203311"] },
    { name: "new_device_online", fired: false, detail: "device seen on 138 prior transactions", entity_ids: ["DEMO-DEV-11AA"] },
    { name: "out_of_region", fired: false, detail: "billing region matches history", entity_ids: [] },
  ],
  rules: [{ rule: "R8", phase: "initial", explanation: "Probability ≤ 0.15 with no signal: close as no fraud." }],
  retrieval: { prior_cases: [], documents: [] },
  subgraph: {
    nodes: [
      node("DEMO-C4410", "Customer", "DEMO-C4410", "subject", 1),
      node("DEMO-C4410-K1", "Card", "DEMO-C4410-K1", "subject", 1),
      node("9203311", "Transaction", "9203311 · $64.00", "flagged", 1, { ts: "2016-10-14 07:00:12", amount_usd: 64 }),
      node("9189022", "Transaction", "9189022 · $64.00", "context", 1, { ts: "2016-09-14 07:00:09", amount_usd: 64 }),
      node("9171440", "Transaction", "9171440 · $64.00", "context", 1, { ts: "2016-08-14 07:00:15", amount_usd: 64 }),
      node("DEMO-M-S120", "Merchant", "Merchant S-120 (subscription)", "context", 1),
      node("DEMO-DEV-11AA", "Device", "Windows 10 · Chrome", "context", 1, { profile: "Windows | Windows 10 | chrome 54.0 | 1366x768" }),
      node("CASE-DEMO-002", "FraudCase", "CASE-DEMO-002", "this_case", 5, { verdict: "legitimate" }),
    ],
    edges: [
      edge("DEMO-C4410", "DEMO-C4410-K1", "OWNS"),
      edge("DEMO-C4410-K1", "9203311", "MADE"),
      edge("DEMO-C4410-K1", "9189022", "MADE"),
      edge("DEMO-C4410-K1", "9171440", "MADE"),
      edge("9203311", "DEMO-M-S120", "AT_MERCHANT"),
      edge("9189022", "DEMO-M-S120", "AT_MERCHANT"),
      edge("9171440", "DEMO-M-S120", "AT_MERCHANT"),
      edge("9203311", "DEMO-DEV-11AA", "USED_DEVICE"),
      edge("CASE-DEMO-002", "DEMO-C4410-K1", "ABOUT_CARD"),
    ],
  },
  graph_write: { written: true, graph_case_id: "CASE-DEMO-002", read_back_ok: true, written_at: "2016-10-15 09:02:09" },
  validation: { passed: true, errors: [], warnings: [] },
});

/* ------------------------------------------------------------- DEMO-003 */
// Uncertain, analyst escalation, NO TRACE FILE (deliberately absent).

const d3 = answer({
  case_id: "DEMO-003",
  status: "escalated",
  verdict: "uncertain",
  p: 0.55,
  pattern: "card_not_present_fraud",
  affected: ["9305120", "9305188"],
  first: "9305120",
  exposure: 1840.0,
  devices: ["Android 7.0 | Chrome Mobile 55.0 | 412x732"],
  evidence: [
    {
      claim: "Two online purchases totalling $1,840.00 are 6.2 robust deviations above the card's median spend.",
      source: "graph",
      ref: "query:get_card_baseline_v1(card_id=DEMO-C5021-K2)",
      entity_ids: ["9305120", "9305188"],
    },
    {
      claim: "The device has been used on this card twice before, which weakens the card-not-present signal.",
      source: "graph",
      ref: "query:extract_temporal_graph_features_v1(card_id=DEMO-C5021-K2)",
      entity_ids: ["DEMO-DEV-A7"],
    },
    {
      claim: "Simulated analyst response: no additional information is available within the case window.",
      source: "external",
      ref: "evidence_request:1",
      entity_ids: [],
    },
  ],
  prior: ["DEMO-CC-0730"],
  summary:
    "An analyst asked for review of two large online purchases on DEMO-C5021-K2. Amount signals point to fraud but the device is known to the card. The simulated analyst reply added nothing, so the case is escalated at probability 0.55.",
  written: true,
  requests: [
    { type: "analyst_info", asked_after_step: 4, assumed_response: "Simulated: the analyst queue has no additional information within the case window." },
  ],
  initial: [
    { action: "VERIFY_WITH_CUSTOMER", route: "auto", reason: "R1: amount outlier is the only strong signal" },
    { action: "CREATE_CASE", route: "auto", reason: "Sec 3a: probability reached 0.30" },
    { action: "ESCALATE_TO_ANALYST", route: "auto", reason: "R8: uncertain verdict with exposure above $500" },
  ],
  final: [
    { action: "MONITOR_CARD", route: "auto", reason: "R4: no customer reply within 24 hours" },
    { action: "DECLINE_TRANSACTION", route: "L1", reason: "R4: decline further attempts until verified" },
    { action: "ESCALATE_TO_ANALYST", route: "auto", reason: "R4/R8: exposure $1,840.00 exceeds $500 and evidence conflicts" },
    { action: "CREATE_CASE", route: "auto", reason: "Sec 3a: probability reached 0.30" },
  ],
  what_changed: "The analyst had nothing to add and the customer did not reply, so verification gave way to monitoring, a decline and an analyst escalation.",
  sar_reason: "Uncertain verdict: filing criteria are not met until an analyst confirms fraud.",
  stop_reason: "Evidence conflicts and the only permitted request returned nothing; escalated to a human analyst as policy requires.",
  tool_calls: 9,
  tokens: 8391,
  latency_s: 17.9,
});

/* ------------------------------------------------------------- DEMO-004 */
// Undocumented pattern, graph write FAILED, unknown step/node/type, long device string.

const d4Device =
  "Linux | Ubuntu 16.04.3 LTS Xenial Xerus x86_64 | HeadlessChrome 62.0.3202.94 (automation build; webdriver=true) | 1280x720 | proxy:IP_PROXY:ANONYMOUS | tz=UTC+00:00 | lang=en-US,en;q=0.9";
const d4 = answer({
  case_id: "DEMO-004",
  status: "closed_fraud",
  verdict: "fraud",
  p: 0.88,
  pattern: "undocumented",
  pattern_description:
    "Refund cycling: small purchases at one merchant are refunded within minutes and immediately repurchased from a headless browser, repeated across five cards in two days — not one of the five documented patterns.",
  affected: ["9400012", "9400019", "9400027", "9400031", "9400044", "9400050"],
  first: "9400012",
  connected_cards: ["DEMO-C6100-K1", "DEMO-C6102-K1", "DEMO-C6107-K3", "DEMO-C6111-K1"],
  devices: [d4Device],
  exposure: 1287.3,
  evidence: [
    {
      claim: "Six purchase/refund/repurchase cycles at merchant DEMO-M-R09 on card DEMO-C6099-K1 within 90 minutes.",
      source: "graph",
      ref: "query:get_transaction_window_v1(card_id=DEMO-C6099-K1, hours_before=48)",
      entity_ids: ["9400012", "9400019", "9400027", "9400031", "9400044", "9400050"],
    },
    {
      claim: "A headless automation browser performed the same cycle on four other customers' cards.",
      source: "graph",
      ref: "query:find_shared_origin_activity_v1(device_profile=headless)",
      entity_ids: ["DEMO-C6100-K1", "DEMO-C6102-K1", "DEMO-C6107-K3", "DEMO-C6111-K1"],
    },
    {
      claim: "Policy R9: undocumented but coordinated, repeated abuse across customers is reported and escalated.",
      source: "document",
      ref: "fraud_policy.md#R9",
      entity_ids: [],
    },
  ],
  prior: [],
  summary:
    "Card DEMO-C6099-K1 shows six rapid purchase-refund-repurchase cycles at one merchant driven by a headless browser. The same automation touched four other customers' cards. No documented pattern fits, so the pattern is recorded as undocumented refund cycling. The graph write for this case failed and must be retried.",
  written: false,
  initial: [
    { action: "DECLINE_TRANSACTION", route: "L1", reason: "R9: stop further cycles on the card" },
    { action: "CREATE_CASE", route: "auto", reason: "R9: coordinated undocumented abuse" },
    { action: "FILE_REPORT", route: "L2", reason: "R9: repeated abuse across five customers" },
    { action: "MONITOR_CONNECTED_CARDS", route: "auto", reason: "R6: four cards share the automation device" },
    { action: "ESCALATE_TO_ANALYST", route: "auto", reason: "R9: new pattern needs analyst review" },
  ],
  sar: {
    file: true,
    reason: "R9: an undocumented pattern repeated across five customers' cards.",
    narrative:
      "Between 2016-12-03 and 2016-12-04 the institution observed six purchase, refund and repurchase cycles on card DEMO-C6099-K1 at a single merchant. " +
      "Each cycle completed within minutes and was driven by a headless automation browser behind an anonymizing proxy. " +
      "The same automation device performed identical cycles on four cards belonging to other customers. " +
      "The activity does not match any documented fraud pattern and appears designed to exploit refund processing timing. " +
      "Net exposure across the affected episode on the subject card is $1,287.30. " +
      "The institution has declined further attempts, opened a case, placed the connected cards under monitoring and escalated the pattern to an analyst.",
    subjects: ["DEMO-C6099", "DEMO-C6099-K1", "DEMO-M-R09"],
    total_amount_usd: 1287.3,
    activity_dates: ["2016-12-03", "2016-12-04"],
  },
  stop_reason: "Coordination across five cards settled the verdict; no evidence request could change the required report.",
  tool_calls: 11,
  tokens: 9905,
  latency_s: 19.2,
});

const d4Trace = traceBase({
  case_id: "DEMO-004",
  tokens: 9905,
  trigger: {
    trigger_type: "analyst_request",
    trigger_text: "Analyst flagged unusual refund volume at merchant DEMO-M-R09 involving card DEMO-C6099-K1.",
    flagged_txn_id: "9400050",
    card_id: "DEMO-C6099-K1",
    customer_id: "DEMO-C6099",
    risk_score: null,
    opened_at: "2016-12-04 16:40:00",
  },
  cutoff_ts: "2016-12-04 16:40:00",
  steps: [
    {
      step: 1,
      node: "gather_evidence",
      label: "Gather case context",
      started_ms: 0,
      duration_ms: 2100,
      summary: "Loaded 48 hours of card activity including refunds.",
      tool_calls: [
        mcp("tigergraph__run_installed_query", q("get_transaction_window_v1", { card_id: "DEMO-C6099-K1", hours_before: 48 }), 13, 820),
        { tool: "find_shared_origin_activity_v1", args: { device_profile: d4Device, api_key: "demo-fixture-api-key-should-be-redacted" }, via: "direct", result_count: 4, duration_ms: 610 },
      ],
    },
    {
      step: 2,
      node: "merchant_refund_probe",
      label: "Merchant refund probe (custom step)",
      started_ms: 2100,
      duration_ms: 940,
      summary: "A step type the UI does not know; it is rendered generically.",
      tool_calls: [
        { tool: "tigergraph__run_installed_query", args: q("merchant_refund_cycles_v0", { merchant: "DEMO-M-R09" }), via: "sidecar", result_count: 22, duration_ms: 940 },
      ],
    },
    { step: 3, node: "assess", label: "Assessment", started_ms: 3040, duration_ms: 4400, summary: "Structured assessment: undocumented, coordinated, probability 0.88.", tool_calls: [] },
    { step: 4, node: "policy", label: "Policy", started_ms: 7440, duration_ms: 11, summary: "R9 and R6 fired.", tool_calls: [] },
    { step: 5, node: "write_sar", label: "Draft SAR narrative", started_ms: 7451, duration_ms: 4300, summary: "Generated a 6-sentence narrative.", tool_calls: [] },
    {
      step: 6,
      node: "write_case",
      label: "Write case memory",
      started_ms: 11751,
      duration_ms: 3020,
      summary: "Write rejected by the graph; case memory was NOT stored.",
      tool_calls: [
        mcp("tigergraph__add_nodes", { vertex_type: "FraudCase", vertex_id: "CASE-DEMO-004" }, 0, 3020, { error: "HTTP 503 from REST++ upsert (fixture)" }),
      ],
    },
  ],
  timeline: [
    { step: 1, label: "Context", fraud_probability: 0.62 },
    { step: 2, label: "Refund probe", fraud_probability: 0.8 },
    { step: 3, label: "Assess", fraud_probability: 0.88 },
  ],
  signals: [
    { name: "refund_cycle", fired: true, detail: "6 purchase→refund→repurchase cycles in 90 minutes", entity_ids: ["9400012", "9400050"] },
    { name: "automation_device", fired: true, detail: "headless browser with webdriver flag", entity_ids: ["DEMO-DEV-HL01"] },
    { name: "shared_device", fired: true, detail: "same device on 5 cards", entity_ids: ["DEMO-DEV-HL01"] },
    { name: "amount_outlier", fired: false, detail: "individual amounts are small", entity_ids: [] },
  ],
  rules: [
    { rule: "R9", phase: "initial", explanation: "Undocumented, coordinated abuse: case, report and analyst escalation." },
    { rule: "R6", phase: "initial", explanation: "Shared device: monitor connected cards." },
  ],
  retrieval: {
    prior_cases: [{ case_id: "DEMO-CC-2201", outcome: "confirmed_fraud", pattern: "card_testing", score: 0.41, used: false }],
    documents: [{ doc_id: "policy:R9", title: "Undocumented patterns", section: "Fraud Policy §R9", score: 0.69 }],
  },
  subgraph: {
    nodes: [
      node("DEMO-C6099", "Customer", "DEMO-C6099", "subject", 1),
      node("DEMO-C6099-K1", "Card", "DEMO-C6099-K1", "subject", 1),
      ...["9400012", "9400019", "9400027", "9400031", "9400044"].map((id, i) =>
        node(id, "Transaction", `${id} · cycle ${i + 1}`, "affected", 1, { ts: `2016-12-04 1${4 + Math.floor(i / 3)}:${10 + i * 7}:00`, sequence: i + 1 }),
      ),
      node("9400050", "Transaction", "9400050 · cycle 6", "flagged", 1, { ts: "2016-12-04 16:01:00", sequence: 6 }),
      node("DEMO-RB-1", "RefundBatch", "Refund batch R09-1204", "anomaly", 2, { refunds: 6 }),
      node("DEMO-M-R09", "Merchant", "Merchant R-09", "connected", 1),
      node("DEMO-DEV-HL01", "Device", "HeadlessChrome 62 · Ubuntu · proxy", "connected", 1, { profile: d4Device }),
      ...["DEMO-C6100-K1", "DEMO-C6102-K1", "DEMO-C6107-K3", "DEMO-C6111-K1"].map((id) => node(id, "Card", id, "connected", 1)),
    ],
    edges: [
      edge("DEMO-C6099", "DEMO-C6099-K1", "OWNS"),
      ...["9400012", "9400019", "9400027", "9400031", "9400044", "9400050"].flatMap((id, i, all) => [
        edge("DEMO-C6099-K1", id, "MADE"),
        edge(id, "DEMO-M-R09", "AT_MERCHANT"),
        edge(id, "DEMO-DEV-HL01", "USED_DEVICE"),
        ...(i > 0 ? [edge(all[i - 1], id, "NEXT_IN_EPISODE")] : []),
      ]),
      edge("DEMO-RB-1", "DEMO-M-R09", "REFUNDED_AT"),
      ...["DEMO-C6100-K1", "DEMO-C6102-K1", "DEMO-C6107-K3", "DEMO-C6111-K1"].map((id) => edge(id, "DEMO-DEV-HL01", "SEEN_ON")),
    ],
  },
  graph_write: { written: false, graph_case_id: "", read_back_ok: false },
  validation: { passed: true, errors: [], warnings: ["graph write failed: case memory not stored (submission gate blocked)"] },
});

/* ------------------------------------------------------------- DEMO-005 */
// Card testing, ~230-node graph, write reported but READ-BACK FAILED.

const affected5 = Array.from({ length: 12 }, (_, i) => String(9500100 + i * 3));
const d5 = answer({
  case_id: "DEMO-005",
  status: "closed_fraud",
  verdict: "fraud",
  p: 0.91,
  pattern: "card_testing",
  affected: affected5,
  first: affected5[0],
  exposure: 23.88,
  devices: ["Windows | Windows 10 | chrome 65.0 | 1920x1080"],
  evidence: [
    {
      claim: "Twelve authorizations of $1.99 each within nine minutes across twelve merchants on card DEMO-C7700-K1.",
      source: "graph",
      ref: "query:get_transaction_window_v1(card_id=DEMO-C7700-K1, hours_before=1)",
      entity_ids: affected5,
    },
    {
      claim: "Simulated step-up authentication failed on the next attempt.",
      source: "customer",
      ref: "evidence_request:1",
      entity_ids: ["DEMO-C7700-K1"],
    },
  ],
  prior: ["DEMO-CC-3301", "DEMO-CC-3310"],
  summary:
    "Card DEMO-C7700-K1 received twelve $1.99 authorizations in nine minutes at twelve merchants, a classic card-testing burst. Step-up authentication was requested and the simulated challenge failed. The card is blocked and monitored.",
  written: true,
  requests: [{ type: "step_up_auth", asked_after_step: 3, assumed_response: "Simulated: the step-up challenge on the next authorization failed." }],
  initial: [
    { action: "DECLINE_TRANSACTION", route: "L1", reason: "R5: card testing — decline the pending authorization" },
    { action: "STEP_UP_AUTH", route: "auto", reason: "R5: challenge the next attempt" },
    { action: "CREATE_CASE", route: "auto", reason: "Sec 3a: probability reached 0.30" },
  ],
  final: [
    { action: "DECLINE_TRANSACTION", route: "L1", reason: "R5: card testing — decline the pending authorization" },
    { action: "BLOCK_CARD", route: "L1", reason: "R5: step-up failed; exposure $23.88 is at or below $2,500" },
    { action: "CREATE_CASE", route: "auto", reason: "Sec 3a: probability reached 0.30" },
    { action: "MONITOR_CARD", route: "auto", reason: "R5: watch for a follow-on large purchase" },
  ],
  what_changed: "The failed step-up challenge replaced the challenge with a block and added card monitoring.",
  sar_reason: "R5: exposure of $23.88 is below the $1,000 threshold and no shared origin connects other customers.",
  stop_reason: "The failed challenge settled the verdict; the remaining actions do not depend on further evidence.",
  tool_calls: 8,
  tokens: 6120,
  latency_s: 12.3,
});

function largeGraph() {
  const nodes = [
    node("DEMO-C7700", "Customer", "DEMO-C7700", "subject", 1),
    node("DEMO-C7700-K1", "Card", "DEMO-C7700-K1", "subject", 1),
    node("DEMO-DEV-W10", "Device", "Windows 10 · Chrome 65", "connected", 1, { profile: "Windows | Windows 10 | chrome 65.0 | 1920x1080" }),
  ];
  const edges = [edge("DEMO-C7700", "DEMO-C7700-K1", "OWNS")];
  affected5.forEach((id, i) => {
    const m = `DEMO-M-T${String(i).padStart(2, "0")}`;
    nodes.push(node(id, "Transaction", `${id} · $1.99`, i === affected5.length - 1 ? "flagged" : "affected", 1, { ts: `2016-11-20 03:${String(10 + i).padStart(2, "0")}:00`, amount_usd: 1.99, sequence: i + 1 }));
    nodes.push(node(m, "Merchant", `Merchant T-${i}`, "context", 1));
    edges.push(edge("DEMO-C7700-K1", id, "MADE"), edge(id, m, "AT_MERCHANT"), edge(id, "DEMO-DEV-W10", "USED_DEVICE"));
    if (i > 0) edges.push(edge(affected5[i - 1], id, "NEXT_IN_EPISODE"));
  });
  // Merchant neighbourhood: other cards at the same test merchants (context).
  for (let k = 0; k < 90; k += 1) {
    const card = `DEMO-CX${String(k).padStart(3, "0")}-K1`;
    const txn = String(9600000 + k * 7);
    const m = `DEMO-M-T${String(k % 12).padStart(2, "0")}`;
    nodes.push(node(card, "Card", card, "context", 2));
    nodes.push(node(txn, "Transaction", `${txn}`, "context", 2, { ts: "2016-11-19 22:00:00" }));
    edges.push(edge(card, txn, "MADE"), edge(txn, m, "AT_MERCHANT"));
    if (k % 9 === 0) {
      const cust = `DEMO-CU${String(k).padStart(3, "0")}`;
      nodes.push(node(cust, "Customer", cust, "context", 2));
      edges.push(edge(cust, card, "OWNS"));
    }
  }
  for (let r = 0; r < 12; r += 1) {
    const id = `DEMO-REG-${300 + r}`;
    nodes.push(node(id, "Region", `Region ${300 + r}`, "context", 2));
    edges.push(edge(`DEMO-M-T${String(r).padStart(2, "0")}`, id, "LOCATED_IN"));
  }
  nodes.push(node("DEMO-CC-3301", "ClosedCase", "DEMO-CC-3301", "prior_case", 3, { outcome: "confirmed_fraud" }));
  nodes.push(node("DEMO-CC-3310", "ClosedCase", "DEMO-CC-3310", "prior_case", 3, { outcome: "confirmed_fraud" }));
  nodes.push(node("CASE-DEMO-005", "FraudCase", "CASE-DEMO-005", "this_case", 7));
  edges.push(edge("DEMO-CC-3301", "DEMO-M-T03", "SIMILAR_TO"), edge("DEMO-CC-3310", "DEMO-M-T07", "SIMILAR_TO"), edge("CASE-DEMO-005", "DEMO-C7700-K1", "ABOUT_CARD"));
  return { nodes, edges };
}

const d5Trace = traceBase({
  case_id: "DEMO-005",
  tokens: 6120,
  trigger: {
    trigger_type: "risk_score",
    trigger_text: `Real-time model scored transaction ${affected5[11]} ($1.99, online) at 0.93. Review and decide.`,
    flagged_txn_id: affected5[11],
    card_id: "DEMO-C7700-K1",
    customer_id: "DEMO-C7700",
    risk_score: 0.93,
    opened_at: "2016-11-20 03:22:30",
  },
  cutoff_ts: "2016-11-20 03:22:30",
  steps: [
    {
      step: 1,
      node: "gather_evidence",
      label: "Gather case context",
      started_ms: 0,
      duration_ms: 2600,
      summary: "Loaded the one-hour window and the merchant neighbourhood.",
      tool_calls: [
        mcp("tigergraph__run_installed_query", q("get_transaction_window_v1", { card_id: "DEMO-C7700-K1", hours_before: 1 }), 12, 700),
        mcp("tigergraph__run_installed_query", q("merchant_neighbourhood_v1", { merchants: 12 }), 214, 1900),
      ],
    },
    { step: 2, node: "assess", label: "Assessment", started_ms: 2600, duration_ms: 3100, summary: "card_testing at 0.84.", tool_calls: [] },
    { step: 3, node: "policy", label: "Initial policy", started_ms: 5700, duration_ms: 9, summary: "R5.", tool_calls: [] },
    { step: 4, node: "request_evidence", label: "Request step-up", started_ms: 5709, duration_ms: 20, summary: "Step-up requested; simulated result: failed.", tool_calls: [] },
    { step: 5, node: "reassess", label: "Reassess", started_ms: 5729, duration_ms: 2900, summary: "Probability 0.91.", tool_calls: [] },
    { step: 6, node: "policy", label: "Final policy", started_ms: 8629, duration_ms: 9, summary: "R5 with failed step-up.", tool_calls: [] },
    {
      step: 7,
      node: "write_case",
      label: "Write case memory",
      started_ms: 8638,
      duration_ms: 1800,
      summary: "Upsert returned success.",
      tool_calls: [mcp("tigergraph__add_nodes", { vertex_type: "FraudCase", vertex_id: "CASE-DEMO-005" }, 1, 1800)],
    },
    {
      step: 8,
      node: "read_back",
      label: "Independent read-back",
      started_ms: 10438,
      duration_ms: 1850,
      summary: "Read-back returned no vertex; memory is unverified.",
      tool_calls: [mcp("tigergraph__get_node", { vertex_type: "FraudCase", vertex_id: "CASE-DEMO-005" }, 0, 1850, { error: "vertex not found on read-back" })],
    },
  ],
  timeline: [
    { step: 1, label: "Context", fraud_probability: 0.77 },
    { step: 2, label: "Assess", fraud_probability: 0.84 },
    { step: 5, label: "Reassess", fraud_probability: 0.91 },
  ],
  signals: [
    { name: "micro_amount_burst", fired: true, detail: "12 × $1.99 in 9 minutes", entity_ids: affected5 },
    { name: "merchant_fanout", fired: true, detail: "12 distinct merchants", entity_ids: [] },
    { name: "new_device_online", fired: false, detail: "device known to the card", entity_ids: ["DEMO-DEV-W10"] },
  ],
  rules: [
    { rule: "R5", phase: "initial", explanation: "Card testing: decline and step up." },
    { rule: "Sec 3a", phase: "initial", explanation: "Probability ≥ 0.30 opens a case." },
    { rule: "R5", phase: "final", explanation: "Failed challenge: block the card and monitor." },
  ],
  retrieval: {
    prior_cases: [
      { case_id: "DEMO-CC-3301", outcome: "confirmed_fraud", pattern: "card_testing", score: 0.88, used: true },
      { case_id: "DEMO-CC-3310", outcome: "confirmed_fraud", pattern: "card_testing", score: 0.86, used: true },
    ],
    documents: [{ doc_id: "policy:R5", title: "Card testing", section: "Fraud Policy §R5", score: 0.82 }],
  },
  subgraph: largeGraph(),
  graph_write: { written: true, graph_case_id: "CASE-DEMO-005", read_back_ok: false, written_at: "2016-11-20 03:22:41" },
  validation: { passed: true, errors: [], warnings: ["read-back did not confirm the graph write"] },
});

/* ------------------------------------------------------------- DEMO-006 */
// Valid answer, deliberately MALFORMED trace.

const d6 = answer({
  case_id: "DEMO-006",
  status: "closed_fraud",
  verdict: "fraud",
  p: 0.86,
  pattern: "out_of_region_use",
  affected: ["9600991"],
  first: "9600991",
  exposure: 402.1,
  evidence: [
    {
      claim: "An in-person purchase in region 441 occurred 20 minutes after an in-person purchase in the home region 118.",
      source: "graph",
      ref: "query:find_region_anomalies_v1(card_id=DEMO-C8801-K1)",
      entity_ids: ["9600991", "9600984"],
    },
  ],
  summary:
    "Card DEMO-C8801-K1 was used in person in region 441 twenty minutes after a purchase in its home region. The two locations cannot both be the cardholder. The card is declined and the customer warned.",
  written: true,
  initial: [
    { action: "DECLINE_TRANSACTION", route: "L1", reason: "R8: out-of-region use settled above 0.85" },
    { action: "CREATE_CASE", route: "auto", reason: "Sec 3a: probability reached 0.30" },
    { action: "WARN_CUSTOMER", route: "auto", reason: "Customer should expect a replacement card" },
  ],
  sar_reason: "Exposure of $402.10 is below the $1,000 threshold with no shared origin.",
  stop_reason: "Concurrent home-region activity settled the verdict.",
  tool_calls: 6,
  tokens: 5020,
  latency_s: 10.1,
});

const d6TraceMalformed = {
  schema_version: "1.0",
  case_id: "DEMO-006",
  fixture_note: `${FIXTURE_NOTE} This trace is deliberately malformed to exercise diagnostics.`,
  trigger: { trigger_type: "customer_report", trigger_text: "Customer reported a card they still hold used abroad." },
  steps: "not-an-array",
  probability_timeline: [{ step: 1, label: "Context", fraud_probability: 1.7 }],
  subgraph: { nodes: [{ id: 9600991, type: "Transaction" }] },
};

/* ---------------------------------------------------------------- write */

const answers = [d1, d2, d3, d4, d5, d6];
const traces = { "DEMO-001": d1Trace, "DEMO-002": d2Trace, "DEMO-004": d4Trace, "DEMO-005": d5Trace, "DEMO-006": d6TraceMalformed };
const triggers = {
  "DEMO-001": ["risk_score", "DEMO-C1001", "DEMO-C1001-K1", "2016-11-29 00:12:40"],
  "DEMO-002": ["customer_report", "DEMO-C4410", "DEMO-C4410-K1", "2016-10-15 09:02:00"],
  "DEMO-003": ["analyst_request", "DEMO-C5021", "DEMO-C5021-K2", "2016-11-02 13:15:00"],
  "DEMO-004": ["analyst_request", "DEMO-C6099", "DEMO-C6099-K1", "2016-12-04 16:40:00"],
  "DEMO-005": ["risk_score", "DEMO-C7700", "DEMO-C7700-K1", "2016-11-20 03:22:30"],
  "DEMO-006": ["customer_report", "DEMO-C8801", "DEMO-C8801-K1", "2016-12-07 19:05:00"],
};
const validationPassed = { "DEMO-001": true, "DEMO-002": true, "DEMO-003": true, "DEMO-004": true, "DEMO-005": true, "DEMO-006": true };

const tally = (key) =>
  answers.reduce((acc, a) => {
    const k = key(a);
    acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {});
const rows = answers.map((a) => {
  const [trigger_type, customer_id, card_id, opened_at] = triggers[a.case_id];
  const ini = a.next_best_actions.initial.map((x) => x.action);
  const fin = a.next_best_actions.final.map((x) => x.action);
  return {
    case_id: a.case_id,
    trigger_type,
    customer_id,
    card_id,
    opened_at,
    verdict: a.case.verdict,
    fraud_probability: a.case.fraud_probability,
    pattern: a.case.pattern,
    status: a.case.status,
    exposure_usd: a.case.exposure_usd,
    sar_file: a.sar.file,
    initial_actions: ini,
    final_actions: fin,
    actions_changed: JSON.stringify(a.next_best_actions.initial) !== JSON.stringify(a.next_best_actions.final),
    validation_passed: validationPassed[a.case_id],
    written_to_graph: a.case.written_to_graph,
    latency_s: a.latency_s,
  };
});
const summary = {
  run_id: "DEMO-FIXTURE-RUN",
  generated_at: "2026-09-23T00:00:00Z",
  git_commit: "fixture",
  fixture_note: FIXTURE_NOTE,
  llm: { provider: "fixture", model: "none (interface fixture)" },
  cases_total: rows.length,
  cases_valid: rows.filter((r) => r.validation_passed).length,
  cases_written_to_graph: rows.filter((r) => r.written_to_graph).length,
  verdicts: tally((a) => a.case.verdict),
  patterns: tally((a) => a.case.pattern),
  sar_filed: rows.filter((r) => r.sar_file).length,
  actions_changed: rows.filter((r) => r.actions_changed).length,
  total_exposure_usd: Math.round(rows.reduce((s, r) => s + r.exposure_usd, 0) * 100) / 100,
  total_tool_calls: answers.reduce((s, a) => s + a.tool_calls, 0),
  total_tokens: answers.reduce((s, a) => s + a.tokens, 0),
  avg_latency_s: Math.round((answers.reduce((s, a) => s + a.latency_s, 0) / answers.length) * 10) / 10,
  cases: rows,
};

rmSync(join(root, "cases"), { recursive: true, force: true });
rmSync(join(root, "traces"), { recursive: true, force: true });
mkdirSync(join(root, "cases"), { recursive: true });
mkdirSync(join(root, "traces"), { recursive: true });
const write = (path, data) => writeFileSync(join(root, path), `${JSON.stringify(data, null, 2)}\n`, "utf8");
for (const a of answers) write(`cases/${a.case_id}.json`, a);
for (const [id, t] of Object.entries(traces)) write(`traces/${id}.trace.json`, t);
write("batch_summary.json", summary);
writeFileSync(
  join(root, "README.md"),
  `# Interface fixtures (NOT investigation results)

Generated by \`node scripts/generate-fixtures.mjs\`. Every value here is invented to
exercise the UI. Case IDs start with \`DEMO-\`. Do not copy these files into the
backend's \`cases/\` or \`runs/\` directories and do not present them as outcomes.

| Case | Exercises |
|---|---|
| DEMO-001 | fraud, customer-validation request, changed actions, affected episode, shared-device graph, filed long SAR, fallback tool call, credential redaction, verified write + read-back |
| DEMO-002 | legitimate, zero exposure, no SAR, no evidence request (initial = final), empty retrieval lists, direct tool call |
| DEMO-003 | uncertain + analyst escalation, **no trace file** |
| DEMO-004 | undocumented pattern with description, **graph write failed**, unknown step node / node type / role / \`via\`, very long device string |
| DEMO-005 | card testing, ~230-node graph (render cap), write reported but **read-back failed** |
| DEMO-006 | valid answer with a deliberately **malformed trace** |
`,
  "utf8",
);
console.log(`fixtures written: ${answers.length} cases, ${Object.keys(traces).length} traces, batch summary`);
