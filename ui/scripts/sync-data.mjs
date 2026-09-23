#!/usr/bin/env node
/**
 * Copy backend artifacts (or interface fixtures) into ui/public/data/ and
 * write data-manifest.json.
 *
 *   node scripts/sync-data.mjs                    official, non-strict (partial allowed)
 *   node scripts/sync-data.mjs --strict           official; exit 1 unless all 20 cases, the
 *                                                  batch summary and final write/validation
 *                                                  fields are present and valid
 *   node scripts/sync-data.mjs --fixtures         frontend interface fixtures (DEMO DATA)
 *   --source <repo-root>                          backend repository root (default: ..)
 *   --require-traces                              missing traces become errors
 *   --reference-evidence <file>                   cross-check against an evidence package
 *
 * Originals are never modified. Only ui/public/data/ is written.
 */
import { resolve } from "node:path";
import { parseArgs, runSync } from "./lib/sync-core.mjs";

let args;
try {
  args = parseArgs(process.argv.slice(2));
} catch (error) {
  console.error(`✖ ${error.message}`);
  process.exit(2);
}
const known = new Set(["fixtures", "strict", "require-traces", "help"]);
const unknown = [...args.flags].filter((f) => !known.has(f));
if (unknown.length || args.flags.has("help")) {
  if (unknown.length) console.error(`✖ unknown flag(s): ${unknown.map((f) => `--${f}`).join(", ")}`);
  console.error("usage: node scripts/sync-data.mjs [--fixtures | --strict] [--source <repo-root>] [--require-traces] [--reference-evidence <file>]");
  process.exit(unknown.length ? 2 : 0);
}

const result = await runSync({
  kind: args.flags.has("fixtures") ? "fixtures" : "official",
  sourceRoot: args.values.source ? resolve(args.values.source) : undefined,
  strict: args.flags.has("strict"),
  requireTraces: args.flags.has("require-traces"),
  referenceEvidence: args.values["reference-evidence"],
});
if (result.exitCode === 2) result.errors.forEach((e) => console.error(`✖ ${e}`));
process.exit(result.exitCode);
