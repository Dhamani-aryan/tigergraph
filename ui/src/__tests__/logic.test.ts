import { describe, expect, it } from "vitest";
import { validateAnswer, validateTrace, type AnswerView, type TraceFile } from "@/contracts";
import { DataClient } from "@/data/client";
import { EMPTY_FILTERS, actionsChanged, buildRows, diffActions, filterRows, sortRows } from "@/data/diagnostics";
import { documentReferenced } from "@/data/evidence";
import { REDACTED, redact, redactText } from "@/lib/redact";
import { selectGraph, toElements } from "@/components/graph/elements";
import { FIXTURE_ANSWERS, FIXTURE_TRACES, clone, fixtureFiles, memoryFetch } from "@/test/fixtures";

const view = (id: string) => validateAnswer(FIXTURE_ANSWERS[id], id).view as AnswerView;
const trace = (id: string) => validateTrace(FIXTURE_TRACES[id], id).trace as TraceFile;

describe("action diff", () => {
  it("marks added, removed and kept actions between phases", () => {
    const d = diffActions(view("DEMO-001"));
    const by = Object.fromEntries(d.map((x) => [x.action, x.status]));
    expect(by).toMatchObject({ BLOCK_CARD: "added", FILE_REPORT: "added", MONITOR_CONNECTED_CARDS: "added", CREATE_CASE: "kept", VERIFY_WITH_CUSTOMER: "removed", STEP_UP_AUTH: "removed" });
    expect(actionsChanged(view("DEMO-001"))).toBe(true);
  });

  it("reports no change when initial equals final", () => {
    expect(actionsChanged(view("DEMO-002"))).toBe(false);
    expect(diffActions(view("DEMO-002")).every((x) => x.status === "kept")).toBe(true);
  });

  it("detects a route change for the same action", () => {
    const a = clone(view("DEMO-002"));
    a.next_best_actions.final = [{ action: "CLOSE_NO_FRAUD", route: "L1", reason: "x" }];
    expect(diffActions(a)[0]?.status).toBe("rerouted");
  });
});

describe("overview filtering, search and sort", () => {
  const load = async () => buildRows(await new DataClient("./data", memoryFetch(fixtureFiles())).loadBundle());

  it("filters by verdict, pattern, trigger, SAR and changed actions", async () => {
    const rows = await load();
    expect(filterRows(rows, { ...EMPTY_FILTERS, verdict: "fraud" }).map((r) => r.caseId)).toEqual(["DEMO-001", "DEMO-004", "DEMO-005", "DEMO-006"]);
    expect(filterRows(rows, { ...EMPTY_FILTERS, pattern: "undocumented" }).map((r) => r.caseId)).toEqual(["DEMO-004"]);
    expect(filterRows(rows, { ...EMPTY_FILTERS, trigger: "analyst_request" }).map((r) => r.caseId)).toEqual(["DEMO-003", "DEMO-004"]);
    expect(filterRows(rows, { ...EMPTY_FILTERS, sar: "filed" }).map((r) => r.caseId)).toEqual(["DEMO-001", "DEMO-004"]);
    expect(filterRows(rows, { ...EMPTY_FILTERS, changed: "unchanged" }).map((r) => r.caseId)).toEqual(["DEMO-002", "DEMO-004", "DEMO-006"]);
  });

  it("searches by case, card and customer ID, case-insensitively", async () => {
    const rows = await load();
    expect(filterRows(rows, { ...EMPTY_FILTERS, query: "demo-005" }).map((r) => r.caseId)).toEqual(["DEMO-005"]);
    expect(filterRows(rows, { ...EMPTY_FILTERS, query: "C4410-K1" }).map((r) => r.caseId)).toEqual(["DEMO-002"]);
    expect(filterRows(rows, { ...EMPTY_FILTERS, query: "DEMO-C8801" }).map((r) => r.caseId)).toEqual(["DEMO-006"]);
    expect(filterRows(rows, { ...EMPTY_FILTERS, query: "nothing-matches" })).toHaveLength(0);
  });

  it("sorts by probability and exposure", async () => {
    const rows = await load();
    expect(sortRows(rows, "probability", "desc")[0]?.caseId).toBe("DEMO-001");
    expect(sortRows(rows, "exposure", "asc")[0]?.caseId).toBe("DEMO-002");
    expect(sortRows(rows, "caseId", "desc")[0]?.caseId).toBe("DEMO-006");
  });
});

