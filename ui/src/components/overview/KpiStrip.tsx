import type { ReactNode } from "react";
import type { OverviewStats } from "@/data/diagnostics";
import { fmtInt, fmtSeconds, fmtUsdCompact } from "@/lib/format";
import { cn } from "@/lib/cn";

function Kpi({ label, value, sub, tone, title }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "danger" | "ok" | "warn"; title?: string }) {
  return (
    <div className="panel min-w-0 px-3 py-3" title={title}>
      <div className="whitespace-nowrap text-[10.5px] font-medium uppercase tracking-wide text-zinc-500">{label}</div>
      <div
        className={cn(
          "mt-1 font-mono text-[22px] font-semibold tabular-nums leading-7 text-white",
          tone === "danger" && "text-red-300",
          tone === "ok" && "text-emerald-300",
          tone === "warn" && "text-amber-200",
        )}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 text-[11px] leading-4 text-zinc-500">{sub}</div>}
    </div>
  );
}

export function KpiStrip({ stats }: { stats: OverviewStats }) {
  const v = stats.verdicts;
  const derived = stats.source === "derived";
  const na = <span className="text-zinc-500">n/a</span>;
  return (
    <section aria-label="Key metrics" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-9">
      <Kpi
        label="Verdicts"
        value={
          <span className="flex items-baseline gap-2 text-[18px]">
            <span className="text-red-300" title="fraud">{v.fraud ?? 0}</span>
            <span className="text-zinc-600">/</span>
            <span className="text-emerald-300" title="legitimate">{v.legitimate ?? 0}</span>
            <span className="text-zinc-600">/</span>
            <span className="text-amber-200" title="uncertain">{v.uncertain ?? 0}</span>
          </span>
        }
        sub="fraud · legit · uncertain"
      />
      <Kpi label="SARs filed" value={fmtInt(stats.sarFiled)} sub={`of ${stats.casesTotal} cases`} />
      <Kpi label="Total exposure" value={fmtUsdCompact(stats.totalExposure)} sub="sum of case exposure_usd" />
      <Kpi label="Changed" value={fmtInt(stats.actionsChanged)} sub="after requested evidence" />
      <Kpi label="Tool calls" value={stats.totalToolCalls === undefined ? na : fmtInt(stats.totalToolCalls)} sub={derived ? "summed from answer files" : "batch total"} />
      <Kpi label="Tokens" value={stats.totalTokens === undefined ? na : fmtInt(stats.totalTokens)} sub={derived ? "summed from answer files" : "batch total"} />
      <Kpi label="Avg latency" value={stats.avgLatency === undefined ? na : fmtSeconds(stats.avgLatency)} sub="per case" />
      <Kpi
        label="Valid cases"
        value={stats.casesValid === undefined ? na : `${stats.casesValid}/${stats.casesTotal}`}
        sub={stats.casesValid === undefined ? `backend not reported · ${stats.contractValid} pass contract` : `backend · ${stats.contractValid} pass contract`}
        tone={stats.casesValid !== undefined && stats.casesValid < stats.casesTotal ? "warn" : undefined}
        title="Backend validation from batch_summary.json; the browser's contract check is shown separately"
      />
      <Kpi
        label="Graph memory"
        value={`${stats.readBackVerified}/${stats.casesTotal}`}
        sub={`read back · ${stats.writtenToGraph} written`}
        tone={stats.readBackVerified < stats.casesTotal ? "warn" : "ok"}
        title="Verified requires a trace reporting a successful independent read-back"
      />
    </section>
  );
}
