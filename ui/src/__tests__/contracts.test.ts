import { describe, expect, it } from "vitest";
import {
  AnswerFileSchema,
  DataManifestSchema,
  countSentences,
  crossCheckAnswerTrace,
  crossCheckSummaryAnswers,
  expectedRoute,
  validateAnswer,
  validateSummary,
  validateTrace,
  type AnswerView,
} from "@/contracts";
import { FIXTURE_ANSWERS, FIXTURE_SUMMARY, FIXTURE_TRACES, clone, fixtureManifest } from "@/test/fixtures";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const answer = (id = "DEMO-001") => clone(FIXTURE_ANSWERS[id]) as Json;
const errorsOf = (raw: unknown, id?: string) => validateAnswer(raw, id).diagnostics.filter((d) => d.level === "error");

describe("answer contract", () => {
  it.each(Object.keys(FIXTURE_ANSWERS))("accepts interface fixture %s", (id) => {
    const v = validateAnswer(FIXTURE_ANSWERS[id], id);
    expect(v.contractValid).toBe(true);
    expect(v.strict?.case_id).toBe(id);
  });

  it("keeps numeric-looking and zero-padded transaction IDs as strings", () => {
    const v = validateAnswer(FIXTURE_ANSWERS["DEMO-001"], "DEMO-001");
    expect(v.strict?.case.affected_txn_ids).toContain("0090460");
    expect(v.strict?.case.affected_txn_ids.every((id) => typeof id === "string")).toBe(true);
  });

  it("rejects a numeric transaction ID instead of coercing it", () => {
    const a = answer();
    a.case.affected_txn_ids[0] = 9100401;
    expect(AnswerFileSchema.safeParse(a).success).toBe(false);
  });

  const mutations: [string, (a: Json) => void, RegExp][] = [
    ["an unknown top-level field", (a) => (a.extra = 1), /unrecognized/i],
    ["an unknown action", (a) => (a.next_best_actions.final[0].action = "FREEZE_ACCOUNT"), /invalid enum/i],
    ["an unknown route", (a) => (a.next_best_actions.final[0].route = "L3"), /invalid enum/i],
    ["an unknown pattern", (a) => (a.case.pattern = "phishing"), /invalid enum/i],
    ["probability above one", (a) => (a.case.fraud_probability = 1.2), /between 0 and 1|less than or equal/i],
    ["first suspicious txn outside the affected list", (a) => (a.case.first_suspicious_txn_id = "123"), /first_suspicious_txn_id/],
    ["written_to_graph without graph_case_id", (a) => (a.case.graph_case_id = ""), /requires a graph_case_id/],
    ["sar.file without FILE_REPORT", (a) => (a.next_best_actions.final = a.next_best_actions.final.filter((x: Json) => x.action !== "FILE_REPORT")), /disagrees with FILE_REPORT/],
    ["a filed SAR without subjects", (a) => (a.sar.subjects = []), /name its subjects/],
    ["a filed SAR with one date", (a) => (a.sar.activity_dates = ["2016-11-28"]), /two activity dates/],
    ["a filed SAR with zero amount", (a) => (a.sar.total_amount_usd = 0), /positive total_amount_usd/],
    ["a filed SAR without narrative", (a) => (a.sar.narrative = ""), /carry a narrative/],
  ];
  it.each(mutations)("rejects %s", (_name, mutate, message) => {
    const a = answer();
    mutate(a);
    const v = validateAnswer(a, "DEMO-001");
    expect(v.contractValid).toBe(false);
    expect(v.diagnostics.some((d) => d.level === "error" && message.test(d.message))).toBe(true);
    // Still renderable through the lenient projection.
    expect(v.view?.case_id).toBe("DEMO-001");
  });

  it("requires a legitimate verdict to have no affected transactions and zero exposure", () => {
    const a = answer("DEMO-002");
    a.case.affected_txn_ids = ["1"];
    a.case.exposure_usd = 10;
    const msgs = errorsOf(a, "DEMO-002").map((d) => d.message);
    expect(msgs).toContain("a legitimate verdict must have no affected transactions");
    expect(msgs).toContain("a legitimate verdict must have zero exposure");
  });

  it("requires no-request answers to keep actions unchanged and say 'nothing'", () => {
    const a = answer("DEMO-002");
    a.next_best_actions.final = [{ action: "MONITOR_CARD", route: "auto", reason: "R4" }];
    a.next_best_actions.what_changed = "it changed";
    const msgs = errorsOf(a, "DEMO-002").map((d) => d.message);
    expect(msgs.some((m) => m.includes("final actions must equal initial actions"))).toBe(true);
    expect(msgs.some((m) => m.includes("what_changed must be 'nothing'"))).toBe(true);
  });

  it("flags a filename / case_id mismatch", () => {
    expect(errorsOf(FIXTURE_ANSWERS["DEMO-001"], "DEMO-009").some((d) => d.path === "case_id")).toBe(true);
  });

  it("returns an unrenderable result, not an exception, for garbage", () => {
    const v = validateAnswer("not an object");
    expect(v.view).toBeUndefined();
    expect(v.contractValid).toBe(false);
  });

  it("counts sentences like the reference validator", () => {
    expect(countSentences("One. Two! Three?")).toBe(3);
    expect(countSentences("No terminator")).toBe(1);
    expect(countSentences("Amount $612.46 was reported. Done.")).toBe(2);
    expect(countSentences("")).toBe(0);
  });

  it("knows the README approval-route table", () => {
    expect(expectedRoute("BLOCK_CARD", 2500)).toBe("L1");
    expect(expectedRoute("BLOCK_CARD", 2500.01)).toBe("L2");
    expect(expectedRoute("FILE_REPORT", 0)).toBe("L2");
    expect(expectedRoute("DECLINE_TRANSACTION", 0)).toBe("L1");
    expect(expectedRoute("CREATE_CASE", 0)).toBe("auto");
    expect(expectedRoute("MADE_UP", 0)).toBeNull();
  });
});

