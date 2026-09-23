import { ArrowRight, Equal, GitCompareArrows, Gavel, Minus, Plus, Repeat, Scale } from "lucide-react";
import { ACTIONS, expectedRoute, type ActionView, type AnswerView, type TraceFile } from "@/contracts";
import type { TraceEntry } from "@/data/client";
import { actionsChanged, diffActions, type ActionDiffItem } from "@/data/diagnostics";
import { RouteBadge } from "@/components/shared/badges";
import { Badge, Panel } from "@/components/ui/primitives";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/cn";

const STATUS_STYLE: Record<ActionDiffItem["status"], { cls: string; icon: typeof Plus; label: string }> = {
  added: { cls: "border-emerald-500/50 bg-emerald-500/10", icon: Plus, label: "added" },
  removed: { cls: "border-red-500/50 bg-red-500/10 line-through decoration-red-400/70", icon: Minus, label: "removed" },
  rerouted: { cls: "border-amber-400/50 bg-amber-400/10", icon: Repeat, label: "route changed" },
  kept: { cls: "border-ink-600 bg-ink-900", icon: Equal, label: "kept" },
};

function ActionCard({ entry, status, exposure }: { entry: ActionView; status?: ActionDiffItem["status"]; exposure: number }) {
  const known = (ACTIONS as readonly string[]).includes(entry.action);
  const expected = expectedRoute(entry.action, exposure);
  const s = status ? STATUS_STYLE[status] : undefined;
  const Icon = s?.icon;
  return (
    <li className={cn("rounded-lg border px-3 py-2.5", s?.cls ?? "border-ink-600 bg-ink-900")}>
      <div className="flex flex-wrap items-center gap-2">
        <code className={cn("id text-[13px] font-semibold", status === "removed" ? "text-red-200" : "text-zinc-100")}>{entry.action}</code>
        <RouteBadge route={entry.route} />
        {!known && <Badge tone="fraud">not a policy action</Badge>}
        {expected && entry.route !== expected && (
          <Badge tone="uncertain" title="The README policy table assigns a different approval route">
            policy expects {expected}
          </Badge>
        )}
        {s && Icon && status !== "kept" && (
          <span className={cn("ml-auto inline-flex items-center gap-0.5 text-[11px] font-semibold uppercase tracking-wider no-underline", status === "added" ? "text-emerald-300" : status === "removed" ? "text-red-300" : "text-amber-200")} style={{ textDecoration: "none" }}>
            <Icon className="h-3 w-3" aria-hidden /> {s.label}
          </span>
        )}
      </div>
      <p className="mt-1 text-[12.5px] leading-5 text-zinc-400" style={{ textDecoration: "none" }}>
        {entry.reason || <em>no reason given</em>}
      </p>
    </li>
  );
}

