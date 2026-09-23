#!/usr/bin/env node
/**
 * Validate data without copying anything (dry run of the sync pipeline).
 *
 *   node scripts/validate-data.mjs                 validate what is in ui/public/data/
 *   node scripts/validate-data.mjs --source <root> validate a backend repo's cases/ + runs/latest/
 *   --strict / --require-traces / --reference-evidence <file>  as in sync-data
 *
 * Exits 1 when any error is found.
 */
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { UI_ROOT, parseArgs, runSync } from "./lib/sync-core.mjs";

let args;
try {
  args = parseArgs(process.argv.slice(2));
} catch (error) {
  console.error(`✖ ${error.message}`);
  process.exit(2);
}

let kind = "official";
let layout;
if (!args.values.source) {
  const dir = join(UI_ROOT, "public", "data");
  const manifestPath = join(dir, "data-manifest.json");
  if (existsSync(manifestPath)) {
    try {
      if (JSON.parse(readFileSync(manifestPath, "utf8")).mode === "fixtures") kind = "fixtures";
    } catch {
      /* the run below reports the problem */
    }
  }
  layout = { casesDir: join(dir, "cases"), tracesDir: join(dir, "traces"), summaryPath: join(dir, "batch_summary.json") };
}

const result = await runSync({
  kind,
  layout,
  sourceRoot: args.values.source ? resolve(args.values.source) : undefined,
  strict: args.flags.has("strict"),
  requireTraces: args.flags.has("require-traces"),
  referenceEvidence: args.values["reference-evidence"],
  dryRun: true,
});
if (kind === "fixtures") console.log("  note: public/data holds INTERFACE FIXTURES (their DEMO-006 trace is malformed on purpose)");
process.exit(kind === "fixtures" ? 0 : result.exitCode);
