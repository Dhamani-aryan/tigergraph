import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity, ChevronDown, CircleDot, CircleOff, FileQuestion, Flag, MessageSquareQuote, OctagonX, Timer, Wrench, Zap } from "lucide-react";
import { KNOWN_STEP_NODES, type AnswerView, type TraceFile, type TraceStep, type TraceToolCall } from "@/contracts";
import type { TraceEntry } from "@/data/client";
import { ViaBadge } from "@/components/shared/badges";
import { EntityChip } from "@/components/shared/EntityChip";
import { Badge, EmptyState, Kv, Panel } from "@/components/ui/primitives";
import { fmtInt, fmtMs, fmtSeconds, humanize } from "@/lib/format";
import { redact } from "@/lib/redact";
import { cn } from "@/lib/cn";

export function TraceUnavailable({ entry, what }: { entry?: TraceEntry; what: string }) {
  const invalid = entry?.present;
  return (
    <EmptyState icon={<FileQuestion className="h-7 w-7" />} title={invalid ? "Execution trace failed validation" : "Execution trace not provided"}>
      {invalid
        ? `A trace file was synchronized for this case but it does not satisfy the trace contract, so ${what} cannot be shown from it. See Raw JSON for the diagnostics.`
        : `The official answer remains available on the other tabs, but no execution trace was synchronized for this case, so ${what} cannot be shown. Nothing has been reconstructed from the answer file.`}
    </EmptyState>
  );
}

function ToolCallRow({ call }: { call: TraceToolCall }) {
  const [open, setOpen] = useState(false);
  const args = redact(call.args);
  const hasArgs = Object.keys(args).length > 0;
  return (
    <li className={cn("rounded-md border bg-ink-950/60", call.error ? "border-red-500/40" : "border-ink-700")}>
      <div className="flex flex-wrap items-center gap-2 px-2.5 py-1.5">
        <Wrench className="h-3.5 w-3.5 shrink-0 text-zinc-500" aria-hidden />
        <code className="id text-[12.5px] text-zinc-100">{call.tool}</code>
        {typeof args["query_name"] === "string" && <code className="id text-[12px] text-tg-300">{String(args["query_name"])}</code>}
        <ViaBadge via={call.via} />
        <span className="ml-auto flex items-center gap-3 font-mono text-[11.5px] text-zinc-400">
          {call.result_count !== undefined && <span title="result count">{fmtInt(call.result_count)} rows</span>}
          {call.duration_ms !== undefined && <span title="duration">{fmtMs(call.duration_ms)}</span>}
          {hasArgs && (
            <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="inline-flex items-center gap-0.5 rounded px-1 text-zinc-400 hover:bg-ink-800 hover:text-zinc-100">
              args <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} aria-hidden />
            </button>
          )}
        </span>
      </div>
      {call.error && (
        <p className="flex items-start gap-1.5 border-t border-red-500/30 px-2.5 py-1.5 text-[12px] text-red-300">
          <OctagonX className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> {call.error}
        </p>
      )}
      {open && hasArgs && (
        <pre className="scroll-thin max-h-60 overflow-auto border-t border-ink-700 px-2.5 py-2 text-[11.5px] leading-5 text-zinc-300" aria-label={`Arguments for ${call.tool}`}>
          {JSON.stringify(args, null, 2)}
        </pre>
      )}
    </li>
  );
}

function StepItem({ step, requestHere, last }: { step: TraceStep; requestHere: AnswerView["evidence_requests"]; last: boolean }) {
  const known = (KNOWN_STEP_NODES as readonly string[]).includes(step.node);
  const nonMcp = step.tool_calls.filter((c) => c.via !== "mcp").length;
  return (
    <li className="relative pl-9">
      {!last && <span className="absolute left-[13px] top-7 h-[calc(100%-12px)] w-px bg-ink-600" aria-hidden />}
      <span
        className={cn(
          "absolute left-0 top-1 grid h-[27px] w-[27px] place-items-center rounded-full border font-mono text-[11px] font-semibold",
          step.tool_calls.some((c) => c.error) ? "border-red-500/60 bg-red-500/15 text-red-200" : known ? "border-tg-500/50 bg-ink-850 text-tg-300" : "border-zinc-500 bg-ink-850 text-zinc-300",
        )}
      >
        {step.step}
      </span>
      <div className="pb-5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[14px] font-semibold text-zinc-100">{step.label || humanize(step.node)}</h3>
          <code className="id rounded bg-ink-800 px-1.5 py-0.5 text-[11px] text-zinc-400">{step.node}</code>
          {!known && <Badge tone="muted" title="Step type not recognised by this UI; rendered generically">unrecognised step</Badge>}
          <span className="ml-auto flex items-center gap-3 font-mono text-[11.5px] text-zinc-500">
            {step.duration_ms !== undefined && (
              <span className="inline-flex items-center gap-1">
                <Timer className="h-3 w-3" aria-hidden />
                {fmtMs(step.duration_ms)}
              </span>
            )}
            <span>
              {step.tool_calls.length} tool call{step.tool_calls.length === 1 ? "" : "s"}
              {nonMcp > 0 && <span className="text-amber-300"> · {nonMcp} not via MCP</span>}
            </span>
          </span>
        </div>
        {step.summary && <p className="mt-1 text-[13px] text-zinc-400">{step.summary}</p>}
        {step.tool_calls.length > 0 && (
          <ul className="mt-2 space-y-1.5">
            {step.tool_calls.map((c, i) => (
              <ToolCallRow key={i} call={c} />
            ))}
          </ul>
        )}
        {requestHere.map((r, i) => (
          <div key={i} className="mt-2.5 rounded-md border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-[13px] text-sky-100">
            <div className="flex items-center gap-1.5 font-semibold text-sky-200">
              <MessageSquareQuote className="h-3.5 w-3.5" aria-hidden /> Evidence requested after step {r.asked_after_step}: {humanize(r.type)}
            </div>
            <p className="mt-0.5 text-sky-100/90">{r.assumed_response}</p>
          </div>
        ))}
      </div>
    </li>
  );
}

