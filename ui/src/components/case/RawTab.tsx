import { FileJson2, ShieldCheck } from "lucide-react";
import type { Diagnostic, TraceFile } from "@/contracts";
import type { CaseEntry, TraceEntry } from "@/data/client";
import { DiagnosticsList } from "@/components/shared/Diagnostics";
import { JsonBlock } from "@/components/shared/JsonBlock";
import { Badge, Panel } from "@/components/ui/primitives";
import { redactText } from "@/lib/redact";

export function RawTab({ entry, traceEntry, trace, diagnostics }: { entry: CaseEntry; traceEntry?: TraceEntry; trace?: TraceFile; diagnostics: Diagnostic[] }) {
  const answerDiags = diagnostics.filter((d) => d.scope === "answer");
  const traceDiags = diagnostics.filter((d) => d.scope === "trace" || d.scope === "crosscheck");
  const backend: Diagnostic[] = trace
    ? [
        ...trace.validation.errors.map((m): Diagnostic => ({ level: "error", scope: "trace", message: m })),
        ...trace.validation.warnings.map((m): Diagnostic => ({ level: "warning", scope: "trace", message: m })),
      ]
    : [];
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_440px]">
      <div className="min-w-0 space-y-4">
        <Panel title="Answer file" icon={<FileJson2 className="h-3.5 w-3.5" aria-hidden />}>
          {entry.file.raw !== undefined ? (
            <JsonBlock value={entry.file.raw} filename={`${entry.caseId}.json`} label="answer JSON" />
          ) : (
            <div className="space-y-2">
              <p className="text-sm text-red-300">{entry.file.error ?? "Answer file is missing."}</p>
              {entry.file.text && (
                <pre className="scroll-thin max-h-80 overflow-auto rounded border border-ink-700 bg-ink-950 p-3 text-[12px] text-zinc-400">{redactText(entry.file.text.slice(0, 20000))}</pre>
              )}
            </div>
          )}
        </Panel>
        <Panel title="Trace file" icon={<FileJson2 className="h-3.5 w-3.5" aria-hidden />}>
          {!traceEntry?.present ? (
            <p className="text-sm text-zinc-500">No trace file was synchronized for this case.</p>
          ) : traceEntry.file?.raw !== undefined ? (
            <JsonBlock value={traceEntry.file.raw} filename={`${entry.caseId}.trace.json`} label="trace JSON" maxHeight="24rem" />
          ) : (
            <div className="space-y-2">
              <p className="text-sm text-red-300">{traceEntry.file?.error ?? "Trace could not be read."}</p>
              {traceEntry.file?.text && (
                <pre className="scroll-thin max-h-60 overflow-auto rounded border border-ink-700 bg-ink-950 p-3 text-[12px] text-zinc-400">{redactText(traceEntry.file.text.slice(0, 20000))}</pre>
              )}
            </div>
          )}
        </Panel>
      </div>
      <div className="min-w-0 space-y-4">
        <Panel
          title="Frontend contract check"
          icon={<ShieldCheck className="h-3.5 w-3.5" aria-hidden />}
          actions={entry.answer?.contractValid ? <Badge tone="legit">valid</Badge> : <Badge tone="fraud">invalid</Badge>}
        >
          <p className="mb-3 text-[12px] text-zinc-500">Runtime Zod validation in the browser. Diagnostic only: it does not replace the backend validator and repairs nothing.</p>
          <DiagnosticsList items={answerDiags} empty="The answer satisfies the frozen contract and its self-consistency rules." />
        </Panel>
        <Panel
          title="Trace validation"
          actions={trace ? trace.validation.passed ? <Badge tone="legit">backend passed</Badge> : <Badge tone="fraud">backend failed</Badge> : <Badge tone="muted">no valid trace</Badge>}
        >
          {trace && (
            <div className="mb-4">
              <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-zinc-500">Backend validator (reported in trace)</h3>
              <DiagnosticsList items={backend} empty="No backend errors or warnings reported." />
            </div>
          )}
          <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-zinc-500">Browser trace contract and cross-checks</h3>
          <DiagnosticsList items={traceDiags} empty={traceEntry?.present ? "Trace satisfies its contract and agrees with the answer." : "Nothing to check: no trace."} />
        </Panel>
      </div>
    </div>
  );
}
