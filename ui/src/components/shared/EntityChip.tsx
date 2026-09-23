import { Crosshair } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * A monospace entity ID. When the ID exists in the trace subgraph the chip is a
 * button that selects it in the Graph tab; otherwise it is plain text with an
 * explanation, so nothing pretends to be linked.
 */
export function EntityChip({ id, inGraph, onSelect, className }: { id: string; inGraph?: boolean; onSelect?: (id: string) => void; className?: string }) {
  const base = "inline-flex max-w-full items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11.5px] leading-4";
  if (inGraph && onSelect) {
    return (
      <button
        type="button"
        onClick={() => onSelect(id)}
        className={cn(base, "border-tg-500/40 bg-tg-500/10 text-tg-200 hover:border-tg-400 hover:bg-tg-500/20", className)}
        title={`Show ${id} in the graph`}
        aria-label={`Show ${id} in the graph`}
      >
        <Crosshair className="h-3 w-3 shrink-0" aria-hidden />
        <span className="id">{id}</span>
      </button>
    );
  }
  return (
    <span className={cn(base, "border-ink-600 bg-ink-800 text-zinc-300", className)} title={inGraph === false ? `${id} is not in the trace subgraph` : id}>
      <span className="id">{id}</span>
    </span>
  );
}
