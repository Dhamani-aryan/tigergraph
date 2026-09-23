/**
 * Core of `sync-data.mjs` / `validate-data.mjs`.
 *
 * Reads backend artifacts (or frontend fixtures), validates them with the SAME
 * Zod contracts the browser uses (bundled on the fly from src/contracts with
 * esbuild), and — unless this is a dry run — copies the original bytes into
 * `ui/public/data/` and writes `data-manifest.json`.
 *
 * Source files are only ever read. Deletion is confined to the output
 * directory's `cases/` and `traces/` folders and its two generated files.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync, copyFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const UI_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

let contractsPromise;
/** Bundle src/contracts/index.ts once per process and import it. */
export function loadContracts() {
  contractsPromise ??= (async () => {
    const { build } = await import("esbuild");
    const out = await build({
      entryPoints: [join(UI_ROOT, "src", "contracts", "index.ts")],
      bundle: true,
      format: "esm",
      platform: "node",
      target: "node20",
      write: false,
      logLevel: "silent",
    });
    const code = out.outputFiles[0].text;
    return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
  })();
  return contractsPromise;
}

const fmt = (d) =>
  `${d.caseId ? `[${d.caseId}] ` : ""}${d.scope}${d.path ? ` · ${d.path}` : ""}: ${d.message}`;

function listJson(dir) {
  if (!dir || !existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isFile() && e.name.toLowerCase().endsWith(".json"))
    .map((e) => e.name)
    .sort();
}

