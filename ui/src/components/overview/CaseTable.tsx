import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowDown, ArrowRight, ArrowUp, ArrowUpDown, Search, X } from "lucide-react";
import { PATTERNS, TRIGGER_TYPES, VERDICTS } from "@/contracts";
import { EMPTY_FILTERS, filterRows, sortRows, type CaseRow, type RowFilters, type SortKey } from "@/data/diagnostics";
import { BackendValidationBadge, ContractBadge, MemoryBadge, PatternText, ProbabilityBar, SarBadge, VerdictBadge } from "@/components/shared/badges";
import { Button, EmptyState } from "@/components/ui/primitives";
import { fmtUsd, humanize } from "@/lib/format";
import { isFixtureId } from "@/data/fixtures";
import { cn } from "@/lib/cn";

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <label className="flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wider text-zinc-500">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 rounded-md border border-ink-600 bg-ink-850 px-2 text-[13px] normal-case tracking-normal text-zinc-200 hover:border-ink-500"
      >
        <option value="">All</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

const COLUMNS: { key: SortKey | null; label: string; className?: string }[] = [
  { key: "caseId", label: "Case ID" },
  { key: "trigger", label: "Trigger" },
  { key: "verdict", label: "Verdict" },
  { key: "probability", label: "Probability" },
  { key: "pattern", label: "Pattern" },
  { key: "exposure", label: "Exposure", className: "text-right" },
  { key: null, label: "Initial → final action" },
  { key: "sar", label: "SAR" },
  { key: "memory", label: "Memory" },
  { key: "validation", label: "Validation" },
];

