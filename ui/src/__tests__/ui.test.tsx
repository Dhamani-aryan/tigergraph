import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "@/App";
import { DEMO_BANNER } from "@/data/fixtures";
import { clone, fixtureClient, fixtureFiles, officialFiles } from "@/test/fixtures";
import { DataClient } from "@/data/client";
import { memoryFetch } from "@/test/fixtures";

// Cytoscape needs a real canvas; the graph tab is replaced with a probe that
// exposes the selection it was given.
vi.mock("@/components/graph/GraphTab", () => ({
  default: ({ selectedId }: { selectedId?: string }) => <div data-testid="graph-probe">selected:{selectedId ?? "none"}</div>,
}));

function renderAt(hash: string, client = fixtureClient()) {
  window.location.hash = hash;
  return render(<App client={client} />);
}

describe("overview", () => {
  it("shows the unmistakable DEMO DATA banner in fixture mode", async () => {
    renderAt("#/");
    expect(await screen.findByText("DEMO DATA")).toBeInTheDocument();
    expect(screen.getByText(DEMO_BANNER.replace(/^DEMO DATA — /, ""))).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "DEMO-001" })).toBeInTheDocument();
  });

  it("shows no demo banner for a valid official data set", async () => {
    renderAt("#/", new DataClient("./data", memoryFetch(officialFiles())));
    expect(await screen.findByRole("link", { name: "HHG-001" })).toBeInTheDocument();
    expect(screen.queryByText("DEMO DATA")).not.toBeInTheDocument();
    expect(screen.getByText(/Official backend results/)).toBeInTheDocument();
  });

  it("shows the partial-data banner when official files fail validation", async () => {
    const files = officialFiles();
    (files["cases/HHG-003.json"] as { case: { exposure_usd: number } }).case.exposure_usd = -1;
    renderAt("#/", new DataClient("./data", memoryFetch(files)));
    expect(await screen.findByText("PARTIAL DATA")).toBeInTheDocument();
  });

  it("filters the case board by search and verdict", async () => {
    const user = userEvent.setup();
    renderAt("#/");
    await screen.findByRole("link", { name: "DEMO-001" });
    const board = screen.getByRole("table");
    await user.type(screen.getByPlaceholderText("Case, card or customer ID"), "C7700");
    expect(within(board).getAllByRole("link").map((l) => l.textContent)).toEqual(["DEMO-005"]);
    await user.clear(screen.getByPlaceholderText("Case, card or customer ID"));
    await user.selectOptions(screen.getByLabelText("Verdict"), "legitimate");
    expect(within(screen.getByRole("table")).getAllByRole("link").map((l) => l.textContent)).toEqual(["DEMO-002"]);
  });

  it("shows an actionable message when no data was synchronized", async () => {
    renderAt("#/", new DataClient("./data", memoryFetch({})));
    expect(await screen.findByText("No synchronized data")).toBeInTheDocument();
  });
});

