/**
 * Static-data client. Reads only files synchronized into `public/data/`, never
 * a backend service. Every loader resolves to a result object — a missing or
 * malformed file becomes a diagnostic, not a thrown error — and parsed results
 * are cached for the session.
 */
import {
  DataManifestSchema,
  crossCheckAnswerTrace,
  crossCheckSummaryAnswers,
  validateAnswer,
  validateSummary,
  validateTrace,
  zodIssues,
  type AnswerValidation,
  type BatchSummary,
  type DataManifest,
  type DataMode,
  type Diagnostic,
  type TraceValidation,
} from "@/contracts";

export type FetchLike = (input: string) => Promise<Pick<Response, "ok" | "status" | "text">>;

export interface LoadedFile {
  /** Original text as served (for Raw JSON / download). */
  text?: string;
  /** Parsed JSON value, when it parsed. */
  raw?: unknown;
  /** Why the file could not be read or parsed. */
  error?: string;
  missing?: boolean;
}

export interface CaseEntry {
  caseId: string;
  file: LoadedFile;
  answer?: AnswerValidation;
}

export interface TraceEntry {
  caseId: string;
  /** false when the manifest lists no trace for this case. */
  present: boolean;
  file?: LoadedFile;
  validation?: TraceValidation;
  crosscheck: Diagnostic[];
}

export type EffectiveMode = DataMode | "unavailable";

export interface DataBundle {
  manifest?: DataManifest;
  manifestError?: string;
  /** Mode shown to the viewer: the manifest's claim, downgraded if the browser disagrees. */
  mode: EffectiveMode;
  summary?: BatchSummary;
  summaryFile?: LoadedFile;
  cases: CaseEntry[];
  traces: Map<string, TraceEntry>;
  diagnostics: Diagnostic[];
}

const joinUrl = (base: string, path: string) => `${base.endsWith("/") ? base : `${base}/`}${path}`;
const safeId = (id: string) => /^[A-Za-z0-9-]+$/.test(id);

export class DataClient {
  private cache = new Map<string, Promise<LoadedFile>>();
  private bundle?: Promise<DataBundle>;

  constructor(
    private readonly baseUrl: string = joinUrl(import.meta.env.BASE_URL, "data"),
    private readonly fetchImpl: FetchLike = (url) => fetch(url, { cache: "no-cache" }),
  ) {}

  private load(path: string): Promise<LoadedFile> {
    let hit = this.cache.get(path);
    if (!hit) {
      hit = (async (): Promise<LoadedFile> => {
        try {
          const res = await this.fetchImpl(joinUrl(this.baseUrl, path));
          if (!res.ok) return { missing: res.status === 404, error: `HTTP ${res.status} for ${path}` };
          const text = await res.text();
          try {
            return { text, raw: JSON.parse(text.replace(/^\uFEFF/, "")) };
          } catch (error) {
            return { text, error: `invalid JSON: ${error instanceof Error ? error.message : String(error)}` };
          }
        } catch (error) {
          return { error: `could not fetch ${path}: ${error instanceof Error ? error.message : String(error)}` };
        }
      })();
      this.cache.set(path, hit);
    }
    return hit;
  }

  loadBundle(): Promise<DataBundle> {
    this.bundle ??= this.buildBundle();
    return this.bundle;
  }

