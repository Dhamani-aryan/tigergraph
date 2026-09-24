/** `public/data/data-manifest.json`, written only by `scripts/sync-data.mjs`. */
import { z } from "zod";

export const DATA_MODES = ["official", "fixtures", "partial"] as const;
export type DataMode = (typeof DATA_MODES)[number];

export const DataManifestSchema = z.object({
  mode: z.enum(DATA_MODES),
  generated_at: z.string(),
  source_commit: z.string().optional(),
  cases_found: z.number().int().min(0),
  valid_cases: z.number().int().min(0),
  traces_found: z.number().int().min(0),
  summary_found: z.boolean(),
  errors: z.array(z.string()),
  warnings: z.array(z.string()),
  /** File stems copied into `cases/` (always strings, e.g. "HHG-019"). */
  case_files: z.array(z.string()).default([]),
  /** Case IDs with a `traces/<id>.trace.json` file. */
  trace_files: z.array(z.string()).default([]),
  /** Where the files came from, relative wording only (never an absolute path). */
  source_label: z.string().optional(),
});

export type DataManifest = z.infer<typeof DataManifestSchema>;
