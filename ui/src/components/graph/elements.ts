/**
 * Pure mapping from a trace subgraph to Cytoscape elements. The trace is never
 * mutated; capping and filtering produce new arrays.
 */
import type { ElementDefinition } from "cytoscape";
import { GRAPH_RENDER_CAP, KNOWN_NODE_TYPES, KNOWN_ROLES, type GraphEdge, type GraphNode, type TraceFile } from "@/contracts";

export interface GraphOptions {
  hideContext: boolean;
  episodeOnly: boolean;
  cap?: number;
}

export interface GraphSelection {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total: number;
  capped: boolean;
  filteredOut: number;
  droppedEdges: number;
}

const ROLE_PRIORITY: Record<string, number> = {
  flagged: 0,
  affected: 1,
  subject: 2,
  this_case: 3,
  connected: 4,
  prior_case: 5,
  context: 6,
};

export const roleClass = (role: string) => ((KNOWN_ROLES as readonly string[]).includes(role) ? role : "unknown");
export const typeClass = (type: string) => ((KNOWN_NODE_TYPES as readonly string[]).includes(type) ? type : "Unknown");

const EPISODE_ROLES = new Set(["flagged", "affected", "subject", "this_case"]);

export function selectGraph(trace: TraceFile, opts: GraphOptions): GraphSelection {
  const cap = opts.cap ?? GRAPH_RENDER_CAP;
  const all = trace.subgraph.nodes;
  const seen = new Set<string>();
  const unique = all.filter((n) => (seen.has(n.id) ? false : (seen.add(n.id), true)));
  let pool = unique;
  if (opts.episodeOnly) pool = pool.filter((n) => EPISODE_ROLES.has(n.role));
  else if (opts.hideContext) pool = pool.filter((n) => roleClass(n.role) !== "context" && roleClass(n.role) !== "unknown");
  const filteredOut = unique.length - pool.length;
  const ranked = pool
    .map((n, i) => ({ n, i }))
    .sort((a, b) => (ROLE_PRIORITY[a.n.role] ?? 7) - (ROLE_PRIORITY[b.n.role] ?? 7) || (a.n.discovered_at_step ?? 99) - (b.n.discovered_at_step ?? 99) || a.i - b.i);
  const kept = ranked.slice(0, cap).map((x) => x.n);
  const keptIds = new Set(kept.map((n) => n.id));
  const edges = trace.subgraph.edges.filter((e) => keptIds.has(e.source) && keptIds.has(e.target));
  const allIds = new Set(unique.map((n) => n.id));
  const droppedEdges = trace.subgraph.edges.filter((e) => !allIds.has(e.source) || !allIds.has(e.target)).length;
  return { nodes: kept, edges, total: unique.length, capped: pool.length > cap, filteredOut, droppedEdges };
}

export function toElements(sel: GraphSelection): ElementDefinition[] {
  // Spread initial positions so no two nodes coincide before the layout runs
  // (overlapping endpoints make Cytoscape warn about undrawable edges).
  const cols = Math.max(1, Math.ceil(Math.sqrt(sel.nodes.length)));
  const nodes: ElementDefinition[] = sel.nodes.map((n, i) => {
    const seq = n.attrs["sequence"];
    const label = typeof seq === "number" ? `#${seq} ${n.label}` : n.label;
    return {
      group: "nodes",
      data: { id: n.id, label: label.length > 42 ? `${label.slice(0, 40)}…` : label, type: n.type, role: n.role },
      classes: `role-${roleClass(n.role)} type-${typeClass(n.type)}`,
      position: { x: (i % cols) * 60, y: Math.floor(i / cols) * 60 },
    };
  });
  const edges: ElementDefinition[] = sel.edges.map((e, i) => ({
    group: "edges",
    data: { id: `e${i}:${e.source}->${e.target}`, source: e.source, target: e.target, type: e.type },
    classes: e.type === "NEXT_IN_EPISODE" ? "episode" : "",
  }));
  return [...nodes, ...edges];
}

export const allNodeIds = (trace?: TraceFile) => new Set(trace?.subgraph.nodes.map((n) => n.id) ?? []);
