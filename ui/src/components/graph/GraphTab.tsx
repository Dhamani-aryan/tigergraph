import { useEffect, useMemo, useRef, useState } from "react";
import CytoscapeComponent from "react-cytoscapejs";
import cytoscape, { type Core, type StylesheetStyle } from "cytoscape";
import fcose from "cytoscape-fcose";
import { Eye, EyeOff, Focus, Info, Maximize2, RotateCcw, Route as RouteIcon, X } from "lucide-react";
import type { GraphNode, TraceFile } from "@/contracts";
import { Badge, Button, EmptyState } from "@/components/ui/primitives";
import { redact } from "@/lib/redact";
import { roleClass, selectGraph, toElements } from "./elements";
import { cn } from "@/lib/cn";

let registered = false;
function ensureLayout() {
  if (!registered) {
    cytoscape.use(fcose);
    registered = true;
  }
}

const ROLE_COLORS: Record<string, { fill: string; border: string; label: string }> = {
  subject: { fill: "#334155", border: "#f8fafc", label: "Subject (strong border)" },
  flagged: { fill: "#b91c1c", border: "#F68B1F", label: "Flagged (orange ring)" },
  affected: { fill: "#dc2626", border: "#7f1d1d", label: "Affected" },
  connected: { fill: "#f59e0b", border: "#78350f", label: "Connected" },
  prior_case: { fill: "#8b5cf6", border: "#4c1d95", label: "Prior case" },
  this_case: { fill: "#10b981", border: "#064e3b", label: "This case" },
  context: { fill: "#52525b", border: "#3f3f46", label: "Context" },
  unknown: { fill: "#3f3f46", border: "#a1a1aa", label: "Other role" },
};

const TYPE_SHAPES: [string, string][] = [
  ["Customer", "ellipse"],
  ["Card", "round-rectangle"],
  ["Transaction", "diamond"],
  ["Device", "hexagon"],
  ["Region", "triangle"],
  ["Email", "vee"],
  ["Merchant", "barrel"],
  ["ClosedCase", "tag"],
  ["FraudCase", "star"],
  ["PolicyDoc", "rectangle"],
  ["Unknown", "octagon"],
];

function stylesheet(): StylesheetStyle[] {
  const s: StylesheetStyle[] = [
    {
      selector: "node",
      style: {
        width: 22,
        height: 22,
        "background-color": "#52525b",
        "border-width": 1.5,
        "border-color": "#3f3f46",
        label: "data(label)",
        color: "#d4d4d8",
        "font-size": 8,
        "font-family": "JetBrains Mono, ui-monospace, monospace",
        "text-valign": "bottom",
        "text-margin-y": 3,
        "text-outline-color": "#07090D",
        "text-outline-width": 2,
        "min-zoomed-font-size": 7,
      },
    },
    {
      selector: "edge",
      style: {
        width: 1,
        "line-color": "#3a4655",
        "curve-style": "straight",
        "target-arrow-shape": "none",
        opacity: 0.75,
      },
    },
    {
      selector: "edge.episode",
      style: { width: 2.5, "line-color": "#f87171", "target-arrow-shape": "triangle", "target-arrow-color": "#f87171", opacity: 1, "arrow-scale": 0.9 },
    },
  ];
  for (const [type, shape] of TYPE_SHAPES) s.push({ selector: `node.type-${type}`, style: { shape: shape as never } });
  for (const [role, c] of Object.entries(ROLE_COLORS)) {
    s.push({ selector: `node.role-${role}`, style: { "background-color": c.fill, "border-color": c.border } });
  }
  s.push(
    { selector: "node.role-subject", style: { "border-width": 4, width: 30, height: 30, "font-size": 9, color: "#fff" } },
    { selector: "node.role-flagged", style: { "border-width": 5, width: 32, height: 32, "underlay-color": "#F68B1F", "underlay-opacity": 0.35, "underlay-padding": 6, color: "#fff", "font-size": 9 } },
    { selector: "node.role-flagged.pulse", style: { "underlay-opacity": 0.08, "underlay-padding": 11 } },
    { selector: "node.role-affected", style: { width: 26, height: 26, color: "#fecaca" } },
    { selector: "node.role-context", style: { width: 14, height: 14, "font-size": 6, color: "#71717a" } },
    { selector: "node.role-unknown", style: { "border-style": "dashed" } },
    { selector: "node:selected", style: { "border-color": "#F68B1F", "border-width": 5, "overlay-color": "#F68B1F", "overlay-opacity": 0.12 } },
    { selector: ".faded", style: { opacity: 0.18 } },
    { selector: "edge.hl", style: { "line-color": "#F68B1F", width: 2, opacity: 1 } },
  );
  return s;
}

