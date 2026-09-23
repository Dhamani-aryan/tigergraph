import { Fragment } from "react";
import {
  ArrowDown,
  ArrowRight,
  BookOpenCheck,
  Boxes,
  BrainCircuit,
  CheckCircle2,
  ClipboardCheck,
  Database,
  FileText,
  GitBranch,
  Scale,
  ScrollText,
  ShieldCheck,
  Users,
  Workflow,
  Wrench,
} from "lucide-react";
import { useData } from "@/data/DataProvider";
import { Panel } from "@/components/ui/primitives";

const PIPELINE = [
  { icon: Boxes, title: "Case pack", body: "20 benchmark alerts (risk score, customer report or analyst request). The risk score is an input signal only." },
  { icon: Database, title: "TigerGraph Savanna", body: "Customers, cards, transactions, devices, regions, emails and closed cases, with vector attributes for retrieval." },
  { icon: GitBranch, title: "Installed GSQL + graph algorithms", body: "Versioned, cutoff-bounded queries and a connected-components (WCC) pass that clusters shared-origin activity." },
  { icon: Wrench, title: "Restricted TigerGraph MCP", body: "The agent reaches the graph through an allow-listed MCP tool surface; every call records whether it went via MCP." },
  { icon: BookOpenCheck, title: "GraphRAG", body: "Vector retrieval over closed cases, the fraud policy and regulatory guidance, admissible only if it predates the case cutoff." },
  { icon: Workflow, title: "Investigation workflow", body: "LangGraph orchestration: gather evidence, optional agent follow-up tool, structured assessment, evidence request, reassessment." },
  { icon: Scale, title: "Deterministic policy rules", body: "R1–R10 map findings to actions and approval routes (auto, L1, L2). The LLM never picks an action directly." },
  { icon: FileText, title: "SAR generation", body: "When policy requires FILE_REPORT, a stand-alone narrative is drafted from the case facts and activity dates." },
  { icon: ClipboardCheck, title: "Answer validation", body: "The exact challenge schema plus cross-field rules: vocabularies, ranges, SAR agreement, no-request-no-change." },
  { icon: ShieldCheck, title: "Graph memory write + read-back", body: "The case is upserted as a FraudCase vertex with an embedding, then independently read back before it counts." },
];

const CHECKLIST = [
  ["TigerGraph Savanna for graph and vector storage", "Backend"],
  ["Versioned GSQL and at least one graph algorithm", "Backend"],
  ["TigerGraph MCP as the agent-facing graph interface", "Backend · provenance shown per tool call"],
  ["GraphRAG grounded in graph, vector and policy evidence", "Backend · retrieval shown on Evidence tab"],
  ["Evidence requests with a changed recommendation", "Backend · Actions tab shows initial vs final"],
  ["Policy-controlled actions with approval routes", "Backend · routes checked against the README table"],
  ["SAR when policy calls for one", "Backend · SAR tab renders filed reports only"],
  ["Case memory written back to the graph", "Backend · Memory badge requires read-back"],
  ["A working analyst interface", "This console"],
  ["Twenty validated answer files", "Backend · strict sync refuses anything less"],
];