export function ProbabilityChart({ trace, answer }: { trace: TraceFile; answer: AnswerView }) {
  const data = trace.probability_timeline.map((p) => ({ ...p }));
  if (data.length === 0) return <p className="text-sm text-zinc-500">The trace carries no probability timeline.</p>;
  const steps = data.map((d) => d.step);
  const xMin = Math.min(...steps, ...answer.evidence_requests.map((r) => r.asked_after_step));
  const xMax = Math.max(...steps);
  return (
    <figure aria-label={`Fraud probability over steps: ${data.map((d) => `step ${d.step} ${d.fraud_probability.toFixed(2)}`).join(", ")}`}>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 24, right: 20, bottom: 4, left: -8 }}>
            <CartesianGrid stroke="#1E2631" vertical={false} />
            <ReferenceArea y1={0.85} y2={1} fill="#ef4444" fillOpacity={0.06} />
            <ReferenceArea y1={0} y2={0.15} fill="#10b981" fillOpacity={0.06} />
            <XAxis dataKey="step" type="number" domain={[xMin, xMax]} allowDecimals={false} stroke="#52525b" fontSize={11} tickLine={false} label={{ value: "step", position: "insideBottomRight", offset: -2, fill: "#71717a", fontSize: 11 }} />
            <YAxis domain={[0, 1]} ticks={[0, 0.15, 0.5, 0.85, 1]} stroke="#52525b" fontSize={11} tickLine={false} />
            <Tooltip
              contentStyle={{ background: "#10151D", border: "1px solid #2A3441", borderRadius: 8, fontSize: 12 }}
              formatter={(v) => [Number(v).toFixed(2), "fraud probability"]}
              labelFormatter={(step) => {
                const p = data.find((d) => d.step === step);
                return `Step ${String(step)}${p ? ` · ${p.label}` : ""}`;
              }}
            />
            {answer.evidence_requests.map((r, i) => (
              <ReferenceLine key={i} x={r.asked_after_step} stroke="#38bdf8" strokeDasharray="4 3" label={{ value: "evidence request", position: "top", fill: "#7dd3fc", fontSize: 11 }} />
            ))}
            <Line type="monotone" dataKey="fraud_probability" stroke="#F68B1F" strokeWidth={2.2} dot={{ r: 4, fill: "#F68B1F", stroke: "#0C1016", strokeWidth: 2 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-zinc-500">
        <span>
          <span className="mr-1 inline-block h-2 w-3 rounded-sm bg-red-500/30" /> ≥ 0.85 settled fraud
        </span>
        <span>
          <span className="mr-1 inline-block h-2 w-3 rounded-sm bg-emerald-500/30" /> ≤ 0.15 settled legitimate
        </span>
        <span>
          <span className="mr-1 inline-block h-0.5 w-3 bg-sky-400 align-middle" /> evidence request
        </span>
        <span>Final answer probability: {answer.case.fraud_probability.toFixed(2)}</span>
      </figcaption>
    </figure>
  );
}

