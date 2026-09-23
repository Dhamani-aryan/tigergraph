import { lazy, Suspense, useCallback, useMemo } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import * as Tabs from "@radix-ui/react-tabs";
import { Activity, FileJson2, FileText, Gavel, Loader2, Network, Radio } from "lucide-react";
import { useData } from "@/data/DataProvider";
import { caseDiagnostics } from "@/data/diagnostics";
import { allNodeIds } from "@/components/graph/elements";
import { CaseHeader } from "@/components/case/CaseHeader";
import { InvestigationTab, TraceUnavailable } from "@/components/case/InvestigationTab";
import { EvidenceTab } from "@/components/case/EvidenceTab";
import { ActionsTab } from "@/components/case/ActionsTab";
import { SarTab } from "@/components/case/SarTab";
import { RawTab } from "@/components/case/RawTab";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { DiagnosticsList } from "@/components/shared/Diagnostics";
import { Skeleton } from "@/components/ui/primitives";
import { NotFound } from "./NotFound";
import { cn } from "@/lib/cn";

const GraphTab = lazy(() => import("@/components/graph/GraphTab"));

const TABS = [
  { id: "investigation", label: "Investigation", icon: Activity },
  { id: "evidence", label: "Evidence", icon: Radio },
  { id: "graph", label: "Graph", icon: Network },
  { id: "actions", label: "Actions", icon: Gavel },
  { id: "sar", label: "SAR", icon: FileText },
  { id: "raw", label: "Raw JSON", icon: FileJson2 },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function CaseDetail() {
  const { caseId = "" } = useParams();
  const state = useData();
  const [params, setParams] = useSearchParams();
  const rawTab = params.get("tab");
  const tab: TabId = TABS.some((t) => t.id === rawTab) ? (rawTab as TabId) : "investigation";
  const selectedNode = params.get("node") ?? undefined;

  const setTab = useCallback(
    (next: string) =>
      setParams(
        (p) => {
          const q = new URLSearchParams(p);
          q.set("tab", next);
          return q;
        },
        { replace: true },
      ),
    [setParams],
  );
  const selectEntity = useCallback(
    (id?: string) =>
      setParams(
        (p) => {
          const q = new URLSearchParams(p);
          q.set("tab", "graph");
          if (id) q.set("node", id);
          else q.delete("node");
          return q;
        },
        { replace: false },
      ),
    [setParams],
  );

  const rows = state.status === "ready" ? state.rows : [];
  const index = rows.findIndex((r) => r.caseId === caseId);
  const row = index >= 0 ? rows[index] : undefined;
  const graphIds = useMemo(() => allNodeIds(row?.trace), [row?.trace]);

  if (state.status === "loading") {
    return (
      <div className="space-y-4" aria-busy="true">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-44" />
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (state.status === "error") return <NotFound title="Case data could not be loaded" detail={state.message} />;
  if (!row) return <NotFound title={`Case ${caseId} is not in the synchronized data`} detail="Check the case ID, or go back to the overview to pick a case." />;

  const diagnostics = caseDiagnostics(state.bundle, row.caseId);
  const answer = row.answer;
  const prevId = rows[index - 1]?.caseId;
  const nextId = rows[index + 1]?.caseId;

  if (!answer) {
    return (
      <div className="space-y-4">
        <CaseHeader row={row} prevId={prevId} nextId={nextId} />
        <div role="alert" className="rounded-lg border border-red-500/50 bg-red-500/10 p-4 text-sm text-red-100">
          <p className="font-semibold">This answer file cannot be rendered.</p>
          <div className="mt-2">
            <DiagnosticsList items={diagnostics} />
          </div>
        </div>
        <RawTab entry={row.entry} traceEntry={row.traceEntry} trace={row.trace} diagnostics={diagnostics} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <CaseHeader row={row} prevId={prevId} nextId={nextId} />
      <Tabs.Root value={tab} onValueChange={setTab}>
        <Tabs.List aria-label="Case sections" className="no-print sticky top-[6.1rem] z-20 -mx-1 flex gap-1 overflow-x-auto rounded-xl border border-ink-700 bg-ink-900/95 p-1 backdrop-blur">
          {TABS.map((t) => {
            const Icon = t.icon;
            const count =
              t.id === "raw" ? diagnostics.filter((d) => d.level === "error").length : t.id === "evidence" ? answer.case.evidence.length : t.id === "actions" ? answer.next_best_actions.final.length : undefined;
            return (
              <Tabs.Trigger
                key={t.id}
                value={t.id}
                className={cn(
                  "inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium text-zinc-400 transition-colors hover:bg-ink-800 hover:text-zinc-100",
                  "data-[state=active]:bg-ink-700 data-[state=active]:text-white data-[state=active]:shadow",
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {t.label}
                {t.id === "sar" && answer.sar.file && <span className="h-1.5 w-1.5 rounded-full bg-tg-500" aria-label="filed" />}
                {count !== undefined && count > 0 && (
                  <span className={cn("rounded px-1 font-mono text-[10.5px]", t.id === "raw" ? "bg-red-500/25 text-red-200" : "bg-ink-600 text-zinc-300")}>{count}</span>
                )}
              </Tabs.Trigger>
            );
          })}
        </Tabs.List>

        <div className="mt-4">
          <Tabs.Content value="investigation" className="outline-none">
            <ErrorBoundary label="Investigation tab" resetKey={row.caseId}>
              <InvestigationTab answer={answer} trace={row.trace} traceEntry={row.traceEntry} graphIds={graphIds} onSelectEntity={selectEntity} />
            </ErrorBoundary>
          </Tabs.Content>
          <Tabs.Content value="evidence" className="outline-none">
            <ErrorBoundary label="Evidence tab" resetKey={row.caseId}>
              <EvidenceTab answer={answer} trace={row.trace} traceEntry={row.traceEntry} graphIds={graphIds} onSelectEntity={selectEntity} />
            </ErrorBoundary>
          </Tabs.Content>
          <Tabs.Content value="graph" className="outline-none">
            <ErrorBoundary label="Graph view" resetKey={`${row.caseId}-${selectedNode ?? ""}`}>
              {row.trace ? (
                <Suspense
                  fallback={
                    <div className="panel flex h-[560px] items-center justify-center gap-2 text-sm text-zinc-400">
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading graph renderer…
                    </div>
                  }
                >
                  {tab === "graph" && <GraphTab trace={row.trace} selectedId={selectedNode} onSelect={selectEntity} />}
                </Suspense>
              ) : (
                <TraceUnavailable entry={row.traceEntry} what="the investigation subgraph" />
              )}
            </ErrorBoundary>
          </Tabs.Content>
          <Tabs.Content value="actions" className="outline-none">
            <ErrorBoundary label="Actions tab" resetKey={row.caseId}>
              <ActionsTab answer={answer} trace={row.trace} traceEntry={row.traceEntry} />
            </ErrorBoundary>
          </Tabs.Content>
          <Tabs.Content value="sar" className="outline-none">
            <ErrorBoundary label="SAR tab" resetKey={row.caseId}>
              <SarTab answer={answer} />
            </ErrorBoundary>
          </Tabs.Content>
          <Tabs.Content value="raw" className="outline-none">
            <ErrorBoundary label="Raw JSON tab" resetKey={row.caseId}>
              <RawTab entry={row.entry} traceEntry={row.traceEntry} trace={row.trace} diagnostics={diagnostics} />
            </ErrorBoundary>
          </Tabs.Content>
        </div>
      </Tabs.Root>
    </div>
  );
}