export function HowItWorks() {
  const state = useData();
  const bundle = state.status === "ready" ? state.bundle : undefined;
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-white">How it works</h1>
        <p className="mt-1 max-w-3xl text-sm text-zinc-400">
          The investigation runs entirely in the backend. This site is a static viewer: it reads the JSON the backend produced, validates it against the challenge contract, and shows what happened,
          including what is missing or failed. It never calls TigerGraph or an LLM.
        </p>
      </header>

      <Panel title="Pipeline" icon={<Workflow className="h-3.5 w-3.5" aria-hidden />}>
        <ol className="grid gap-2 md:grid-cols-5" aria-label="Investigation pipeline, in order">
          {PIPELINE.map((p, i) => {
            const Icon = p.icon;
            return (
              <Fragment key={p.title}>
                <li className="relative rounded-lg border border-ink-700 bg-ink-950/60 p-3">
                  <div className="flex items-center gap-2">
                    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-tg-500/15 text-tg-300 ring-1 ring-tg-500/30">
                      <Icon className="h-4 w-4" aria-hidden />
                    </span>
                    <span className="font-mono text-[11px] text-zinc-500">{String(i + 1).padStart(2, "0")}</span>
                  </div>
                  <h2 className="mt-2 text-[13.5px] font-semibold text-zinc-100">{p.title}</h2>
                  <p className="mt-1 text-[12.5px] leading-5 text-zinc-400">{p.body}</p>
                  {i < PIPELINE.length - 1 && (
                    <>
                      {(i + 1) % 5 !== 0 && <ArrowRight className="absolute -right-2.5 top-1/2 z-10 hidden h-4 w-4 -translate-y-1/2 text-tg-500 md:block" aria-hidden />}
                      <ArrowDown className="mx-auto mt-2 h-4 w-4 text-tg-500 md:hidden" aria-hidden />
                    </>
                  )}
                </li>
              </Fragment>
            );
          })}
        </ol>
        <p className="mt-3 text-[12px] text-zinc-500">
          Outputs per case: <code className="id">cases/HHG-xxx.json</code> (the challenge answer), <code className="id">runs/latest/traces/HHG-xxx.trace.json</code> (execution projection) and one{" "}
          <code className="id">runs/latest/batch_summary.json</code>. <code className="id">npm run sync-data</code> copies them into this site.
        </p>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Challenge components" icon={<CheckCircle2 className="h-3.5 w-3.5" aria-hidden />}>
          <ul className="space-y-1.5">
            {CHECKLIST.map(([item, where]) => (
              <li key={item} className="flex items-start gap-2 text-[13px]">
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-500" aria-hidden />
                <span className="text-zinc-200">
                  {item} <span className="text-zinc-500">— {where}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[12px] text-zinc-500">This list states where each requirement is implemented and displayed. Whether a given run met it is shown per case, from backend data only.</p>
        </Panel>

        <Panel title="Honesty rules" icon={<ShieldCheck className="h-3.5 w-3.5" aria-hidden />} className="border-tg-500/30">
          <ul className="space-y-2.5 text-[13.5px] text-zinc-200">
            <li>
              <strong className="text-tg-300">Simulated responses.</strong> The challenge supplies no customer or analyst replies. When the agent requests evidence, the response is simulated and
              labelled as such.
            </li>
            <li>
              <strong className="text-tg-300">Bounded by the cutoff.</strong> Evidence and prior cases are only admissible if they exist before the case was opened; nothing from the future is used.
            </li>
            <li>
              <strong className="text-tg-300">Risk score is an input.</strong> The bank model&apos;s score starts an investigation; it is never treated as the verdict.
            </li>
            <li>
              <strong className="text-tg-300">Missing means missing.</strong> A case without a trace shows the answer and says the trace was not provided. No step, graph or tool call is reconstructed.
            </li>
            <li>
              <strong className="text-tg-300">Memory needs a read-back.</strong> A write that was not independently read back is shown as unverified, never as success.
            </li>
            <li>
              <strong className="text-tg-300">Fixtures are labelled.</strong> Interface fixtures carry DEMO- identifiers and a permanent banner.
            </li>
          </ul>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Technology" icon={<BrainCircuit className="h-3.5 w-3.5" aria-hidden />}>
          <dl className="space-y-2 text-[13px]">
            <div>
              <dt className="text-zinc-500">Backend</dt>
              <dd className="text-zinc-200">Python, TigerGraph Savanna, GSQL, TigerGraph MCP, LangGraph, structured LLM calls, deterministic policy engine</dd>
            </div>
            <div>
              <dt className="text-zinc-500">Console</dt>
              <dd className="text-zinc-200">Vite, React 18, TypeScript (strict), Tailwind, Radix Tabs, Cytoscape.js + fCoSE, Recharts, Zod runtime validation</dd>
            </div>
            <div>
              <dt className="text-zinc-500">Hosting</dt>
              <dd className="text-zinc-200">Static files only (Vercel or GitHub Pages). No server, no credentials.</dd>
            </div>
          </dl>
        </Panel>
        <Panel title="Team" icon={<Users className="h-3.5 w-3.5" aria-hidden />}>
          <dl className="space-y-2 text-[13px]">
            <div>
              <dt className="text-zinc-500">Naman — backend &amp; integration</dt>
              <dd className="text-zinc-200">Agent workflow, policy engine, SAR drafting, batch run and the final JSON artifacts.</dd>
            </div>
            <div>
              <dt className="text-zinc-500">Aryan — evidence foundation &amp; frontend</dt>
              <dd className="text-zinc-200">Bounded GSQL, restricted MCP, feature vector, prior-case and policy retrieval, answer validation, and this console.</dd>
            </div>
          </dl>
        </Panel>
        <Panel title="Dataset facts" icon={<ScrollText className="h-3.5 w-3.5" aria-hidden />}>
          <p className="mb-2 text-[12px] text-zinc-500">From the challenge README. These describe the supplied data, not run results.</p>
          <ul className="space-y-1 text-[13px] text-zinc-200">
            <li>IEEE-CIS Fraud Detection transactions (Vesta), July–December 2016, fraud label removed</li>
            <li>590,742 transactions · 144,432 identity records</li>
            <li>5,565 closed cases (July–October): 4,665 confirmed fraud, 900 cleared</li>
            <li>20 benchmark cases from November and December</li>
          </ul>
          {bundle?.summary && (
            <p className="mt-3 border-t border-ink-700/70 pt-2 text-[12px] text-zinc-400">
              Run results for this deployment are on the Overview page, sourced from <code className="id">batch_summary.json</code> ({bundle.summary.run_id}).
            </p>
          )}
        </Panel>
      </div>
    </div>
  );
}