export function ActionsTab({ answer, trace, traceEntry }: { answer: AnswerView; trace?: TraceFile; traceEntry?: TraceEntry }) {
  const { initial, final, what_changed } = answer.next_best_actions;
  const changed = actionsChanged(answer);
  const diff = diffActions(answer);
  const statusOf = (action: string) => diff.find((d) => d.action === action)?.status;
  const exposure = answer.case.exposure_usd;
  const rules = trace?.rules_fired ?? [];
  const phases = [...new Set(rules.map((r) => r.phase))].sort((a, b) => (a === "initial" ? -1 : b === "initial" ? 1 : a === "final" ? -1 : b === "final" ? 1 : a.localeCompare(b)));
  const removed = diff.filter((d) => d.status === "removed").map((d) => initial.find((x) => x.action === d.action)).filter((x): x is ActionView => !!x);

  return (
    <div className="space-y-4">
      <div
        className={cn(
          "flex items-start gap-3 rounded-xl border px-4 py-3",
          changed ? "border-tg-500/40 bg-tg-500/10" : "border-ink-600 bg-ink-900",
        )}
        role="note"
      >
        <GitCompareArrows className={cn("mt-0.5 h-5 w-5 shrink-0", changed ? "text-tg-400" : "text-zinc-500")} aria-hidden />
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">{changed ? "The recommendation changed after evidence" : "No change between initial and final actions"}</h2>
          <p className="mt-0.5 text-sm text-zinc-300">
            <span className="text-zinc-500">what_changed: </span>
            {what_changed ? `“${what_changed}”` : <em className="text-zinc-500">empty</em>}
          </p>
          {!changed && (
            <p className="mt-1 text-[12.5px] text-zinc-500">
              {answer.evidence_requests.length === 0 ? "No evidence was requested, so the policy output could not move — initial and final actions are identical by rule." : "Evidence was requested but the policy output stayed the same."}
            </p>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(220px,300px)_minmax(0,1fr)]">
        <Panel title={`Initial actions (${initial.length})`} icon={<Scale className="h-3.5 w-3.5" aria-hidden />}>
          <ul className="space-y-2">
            {initial.map((e, i) => (
              <ActionCard key={`${e.action}-${i}`} entry={e} status={statusOf(e.action) === "removed" ? "removed" : undefined} exposure={exposure} />
            ))}
          </ul>
        </Panel>

        <div className="flex flex-col justify-center gap-3" aria-label="Between phases">
          {answer.evidence_requests.length > 0 ? (
            answer.evidence_requests.map((r, i) => (
              <div key={i} className="relative rounded-lg border border-sky-500/40 bg-sky-500/10 p-3 text-[13px] text-sky-100">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-sky-300">Evidence request {i + 1}</div>
                <div className="mt-0.5 font-semibold">{humanize(r.type)}</div>
                <div className="text-[12px] text-sky-200/80">after step {Number.isFinite(r.asked_after_step) ? r.asked_after_step : "—"}</div>
                <p className="mt-1.5 border-t border-sky-500/30 pt-1.5 text-sky-100/90">{r.assumed_response}</p>
              </div>
            ))
          ) : (
            <div className="rounded-lg border border-dashed border-ink-600 p-3 text-center text-[13px] text-zinc-500">No evidence requested</div>
          )}
          <ArrowRight className="mx-auto hidden h-5 w-5 text-zinc-500 lg:block" aria-hidden />
        </div>

        <Panel title={`Final actions (${final.length})`} icon={<Gavel className="h-3.5 w-3.5" aria-hidden />}>
          <ul className="space-y-2">
            {final.map((e, i) => (
              <ActionCard key={`${e.action}-${i}`} entry={e} status={changed ? statusOf(e.action) : undefined} exposure={exposure} />
            ))}
            {changed && removed.length > 0 && (
              <li className="pt-2 text-[11px] font-medium uppercase tracking-wider text-zinc-500" aria-hidden>
                No longer recommended
              </li>
            )}
            {changed &&
              removed.map((e, i) => <ActionCard key={`removed-${e.action}-${i}`} entry={e} status="removed" exposure={exposure} />)}
          </ul>
        </Panel>
      </div>

      <Panel title="Policy rules fired" icon={<Gavel className="h-3.5 w-3.5" aria-hidden />}>
        {!trace ? (
          <p className="text-sm text-zinc-500">
            Rule provenance comes from the trace, which is {traceEntry?.present ? "invalid" : "not available"} for this case. Each action&apos;s own reason above still cites its rule.
          </p>
        ) : rules.length === 0 ? (
          <p className="text-sm text-zinc-500">The trace records no fired rules.</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {phases.map((phase) => (
              <div key={phase}>
                <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-zinc-500">{humanize(phase)} phase</h3>
                <ul className="space-y-1.5">
                  {rules
                    .filter((r) => r.phase === phase)
                    .map((r, i) => (
                      <li key={`${r.rule}-${i}`} className="flex gap-2.5 rounded-md border border-ink-700 bg-ink-950/40 px-2.5 py-2">
                        <span className="h-fit shrink-0 rounded bg-tg-500/15 px-1.5 py-0.5 font-mono text-[12px] font-semibold text-tg-300">{r.rule}</span>
                        <span className="text-[13px] text-zinc-300">{r.explanation}</span>
                      </li>
                    ))}
                </ul>
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 border-t border-ink-700/70 pt-2 text-[12px] text-zinc-500">
          Routes: <RouteBadge route="auto" /> agent may act alone · <RouteBadge route="L1" /> team lead · <RouteBadge route="L2" /> fraud manager. Actions and routes come from the backend&apos;s deterministic
          policy engine; the UI only flags disagreements with the README route table.
        </p>
      </Panel>
    </div>
  );
}