  private async buildBundle(): Promise<DataBundle> {
    const diagnostics: Diagnostic[] = [];
    const traces = new Map<string, TraceEntry>();
    const mfile = await this.load("data-manifest.json");
    if (mfile.error || mfile.raw === undefined) {
      return {
        manifestError: mfile.missing
          ? "data-manifest.json was not found. Run `npm run sync-data -- --fixtures` (or an official sync) before building."
          : `data-manifest.json could not be read (${mfile.error ?? "empty"}).`,
        mode: "unavailable",
        cases: [],
        traces,
        diagnostics,
      };
    }
    const parsed = DataManifestSchema.safeParse(mfile.raw);
    if (!parsed.success) {
      return {
        manifestError: "data-manifest.json does not match the manifest contract; the data cannot be trusted.",
        mode: "unavailable",
        cases: [],
        traces,
        diagnostics: zodIssues(parsed.error, "manifest"),
      };
    }
    const manifest = parsed.data;
    // Run-level sync findings (missing files, missing summary, …) cannot be
    // re-derived from what was copied, so they are carried over. Case-scoped
    // lines ("[HHG-004] …") are re-checked below from the files themselves.
    for (const [level, lines] of [["error", manifest.errors], ["warning", manifest.warnings]] as const) {
      for (const message of lines) if (!message.startsWith("[")) diagnostics.push({ level, scope: "manifest", message });
    }
    const caseIds = manifest.case_files.filter(safeId);
    const traceIds = new Set(manifest.trace_files.filter(safeId));

    const [summaryFile, ...caseFiles] = await Promise.all([
      manifest.summary_found ? this.load("batch_summary.json") : Promise.resolve<LoadedFile>({ missing: true }),
      ...caseIds.map((id) => this.load(`cases/${id}.json`)),
    ]);

    const cases: CaseEntry[] = caseIds.map((caseId, i) => {
      const file = caseFiles[i] ?? { missing: true };
      if (file.raw === undefined) {
        diagnostics.push({ level: "error", scope: "answer", caseId, message: file.error ?? "answer file is missing" });
        return { caseId, file };
      }
      const answer = validateAnswer(file.raw, caseId);
      diagnostics.push(...answer.diagnostics);
      return { caseId, file, answer };
    });

    let summary: BatchSummary | undefined;
    if (manifest.summary_found) {
      if (summaryFile.raw === undefined) {
        diagnostics.push({ level: "error", scope: "summary", message: summaryFile.error ?? "batch_summary.json is missing" });
      } else {
        const v = validateSummary(summaryFile.raw);
        diagnostics.push(...v.diagnostics);
        summary = v.summary;
        if (summary) {
          const views = cases.flatMap((c) => (c.answer?.view ? [c.answer.view] : []));
          diagnostics.push(...crossCheckSummaryAnswers(summary, views));
        }
      }
    }

    // Traces load in parallel; failures stay per-case.
    const traceEntries = await Promise.all(
      caseIds.map(async (caseId): Promise<TraceEntry> => {
        if (!traceIds.has(caseId)) return { caseId, present: false, crosscheck: [] };
        const file = await this.load(`traces/${caseId}.trace.json`);
        if (file.raw === undefined) {
          return {
            caseId,
            present: true,
            file,
            validation: { diagnostics: [{ level: "error", scope: "trace", caseId, message: file.error ?? "trace file is missing" }] },
            crosscheck: [],
          };
        }
        const validation = validateTrace(file.raw, caseId);
        const view = cases.find((c) => c.caseId === caseId)?.answer?.view;
        const crosscheck = validation.trace && view ? crossCheckAnswerTrace(view, validation.trace) : [];
        return { caseId, present: true, file, validation, crosscheck };
      }),
    );
    for (const t of traceEntries) {
      traces.set(t.caseId, t);
      diagnostics.push(...(t.validation?.diagnostics ?? []), ...t.crosscheck);
    }

    return {
      manifest,
      mode: effectiveMode(manifest, cases, summary, diagnostics),
      summary,
      summaryFile: manifest.summary_found ? summaryFile : undefined,
      cases,
      traces,
      diagnostics,
    };
  }
}

/**
 * The manifest's mode, downgraded when what the browser actually loaded does
 * not support it. Anything carrying a DEMO- identifier is always fixture data.
 */
export function effectiveMode(
  manifest: DataManifest,
  cases: CaseEntry[],
  summary: BatchSummary | undefined,
  diagnostics: Diagnostic[],
): DataMode {
  const demo = cases.some((c) => c.caseId.startsWith("DEMO-")) || summary?.run_id.startsWith("DEMO-") === true;
  if (manifest.mode === "fixtures" || demo) return "fixtures";
  if (manifest.mode === "official") {
    const blocking = diagnostics.some((d) => d.level === "error");
    const allValid = cases.length > 0 && cases.every((c) => c.answer?.contractValid);
    if (blocking || !allValid || !summary) return "partial";
  }
  return manifest.mode;
}

export const defaultClient = new DataClient();
