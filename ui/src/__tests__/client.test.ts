import { describe, expect, it } from "vitest";
import { DataClient } from "@/data/client";
import { buildRows, memoryState, overviewStats } from "@/data/diagnostics";
import { clone, fixtureFiles, memoryFetch, officialFiles } from "@/test/fixtures";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe("data client modes", () => {
  it("loads fixture data as fixtures mode", async () => {
    const b = await new DataClient("./data", memoryFetch(fixtureFiles())).loadBundle();
    expect(b.mode).toBe("fixtures");
    expect(b.cases).toHaveLength(6);
    expect(b.summary?.run_id).toBe("DEMO-FIXTURE-RUN");
  });

  it("loads a complete valid official set as official", async () => {
    const b = await new DataClient("./data", memoryFetch(officialFiles())).loadBundle();
    expect(b.diagnostics.filter((d) => d.level === "error")).toEqual([]);
    expect(b.mode).toBe("official");
  });

  it("downgrades an 'official' manifest to partial when a file fails validation", async () => {
    const files = officialFiles();
    (files["cases/HHG-007.json"] as Json).case.fraud_probability = 3;
    const b = await new DataClient("./data", memoryFetch(files)).loadBundle();
    expect(b.mode).toBe("partial");
    expect(b.diagnostics.some((d) => d.caseId === "HHG-007" && d.level === "error")).toBe(true);
    // The invalid case is still rendered, never silently replaced.
    expect(b.cases.find((c) => c.caseId === "HHG-007")?.answer?.view?.case.fraud_probability).toBe(3);
  });

  it("keeps partial mode and renders what exists when files are missing", async () => {
    const files = officialFiles();
    const m = files["data-manifest.json"] as Json;
    m.mode = "partial";
    delete files["cases/HHG-020.json"];
    delete files["batch_summary.json"];
    m.summary_found = false;
    const b = await new DataClient("./data", memoryFetch(files)).loadBundle();
    expect(b.mode).toBe("partial");
    expect(b.cases.find((c) => c.caseId === "HHG-020")?.answer).toBeUndefined();
    expect(b.diagnostics.some((d) => d.caseId === "HHG-020" && /HTTP 404/.test(d.message))).toBe(true);
    const stats = overviewStats(b, buildRows(b));
    expect(stats.source).toBe("derived");
    expect(stats.casesValid).toBeUndefined(); // backend validation is not invented
  });

  it("forces fixtures mode when DEMO identifiers appear under an official manifest", async () => {
    const files = fixtureFiles();
    (files["data-manifest.json"] as Json).mode = "official";
    const b = await new DataClient("./data", memoryFetch(files)).loadBundle();
    expect(b.mode).toBe("fixtures");
  });

  it("reports a missing manifest as unavailable instead of throwing", async () => {
    const b = await new DataClient("./data", memoryFetch({})).loadBundle();
    expect(b.mode).toBe("unavailable");
    expect(b.manifestError).toMatch(/sync-data/);
  });

  it("survives invalid JSON in an answer file", async () => {
    const files = fixtureFiles();
    files["cases/DEMO-002.json"] = "{ not json";
    const b = await new DataClient("./data", memoryFetch(files)).loadBundle();
    const entry = b.cases.find((c) => c.caseId === "DEMO-002");
    expect(entry?.file.error).toMatch(/invalid JSON/);
    expect(entry?.file.text).toBe("{ not json");
  });

  it("caches: loading twice does not refetch", async () => {
    const fetch = memoryFetch(fixtureFiles());
    const client = new DataClient("./data", fetch);
    await client.loadBundle();
    const n = fetch.calls.length;
    await client.loadBundle();
    expect(fetch.calls.length).toBe(n);
    expect(new Set(fetch.calls).size).toBe(fetch.calls.length);
  });
});

describe("missing and malformed traces", () => {
  it("renders an answer whose trace is absent, marking memory unverified", async () => {
    const b = await new DataClient("./data", memoryFetch(fixtureFiles())).loadBundle();
    const t = b.traces.get("DEMO-003");
    expect(t?.present).toBe(false);
    const row = buildRows(b).find((r) => r.caseId === "DEMO-003")!;
    expect(row.answer?.case.verdict).toBe("uncertain");
    expect(row.memory).toBe("written_unverified");
  });

  it("keeps the answer when the trace is malformed", async () => {
    const b = await new DataClient("./data", memoryFetch(fixtureFiles())).loadBundle();
    const t = b.traces.get("DEMO-006");
    expect(t?.present).toBe(true);
    expect(t?.validation?.trace).toBeUndefined();
    const row = buildRows(b).find((r) => r.caseId === "DEMO-006")!;
    expect(row.contractValid).toBe(true);
    expect(row.trace).toBeUndefined();
  });

  it("distinguishes every graph-memory state", () => {
    const files = fixtureFiles();
    const a = clone(files["cases/DEMO-001.json"]) as Json;
    const t = clone(files["traces/DEMO-001.trace.json"]) as Json;
    expect(memoryState(a as never, t as never)).toBe("verified");
    t.graph_write.read_back_ok = false;
    expect(memoryState(a as never, t as never)).toBe("readback_failed");
    expect(memoryState(a as never, undefined)).toBe("written_unverified");
    t.graph_write.written = false;
    expect(memoryState(a as never, t as never)).toBe("conflict");
    a.case.written_to_graph = false;
    expect(memoryState(a as never, t as never)).toBe("not_written");
    expect(memoryState(undefined, undefined)).toBe("unknown");
  });
});
