import { useState } from "react";
import { AlertTriangle, ChevronDown, FlaskConical, ShieldCheck } from "lucide-react";
import type { DataBundle } from "@/data/client";
import { DEMO_BANNER } from "@/data/fixtures";
import { GroupedDiagnostics } from "@/components/shared/Diagnostics";
import { cn } from "@/lib/cn";

/** The persistent data-honesty banner. Fixture and partial modes can never be hidden. */
export function DataModeBanner({ bundle }: { bundle: DataBundle }) {
  const [open, setOpen] = useState(false);
  const errors = bundle.diagnostics.filter((d) => d.level === "error");
  const warnings = bundle.diagnostics.filter((d) => d.level === "warning");

  if (bundle.mode === "fixtures") {
    return (
      <div role="status" aria-live="polite" className="border-y-2 border-amber-300 bg-[repeating-linear-gradient(135deg,#f59e0b_0_14px,#d97706_14px_28px)] text-ink-950 shadow-lg">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
          <span className="inline-flex items-center gap-1.5 rounded bg-ink-950 px-2 py-0.5 text-[12px] font-black tracking-[0.18em] text-amber-300">
            <FlaskConical className="h-3.5 w-3.5" aria-hidden /> DEMO DATA
          </span>
          <p className="min-w-0 flex-1 text-[13px] font-semibold">{DEMO_BANNER.replace(/^DEMO DATA — /, "")}</p>
        </div>
      </div>
    );
  }

  if (bundle.mode === "partial") {
    return (
      <div role="status" className="border-y border-red-500/60 bg-red-950/95 text-red-100 shadow-lg backdrop-blur">
        <div className="mx-auto max-w-[1400px] px-4 py-2">
          <div className="flex flex-wrap items-center gap-3">
            <span className="inline-flex items-center gap-1.5 rounded bg-red-500 px-2 py-0.5 text-[12px] font-black tracking-[0.16em] text-white">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> PARTIAL DATA
            </span>
            <p className="min-w-0 flex-1 text-[13px]">
              Some official backend files are missing or failed validation ({errors.length} error{errors.length === 1 ? "" : "s"}, {warnings.length} warning
              {warnings.length === 1 ? "" : "s"}). Only what passed is presented as trustworthy; nothing missing has been filled in.
            </p>
            <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="inline-flex items-center gap-1 rounded border border-red-400/50 px-2 py-0.5 text-[12px] hover:bg-red-500/20">
              Diagnostics <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} aria-hidden />
            </button>
          </div>
          {open && (
            <div className="scroll-thin mt-2 max-h-72 overflow-auto pb-1">
              <GroupedDiagnostics items={[...errors, ...warnings]} empty="" />
              {(bundle.manifest?.errors.length ?? 0) > 0 && (
                <details className="mt-2 text-[12px] text-red-200/80">
                  <summary className="cursor-pointer">Sync-time errors recorded in data-manifest.json ({bundle.manifest?.errors.length})</summary>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 font-mono">
                    {bundle.manifest?.errors.map((e, i) => <li key={i}>{e}</li>)}
                  </ul>
                </details>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (bundle.mode === "official") {
    return (
      <div className="border-b border-emerald-500/20 bg-emerald-500/5">
        <div className="mx-auto flex max-w-[1400px] items-center gap-2 px-4 py-1.5 text-[12px] text-emerald-200/90">
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
          Official backend results · {bundle.manifest?.valid_cases}/{bundle.manifest?.cases_found} answer files contract-valid · {bundle.manifest?.traces_found} traces
          {warnings.length > 0 && <span className="text-amber-200"> · {warnings.length} warning{warnings.length === 1 ? "" : "s"}</span>}
        </div>
      </div>
    );
  }
  return null;
}
