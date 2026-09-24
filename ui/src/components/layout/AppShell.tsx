import type { ReactNode } from "react";
import { NavLink, Link } from "react-router-dom";
import { Network } from "lucide-react";
import { useData } from "@/data/DataProvider";
import { DataModeBanner } from "./DataModeBanner";
import { Badge } from "@/components/ui/primitives";
import { shortSha } from "@/lib/format";
import { cn } from "@/lib/cn";

const navCls = ({ isActive }: { isActive: boolean }) =>
  cn(
    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
    isActive ? "bg-ink-700 text-white" : "text-zinc-400 hover:bg-ink-800 hover:text-zinc-100",
  );

export function AppShell({ children }: { children: ReactNode }) {
  const state = useData();
  const bundle = state.status === "ready" ? state.bundle : undefined;
  const modeChip =
    bundle?.mode === "fixtures" ? (
      <Badge tone="uncertain">FIXTURES</Badge>
    ) : bundle?.mode === "partial" ? (
      <Badge tone="fraud">PARTIAL</Badge>
    ) : bundle?.mode === "official" ? (
      <Badge tone="legit">OFFICIAL</Badge>
    ) : null;

  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-tg-500 focus:px-3 focus:py-1.5 focus:text-ink-950">
        Skip to content
      </a>
      <header className="no-print sticky top-0 z-40 h-14 border-b border-ink-700/80 bg-ink-950/90 backdrop-blur">
        <div className="mx-auto flex h-full max-w-[1400px] items-center gap-4 px-4">
          <Link to="/" className="flex items-center gap-2.5" aria-label="FraudGraph Investigator — overview">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-tg-500/15 ring-1 ring-tg-500/40">
              <Network className="h-4.5 w-4.5 text-tg-400" aria-hidden />
            </span>
            <span className="leading-tight">
              <span className="block text-[15px] font-semibold tracking-tight text-white">FraudGraph Investigator</span>
              <span className="block text-[11px] text-zinc-500">TigerGraph × Hacker House Goa</span>
            </span>
          </Link>
          <nav aria-label="Primary" className="ml-4 flex items-center gap-1">
            <NavLink to="/" end className={navCls}>
              Overview
            </NavLink>
            <NavLink to="/how-it-works" className={navCls}>
              How It Works
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-2">{modeChip}</div>
        </div>
      </header>
      {bundle && (
        <div className="no-print sticky top-14 z-30">
          <DataModeBanner bundle={bundle} />
        </div>
      )}
      <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6">
        {children}
      </main>
      <footer className="no-print border-t border-ink-800 py-4 text-[12px] text-zinc-500">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-4 gap-y-1 px-4">
          <span>Static console · reads synchronized JSON only · no backend, graph or LLM calls from the browser</span>
          {bundle?.manifest && (
            <span className="font-mono">
              data {bundle.manifest.mode} · synced {bundle.manifest.generated_at.slice(0, 19).replace("T", " ")} · source {shortSha(bundle.manifest.source_commit)}
            </span>
          )}
        </div>
      </footer>
    </div>
  );
}