const LAYOUT = { name: "fcose", quality: "default", randomize: true, animate: false, nodeRepulsion: 6500, idealEdgeLength: 70, nodeSeparation: 60, padding: 24, packComponents: true } as const;

function prefersReducedMotion() {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export default function GraphTab({ trace, selectedId, onSelect }: { trace: TraceFile; selectedId?: string; onSelect: (id?: string) => void }) {
  ensureLayout();
  const [hideContext, setHideContext] = useState(false);
  const [episodeOnly, setEpisodeOnly] = useState(false);
  const [layoutKey, setLayoutKey] = useState(0);
  const cyRef = useRef<Core | null>(null);
  const bound = useRef<Core | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  const selection = useMemo(() => selectGraph(trace, { hideContext, episodeOnly }), [trace, hideContext, episodeOnly]);
  const elements = useMemo(() => toElements(selection), [selection]);
  const styles = useMemo(stylesheet, []);
  const byId = useMemo(() => new Map(trace.subgraph.nodes.map((n) => [n.id, n])), [trace]);
  const rendered = useMemo(() => new Set(selection.nodes.map((n) => n.id)), [selection]);
  const selected: GraphNode | undefined = selectedId ? byId.get(selectedId) : undefined;
  const mountKey = `${hideContext}-${episodeOnly}-${layoutKey}`;

  // Selection + neighbourhood highlight.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("faded hl");
    cy.$(":selected").unselect();
    if (!selectedId) return;
    const node = cy.getElementById(selectedId);
    if (node.empty()) return;
    node.select();
    const hood = node.closedNeighborhood();
    cy.elements().difference(hood).addClass("faded");
    node.connectedEdges().addClass("hl");
    cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.1) }, { duration: prefersReducedMotion() ? 0 : 250 });
  }, [selectedId, mountKey]);

  // Flagged-node pulse, disabled under prefers-reduced-motion (static ring remains).
  useEffect(() => {
    if (prefersReducedMotion()) return;
    const id = window.setInterval(() => cyRef.current?.$("node.role-flagged").toggleClass("pulse"), 1100);
    return () => window.clearInterval(id);
  }, [mountKey]);

  if (trace.subgraph.nodes.length === 0) {
    return (
      <EmptyState icon={<Info className="h-7 w-7" />} title="The trace carries an empty subgraph">
        The backend reported no graph entities for this case, so there is nothing to draw.
      </EmptyState>
    );
  }

  const bindCy = (cy: Core) => {
    cyRef.current = cy;
    if (bound.current === cy) return;
    bound.current = cy;
    cy.on("tap", "node", (evt) => onSelectRef.current(evt.target.id()));
    cy.on("tap", (evt) => {
      if (evt.target === cy) onSelectRef.current(undefined);
    });
    cy.one("layoutstop", () => {
      if (selectedId) {
        const n = cy.getElementById(selectedId);
        if (!n.empty()) {
          n.select();
          cy.elements().difference(n.closedNeighborhood()).addClass("faded");
          n.connectedEdges().addClass("hl");
        }
      }
    });
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="panel overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-700/70 px-3 py-2">
          <Button size="sm" onClick={() => cyRef.current?.fit(undefined, 24)} aria-label="Fit graph to view">
            <Maximize2 className="h-3.5 w-3.5" aria-hidden /> Fit
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setHideContext(false);
              setEpisodeOnly(false);
              onSelect(undefined);
              setLayoutKey((k) => k + 1);
            }}
            aria-label="Reset graph layout and filters"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden /> Reset
          </Button>
          <Button size="sm" onClick={() => setHideContext((v) => !v)} aria-pressed={hideContext} disabled={episodeOnly} className={cn(hideContext && "border-tg-500/60 text-tg-200")}>
            {hideContext ? <EyeOff className="h-3.5 w-3.5" aria-hidden /> : <Eye className="h-3.5 w-3.5" aria-hidden />} Hide context
          </Button>
          <Button size="sm" onClick={() => setEpisodeOnly((v) => !v)} aria-pressed={episodeOnly} className={cn(episodeOnly && "border-tg-500/60 text-tg-200")}>
            <RouteIcon className="h-3.5 w-3.5" aria-hidden /> Affected episode only
          </Button>
          <span className="ml-auto font-mono text-[11.5px] text-zinc-500">
            {selection.nodes.length}/{selection.total} nodes · {selection.edges.length} edges
          </span>
        </div>
        {(selection.capped || selection.droppedEdges > 0) && (
          <div role="status" className="flex items-center gap-2 border-b border-amber-400/30 bg-amber-400/10 px-3 py-1.5 text-[12px] text-amber-100">
            <Info className="h-3.5 w-3.5" aria-hidden />
            {selection.capped && `Rendering capped at ${selection.nodes.length} nodes by role priority (flagged, affected and subject first); the trace file itself is unchanged. `}
            {selection.droppedEdges > 0 && `${selection.droppedEdges} edge(s) reference nodes missing from the subgraph and are not drawn.`}
          </div>
        )}
        {selectedId && !rendered.has(selectedId) && (
          <div role="status" className="flex items-center gap-2 border-b border-sky-500/30 bg-sky-500/10 px-3 py-1.5 text-[12px] text-sky-100">
            <Focus className="h-3.5 w-3.5" aria-hidden /> <span className="id">{selectedId}</span> {byId.has(selectedId) ? "is hidden by the current filter or render cap." : "is not part of this trace's subgraph."}
          </div>
        )}
        <div className="relative h-[560px] bg-[radial-gradient(circle_at_center,#10151D_0%,#07090D_75%)]" aria-label="Case subgraph. Use the entity chips on the Evidence tab or click nodes to inspect them." role="img">
          <CytoscapeComponent
            key={mountKey}
            elements={elements}
            stylesheet={styles}
            layout={LAYOUT as unknown as cytoscape.LayoutOptions}
            cy={bindCy}
            style={{ width: "100%", height: "100%" }}
            minZoom={0.15}
            maxZoom={3}
            boxSelectionEnabled={false}
          />
        </div>
        <Legend />
      </div>
      <NodePanel node={selected} trace={trace} onClear={() => onSelect(undefined)} />
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 border-t border-ink-700/70 px-3 py-2 text-[11.5px] text-zinc-400" aria-label="Legend">
      {Object.entries(ROLE_COLORS).map(([role, c]) => (
        <span key={role} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-full" style={{ background: c.fill, boxShadow: `0 0 0 2px ${c.border}` }} aria-hidden />
          {c.label}
        </span>
      ))}
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block h-0.5 w-5 bg-red-400" aria-hidden /> Episode sequence (#n)
      </span>
      <span className="text-zinc-500">Shapes: ● customer ▢ card ◆ transaction ⬡ device ▲ region ★ this case</span>
    </div>
  );
}

