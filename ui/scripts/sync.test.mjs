// @vitest-environment node
/**
 * Node-side tests for the sync pipeline. Each test builds a throwaway backend
 * layout in a temp directory; the real ui/public/data is never touched.
 */
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { UI_ROOT, assertSafeOutDir, crossCheckEvidencePackage, runSync } from "./lib/sync-core.mjs";

const read = (p) => JSON.parse(readFileSync(p, "utf8"));
const legit = read(join(UI_ROOT, "fixtures", "cases", "DEMO-002.json"));
const legitTrace = read(join(UI_ROOT, "fixtures", "traces", "DEMO-002.trace.json"));
const fixtureSummary = read(join(UI_ROOT, "fixtures", "batch_summary.json"));
const IDS = Array.from({ length: 20 }, (_, i) => `HHG-${String(i + 1).padStart(3, "0")}`);

let tmp;
let source;
let out;
const quiet = () => {};
const write = (rel, data) => {
  const p = join(source, rel);
  mkdirSync(join(p, ".."), { recursive: true });
  writeFileSync(p, typeof data === "string" ? data : JSON.stringify(data, null, 2));
};

function writeOfficial({ skip = [], traces = true } = {}) {
  const rows = [];
  for (const id of IDS) {
    if (skip.includes(id)) continue;
    const a = structuredClone(legit);
    a.case_id = id;
    a.case.graph_case_id = `CASE-${id}`;
    write(`cases/${id}.json`, a);
    if (traces) {
      const t = structuredClone(legitTrace);
      t.case_id = id;
      t.graph_write.graph_case_id = `CASE-${id}`;
      write(`runs/latest/traces/${id}.trace.json`, t);
    }
    rows.push({ ...structuredClone(fixtureSummary.cases[1]), case_id: id });
  }
  write("runs/latest/batch_summary.json", {
    ...structuredClone(fixtureSummary),
    run_id: "run-test",
    cases_total: rows.length,
    cases_valid: rows.length,
    cases_written_to_graph: rows.length,
    verdicts: { legitimate: rows.length },
    patterns: { none: rows.length },
    sar_filed: 0,
    actions_changed: 0,
    total_exposure_usd: 0,
    cases: rows,
  });
}

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "fg-sync-"));
  source = join(tmp, "backend");
  out = join(tmp, "site", "public", "data");
  mkdirSync(source, { recursive: true });
});
afterEach(() => rmSync(tmp, { recursive: true, force: true }));

describe("strict sync", () => {
  it("succeeds with 20 valid cases, traces and a summary, producing an official manifest", async () => {
    writeOfficial();
    const r = await runSync({ kind: "official", sourceRoot: source, outDir: out, strict: true, log: quiet });
    expect(r.errors).toEqual([]);
    expect(r.exitCode).toBe(0);
    const m = read(join(out, "data-manifest.json"));
    expect(m).toMatchObject({ mode: "official", cases_found: 20, valid_cases: 20, traces_found: 20, summary_found: true });
    expect(readdirSync(join(out, "cases"))).toHaveLength(20);
    // Bytes are copied, not re-serialized.
    expect(readFileSync(join(out, "cases", "HHG-001.json"), "utf8")).toBe(readFileSync(join(source, "cases", "HHG-001.json"), "utf8"));
  });

  it("fails with exit 1 and leaves the output untouched when a case is missing", async () => {
    writeOfficial({ skip: ["HHG-013"] });
    mkdirSync(out, { recursive: true });
    writeFileSync(join(out, "sentinel.txt"), "keep");
    const r = await runSync({ kind: "official", sourceRoot: source, outDir: out, strict: true, log: quiet });
    expect(r.exitCode).toBe(1);
    expect(r.errors.join("\n")).toMatch(/missing answer files: HHG-013/);
    expect(existsSync(join(out, "data-manifest.json"))).toBe(false);
    expect(readFileSync(join(out, "sentinel.txt"), "utf8")).toBe("keep");
  });

  it("fails when a final write field is false", async () => {
    writeOfficial();
    const a = read(join(source, "cases", "HHG-004.json"));
    a.case.written_to_graph = false;
    a.case.graph_case_id = "";
    write("cases/HHG-004.json", a);
    const r = await runSync({ kind: "official", sourceRoot: source, outDir: out, strict: true, log: quiet });
    expect(r.exitCode).toBe(1);
    expect(r.errors.join("\n")).toMatch(/\[HHG-004\] final: written_to_graph is false/);
  });

  it("fails when read-back did not succeed", async () => {
    writeOfficial();
    const t = read(join(source, "runs", "latest", "traces", "HHG-009.trace.json"));
    t.graph_write.read_back_ok = false;
    write("runs/latest/traces/HHG-009.trace.json", t);
    const r = await runSync({ kind: "official", sourceRoot: source, outDir: out, strict: true, log: quiet });
    expect(r.exitCode).toBe(1);
    expect(r.errors.join("\n")).toMatch(/HHG-009\] final: trace graph_write.read_back_ok is false/);
  });

  it("treats missing traces as warnings unless --require-traces", async () => {
    writeOfficial({ traces: false });
    const lenient = await runSync({ kind: "official", sourceRoot: source, outDir: out, strict: true, log: quiet });
    expect(lenient.exitCode).toBe(0);
    expect(lenient.warnings.join("\n")).toMatch(/without a valid trace/);
    const strict = await runSync({ kind: "official", sourceRoot: source, outDir: out, strict: true, requireTraces: true, log: quiet });
    expect(strict.exitCode).toBe(1);
  });

  it("refuses to run when no backend answers exist, changing nothing", async () => {
    const r = await runSync({ kind: "official", sourceRoot: source, outDir: out, log: quiet });
    expect(r.exitCode).toBe(1);
    expect(r.errors[0]).toMatch(/no backend answer files found/);
    expect(existsSync(out)).toBe(false);
  });

  it("rejects --strict together with --fixtures", async () => {
    const r = await runSync({ kind: "fixtures", strict: true, outDir: out, log: quiet });
    expect(r.exitCode).toBe(2);
  });
});