export function InvestigationTab({ answer, trace, traceEntry, graphIds, onSelectEntity }: { answer: AnswerView; trace?: TraceFile; traceEntry?: TraceEntry; graphIds: Set<string>; onSelectEntity: (id: string) => void }) {
  const fired = trace?.signals.filter((s) => s.fired) ?? [];
  const notFired = trace?.signals.filter((s) => !s.fired) ?? [];
  const lastStep = trace?.steps[trace.steps.length - 1]?.step ?? 0;
  const requestsFor = (step: TraceStep, index: number) =>
    answer.evidence_requests.filter((r) => {
      const next = trace?.steps[index + 1];
      return r.asked_after_step >= step.step && (!next || r.asked_after_step < next.step);
    });
  const totalCalls = trace?.steps.reduce((n, s) => n + s.tool_calls.length, 0) ?? 0;
  const viaCounts = trace?.steps.flatMap((s) => s.tool_calls).reduce<Record<string, number>>((acc, c) => ({ ...acc, [c.via]: (acc[c.via] ?? 0) + 1 }), {}) ?? {};

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
      <Panel
        title="Execution timeline"
        icon={<Activity className="h-3.5 w-3.5" aria-hidden />}
        actions={
          trace && (
            <span className="flex flex-wrap items-center gap-1.5 text-[12px] text-zinc-500">
              {totalCalls} traced tool calls
              {Object.entries(viaCounts).map(([via, n]) => (
                <span key={via} className="inline-flex items-center gap-1">
                  <ViaBadge via={via} /> <span className="font-mono">{n}</span>
                </span>
              ))}
            </span>
          )
        }
      >
        {trace ? (
          trace.steps.length ? (
            <ol aria-label="Investigation steps">
              {trace.steps.map((s, i) => (
                <StepItem key={`${s.step}-${i}`} step={s} requestHere={requestsFor(s, i)} last={s.step === lastStep && i === trace.steps.length - 1} />
              ))}
            </ol>
          ) : (
            <p className="text-sm text-zinc-500">The trace lists no steps.</p>
          )
        ) : (
          <TraceUnavailable entry={traceEntry} what="the step-by-step execution, tool provenance and probability timeline" />
        )}
      </Panel>

      <div className="space-y-4">
        <Panel title="Fraud probability" icon={<Zap className="h-3.5 w-3.5" aria-hidden />}>
          {trace ? <ProbabilityChart trace={trace} answer={answer} /> : <p className="text-sm text-zinc-500">No timeline without a trace. Final answer probability: <span className="font-mono text-zinc-200">{Number.isFinite(answer.case.fraud_probability) ? answer.case.fraud_probability.toFixed(2) : "—"}</span></p>}
        </Panel>

        {!trace && answer.evidence_requests.length > 0 && (
          <Panel title="Evidence requests" icon={<MessageSquareQuote className="h-3.5 w-3.5" aria-hidden />}>
            <ul className="space-y-2">
              {answer.evidence_requests.map((r, i) => (
                <li key={i} className="rounded-md border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-[13px] text-sky-100">
                  <div className="font-semibold text-sky-200">
                    {humanize(r.type)} · after step {r.asked_after_step}
                  </div>
                  <p>{r.assumed_response}</p>
                </li>
              ))}
            </ul>
          </Panel>
        )}

        <Panel title="Signals" icon={<CircleDot className="h-3.5 w-3.5" aria-hidden />}>
          {!trace ? (
            <p className="text-sm text-zinc-500">Signals are reported by the trace, which is unavailable.</p>
          ) : trace.signals.length === 0 ? (
            <p className="text-sm text-zinc-500">The trace reports no signals.</p>
          ) : (
            <div className="space-y-3">
              <SignalList title={`Fired (${fired.length})`} items={fired} fired graphIds={graphIds} onSelect={onSelectEntity} />
              <SignalList title={`Checked, not fired (${notFired.length})`} items={notFired} fired={false} graphIds={graphIds} onSelect={onSelectEntity} />
            </div>
          )}
        </Panel>

        <Panel title="Stop reason" icon={<Flag className="h-3.5 w-3.5" aria-hidden />}>
          <p className="text-sm text-zinc-200">{answer.stop_reason || <span className="text-zinc-500">No stop reason supplied.</span>}</p>
          <dl className="mt-3 grid grid-cols-3 gap-3 border-t border-ink-700/70 pt-3">
            <Kv label="Tool calls">{fmtInt(answer.tool_calls)}</Kv>
            <Kv label="Tokens">{fmtInt(answer.tokens)}</Kv>
            <Kv label="Latency">{fmtSeconds(answer.latency_s)}</Kv>
          </dl>
          {trace?.llm && (
            <p className="mt-2 text-[12px] text-zinc-500">
              LLM: <span className="id text-zinc-300">{trace.llm.provider} · {trace.llm.model}</span>
            </p>
          )}
        </Panel>
      </div>
    </div>
  );
}

function SignalList({ title, items, fired, graphIds, onSelect }: { title: string; items: TraceFile["signals"]; fired: boolean; graphIds: Set<string>; onSelect: (id: string) => void }) {
  return (
    <div>
      <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-zinc-500">{title}</h3>
      {items.length === 0 ? (
        <p className="text-[13px] text-zinc-600">None.</p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((s) => (
            <li key={s.name} className={cn("rounded-md border px-2.5 py-1.5", fired ? "border-tg-500/30 bg-tg-500/5" : "border-ink-700 bg-ink-950/40")}>
              <div className="flex items-center gap-1.5">
                {fired ? <Zap className="h-3.5 w-3.5 text-tg-400" aria-label="fired" /> : <CircleOff className="h-3.5 w-3.5 text-zinc-500" aria-label="not fired" />}
                <code className={cn("id text-[12.5px]", fired ? "text-zinc-100" : "text-zinc-400")}>{s.name}</code>
              </div>
              {s.detail && <p className="mt-0.5 text-[12.5px] text-zinc-400">{s.detail}</p>}
              {s.entity_ids.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {s.entity_ids.map((id) => (
                    <EntityChip key={id} id={id} inGraph={graphIds.has(id)} onSelect={onSelect} />
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