describe("case detail edge cases", () => {
  it("legitimate: zero exposure, no SAR document, no-change explanation", async () => {
    const user = userEvent.setup();
    renderAt("#/case/DEMO-002");
    expect(await screen.findByRole("heading", { name: "DEMO-002" })).toBeInTheDocument();
    expect(screen.getByText("$0.00")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /SAR/ }));
    expect(screen.getByText("No suspicious activity report was filed for this case")).toBeInTheDocument();
    expect(screen.queryByLabelText("Suspicious activity report")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Actions/ }));
    expect(screen.getByText("No change between initial and final actions")).toBeInTheDocument();
    expect(screen.getByText(/No evidence was requested, so the policy output could not move/)).toBeInTheDocument();
  });

  it("uncertain: escalation is prominent and a missing trace is explained", async () => {
    renderAt("#/case/DEMO-003");
    expect(await screen.findByText(/Escalated — uncertain verdict/)).toBeInTheDocument();
    expect(screen.getByText(/ESCALATE_TO_ANALYST\)/)).toBeInTheDocument();
    expect(screen.getByText("Execution trace not provided")).toBeInTheDocument();
  });

  it("undocumented pattern shows the discovered description; failed write is a red alert", async () => {
    renderAt("#/case/DEMO-004");
    expect(await screen.findByText("Discovered pattern")).toBeInTheDocument();
    expect(screen.getByText(/Refund cycling/)).toBeInTheDocument();
    const alert = screen.getAllByRole("alert").find((a) => /NOT written to TigerGraph/.test(a.textContent ?? ""));
    expect(alert).toBeDefined();
  });

  it("write without read-back is shown as failed, never as success", async () => {
    renderAt("#/case/DEMO-005");
    expect(await screen.findByText(/independent read-back did not confirm it/)).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });

  it("filed SAR renders as a formal document with subjects, amount and dates", async () => {
    const user = userEvent.setup();
    renderAt("#/case/DEMO-001");
    await screen.findByRole("heading", { name: "DEMO-001" });
    await user.click(screen.getByRole("tab", { name: /SAR/ }));
    const doc = screen.getByLabelText("Suspicious activity report");
    expect(within(doc).getByText("$612.46")).toBeInTheDocument();
    expect(within(doc).getByText("DEMO-C2044-K1")).toBeInTheDocument();
    expect(within(doc).getAllByText(/2016-11-28/).length).toBeGreaterThan(0);
    expect(within(doc).getByText("Demo fixture — not a real filing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Print \/ save PDF/ })).toBeInTheDocument();
  });

  it("shows MCP and non-MCP tool provenance honestly", async () => {
    renderAt("#/case/DEMO-001");
    await screen.findByRole("heading", { name: "DEMO-001" });
    expect(screen.getAllByText("FALLBACK · not MCP").length).toBeGreaterThan(0);
    expect(screen.getAllByText("MCP").length).toBeGreaterThan(5);
  });

  it("malformed trace keeps the answer page working and reports diagnostics", async () => {
    const user = userEvent.setup();
    renderAt("#/case/DEMO-006");
    expect(await screen.findByRole("heading", { name: "DEMO-006" })).toBeInTheDocument();
    expect(screen.getByText("Execution trace failed validation")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Raw JSON/ }));
    expect(screen.getAllByText(/does not satisfy the trace contract/).length).toBeGreaterThan(0);
  });

  it("clicking an evidence entity selects it in the graph tab", async () => {
    const user = userEvent.setup();
    renderAt("#/case/DEMO-001?tab=evidence");
    await user.click(await screen.findByRole("button", { name: "Show DEMO-DEV-7F21 in the graph" }));
    expect(await screen.findByTestId("graph-probe")).toHaveTextContent("selected:DEMO-DEV-7F21");
    expect(screen.getByRole("tab", { name: /Graph/ })).toHaveAttribute("aria-selected", "true");
  });

  it("an entity absent from the subgraph is not presented as a link", async () => {
    const files = fixtureFiles();
    const t = clone(files["traces/DEMO-001.trace.json"]) as { subgraph: { nodes: { id: string }[]; edges: { source: string; target: string }[] } };
    t.subgraph.nodes = t.subgraph.nodes.filter((n) => n.id !== "DEMO-C2044-K1");
    t.subgraph.edges = t.subgraph.edges.filter((e) => e.source !== "DEMO-C2044-K1" && e.target !== "DEMO-C2044-K1");
    files["traces/DEMO-001.trace.json"] = t;
    renderAt("#/case/DEMO-001?tab=evidence", new DataClient("./data", memoryFetch(files)));
    await screen.findByRole("heading", { name: "DEMO-001" });
    expect(screen.getAllByRole("button", { name: "Show DEMO-C3310-K2 in the graph" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Show DEMO-C2044-K1 in the graph" })).not.toBeInTheDocument();
    expect(screen.getAllByTitle("DEMO-C2044-K1 is not in the trace subgraph").length).toBeGreaterThan(0);
  });

  it("raw JSON redacts the fixture credential", async () => {
    const user = userEvent.setup();
    renderAt("#/case/DEMO-001?tab=raw");
    await screen.findByRole("heading", { name: "DEMO-001" });
    expect(screen.getByLabelText("trace JSON").textContent).not.toMatch(/demo-fixture-value-should-be-redacted/);
    expect(screen.getByText(/credential-like value redacted/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Investigation/ }));
  });

  it("an answer with an unknown action renders with a diagnostic instead of crashing", async () => {
    const files = fixtureFiles();
    const a = clone(files["cases/DEMO-002.json"]) as { next_best_actions: { initial: { action: string }[]; final: { action: string }[] } };
    a.next_best_actions.initial[0]!.action = "FREEZE_EVERYTHING";
    a.next_best_actions.final[0]!.action = "FREEZE_EVERYTHING";
    files["cases/DEMO-002.json"] = a;
    const user = userEvent.setup();
    renderAt("#/case/DEMO-002?tab=actions", new DataClient("./data", memoryFetch(files)));
    expect((await screen.findAllByText("FREEZE_EVERYTHING")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("not a policy action").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("tab", { name: /Raw JSON/ }));
    expect(screen.getByText("invalid")).toBeInTheDocument();
  });
});

describe("routing", () => {
  it("unknown case IDs offer a way back", async () => {
    renderAt("#/case/HHG-999");
    expect(await screen.findByText("Case HHG-999 is not in the synchronized data")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the overview" })).toBeInTheDocument();
  });

  it("unknown routes render the not-found page", async () => {
    renderAt("#/definitely/not/here");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });

  it("how it works explains the honesty rules", async () => {
    renderAt("#/how-it-works");
    expect(await screen.findByText("Honesty rules")).toBeInTheDocument();
    expect(screen.getByText(/Missing means missing/)).toBeInTheDocument();
  });

  it("previous/next navigation moves between cases", async () => {
    const user = userEvent.setup();
    renderAt("#/case/DEMO-002");
    await user.click(await screen.findByRole("link", { name: "Next case DEMO-003" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "DEMO-003" })).toBeInTheDocument());
  });
});
