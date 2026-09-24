import { BookOpenText, Building2, Database, FileText, History, MessageSquareQuote, Radio, UserRound } from "lucide-react";
import type { AnswerView, TraceFile } from "@/contracts";
import type { TraceEntry } from "@/data/client";
import { EntityChip } from "@/components/shared/EntityChip";
import { Badge, EmptyState, Panel } from "@/components/ui/primitives";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/cn";
import { documentReferenced } from "@/data/evidence";

const SOURCE: Record<string, { icon: typeof Database; tone: string; label: string }> = {
  graph: { icon: Database, tone: "border-tg-500/40 text-tg-300", label: "Graph" },
  document: { icon: FileText, tone: "border-violet-500/40 text-violet-300", label: "Document" },
  customer: { icon: UserRound, tone: "border-sky-500/40 text-sky-300", label: "Customer (simulated)" },
  external: { icon: Building2, tone: "border-zinc-500/60 text-zinc-300", label: "External" },
};

export function EvidenceTab({ answer, trace, traceEntry, graphIds, onSelectEntity }: { answer: AnswerView; trace?: TraceFile; traceEntry?: TraceEntry; graphIds: Set<string>; onSelectEntity: (id: string) => void }) {
  const cited = new Set(answer.case.similar_prior_cases);
  const retrieved = trace?.retrieval.prior_cases ?? [];
  const citedNotRetrieved = trace ? answer.case.similar_prior_cases.filter((id) => !retrieved.some((r) => r.case_id === id)) : [];

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
      <Panel title={`Evidence (${answer.case.evidence.length})`} icon={<Radio className="h-3.5 w-3.5" aria-hidden />}>
        {answer.case.evidence.length === 0 ? (
          <EmptyState title="No evidence items in the answer file" />
        ) : (
          <ol className="space-y-3">
            {answer.case.evidence.map((e, i) => {
              const s = SOURCE[e.source] ?? { icon: Database, tone: "border-zinc-600 text-zinc-400", label: e.source || "unknown" };
              const Icon = s.icon;
              return (
                <li key={i} className="rounded-lg border border-ink-700 bg-ink-950/50 p-3.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] text-zinc-500">#{i + 1}</span>
                    <span className={cn("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-semibold", s.tone)}>
                      <Icon className="h-3 w-3" aria-hidden /> {s.label}
                    </span>
                    <code className="id min-w-0 flex-1 text-[11.5px] text-zinc-400" title="ref">
                      {e.ref}
                    </code>
                  </div>
                  <p className="mt-2 text-[14px] leading-6 text-zinc-100">{e.claim}</p>
                  {e.entity_ids.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Entities">
                      {e.entity_ids.map((id) => (
                        <EntityChip key={id} id={id} inGraph={trace ? graphIds.has(id) : undefined} onSelect={onSelectEntity} />
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {(answer.case.connected_card_ids.length > 0 || answer.case.connected_device_profiles.length > 0 || answer.case.affected_txn_ids.length > 0) && (
          <div className="mt-4 grid gap-3 border-t border-ink-700/70 pt-4 md:grid-cols-3">
            <IdGroup title="Affected transactions" ids={answer.case.affected_txn_ids} graphIds={graphIds} onSelect={onSelectEntity} highlight={answer.case.first_suspicious_txn_id} />
            <IdGroup title="Connected cards" ids={answer.case.connected_card_ids} graphIds={graphIds} onSelect={onSelectEntity} />
            <div className="min-w-0">
              <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-zinc-500">Connected device profiles</h3>
              {answer.case.connected_device_profiles.length ? (
                <ul className="space-y-1">
                  {answer.case.connected_device_profiles.map((d) => (
                    <li key={d} className="id rounded border border-ink-700 bg-ink-900 px-2 py-1 text-[11.5px] text-zinc-300">
                      {d}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[13px] text-zinc-600">None.</p>
              )}
            </div>
          </div>
        )}
      </Panel>

      <div className="space-y-4">
        <Panel title="Retrieved prior cases" icon={<History className="h-3.5 w-3.5" aria-hidden />}>
          <p className="mb-3 text-[12px] text-zinc-500">
            <Badge tone="accent">cited</Badge> appears in the answer&apos;s similar_prior_cases · <Badge tone="neutral">used</Badge> the trace marks it used · others were returned candidates only.
          </p>
          {!trace ? (
            <>
              <CitedOnly ids={answer.case.similar_prior_cases} />
              <p className="mt-2 text-[12px] text-zinc-500">Retrieval candidates and scores come from the trace, which is {traceEntry?.present ? "invalid" : "not available"}.</p>
            </>
          ) : retrieved.length === 0 && answer.case.similar_prior_cases.length === 0 ? (
            <EmptyState title="No prior cases retrieved" className="py-6">
              The retrieval returned no closed cases for this investigation.
            </EmptyState>
          ) : (
            <ul className="space-y-1.5">
              {retrieved.map((p) => (
                <li key={p.case_id} className={cn("grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 rounded-md border px-2.5 py-2", cited.has(p.case_id) ? "border-tg-500/40 bg-tg-500/5" : p.used ? "border-ink-600 bg-ink-900" : "border-dashed border-ink-700 bg-transparent opacity-80")}>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <EntityChip id={p.case_id} inGraph={graphIds.has(p.case_id)} onSelect={onSelectEntity} />
                      {cited.has(p.case_id) && <Badge tone="accent">cited</Badge>}
                      {p.used ? <Badge tone="neutral">used</Badge> : <Badge tone="muted">candidate</Badge>}
                    </div>
                    <div className="mt-1 text-[12px] text-zinc-400">
                      <span className={p.outcome === "confirmed_fraud" ? "text-red-300" : p.outcome === "cleared" ? "text-emerald-300" : ""}>{humanize(p.outcome)}</span> · <span className="font-mono">{p.pattern}</span>
                    </div>
                  </div>
                  <span className="self-center font-mono text-[12px] tabular-nums text-zinc-300" title="retrieval score">
                    {p.score !== undefined ? p.score.toFixed(3) : "—"}
                  </span>
                </li>
              ))}
              {citedNotRetrieved.map((id) => (
                <li key={id} className="rounded-md border border-amber-400/40 bg-amber-400/5 px-2.5 py-2 text-[12.5px] text-amber-100">
                  <span className="id font-semibold">{id}</span> is cited by the answer but absent from the trace&apos;s retrieval results.
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Policy & regulatory documents" icon={<BookOpenText className="h-3.5 w-3.5" aria-hidden />}>
          {!trace ? (
            <DocumentEvidenceOnly answer={answer} />
          ) : trace.retrieval.documents.length === 0 ? (
            <EmptyState title="No documents retrieved" className="py-6" />
          ) : (
            <ul className="space-y-1.5">
              {trace.retrieval.documents.map((d) => {
                const ref = documentReferenced(d.doc_id, answer);
                return (
                  <li key={d.doc_id} className={cn("grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 rounded-md border px-2.5 py-2", ref ? "border-violet-500/40 bg-violet-500/5" : "border-dashed border-ink-700")}>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[13px] font-medium text-zinc-100">{d.title}</span>
                        {ref ? (
                          <Badge tone="purple" title="An evidence ref names this document's anchor">
                            referenced
                          </Badge>
                        ) : (
                          <Badge tone="muted">returned</Badge>
                        )}
                      </div>
                      <div className="mt-0.5 flex flex-wrap gap-x-2 text-[12px] text-zinc-500">
                        <code className="id">{d.doc_id}</code>
                        <span>{d.section}</span>
                      </div>
                    </div>
                    <span className="self-center font-mono text-[12px] tabular-nums text-zinc-300">{d.score !== undefined ? d.score.toFixed(3) : "—"}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>

        {answer.evidence_requests.length > 0 && (
          <Panel title="Requested evidence (simulated)" icon={<MessageSquareQuote className="h-3.5 w-3.5" aria-hidden />}>
            <ul className="space-y-2">
              {answer.evidence_requests.map((r, i) => (
                <li key={i} className="rounded-md border border-sky-500/30 bg-sky-500/5 px-3 py-2 text-[13px]">
                  <div className="font-semibold text-sky-200">
                    {humanize(r.type)} · after step {Number.isFinite(r.asked_after_step) ? r.asked_after_step : "—"}
                  </div>
                  <p className="text-sky-100/90">{r.assumed_response}</p>
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>
    </div>
  );
}

function IdGroup({ title, ids, graphIds, onSelect, highlight }: { title: string; ids: string[]; graphIds: Set<string>; onSelect: (id: string) => void; highlight?: string }) {
  return (
    <div className="min-w-0">
      <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-zinc-500">{title}</h3>
      {ids.length ? (
        <div className="flex flex-wrap gap-1">
          {ids.map((id) => (
            <span key={id} className="inline-flex items-center gap-1">
              <EntityChip id={id} inGraph={graphIds.has(id)} onSelect={onSelect} className={highlight === id ? "ring-1 ring-red-400/60" : undefined} />
              {highlight === id && <span className="text-[10px] font-semibold uppercase text-red-300">first</span>}
            </span>
          ))}
        </div>
      ) : (
        <p className="text-[13px] text-zinc-600">None.</p>
      )}
    </div>
  );
}

function CitedOnly({ ids }: { ids: string[] }) {
  if (!ids.length) return <p className="text-[13px] text-zinc-500">The answer cites no similar prior cases.</p>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {ids.map((id) => (
        <span key={id} className="inline-flex items-center gap-1">
          <EntityChip id={id} />
          <Badge tone="accent">cited</Badge>
        </span>
      ))}
    </div>
  );
}

function DocumentEvidenceOnly({ answer }: { answer: AnswerView }) {
  const docs = answer.case.evidence.filter((e) => e.source === "document");
  if (!docs.length) return <p className="text-[13px] text-zinc-500">No document evidence in the answer, and no trace retrieval to show.</p>;
  return (
    <ul className="space-y-1.5">
      {docs.map((d, i) => (
        <li key={i} className="rounded-md border border-violet-500/30 px-2.5 py-2 text-[12.5px]">
          <code className="id text-violet-300">{d.ref}</code>
          <p className="text-zinc-300">{d.claim}</p>
        </li>
      ))}
      <li className="text-[12px] text-zinc-500">Retrieval scores are unavailable without a trace.</li>
    </ul>
  );
}