describe("trace contract", () => {
  it("accepts valid fixture traces and reports unknown vocabulary as diagnostics", () => {
    const v = validateTrace(FIXTURE_TRACES["DEMO-004"], "DEMO-004");
    expect(v.trace).toBeDefined();
    const msgs = v.diagnostics.map((d) => d.message).join("\n");
    expect(msgs).toMatch(/unrecognised step node "merchant_refund_probe"/);
    expect(msgs).toMatch(/RefundBatch/);
    expect(msgs).toMatch(/via="sidecar"/);
  });

  it("rejects the malformed trace without throwing", () => {
    const v = validateTrace(FIXTURE_TRACES["DEMO-006"], "DEMO-006");
    expect(v.trace).toBeUndefined();
    expect(v.diagnostics[0]?.message).toMatch(/does not satisfy the trace contract/);
    expect(v.diagnostics.some((d) => d.path === "steps")).toBe(true);
  });

  it("treats a write without read-back as unverified", () => {
    const v = validateTrace(FIXTURE_TRACES["DEMO-005"], "DEMO-005");
    expect(v.diagnostics.some((d) => /read-back did not succeed/.test(d.message))).toBe(true);
  });

  it("flags graph nodes timestamped after the cutoff", () => {
    const t = clone(FIXTURE_TRACES["DEMO-001"]) as Json;
    t.subgraph.nodes[2].attrs.ts = "2017-01-01 00:00:00";
    const v = validateTrace(t, "DEMO-001");
    expect(v.diagnostics.some((d) => d.level === "error" && /after the case cutoff/.test(d.message))).toBe(true);
  });

  it("cross-checks answer and trace", () => {
    const a = validateAnswer(FIXTURE_ANSWERS["DEMO-001"]).view as AnswerView;
    const t = clone(FIXTURE_TRACES["DEMO-001"]) as Json;
    expect(crossCheckAnswerTrace(a, validateTrace(t).trace!).filter((d) => d.level === "error")).toHaveLength(0);
    t.graph_write.written = false;
    t.retrieval.prior_cases = [];
    const d = crossCheckAnswerTrace(a, validateTrace(t).trace!);
    expect(d.some((x) => /written_to_graph=true but trace graph_write.written=false/.test(x.message))).toBe(true);
    expect(d.some((x) => /not present in trace retrieval/.test(x.message))).toBe(true);
  });
});

describe("summary and manifest contracts", () => {
  it("accepts the fixture summary and agrees with the answers", () => {
    const v = validateSummary(FIXTURE_SUMMARY);
    expect(v.summary?.cases).toHaveLength(6);
    expect(v.diagnostics.filter((d) => d.level === "error")).toHaveLength(0);
    const views = Object.values(FIXTURE_ANSWERS).map((a) => validateAnswer(a).view as AnswerView);
    expect(crossCheckSummaryAnswers(v.summary!, views).filter((d) => d.level === "error")).toHaveLength(0);
  });

  it("detects summary totals that disagree with rows, and rows that disagree with answers", () => {
    const s = clone(FIXTURE_SUMMARY) as Json;
    s.sar_filed = 5;
    s.cases[0].verdict = "legitimate";
    const v = validateSummary(s);
    expect(v.diagnostics.some((d) => /sar_filed disagrees/.test(d.message))).toBe(true);
    const views = Object.values(FIXTURE_ANSWERS).map((a) => validateAnswer(a).view as AnswerView);
    expect(crossCheckSummaryAnswers(v.summary!, views).some((d) => d.caseId === "DEMO-001" && /verdict/.test(d.message))).toBe(true);
  });

  it("rejects a summary with an unknown trigger type", () => {
    const s = clone(FIXTURE_SUMMARY) as Json;
    s.cases[0].trigger_type = "chargeback";
    expect(validateSummary(s).summary).toBeUndefined();
  });

  it("parses the manifest and rejects an unknown mode", () => {
    expect(DataManifestSchema.safeParse(fixtureManifest()).success).toBe(true);
    expect(DataManifestSchema.safeParse(fixtureManifest({ mode: "live" })).success).toBe(false);
  });
});