function readJson(path) {
  const text = readFileSync(path, "utf8").replace(/^\uFEFF/, "");
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function gitCommit(root) {
  try {
    return execFileSync("git", ["-C", root, "rev-parse", "HEAD"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return undefined;
  }
}

/** Refuse to write anywhere except a `.../public/data` directory. */
export function assertSafeOutDir(outDir) {
  const abs = resolve(outDir);
  if (basename(abs) !== "data" || basename(dirname(abs)) !== "public") {
    throw new Error(`refusing to write outside a public/data directory: ${abs}`);
  }
  return abs;
}

/**
 * Optional sanity check of an official answer/trace against Aryan's Gate 2
 * evidence package (reference only — never a substitute for either file).
 */
export function crossCheckEvidencePackage(answer, trace, pkg) {
  const out = [];
  const add = (level, message) => out.push({ level, scope: "crosscheck", caseId: answer?.case_id, message: `evidence-package: ${message}` });
  if (!answer || !pkg) return out;
  if (pkg.case_id !== answer.case_id) {
    add("warning", `reference package is for ${pkg.case_id}, not ${answer.case_id}; skipped`);
    return out;
  }
  const observed = new Set((pkg.observed_entity_ids ?? []).map(String));
  const unseen = [...new Set(answer.case.evidence.flatMap((e) => e.entity_ids))].filter((id) => !observed.has(id));
  if (unseen.length) add("warning", `evidence entity IDs not observed by the reference package: ${unseen.slice(0, 12).join(", ")}${unseen.length > 12 ? " …" : ""}`);
  const retrieved = new Set((pkg.prior_case_retrieval?.cases ?? []).map((c) => c.case_id));
  const notRetrieved = answer.case.similar_prior_cases.filter((id) => !retrieved.has(id));
  if (notRetrieved.length) add("info", `similar_prior_cases absent from the reference retrieval: ${notRetrieved.join(", ")}`);
  const episode = pkg.features?.candidate_episode_txn_ids?.value;
  if (Array.isArray(episode)) {
    const a = [...answer.case.affected_txn_ids].sort().join(",");
    const b = episode.map(String).sort().join(",");
    if (a !== b) add("info", `affected_txn_ids [${a}] differ from the reference candidate episode [${b}]`);
  }
  const refExposure = pkg.features?.candidate_episode_exposure_usd?.value;
  if (typeof refExposure === "number" && Math.abs(refExposure - answer.case.exposure_usd) > 0.01) {
    add("info", `exposure ${answer.case.exposure_usd} differs from the reference candidate-episode exposure ${refExposure}`);
  }
  if (trace && pkg.trigger) {
    for (const key of ["flagged_txn_id", "card_id", "customer_id", "opened_at"]) {
      if (pkg.trigger[key] !== undefined && String(pkg.trigger[key]) !== String(trace.trigger[key])) {
        add("error", `trace trigger.${key}=${JSON.stringify(trace.trigger[key])} but reference has ${JSON.stringify(pkg.trigger[key])}`);
      }
    }
    if (pkg.anchor_time && trace.cutoff_ts && trace.cutoff_ts > pkg.anchor_time) {
      add("warning", `trace cutoff ${trace.cutoff_ts} is later than the reference evidence anchor ${pkg.anchor_time}`);
    }
  }
  return out;
}

/**
 * @param {object} opts
 * @param {"fixtures"|"official"} opts.kind
 * @param {string} [opts.sourceRoot]   backend repo root (official)
 * @param {{casesDir:string,tracesDir:string,summaryPath:string}} [opts.layout] explicit input layout
 * @param {string} [opts.outDir]       must be a public/data directory
 * @param {boolean} [opts.strict]
 * @param {boolean} [opts.requireTraces]
 * @param {boolean} [opts.dryRun]
 * @param {string} [opts.referenceEvidence] path to an evidence package JSON
 * @param {(line:string)=>void} [opts.log]
 */
export async function runSync(opts) {
  const log = opts.log ?? ((line) => console.log(line));
  const C = await loadContracts();
  const fixtures = opts.kind === "fixtures";
  const sourceRoot = resolve(opts.sourceRoot ?? join(UI_ROOT, ".."));
  const layout =
    opts.layout ??
    (fixtures
      ? {
          casesDir: join(UI_ROOT, "fixtures", "cases"),
          tracesDir: join(UI_ROOT, "fixtures", "traces"),
          summaryPath: join(UI_ROOT, "fixtures", "batch_summary.json"),
        }
      : {
          casesDir: join(sourceRoot, "cases"),
          tracesDir: join(sourceRoot, "runs", "latest", "traces"),
          summaryPath: join(sourceRoot, "runs", "latest", "batch_summary.json"),
        });

  const errors = [];
  const warnings = [];
  const push = (d) => {
    if (d.level === "error") errors.push(fmt(d));
    else if (d.level === "warning") warnings.push(fmt(d));
  };

  if (fixtures && opts.strict) {
    return { exitCode: 2, errors: ["--strict cannot be combined with --fixtures: fixtures are never official data"], wrote: false };
  }

  const caseNames = listJson(layout.casesDir);
  if (!fixtures && caseNames.length === 0) {
    const msg = `no backend answer files found in cases/ under the source repository (expected cases/HHG-001.json … HHG-020.json). Nothing was changed. Run the backend batch first, or use --fixtures for interface fixtures.`;
    log(`✖ ${msg}`);
    return { exitCode: 1, errors: [msg], wrote: false };
  }

  /* ---- answers */
  const answers = new Map(); // caseId -> { file, view, valid }
  const copyCases = [];
  for (const name of caseNames) {
    const m = C.CASE_FILE_RE.exec(name);
    if (!m) {
      warnings.push(`cases/${name}: filename is not <CASE-ID>.json; skipped`);
      continue;
    }
    const stem = m[1];
    if (!fixtures && !stem.startsWith("HHG-")) {
      warnings.push(`cases/${name}: not a benchmark case ID; skipped`);
      continue;
    }
    if (fixtures && !stem.startsWith("DEMO-")) {
      errors.push(`fixtures/cases/${name}: fixture IDs must start with DEMO-`);
      continue;
    }
    const path = join(layout.casesDir, name);
    copyCases.push({ path, name });
    const parsed = readJson(path);
    if (!parsed.ok) {
      errors.push(`[${stem}] answer: invalid JSON (${parsed.error})`);
      continue;
    }
    const v = C.validateAnswer(parsed.value, stem);
    v.diagnostics.forEach(push);
    if (v.view) {
      if ([...answers.values()].some((x) => x.view.case_id === v.view.case_id)) {
        errors.push(`[${v.view.case_id}] answer: duplicate case_id across files`);
      }
      answers.set(stem, { view: v.view, valid: v.contractValid });
    }
  }

  /* ---- traces */
  const traces = new Map();
  const copyTraces = [];
  for (const name of listJson(layout.tracesDir)) {
    const m = C.TRACE_FILE_RE.exec(name);
    if (!m) {
      warnings.push(`traces/${name}: filename is not <CASE-ID>.trace.json; skipped`);
      continue;
    }
    const stem = m[1];
    if (!answers.has(stem) && !copyCases.some((c) => c.name === `${stem}.json`)) {
      warnings.push(`[${stem}] trace: no matching answer file; skipped`);
      continue;
    }
    copyTraces.push({ path: join(layout.tracesDir, name), name });
    const parsed = readJson(join(layout.tracesDir, name));
    if (!parsed.ok) {
      errors.push(`[${stem}] trace: invalid JSON (${parsed.error})`);
      continue;
    }
    const v = C.validateTrace(parsed.value, stem);
    v.diagnostics.forEach(push);
    if (v.trace) {
      traces.set(stem, v.trace);
      const a = answers.get(stem);
      if (a) C.crossCheckAnswerTrace(a.view, v.trace).forEach(push);
    }
  }

  /* ---- summary */
  let summaryFound = false;
  let summaryValid = false;
  let summary;
  if (existsSync(layout.summaryPath)) {
    summaryFound = true;
    const parsed = readJson(layout.summaryPath);
    if (!parsed.ok) errors.push(`summary: batch_summary.json is invalid JSON (${parsed.error})`);
    else {
      const v = C.validateSummary(parsed.value);
      v.diagnostics.forEach(push);
      if (v.summary) {
        summary = v.summary;
        summaryValid = !v.diagnostics.some((d) => d.level === "error");
        C.crossCheckSummaryAnswers(v.summary, [...answers.values()].map((a) => a.view)).forEach(push);
      }
    }
  } else if (!fixtures) {
    errors.push("summary: runs/latest/batch_summary.json is missing");
  }

  /* ---- official completeness + final fields */
  if (!fixtures) {
    const missing = C.OFFICIAL_CASE_IDS.filter((id) => !answers.has(id));
    if (missing.length) errors.push(`missing answer files: ${missing.join(", ")}`);
    const extra = [...answers.keys()].filter((id) => !C.OFFICIAL_CASE_IDS.includes(id));
    if (extra.length) warnings.push(`unexpected case IDs (not HHG-001…HHG-020): ${extra.join(", ")}`);
    for (const [id, a] of answers) {
      if (!a.view.case.written_to_graph) errors.push(`[${id}] final: written_to_graph is false — case memory not written`);
      const t = traces.get(id);
      if (t) {
        if (!t.graph_write.read_back_ok) errors.push(`[${id}] final: trace graph_write.read_back_ok is false — memory unverified`);
        if (!t.validation.passed) errors.push(`[${id}] final: trace validation.passed is false`);
      }
      const row = summary?.cases.find((c) => c.case_id === id);
      if (row && !row.validation_passed) errors.push(`[${id}] final: batch summary validation_passed is false`);
    }
    const noTrace = [...answers.keys()].filter((id) => !traces.has(id));
    if (noTrace.length) {
      const msg = `answer files without a valid trace: ${noTrace.join(", ")}`;
      if (opts.requireTraces) errors.push(msg);
      else warnings.push(msg);
    }
  }

  /* ---- optional reference evidence package */
  if (opts.referenceEvidence) {
    const parsed = existsSync(opts.referenceEvidence) ? readJson(opts.referenceEvidence) : { ok: false, error: "file not found" };
    if (!parsed.ok) warnings.push(`reference evidence package could not be read (${parsed.error})`);
    else {
      const id = parsed.value.case_id;
      const a = answers.get(id);
      if (!a) warnings.push(`reference evidence package is for ${id}, but no answer file for ${id} was found`);
      else crossCheckEvidencePackage(a.view, traces.get(id), parsed.value).forEach(push);
    }
  }

  const validCases = [...answers.values()].filter((a) => a.valid).length;
  let mode;
  if (fixtures) mode = "fixtures";
  else if (errors.length === 0 && validCases === C.OFFICIAL_CASE_IDS.length && summaryValid) mode = "official";
  else mode = "partial";

  const manifest = {
    mode,
    generated_at: new Date().toISOString(),
    source_commit: gitCommit(fixtures ? UI_ROOT : sourceRoot),
    cases_found: answers.size,
    valid_cases: validCases,
    traces_found: traces.size,
    summary_found: summaryFound,
    errors,
    warnings,
    case_files: copyCases.map((c) => c.name.replace(/\.json$/, "")),
    trace_files: copyTraces.map((c) => c.name.replace(/\.trace\.json$/, "")),
    source_label: fixtures ? "ui/fixtures — interface fixtures" : "backend repository — cases/ and runs/latest/",
  };

  const strictFailed = !fixtures && opts.strict && mode !== "official";
  let wrote = false;
  if (!opts.dryRun && !strictFailed) {
    const outDir = assertSafeOutDir(opts.outDir ?? join(UI_ROOT, "public", "data"));
    const casesOut = join(outDir, "cases");
    const tracesOut = join(outDir, "traces");
    rmSync(casesOut, { recursive: true, force: true });
    rmSync(tracesOut, { recursive: true, force: true });
    rmSync(join(outDir, "batch_summary.json"), { force: true });
    mkdirSync(casesOut, { recursive: true });
    mkdirSync(tracesOut, { recursive: true });
    for (const c of copyCases) copyFileSync(c.path, join(casesOut, c.name));
    for (const t of copyTraces) copyFileSync(t.path, join(tracesOut, t.name));
    if (summaryFound) copyFileSync(layout.summaryPath, join(outDir, "batch_summary.json"));
    writeFileSync(join(outDir, "data-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
    wrote = true;
  }

  /* ---- report */
  log(`FraudGraph data sync — ${fixtures ? "INTERFACE FIXTURES" : opts.strict ? "official (strict)" : "official"}${opts.dryRun ? " [dry run]" : ""}`);
  log(`  mode           ${mode}`);
  log(`  answer files   ${answers.size} found, ${validCases} contract-valid`);
  log(`  traces         ${traces.size} valid of ${copyTraces.length} present`);
  log(`  batch summary  ${summaryFound ? (summaryValid ? "present, valid" : "present, INVALID") : "missing"}`);
  log(`  errors         ${errors.length}`);
  log(`  warnings       ${warnings.length}`);
  const show = (list, mark) => {
    list.slice(0, 25).forEach((line) => log(`    ${mark} ${line}`));
    if (list.length > 25) log(`    … ${list.length - 25} more (see data-manifest.json)`);
  };
  show(errors, "✖");
  show(warnings, "!");
  if (strictFailed) {
    log("✖ STRICT SYNC FAILED — official data is incomplete or invalid; public/data was left unchanged.");
    log("  Fix the backend artifacts listed above and rerun `npm run sync-data -- --strict`.");
  } else if (wrote) {
    log(`✔ wrote public/data (${copyCases.length} case files, ${copyTraces.length} trace files${summaryFound ? ", batch summary" : ""}, data-manifest.json)`);
  }

  const exitCode = strictFailed ? 1 : opts.dryRun && errors.length ? 1 : 0;
  return { exitCode, manifest, errors, warnings, wrote };
}

/** Minimal argv parser shared by the two CLIs. */
export function parseArgs(argv) {
  const out = { flags: new Set(), values: {} };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--source" || arg === "--reference-evidence" || arg === "--dir") {
      const value = argv[i + 1];
      if (!value || value.startsWith("--")) throw new Error(`${arg} requires a path`);
      out.values[arg.slice(2)] = value;
      i += 1;
    } else if (arg.startsWith("--")) out.flags.add(arg.slice(2));
    else throw new Error(`unexpected argument ${arg}`);
  }
  return out;
}