function NodePanel({ node, trace, onClear }: { node?: GraphNode; trace: TraceFile; onClear: () => void }) {
  if (!node) {
    return (
      <aside className="panel p-4 text-sm text-zinc-400" aria-label="Selected node">
        <h2 className="panel-title mb-2">Selected node</h2>
        <p>Click a node, or an entity chip on the Evidence or Investigation tab, to inspect it here.</p>
      </aside>
    );
  }
  const attrs = redact(node.attrs);
  const degree = trace.subgraph.edges.filter((e) => e.source === node.id || e.target === node.id);
  return (
    <aside className="panel min-w-0 p-4" aria-label="Selected node" aria-live="polite">
      <div className="flex items-start justify-between gap-2">
        <h2 className="panel-title">Selected node</h2>
        <button type="button" onClick={onClear} className="rounded p-0.5 text-zinc-500 hover:bg-ink-800 hover:text-zinc-200" aria-label="Clear selection">
          <X className="h-4 w-4" />
        </button>
      </div>
      <p className="id mt-2 text-[15px] font-semibold text-white">{node.id}</p>
      <p className="mt-0.5 text-[13px] text-zinc-400">{node.label}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Badge tone="neutral">{node.type}</Badge>
        <Badge tone={roleClass(node.role) === "unknown" ? "muted" : node.role === "affected" || node.role === "flagged" ? "fraud" : node.role === "connected" ? "uncertain" : node.role === "prior_case" ? "purple" : node.role === "this_case" ? "legit" : "neutral"}>
          role: {node.role}
        </Badge>
        {node.discovered_at_step !== undefined && <Badge tone="info">step {node.discovered_at_step}</Badge>}
      </div>
      <h3 className="mb-1 mt-4 text-[11px] font-medium uppercase tracking-wider text-zinc-500">Attributes</h3>
      {Object.keys(attrs).length ? (
        <dl className="space-y-1 text-[12.5px]">
          {Object.entries(attrs).map(([k, v]) => (
            <div key={k} className="grid grid-cols-[110px_minmax(0,1fr)] gap-2">
              <dt className="truncate font-mono text-zinc-500" title={k}>
                {k}
              </dt>
              <dd className="id text-zinc-200">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-[13px] text-zinc-600">No attributes.</p>
      )}
      <h3 className="mb-1 mt-4 text-[11px] font-medium uppercase tracking-wider text-zinc-500">Edges ({degree.length})</h3>
      <ul className="scroll-thin max-h-48 space-y-0.5 overflow-auto font-mono text-[11.5px] text-zinc-400">
        {degree.slice(0, 60).map((e, i) => (
          <li key={i} className="id">
            {e.source === node.id ? "→" : "←"} {e.type} {e.source === node.id ? e.target : e.source}
          </li>
        ))}
      </ul>
    </aside>
  );
}
