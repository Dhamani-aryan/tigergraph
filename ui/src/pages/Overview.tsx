import { useMemo } from "react";
import { AlertTriangle, Database, Info, ListChecks } from "lucide-react";
import { useData } from "@/data/DataProvider";
import { overviewStats } from "@/data/diagnostics";
import { KpiStrip } from "@/components/overview/KpiStrip";
import { VerdictChart } from "@/components/overview/DistributionCharts";
import { CaseTable } from "@/components/overview/CaseTable";
import { GroupedDiagnostics } from "@/components/shared/Diagnostics";
import { EmptyState, Kv, Panel, Skeleton } from "@/components/ui/primitives";
import { fmtInt, shortSha } from "@/lib/format";
import { cn } from "@/lib/cn";

export function Overview() {
  const state = useData();

  if (state.status === "loading") {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading case data">
        <Skeleton className="h-8 w-72" />
        <div className="grid grid-cols-3 gap-3 lg:grid-cols-9">
          {Array.from({ length: 9 }, (_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <EmptyState icon={<AlertTriangle className="h-7 w-7 text-red-400" />} title="Case data could not be loaded">
        {state.message}
      </EmptyState>
    );
  }
  return <OverviewReady />;
}

function OverviewReady() {
  const state = useData();
  const { bundle, rows } = state.status === "ready" ? state : { bundle: undefined, rows: [] };
  const stats = useMemo(() => (bundle ? overviewStats(bundle, rows) : undefined), [bundle, rows]);
  if (!bundle || !stats) return null;

  if (bundle.mode === "unavailable") {
    return (
      <EmptyState icon={<Database className="h-7 w-7" />} title="No synchronized data">
        <p>{bundle.manifestError}</p>
        <pre className="mt-3 rounded bg-ink-950 px-3 py-2 text-left font-mono text-[12px] text-zinc-300">npm run sync-data -- --fixtures{"\n"}npm run sync-data -- --strict</pre>
      </EmptyState>
    );
  }

  const errors = bundle.diagnostics.filter((d) => d.level === "error");
  const warnings = bundle.diagnostics.filter((d) => d.level === "warning");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">Investigation overview</h1>
          <p className="mt-1 text-sm text-zinc-400">
            {bundle.mode === "fixtures"
              ? "Interface fixtures exercising every verdict, action and memory state. These are not investigation outcomes."
              : bundle.mode === "partial"
                ? `Partial backend output: ${rows.length} answer file${rows.length === 1 ? "" : "s"} synchronized of the 20 expected. See the diagnostics for what is missing or invalid.`
                : `Agent conclusions for ${stats.casesTotal} benchmark case${stats.casesTotal === 1 ? "" : "s"}, as produced by the backend batch run.`}
          </p>
        </div>
        {stats.runId && (
          <div className="text-right font-mono text-[12px] text-zinc-500">
            run <span className="text-zinc-300">{stats.runId}</span> · commit <span className="text-zinc-300">{shortSha(stats.gitCommit)}</span>
          </div>
        )}
      </div>

      {stats.source === "derived" && (
        <div role="note" className="flex items-start gap-2 rounded-lg border border-sky-500/30 bg-sky-500/5 px-3 py-2 text-[13px] text-sky-100">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <p>
            <strong>Temporary overview derived from answer files.</strong> batch_summary.json was not synchronized, so trigger metadata and backend validation are not shown, and tool, token
            and latency figures are sums of what each answer file reports.
          </p>
        </div>
      )}

      <KpiStrip stats={stats} />

      <div className="grid gap-4 lg:grid-cols-3">
        <VerdictChart stats={stats} />
        <Panel title="Run provenance" icon={<Database className="h-3.5 w-3.5" aria-hidden />}>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Kv label="Data mode">
              <span className={cn("font-semibold", bundle.mode === "official" ? "text-emerald-300" : bundle.mode === "fixtures" ? "text-amber-200" : "text-red-300")}>
                {bundle.mode === "fixtures" ? "Interface fixtures" : bundle.mode === "partial" ? "Partial / diagnostic" : "Official"}
              </span>
            </Kv>
            <Kv label="Overview source">{stats.source === "batch_summary" ? "batch_summary.json" : "derived from answers"}</Kv>
            <Kv label="LLM">{stats.llm ? <span className="id">{`${stats.llm.provider} · ${stats.llm.model}`}</span> : <span className="text-zinc-500">not reported</span>}</Kv>
            <Kv label="Generated">{stats.generatedAt ? <span className="id">{stats.generatedAt}</span> : <span className="text-zinc-500">—</span>}</Kv>
            <Kv label="Answer files">
              {fmtInt(bundle.manifest?.cases_found)} found · {fmtInt(stats.contractValid)} contract-valid
            </Kv>
            <Kv label="Traces">
              {fmtInt(bundle.manifest?.traces_found)} valid of {fmtInt(bundle.manifest?.trace_files.length)} synced
            </Kv>
            <Kv label="Source" className="col-span-2">
              <span className="text-zinc-300">{bundle.manifest?.source_label ?? "—"}</span>
            </Kv>
          </dl>
        </Panel>
        <Panel
          title="Diagnostics"
          icon={<ListChecks className="h-3.5 w-3.5" aria-hidden />}
          actions={
            <span className="text-[12px] text-zinc-500">
              <span className={errors.length ? "text-red-300" : ""}>{errors.length} errors</span> · <span className={warnings.length ? "text-amber-200" : ""}>{warnings.length} warnings</span>
            </span>
          }
          bodyClassName="scroll-thin max-h-[19rem] overflow-auto"
        >
          <GroupedDiagnostics items={[...errors, ...warnings]} empty="Every synchronized file passed the browser contract and cross-checks." />
        </Panel>
      </div>

      <CaseTable rows={rows} />
    </div>
  );
}
