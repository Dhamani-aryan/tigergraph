import { Link } from "react-router-dom";
import { AlertOctagon, ChevronLeft, ChevronRight, Siren, UserRoundSearch } from "lucide-react";
import type { CaseRow } from "@/data/diagnostics";
import { MEMORY_LABEL } from "@/data/diagnostics";
import { BackendValidationBadge, ContractBadge, MemoryBadge, PatternText, ProbabilityBar, SarBadge, StatusBadge, VerdictBadge } from "@/components/shared/badges";
import { Kv } from "@/components/ui/primitives";
import { fmtUsd, humanize } from "@/lib/format";
import { isFixtureId, FIXTURE_PURPOSE } from "@/data/fixtures";
import { cn } from "@/lib/cn";

export function CaseHeader({ row, prevId, nextId }: { row: CaseRow; prevId?: string; nextId?: string }) {
  const a = row.answer;
  const t = row.trace;
  const trigger = t?.trigger;
  const memoryAlarm = row.memory === "not_written" || row.memory === "readback_failed" || row.memory === "conflict";

  return (
    <div className="space-y-3">
      <nav aria-label="Case navigation" className="flex items-center justify-between gap-2 text-[13px]">
        <Link to="/" className="inline-flex items-center gap-1 text-zinc-400 hover:text-zinc-100">
          <ChevronLeft className="h-4 w-4" aria-hidden /> All cases
        </Link>
        <div className="flex items-center gap-1">
          {prevId ? (
            <Link to={`/case/${encodeURIComponent(prevId)}`} className="inline-flex items-center gap-1 rounded-md border border-ink-700 px-2 py-1 text-zinc-300 hover:bg-ink-800" aria-label={`Previous case ${prevId}`}>
              <ChevronLeft className="h-3.5 w-3.5" aria-hidden /> <span className="id">{prevId}</span>
            </Link>
          ) : (
            <span className="rounded-md border border-ink-800 px-2 py-1 text-zinc-600">First case</span>
          )}
          {nextId ? (
            <Link to={`/case/${encodeURIComponent(nextId)}`} className="inline-flex items-center gap-1 rounded-md border border-ink-700 px-2 py-1 text-zinc-300 hover:bg-ink-800" aria-label={`Next case ${nextId}`}>
              <span className="id">{nextId}</span> <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : (
            <span className="rounded-md border border-ink-800 px-2 py-1 text-zinc-600">Last case</span>
          )}
        </div>
      </nav>

      <section className="panel overflow-hidden" aria-labelledby="case-title">
        <div className="flex flex-wrap items-start gap-x-6 gap-y-3 border-b border-ink-700/70 px-5 py-4">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 id="case-title" className="id text-2xl font-semibold tracking-tight text-white">
                {row.caseId}
              </h1>
              <VerdictBadge verdict={a?.case.verdict} className="text-[12px]" />
              <StatusBadge status={a?.case.status} />
              {isFixtureId(row.caseId) && <span className="rounded bg-amber-400 px-1.5 py-0.5 text-[10px] font-black tracking-widest text-ink-950">DEMO FIXTURE</span>}
            </div>
            {isFixtureId(row.caseId) && FIXTURE_PURPOSE[row.caseId] && <p className="mt-1 text-[12px] text-amber-200/80">Fixture exercises: {FIXTURE_PURPOSE[row.caseId]}</p>}
            {trigger ? (
              <p className="mt-2 max-w-3xl text-sm text-zinc-300">
                <span className="mr-2 rounded bg-ink-700 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-300">{humanize(trigger.trigger_type)}</span>
                {trigger.trigger_text}
              </p>
            ) : (
              <p className="mt-2 text-[13px] text-zinc-500">Trigger text is supplied by the execution trace, which is {row.traceEntry?.present ? "invalid" : "not available"} for this case.</p>
            )}
          </div>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
            <Kv label="Customer">{row.customerId ? <span className="id">{row.customerId}</span> : <span className="text-zinc-500">—</span>}</Kv>
            <Kv label="Card">{row.cardId ? <span className="id">{row.cardId}</span> : <span className="text-zinc-500">—</span>}</Kv>
            <Kv label="Opened">{trigger?.opened_at || row.summaryRow?.opened_at ? <span className="id">{trigger?.opened_at ?? row.summaryRow?.opened_at}</span> : <span className="text-zinc-500">—</span>}</Kv>
            <Kv label="Risk score (input)">
              {typeof trigger?.risk_score === "number" ? <span className="font-mono">{trigger.risk_score.toFixed(2)}</span> : <span className="text-zinc-500">—</span>}
            </Kv>
          </dl>
        </div>

        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-5 py-3.5 sm:grid-cols-4 lg:grid-cols-8">
          <Kv label="Fraud probability">
            <ProbabilityBar value={a?.case.fraud_probability} />
          </Kv>
          <Kv label="Pattern" className="sm:col-span-2 lg:col-span-2">
            <PatternText pattern={a?.case.pattern} />
          </Kv>
          <Kv label="Exposure">
            <span className="font-mono tabular-nums">{fmtUsd(a?.case.exposure_usd)}</span>
          </Kv>
          <Kv label="SAR">
            <SarBadge filed={a?.sar.file} />
          </Kv>
          <Kv label="Validation">
            <div className="flex flex-wrap gap-1">
              <BackendValidationBadge state={row.backend} />
              <ContractBadge valid={row.contractValid} errors={row.errorCount} />
            </div>
          </Kv>
          <Kv label="Graph memory">
            <MemoryBadge state={row.memory} compact />
          </Kv>
          <Kv label="Graph case ID">{a?.case.graph_case_id ? <span className="id">{a.case.graph_case_id}</span> : <span className="text-zinc-500">none</span>}</Kv>
        </dl>

        {a?.case.pattern === "undocumented" && (
          <div className="border-t border-violet-500/30 bg-violet-500/10 px-5 py-3 text-sm text-violet-100">
            <span className="mr-2 font-semibold uppercase tracking-wider text-violet-300">Discovered pattern</span>
            {a.case.pattern_description || <em className="text-violet-300/70">No pattern_description supplied.</em>}
          </div>
        )}
        {a?.case.verdict === "uncertain" && (
          <div className="flex items-start gap-2 border-t border-amber-400/40 bg-amber-400/10 px-5 py-3 text-sm text-amber-100">
            <UserRoundSearch className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden />
            <p>
              <strong className="text-amber-200">Escalated — uncertain verdict.</strong>{" "}
              {a.next_best_actions.final.some((x) => x.action === "ESCALATE_TO_ANALYST")
                ? "Final actions route this case to a human analyst (ESCALATE_TO_ANALYST)."
                : "The final actions do not include ESCALATE_TO_ANALYST."}{" "}
              Status: {humanize(a.case.status)}.
            </p>
          </div>
        )}
        {memoryAlarm && (
          <div role="alert" className={cn("flex items-start gap-2 border-t px-5 py-3 text-sm", "border-red-500/60 bg-red-600/15 text-red-100")}>
            <AlertOctagon className="mt-0.5 h-4 w-4 shrink-0 text-red-400" aria-hidden />
            <p>
              <strong className="text-red-300">{MEMORY_LABEL[row.memory]}.</strong>{" "}
              {row.memory === "not_written" && "This case was NOT written to TigerGraph case memory; later investigations cannot retrieve it."}
              {row.memory === "readback_failed" && "A write was reported, but the independent read-back did not confirm it. Case memory is unverified and not counted as a success."}
              {row.memory === "conflict" && "The answer file and the trace disagree about whether the case was written."}
            </p>
          </div>
        )}
        {a?.case.verdict === "fraud" && a.case.affected_txn_ids.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-t border-ink-700/70 px-5 py-2.5 text-[12.5px] text-zinc-400">
            <Siren className="h-3.5 w-3.5 text-red-400" aria-hidden />
            Affected episode: <span className="font-mono text-zinc-200">{a.case.affected_txn_ids.length}</span> transaction{a.case.affected_txn_ids.length === 1 ? "" : "s"}, first suspicious{" "}
            <span className="id text-zinc-200">{a.case.first_suspicious_txn_id || "—"}</span>
          </div>
        )}
      </section>
    </div>
  );
}