describe("credential redaction", () => {
  it("redacts credential-like keys at any depth, keeps numeric token counts", () => {
    const out = redact({ tokens: 9120, api_key: "abc", nested: { Authorization: "Bearer x", password: "p", list: [{ secret_value: "s" }] }, ok: "fine" });
    expect(out).toEqual({ tokens: 9120, api_key: REDACTED, nested: { Authorization: REDACTED, password: REDACTED, list: [{ secret_value: REDACTED }] }, ok: "fine" });
  });

  it("redacts bearer/API-key-shaped values whatever the key", () => {
    expect(redact({ note: "Bearer abc.def.ghi" }).note).toBe(REDACTED);
    expect(redact({ note: "sk-abcdefghijklmnop" }).note).toBe(REDACTED);
    expect(redact({ note: "a normal sentence" }).note).toBe("a normal sentence");
  });

  it("redacts diagnostic text", () => {
    expect(redactText('failed with token=abc123 and "api_key": "zzz"')).not.toMatch(/abc123|zzz/);
    expect(redactText("Authorization: Bearer abc.def")).not.toMatch(/abc\.def/);
  });

  it("redacts the fixture tool-call credential before display", () => {
    const args = trace("DEMO-001").steps[3]!.tool_calls[0]!.args;
    expect(redact(args)["authorization"]).toBe(REDACTED);
  });
});

describe("graph mapping", () => {
  it("maps entities, keeping IDs as strings and marking the episode sequence", () => {
    const t = trace("DEMO-001");
    const sel = selectGraph(t, { hideContext: false, episodeOnly: false });
    const els = toElements(sel);
    const flagged = els.find((e) => e.data.id === "0090460");
    expect(flagged?.classes).toContain("role-flagged");
    expect(flagged?.data.label).toMatch(/^#4 /);
    expect(els.filter((e) => e.classes === "episode")).toHaveLength(3);
    expect(els.every((e) => typeof e.data.id === "string")).toBe(true);
  });

  it("caps rendering at 250 nodes by role priority without mutating the trace", () => {
    const t = clone(trace("DEMO-005"));
    for (let i = 0; i < 120; i += 1) t.subgraph.nodes.push({ id: `X${i}`, type: "Merchant", label: `X${i}`, role: "context", attrs: {} });
    const before = t.subgraph.nodes.length;
    const sel = selectGraph(t, { hideContext: false, episodeOnly: false });
    expect(sel.nodes).toHaveLength(250);
    expect(sel.capped).toBe(true);
    expect(t.subgraph.nodes).toHaveLength(before);
    const kept = new Set(sel.nodes.map((n) => n.id));
    t.subgraph.nodes.filter((n) => n.role === "affected" || n.role === "flagged" || n.role === "subject").forEach((n) => expect(kept.has(n.id)).toBe(true));
    expect(sel.edges.every((e) => kept.has(e.source) && kept.has(e.target))).toBe(true);
  });

  it("filters to the affected episode or hides context", () => {
    const t = trace("DEMO-001");
    const episode = selectGraph(t, { hideContext: false, episodeOnly: true });
    expect(new Set(episode.nodes.map((n) => n.role))).toEqual(new Set(["subject", "affected", "flagged", "this_case"]));
    const noCtx = selectGraph(t, { hideContext: true, episodeOnly: false });
    expect(noCtx.nodes.some((n) => n.role === "context")).toBe(false);
  });

  it("renders unknown node types and roles generically", () => {
    const els = toElements(selectGraph(trace("DEMO-004"), { hideContext: false, episodeOnly: false }));
    expect(els.find((e) => e.data.id === "DEMO-RB-1")?.classes).toBe("role-unknown type-Unknown");
  });
});

describe("document reference matching", () => {
  it("marks a retrieved document as referenced only when an evidence ref names it", () => {
    const a = view("DEMO-001");
    expect(documentReferenced("policy:R6", a)).toBe(true);
    expect(documentReferenced("policy:R1", a)).toBe(false);
  });
});
