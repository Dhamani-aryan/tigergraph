import {
  AlertTriangle,
  BadgeCheck,
  CircleHelp,
  CircleSlash,
  DatabaseZap,
  FileCheck2,
  FileX2,
  HelpCircle,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  XCircle,
} from "lucide-react";
import { Badge, type BadgeTone } from "@/components/ui/primitives";
import { MEMORY_LABEL, type BackendValidation, type MemoryState } from "@/data/diagnostics";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/cn";

const VERDICT: Record<string, { tone: BadgeTone; icon: typeof ShieldAlert; label: string }> = {
  fraud: { tone: "fraud", icon: ShieldAlert, label: "Fraud" },
  legitimate: { tone: "legit", icon: ShieldCheck, label: "Legitimate" },
  uncertain: { tone: "uncertain", icon: ShieldQuestion, label: "Uncertain" },
};

export function VerdictBadge({ verdict, className }: { verdict?: string; className?: string }) {
  const v = verdict ? VERDICT[verdict] : undefined;
  if (!v) {
    return (
      <Badge tone="muted" className={className} title="Verdict not in the contract vocabulary">
        <HelpCircle className="h-3 w-3" aria-hidden /> {verdict ? humanize(verdict) : "Unknown"}
      </Badge>
    );
  }
  const Icon = v.icon;
  return (
    <Badge tone={v.tone} className={className}>
      <Icon className="h-3 w-3" aria-hidden /> {v.label}
    </Badge>
  );
}

const STATUS_TONE: Record<string, BadgeTone> = {
  closed_fraud: "fraud",
  closed_legitimate: "legit",
  escalated: "uncertain",
  open: "info",
};
export function StatusBadge({ status }: { status?: string }) {
  return <Badge tone={(status && STATUS_TONE[status]) || "muted"}>{humanize(status ?? "")}</Badge>;
}

export function RouteBadge({ route }: { route?: string }) {
  const map: Record<string, string> = {
    auto: "border-emerald-500/70 text-emerald-300",
    L1: "border-amber-400/70 bg-amber-400/10 text-amber-200",
    L2: "border-red-500/70 bg-red-500/10 text-red-300",
  };
  const title: Record<string, string> = {
    auto: "Auto-approved: the agent may execute alone",
    L1: "L1: team-lead approval required",
    L2: "L2: fraud-manager approval required",
  };
  return (
    <span
      className={cn(
        "inline-flex h-5 min-w-[2.25rem] items-center justify-center rounded border px-1.5 font-mono text-[11px] font-semibold",
        (route && map[route]) || "border-zinc-600 text-zinc-400",
      )}
      title={(route && title[route]) || "Unknown route"}
    >
      {route || "?"}
    </span>
  );
}

const MEMORY_STYLE: Record<MemoryState, { tone: BadgeTone; icon: typeof BadgeCheck }> = {
  verified: { tone: "legit", icon: BadgeCheck },
  written_unverified: { tone: "uncertain", icon: CircleHelp },
  readback_failed: { tone: "fraud", icon: AlertTriangle },
  not_written: { tone: "danger", icon: XCircle },
  conflict: { tone: "fraud", icon: AlertTriangle },
  unknown: { tone: "muted", icon: CircleSlash },
};

export function MemoryBadge({ state, compact }: { state: MemoryState; compact?: boolean }) {
  const s = MEMORY_STYLE[state];
  const Icon = s.icon;
  return (
    <Badge tone={s.tone} title={`Graph memory: ${MEMORY_LABEL[state]}`}>
      <Icon className="h-3 w-3" aria-hidden />
      {compact ? { verified: "Verified", written_unverified: "Unverified", readback_failed: "Read-back failed", not_written: "Not written", conflict: "Conflict", unknown: "Unknown" }[state] : MEMORY_LABEL[state]}
    </Badge>
  );
}

export function BackendValidationBadge({ state }: { state: BackendValidation }) {
  if (state === "passed")
    return (
      <Badge tone="legit" title="Backend validator reported pass">
        <FileCheck2 className="h-3 w-3" aria-hidden /> Passed
      </Badge>
    );
  if (state === "failed")
    return (
      <Badge tone="fraud" title="Backend validator reported failure">
        <FileX2 className="h-3 w-3" aria-hidden /> Failed
      </Badge>
    );
  return (
    <Badge tone="muted" title="No backend validation result was supplied (no trace or batch summary)">
      <CircleHelp className="h-3 w-3" aria-hidden /> Not reported
    </Badge>
  );
}

export function ContractBadge({ valid, errors }: { valid: boolean; errors: number }) {
  return valid ? (
    <Badge tone="neutral" title="Answer satisfies the frozen contract (browser check)">
      Contract ok
    </Badge>
  ) : (
    <Badge tone="fraud" title="Answer violates the frozen contract (browser check)">
      {errors} contract error{errors === 1 ? "" : "s"}
    </Badge>
  );
}

export function ViaBadge({ via }: { via: string }) {
  if (via === "mcp")
    return (
      <Badge tone="accent" title="Invoked through the restricted TigerGraph MCP server">
        <DatabaseZap className="h-3 w-3" aria-hidden /> MCP
      </Badge>
    );
  if (via === "direct")
    return (
      <Badge tone="info" title="Invoked directly, NOT through MCP">
        DIRECT · not MCP
      </Badge>
    );
  if (via === "fallback")
    return (
      <Badge tone="uncertain" title="Fallback path, NOT through MCP">
        FALLBACK · not MCP
      </Badge>
    );
  return (
    <Badge tone="muted" title="Unrecognised invocation path">
      {via || "unknown"} · not MCP
    </Badge>
  );
}

export function SarBadge({ filed }: { filed?: boolean }) {
  if (filed === undefined) return <Badge tone="muted">—</Badge>;
  return filed ? <Badge tone="accent">SAR filed</Badge> : <Badge tone="muted">No SAR</Badge>;
}

export function PatternText({ pattern, className }: { pattern?: string; className?: string }) {
  if (!pattern) return <span className="text-zinc-500">—</span>;
  const undocumented = pattern === "undocumented";
  return (
    <span className={cn("inline-flex items-center gap-1", undocumented && "text-violet-300", className)}>
      {pattern === "none" ? <span className="text-zinc-400">none</span> : <span className="id font-mono text-[12.5px]">{pattern}</span>}
      {undocumented && (
        <Badge tone="purple" className="ml-0.5">
          NEW
        </Badge>
      )}
    </span>
  );
}

export function ProbabilityBar({ value, className }: { value?: number; className?: string }) {
  const ok = typeof value === "number" && Number.isFinite(value);
  const pct = ok ? Math.max(0, Math.min(1, value)) * 100 : 0;
  const color = !ok ? "bg-zinc-600" : value >= 0.85 ? "bg-red-500" : value <= 0.15 ? "bg-emerald-500" : "bg-amber-400";
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <span className="w-9 text-right font-mono text-[13px] tabular-nums text-zinc-100">{ok ? value.toFixed(2) : "—"}</span>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-ink-700" role="img" aria-label={ok ? `fraud probability ${value.toFixed(2)}` : "probability unavailable"}>
        <div className={cn("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