describe("non-strict sync", () => {
  it("produces partial mode, cleans stale files and reports duplicates and bad names", async () => {
    writeOfficial({ skip: ["HHG-020"] });
    mkdirSync(join(out, "cases"), { recursive: true });
    writeFileSync(join(out, "cases", "HHG-099.json"), "{}"); // stale from an earlier sync
    const dup = read(join(source, "cases", "HHG-001.json"));
    dup.case_id = "HHG-002";
    write("cases/HHG-001.json", dup); // declares the wrong case_id
    write("cases/notes.json", {}); // not a case filename
    write("cases/HHG-005.json", "{ broken");
    const r = await runSync({ kind: "official", sourceRoot: source, outDir: out, log: quiet });
    expect(r.exitCode).toBe(0);
    const m = read(join(out, "data-manifest.json"));
    expect(m.mode).toBe("partial");
    const errs = m.errors.join("\n");
    expect(errs).toMatch(/HHG-001\.json declares case_id "HHG-002"/);
    expect(errs).toMatch(/duplicate case_id/);
    expect(errs).toMatch(/\[HHG-005\] answer: invalid JSON/);
    expect(errs).toMatch(/missing answer files: .*HHG-020/);
    expect(m.warnings.join("\n")).toMatch(/notes\.json: filename is not/);
    expect(existsSync(join(out, "cases", "HHG-099.json"))).toBe(false);
    expect(existsSync(join(out, "cases", "notes.json"))).toBe(false);
    // Invalid files are still copied so the UI can show their diagnostics.
    expect(existsSync(join(out, "cases", "HHG-005.json"))).toBe(true);
  });

  it("never modifies the source files", async () => {
    writeOfficial();
    const before = readFileSync(join(source, "runs", "latest", "batch_summary.json"), "utf8");
    await runSync({ kind: "official", sourceRoot: source, outDir: out, log: quiet });
    expect(readFileSync(join(source, "runs", "latest", "batch_summary.json"), "utf8")).toBe(before);
  });

  it("syncs fixtures into fixtures mode with DEMO case files only", async () => {
    const r = await runSync({ kind: "fixtures", outDir: out, log: quiet });
    expect(r.exitCode).toBe(0);
    const m = read(join(out, "data-manifest.json"));
    expect(m.mode).toBe("fixtures");
    expect(m.case_files.every((id) => id.startsWith("DEMO-"))).toBe(true);
    expect(m.trace_files).not.toContain("DEMO-003");
  });
});

describe("safety and reference checks", () => {
  it("refuses output directories other than public/data", () => {
    expect(() => assertSafeOutDir(join(tmp, "cases"))).toThrow(/refusing to write/);
    expect(() => assertSafeOutDir(join(tmp, "public", "data"))).not.toThrow();
  });

  it("cross-checks an answer against an evidence package without trusting either", () => {
    const a = structuredClone(legit);
    a.case_id = "HHG-019";
    a.case.similar_prior_cases = ["CC-0001"];
    const pkg = {
      case_id: "HHG-019",
      anchor_time: "2016-12-01 17:28:53",
      observed_entity_ids: ["9203311"],
      prior_case_retrieval: { cases: [{ case_id: "CC-5111" }] },
      features: { candidate_episode_txn_ids: { value: ["3503878"] }, candidate_episode_exposure_usd: { value: 99.92 } },
      trigger: { flagged_txn_id: "3503878", card_id: "C07987-K2", customer_id: "C07987", opened_at: "2016-12-01 22:28:53" },
    };
    const t = structuredClone(legitTrace);
    const d = crossCheckEvidencePackage(a, t, pkg);
    const text = d.map((x) => x.message).join("\n");
    expect(text).toMatch(/not observed by the reference package: DEMO-C4410-K1/);
    expect(text).toMatch(/absent from the reference retrieval: CC-0001/);
    expect(text).toMatch(/exposure 0 differs/);
    expect(d.some((x) => x.level === "error" && /trigger.flagged_txn_id/.test(x.message))).toBe(true);
  });
});
