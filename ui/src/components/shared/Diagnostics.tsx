import { AlertCircle, AlertTriangle, Info } from "lucide-react";
import type { Diagnostic } from "@/contracts";
import { redactText } from "@/lib/redact";
import { cn } from "@/lib/cn";

const ICON = { error: AlertCircle, warning: AlertTriangle, info: Info } as const;
const TONE = {
  error: "border-red-500/30 bg-red-500/5 text-red-200",
  warning: "border-amber-400/30 bg-amber-400/5 text-amber-100",
  info: "border-sky-500/20 bg-sky-500/5 text-sky-100",
} as const;

export function DiagnosticsList({ items, empty = "No diagnostics.", showCase }: { items: Diagnostic[]; empty?: string; showCase?: boolean }) {
  if (items.length === 0) return <p className="text-sm text-zinc-500">{empty}</p>;
  const order = { error: 0, warning: 1, info: 2 };
  const sorted = [...items].sort((a, b) => order[a.level] - order[b.level]);
  return (
    <ul className="space-y-1.5">
      {sorted.map((d, i) => {
        const Icon = ICON[d.level];
        return (
          <li key={i} className={cn("flex items-start gap-2 rounded-md border px-2.5 py-1.5 text-[13px]", TONE[d.level])}>
            <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-label={d.level} />
            <div className="min-w-0">
              <span className="mr-1.5 font-mono text-[11px] uppercase tracking-wider opacity-70">
                {showCase && d.caseId ? `${d.caseId} · ` : ""}
                {d.scope}
              </span>
              {d.path && <code className="id mr-1.5 text-[12px] opacity-80">{d.path}</code>}
              <span className="break-words">{redactText(d.message)}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Diagnostics grouped by case and scope, so one malformed file shows as one
 * line with a count instead of dozens of schema issues. Each group expands.
 */
export function GroupedDiagnostics({ items, empty }: { items: Diagnostic[]; empty: string }) {
  if (items.length === 0) return <p className="text-sm text-zinc-500">{empty}</p>;
  const groups = new Map<string, Diagnostic[]>();
  for (const d of items) {
    const key = `${d.caseId ?? "run"}·${d.scope}·${d.level}`;
    groups.set(key, [...(groups.get(key) ?? []), d]);
  }
  const order = { error: 0, warning: 1, info: 2 };
  const sorted = [...groups.values()].sort((a, b) => order[a[0]!.level] - order[b[0]!.level] || (a[0]!.caseId ?? "").localeCompare(b[0]!.caseId ?? ""));
  return (
    <ul className="space-y-1.5">
      {sorted.map((g) => {
        const first = g[0]!;
        const Icon = ICON[first.level];
        return (
          <li key={`${first.caseId}-${first.scope}-${first.level}`} className={cn("rounded-md border text-[13px]", TONE[first.level])}>
            <details>
              <summary className="flex cursor-pointer list-none items-start gap-2 px-2.5 py-1.5">
                <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-label={first.level} />
                <span className="min-w-0 flex-1">
                  <span className="mr-1.5 font-mono text-[11px] uppercase tracking-wider opacity-70">
                    {first.caseId ? `${first.caseId} · ` : ""}
                    {first.scope}
                  </span>
                  <span className="break-words">{redactText(first.message)}</span>
                </span>
                {g.length > 1 && <span className="shrink-0 rounded bg-black/30 px-1.5 font-mono text-[11px]">+{g.length - 1}</span>}
              </summary>
              {g.length > 1 && (
                <ul className="space-y-0.5 border-t border-white/5 px-3 py-1.5 pl-8 text-[12px] opacity-90">
                  {g.slice(1).map((d, i) => (
                    <li key={i}>
                      {d.path && <code className="id mr-1.5 opacity-80">{d.path}</code>}
                      {redactText(d.message)}
                    </li>
                  ))}
                </ul>
              )}
            </details>
          </li>
        );
      })}
    </ul>
  );
}