export function CaseTable({ rows }: { rows: CaseRow[] }) {
  const [filters, setFilters] = useState<RowFilters>(EMPTY_FILTERS);
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "caseId", dir: "asc" });
  const navigate = useNavigate();
  const set = <K extends keyof RowFilters>(k: K, v: RowFilters[K]) => setFilters((f) => ({ ...f, [k]: v }));

  const visible = useMemo(() => sortRows(filterRows(rows, filters), sort.key, sort.dir), [rows, filters, sort]);
  const active = JSON.stringify(filters) !== JSON.stringify(EMPTY_FILTERS);
  const toggleSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "probability" || key === "exposure" ? "desc" : "asc" }));

  const extraPatterns = [...new Set(rows.map((r) => r.pattern).filter((p): p is string => !!p && !(PATTERNS as readonly string[]).includes(p)))];

  return (
    <section className="panel" aria-labelledby="case-board-title">
      <header className="panel-header flex-wrap">
        <h2 id="case-board-title" className="panel-title">
          Case board
        </h2>
        <span className="text-[12px] text-zinc-500" aria-live="polite">
          {visible.length} of {rows.length} cases
        </span>
      </header>
      <div className="flex flex-wrap items-end gap-3 border-b border-ink-700/70 px-4 py-3">
        <label className="flex min-w-[240px] flex-1 flex-col gap-1 text-[11px] font-medium uppercase tracking-wider text-zinc-500">
          Search
          <span className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-zinc-500" aria-hidden />
            <input
              type="search"
              value={filters.query}
              onChange={(e) => set("query", e.target.value)}
              placeholder="Case, card or customer ID"
              className="h-8 w-full rounded-md border border-ink-600 bg-ink-850 pl-8 pr-2 font-mono text-[13px] normal-case tracking-normal text-zinc-100 placeholder:font-sans placeholder:text-zinc-500 hover:border-ink-500"
            />
          </span>
        </label>
        <Select label="Verdict" value={filters.verdict} onChange={(v) => set("verdict", v)} options={VERDICTS.map((v) => ({ value: v, label: humanize(v) }))} />
        <Select label="Pattern" value={filters.pattern} onChange={(v) => set("pattern", v)} options={[...PATTERNS, ...extraPatterns].map((p) => ({ value: p, label: p }))} />
        <Select label="Trigger" value={filters.trigger} onChange={(v) => set("trigger", v)} options={TRIGGER_TYPES.map((t) => ({ value: t, label: humanize(t) }))} />
        <Select label="SAR" value={filters.sar} onChange={(v) => set("sar", v as RowFilters["sar"])} options={[{ value: "filed", label: "Filed" }, { value: "not_filed", label: "Not filed" }]} />
        <Select label="Actions" value={filters.changed} onChange={(v) => set("changed", v as RowFilters["changed"])} options={[{ value: "changed", label: "Changed" }, { value: "unchanged", label: "Unchanged" }]} />
        {active && (
          <Button size="sm" variant="ghost" onClick={() => setFilters(EMPTY_FILTERS)}>
            <X className="h-3.5 w-3.5" aria-hidden /> Clear
          </Button>
        )}
      </div>
      {visible.length === 0 ? (
        <div className="p-4">
          <EmptyState icon={<Search className="h-6 w-6" />} title={rows.length ? "No cases match these filters" : "No case files were synchronized"}>
            {rows.length ? "Clear a filter or change the search." : "Run npm run sync-data to copy backend results into public/data."}
          </EmptyState>
        </div>
      ) : (
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[1040px] border-collapse text-left text-[13px]">
            <thead>
              <tr className="border-b border-ink-700 text-[11px] uppercase tracking-wider text-zinc-500">
                {COLUMNS.map((c) => {
                  const sorted = c.key && sort.key === c.key;
                  return (
                    <th
                      key={c.label}
                      scope="col"
                      aria-sort={sorted ? (sort.dir === "asc" ? "ascending" : "descending") : c.key ? "none" : undefined}
                      className={cn("whitespace-nowrap px-2.5 py-2.5 font-medium first:pl-4 last:pr-4", c.className)}
                    >
                      {c.key ? (
                        <button type="button" className="th-sort" onClick={() => toggleSort(c.key as SortKey)}>
                          {c.label}
                          {sorted ? sort.dir === "asc" ? <ArrowUp className="h-3 w-3" aria-hidden /> : <ArrowDown className="h-3 w-3" aria-hidden /> : <ArrowUpDown className="h-3 w-3 opacity-40" aria-hidden />}
                        </button>
                      ) : (
                        c.label
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr
                  key={r.caseId}
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest("a,button")) return;
                    navigate(`/case/${encodeURIComponent(r.caseId)}`);
                  }}
                  className={cn(
                    "cursor-pointer border-b border-ink-800 transition-colors hover:bg-ink-800/60",
                    r.verdict === "uncertain" && "bg-amber-400/[0.03]",
                  )}
                >
                  <td className="px-2.5 py-2.5 pl-4">
                    <Link to={`/case/${encodeURIComponent(r.caseId)}`} className="whitespace-nowrap font-mono text-[12.5px] font-semibold text-tg-300 hover:text-tg-200 hover:underline">
                      {r.caseId}
                    </Link>
                    {isFixtureId(r.caseId) && <span className="ml-1.5 rounded bg-amber-400/20 px-1 text-[10px] font-bold text-amber-200">DEMO</span>}
                    {r.cardId && <div className="id text-[11px] text-zinc-500">{r.cardId}</div>}
                  </td>
                  <td className="whitespace-nowrap px-2.5 py-2.5 text-zinc-300">{r.triggerType ? humanize(r.triggerType) : <span className="text-zinc-600" title="Trigger metadata comes from batch_summary.json or the trace">—</span>}</td>
                  <td className="px-2.5 py-2.5">
                    <VerdictBadge verdict={r.verdict} />
                  </td>
                  <td className="px-2.5 py-2.5">
                    <ProbabilityBar value={r.probability} />
                  </td>
                  <td className="px-2.5 py-2.5">
                    <PatternText pattern={r.pattern} />
                  </td>
                  <td className="whitespace-nowrap px-2.5 py-2.5 text-right font-mono tabular-nums text-zinc-200">{fmtUsd(r.exposure)}</td>
                  <td className="px-2.5 py-2.5">
                    <span className="inline-flex flex-wrap items-center gap-x-1.5 font-mono text-[11px]">
                      <span className="text-zinc-400">{r.initialFirst ?? "—"}</span>
                      <ArrowRight className={cn("h-3 w-3 shrink-0", r.changed ? "text-tg-400" : "text-zinc-600")} aria-label={r.changed ? "changed to" : "unchanged"} />
                      <span className={r.changed ? "text-zinc-100" : "text-zinc-400"}>{r.finalFirst ?? "—"}</span>
                    </span>
                  </td>
                  <td className="px-2.5 py-2.5">
                    <SarBadge filed={r.sar} />
                  </td>
                  <td className="px-2.5 py-2.5">
                    <MemoryBadge state={r.memory} compact />
                  </td>
                  <td className="px-2.5 py-2.5">
                    <div className="flex flex-col items-start gap-1">
                      <BackendValidationBadge state={r.backend} />
                      {!r.contractValid && <ContractBadge valid={false} errors={r.errorCount} />}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
