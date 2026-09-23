# FraudGraph Investigator: Frontend Build Spec

**Owner:** Aryan (frontend) · **Backend owner:** Naman
**Repo:** `Namans12/tigergraph`. Work on branch `ui`, and open PRs into `tigergraph-fraud-agent`.
**Hard deadline:** 2026-09-24 23:59 IST. **UI feature freeze:** 2026-09-24 18:00 IST. After that, only bug fixes and swapping in the real data.

---

## 0. What you are building (read this first)

A **case-investigation console** that judges can click through. It shows, for each of the 20 cases:

1. what the agent concluded,
2. **how** it got there: graph tools called, evidence, probability moving, policy rules fired,
3. what it recommends before and after asking for evidence,
4. the SAR (regulatory report) when one is filed,
5. that the case was written back into TigerGraph as memory.

**Hard rule: the UI never talks to TigerGraph or the LLM.** It only reads JSON files the backend produces, which means:
- you can start **now**, using the mock data in this doc,
- the backend can change internally without breaking you,
- the deployed site is a static link judges can open with no server.

The JSON contract in §3 is **frozen**. If you need a new field, ask Naman. Don't invent one and don't read anything else.

---

## 1. Tech stack (fixed, so there's no debate)

| Concern | Choice | Why |
|---|---|---|
| Build | **Vite + React 18 + TypeScript** | Fast; static output |
| Styling | **Tailwind CSS** + **shadcn/ui** components | Polished fast |
| Graph viz | **Cytoscape.js** (`react-cytoscapejs`), layout `cose-bilkent` or `fcose` | Handles 200+ nodes, click events |
| Charts | **Recharts** | Probability timeline, KPI bars |
| Routing | **react-router** (hash router, so static hosting works) | `/#/case/HHG-017` |
| Icons | `lucide-react` | Ships with shadcn |
| Deploy | **Vercel** (free) or GitHub Pages | Public link for the submission form |

Folder: everything lives in `ui/` at the repo root. Don't touch `src/`, `cases/`, `runs/`.

```
ui/
  package.json
  scripts/sync-data.mjs      # copies ../cases/*.json and ../runs/latest/** into public/data/
  public/data/
    batch_summary.json
    cases/HHG-001.json ... HHG-020.json
    traces/HHG-001.trace.json ... HHG-020.trace.json
  src/
    types.ts                 # TS types = §3, copied exactly
    data.ts                  # fetch + cache helpers; a missing trace file must not crash
    pages/Overview.tsx
    pages/CaseDetail.tsx
    pages/HowItWorks.tsx
    components/...
```

`npm run sync-data` copies the backend output into `public/data/`. `npm run dev` / `npm run build` work as usual.

---

## 2. Screens

### 2.1 Overview (`/#/`): "The 20-case board"

**Top KPI strip.** Everything comes from `batch_summary.json`:
- Verdict split: fraud / legitimate / uncertain (a donut or stacked bar). *Aim to show balance: the README says about half the cases are legitimate.*
- SARs filed (count), total exposure (USD), cases whose actions changed after evidence (count).
- Total tool calls, total tokens, average latency.
- **Validation badge**: "20/20 answer files valid", green or red.
- **Case memory badge**: "20/20 written to TigerGraph (read-back verified)".

**Case table.** One row per case, sortable and filterable:

| Column | Source |
|---|---|
| Case ID (link) | `case_id` |
| Trigger | `trigger_type` chip (risk_score / customer_report / analyst_request) |
| Verdict | colored chip |
| Probability | small bar, 0–1 |
| Pattern | chip |
| Exposure | USD |
| Initial → Final | first action of each, with an arrow; highlight when `actions_changed` |
| SAR | ✓ / — |
| Memory | ✓ if `written_to_graph` |

Filters: verdict, pattern, trigger type, SAR yes/no, "actions changed". Search by case/card/customer ID.

### 2.2 Case detail (`/#/case/:caseId`): the core screen

**Header (always visible):**
- Case ID, customer, card, opened_at, trigger text (quote style).
- Big verdict chip + probability gauge + pattern chip + status chip.
- Exposure (USD), SAR filed yes/no, "Written to TigerGraph as `graph_case_id`" with a read-back tick.
- Prev/next case arrows.

**Tabs:**

1. **Investigation**: the agent story, top to bottom:
   - A **step timeline** from `trace.steps[]`. Each step is a card with: node label, duration, and the tool calls made (tool name, args, result count, `via: mcp`). Collapsible.
   - A **probability timeline chart** from `trace.probability_timeline[]`: a line from initial to final assessment, with the evidence request marked as a vertical line labelled with its type and assumed response.
   - `stop_reason` shown at the bottom as the "why it stopped" callout.
   - **Signals** list from `trace.signals[]`: fired signals in red or amber, non-fired ones greyed out. *Judges should see that the agent checked things and decided they didn't apply.*

2. **Evidence**: `case.evidence[]` as cards:
   - claim text, a source badge (graph / document / customer / external), and `ref` in mono font,
   - `entity_ids` as chips. **Clicking a chip switches to the Graph tab with that node highlighted.**
   - Below the cards, **Retrieved memory** from `trace.retrieval`: prior closed cases (ID, outcome, pattern, similarity score, used ✓) and regulatory/policy documents (title, section, score). This is the GraphRAG proof, so make it visible.

3. **Graph**: Cytoscape view of `trace.subgraph`.
   - Node color by `type`, border or glow by `role` (§3.3 table).
   - Click a node for a side panel with its `attrs` (a transaction shows amount, ts, channel, product, risk_score, region).
   - Legend. Toggles: hide context nodes, show only affected episode.
   - Affected transactions are drawn in red and linked in time order (the NEXT edges), so the fraud episode reads left to right.

4. **Actions**: the policy story.
   - **Two columns: Initial | Final.** Each action is a row: action name, route badge (`auto` green, `L1` amber, `L2` red), and reason (the rule cited, e.g. "R1: …").
   - Between the columns sits an **Evidence request card**: `evidence_requests[]` (type, asked after step N, assumed response). If the list is empty, show "No evidence requested, final = initial".
   - Diff highlighting: actions added in Final show a green "+", actions dropped show a strikethrough.
   - `what_changed` as a callout.
   - **Rules fired** from `trace.rules_fired[]`, grouped by phase (initial/final), with a short explanation each.

5. **SAR**:
   - If `sar.file` is true, render it as a **formal document**: header "Suspicious Activity Report", subjects, total amount, activity date range, narrative in readable serif, and the reason (rule). Add a "Print / Save PDF" button (`window.print()` with print CSS).
   - If false, a muted panel: "No SAR filed", plus `sar.reason`.

6. **Raw JSON**: pretty-printed answer file with copy and download buttons. Also a **validation panel** from `trace.validation` (passed ✓, or the errors list).

### 2.3 How it works (`/#/how-it-works`): for judges

A static page. Make it look good; it's the first thing judges will read. Content:

- A **pipeline diagram** (SVG or React Flow), in this order: Case pack → TigerGraph Savanna (graph loaded: 590,742 txns, 13.5k customers/cards, 5,565 closed cases) → GSQL installed queries + graph algorithm (connected components / shared-origin rings) → **TigerGraph MCP** (the agent calls graph tools) → **GraphRAG** (vector search over closed cases, fraud policy and FinCEN/FATF/FFIEC docs stored in TigerGraph vector attributes) → LangGraph agent (gather → follow-up → assess → request evidence → reassess) → deterministic policy engine R1–R10 → SAR writer → answer file + **case written back into the graph as memory**.
- A "Required components" checklist mapping each hackathon requirement to where it lives: Savanna, GSQL + graph algorithms, MCP, GraphRAG, UI.
- **Honesty panel**: "Customer replies are simulated (README §5); assumptions are shown on every case", "Evidence is time-bounded to the case open time (no future data)", "Risk score is treated as input, never a verdict".
- Tech stack and team.

Copy for this page: Naman will send the final numbers by 2026-09-24 12:00 IST. Use the numbers above until then.

### 2.4 (Stretch, only if everything above is done by 2026-09-24 14:00 IST) Replay mode

A "▶ Replay investigation" button on Case detail that animates `trace.steps[]` one by one, about 1s each: tool calls appear, graph nodes fade in as they're discovered (use `node.discovered_at_step`), and the probability line draws. **Purely client-side from the trace file; no live backend.** It looks live in the demo video and carries zero risk.

---

## 3. Data contract (frozen)

### 3.1 `cases/HHG-XXX.json`: the answer file

This is **exactly** the README "Answer Format" schema. TypeScript:

```ts
type Pattern = "card_testing" | "card_not_present_fraud" | "card_not_present_new_device"
  | "out_of_region_use" | "account_takeover" | "undocumented" | "none";
type Verdict = "fraud" | "legitimate" | "uncertain";
type Status = "open" | "closed_fraud" | "closed_legitimate" | "escalated";
type Route = "auto" | "L1" | "L2";
type Action = "ALLOW_TRANSACTION" | "DECLINE_TRANSACTION" | "MONITOR_CARD" | "MONITOR_CONNECTED_CARDS"
  | "WARN_CUSTOMER" | "VERIFY_WITH_CUSTOMER" | "STEP_UP_AUTH" | "BLOCK_CARD" | "BLOCK_ALL_CARDS"
  | "GENERATE_REPORT" | "CREATE_CASE" | "FILE_REPORT" | "ESCALATE_TO_ANALYST" | "CLOSE_NO_FRAUD";

interface Evidence { claim: string; source: "graph" | "document" | "customer" | "external"; ref: string; entity_ids: string[]; }
interface ActionEntry { action: Action; route: Route; reason: string; }
interface AnswerFile {
  case_id: string;
  case: {
    status: Status; verdict: Verdict; fraud_probability: number; pattern: Pattern;
    pattern_description: string; affected_txn_ids: string[]; first_suspicious_txn_id: string;
    connected_card_ids: string[]; connected_device_profiles: string[]; exposure_usd: number;
    evidence: Evidence[]; similar_prior_cases: string[]; summary: string;
    written_to_graph: boolean; graph_case_id: string;
  };
  evidence_requests: { type: "customer_validation" | "step_up_auth" | "analyst_info"; asked_after_step: number; assumed_response: string; }[];
  next_best_actions: { initial: ActionEntry[]; final: ActionEntry[]; what_changed: string; };
  sar: { file: boolean; reason: string; narrative: string; subjects: string[]; total_amount_usd: number; activity_dates: string[]; };
  stop_reason: string; tool_calls: number; tokens: number; latency_s: number;
}
```

Mock: the README's HHG-017 example (README.md, "Example" section) is a valid file. Use it as `HHG-017.json`, then clone and vary it for 3–4 more mocks: one `legitimate` (empty lists, exposure 0, no SAR), one `uncertain` with ESCALATE_TO_ANALYST, and one `undocumented` with `pattern_description`.

### 3.2 `runs/latest/batch_summary.json`

```json
{
  "run_id": "2026-09-24T10-12-00Z",
  "generated_at": "2026-09-24T10:31:44Z",
  "git_commit": "abc1234",
  "llm": { "provider": "groq", "model": "openai/gpt-oss-120b" },
  "cases_total": 20,
  "cases_valid": 20,
  "cases_written_to_graph": 20,
  "verdicts": { "fraud": 8, "legitimate": 9, "uncertain": 3 },
  "patterns": { "card_testing": 1, "card_not_present_fraud": 3, "none": 9, "undocumented": 1 },
  "sar_filed": 5,
  "actions_changed": 11,
  "total_exposure_usd": 4210.55,
  "total_tool_calls": 214,
  "total_tokens": 251330,
  "avg_latency_s": 21.4,
  "cases": [
    {
      "case_id": "HHG-017", "trigger_type": "risk_score", "customer_id": "C04570", "card_id": "C04570-K1",
      "opened_at": "2016-11-12 00:46:24",
      "verdict": "fraud", "fraud_probability": 0.86, "pattern": "card_not_present_new_device", "status": "closed_fraud",
      "exposure_usd": 268.43, "sar_file": true,
      "initial_actions": ["VERIFY_WITH_CUSTOMER", "CREATE_CASE"],
      "final_actions": ["BLOCK_CARD", "CREATE_CASE", "FILE_REPORT"],
      "actions_changed": true, "validation_passed": true, "written_to_graph": true, "latency_s": 18.7
    }
  ]
}
```

### 3.3 `runs/latest/traces/HHG-XXX.trace.json`: how the agent got there

```json
{
  "schema_version": "1.0",
  "case_id": "HHG-017",
  "trigger": { "trigger_type": "risk_score", "trigger_text": "...", "flagged_txn_id": "3450629",
               "card_id": "C04570-K1", "customer_id": "C04570", "risk_score": 0.57, "opened_at": "2016-11-12 00:46:24" },
  "cutoff_ts": "2016-11-12 00:46:24",
  "steps": [
    {
      "step": 1, "node": "gather_evidence", "label": "Gather graph evidence",
      "started_ms": 0, "duration_ms": 4210,
      "tool_calls": [
        { "tool": "card_window", "args": { "card_id": "C04570-K1", "hours": 48 }, "via": "mcp",
          "result_count": 7, "duration_ms": 812 }
      ],
      "summary": "7 txns in 48h window; device shared with 3 other cards"
    },
    { "step": 2, "node": "agentic_followup", "label": "Agent chose a follow-up tool", "started_ms": 4210, "duration_ms": 2300,
      "tool_calls": [ { "tool": "wider_card_window", "args": { "hours": 168 }, "via": "mcp", "result_count": 19, "duration_ms": 990 } ],
      "summary": "LLM requested a 7-day window because the 48h window looked incomplete" },
    { "step": 3, "node": "assess", "label": "Initial assessment", "started_ms": 6510, "duration_ms": 3100, "tool_calls": [],
      "summary": "p=0.45, single signal" },
    { "step": 4, "node": "request_evidence", "label": "Asked customer to verify", "started_ms": 9610, "duration_ms": 1200, "tool_calls": [],
      "summary": "Simulated reply: customer denies" },
    { "step": 5, "node": "reassess", "label": "Reassessment", "started_ms": 10810, "duration_ms": 2900, "tool_calls": [], "summary": "p=0.86" },
    { "step": 6, "node": "policy", "label": "Policy R1-R10 applied", "started_ms": 13710, "duration_ms": 40, "tool_calls": [], "summary": "" },
    { "step": 7, "node": "write_memory", "label": "Case written to TigerGraph", "started_ms": 13750, "duration_ms": 1900,
      "tool_calls": [ { "tool": "add_nodes", "args": { "vertex_type": "FraudCase" }, "via": "mcp", "result_count": 1, "duration_ms": 700 } ],
      "summary": "CASE-HHG-017 written and read back" }
  ],
  "probability_timeline": [
    { "step": 3, "label": "Initial assessment", "fraud_probability": 0.45 },
    { "step": 5, "label": "After customer_validation", "fraud_probability": 0.86 }
  ],
  "signals": [
    { "name": "card_testing_sequence", "fired": false, "detail": "No >=3 sub-$5 online auths within 1h", "entity_ids": [] },
    { "name": "new_device", "fired": true, "detail": "id_15 = New for this account", "entity_ids": ["3450629"] },
    { "name": "shared_device", "fired": true, "detail": "Device profile used by 3 other cards before cutoff", "entity_ids": ["C00877-K1"] },
    { "name": "out_of_region", "fired": false, "detail": "Billing region 204 is home region", "entity_ids": [] }
  ],
  "rules_fired": [
    { "rule": "R1", "phase": "initial", "explanation": "Single signal, p<0.70 -> verify before block" },
    { "rule": "R2", "phase": "final", "explanation": "Customer denied -> BLOCK_CARD + CREATE_CASE" }
  ],
  "retrieval": {
    "prior_cases": [ { "case_id": "CC-0141", "outcome": "confirmed_fraud", "pattern": "card_testing", "score": 0.83, "used": true } ],
    "documents": [ { "doc_id": "fincen-sar-narrative-12", "title": "SAR Narrative Guidance", "section": "Who/What/When", "score": 0.71 } ]
  },
  "subgraph": {
    "nodes": [
      { "id": "C04570", "type": "Customer", "label": "C04570", "role": "subject", "discovered_at_step": 1, "attrs": {} },
      { "id": "C04570-K1", "type": "Card", "label": "C04570-K1", "role": "subject", "discovered_at_step": 1, "attrs": {} },
      { "id": "3450629", "type": "Transaction", "label": "$100.09", "role": "flagged", "discovered_at_step": 1,
        "attrs": { "ts": "2016-11-11 23:46:00", "amount": 100.09, "channel": "online", "product_cd": "C", "risk_score": 0.57, "addr1": "204" } },
      { "id": "DEV-8f21", "type": "DeviceProfile", "label": "SAMSUNG SM-G892A | Android 7.0 | ...", "role": "connected", "discovered_at_step": 1, "attrs": {} },
      { "id": "C00877-K1", "type": "Card", "label": "C00877-K1", "role": "connected", "discovered_at_step": 1, "attrs": {} },
      { "id": "CC-0141", "type": "ClosedCase", "label": "CC-0141", "role": "prior_case", "discovered_at_step": 1, "attrs": { "outcome": "confirmed_fraud" } },
      { "id": "CASE-HHG-017", "type": "FraudCase", "label": "CASE-HHG-017", "role": "this_case", "discovered_at_step": 7, "attrs": {} }
    ],
    "edges": [
      { "source": "C04570", "target": "C04570-K1", "type": "OWNS" },
      { "source": "C04570-K1", "target": "3450629", "type": "MADE" },
      { "source": "3450629", "target": "DEV-8f21", "type": "FROM_DEVICE" }
    ]
  },
  "graph_write": { "written": true, "graph_case_id": "CASE-HHG-017", "read_back_ok": true, "written_at": "2026-09-24T10:14:02Z" },
  "validation": { "passed": true, "errors": [], "warnings": [] },
  "llm": { "provider": "groq", "model": "openai/gpt-oss-120b", "tokens": 12480 }
}
```

**Node `type`** → color: `Customer` · `Card` · `Transaction` · `DeviceProfile` · `BillingRegion` · `EmailDomain` · `ClosedCase` · `FraudCase`.
**Node `role`** → emphasis:

| role | meaning | style |
|---|---|---|
| `subject` | the case's own customer/card | thick border |
| `flagged` | the alerted transaction | pulsing ring |
| `affected` | in the fraud episode | red fill |
| `connected` | other cards/devices linked to it | amber |
| `prior_case` | retrieved closed case | purple |
| `this_case` | the case vertex we wrote | green |
| `context` | everything else | grey, hideable |

Rules for the data layer:
- Subgraphs are capped at about 250 nodes by the backend. Don't assume fewer than 10.
- Transaction `id`s are numeric strings. Don't parse them as numbers.
- A trace file may be missing for a case. The page must still render from the answer file alone, with a "trace unavailable" note on the Investigation and Graph tabs.
- `steps[].node` values you'll see: `gather_evidence`, `agentic_followup`, `apply_followup`, `assess`, `request_evidence`, `reassess`, `policy`, `write_memory`, `sar`. Render unknown ones generically.

---

## 4. Edge cases the UI must handle

- **Legitimate verdict**: `affected_txn_ids` is empty, exposure is 0, and there is no SAR. Show a green "Closed: no fraud" state, not empty boxes.
- **`uncertain`**: show "Escalated to analyst" prominently.
- **No evidence requests**: `initial == final`. Show one column or a "no change" state.
- **`pattern: "undocumented"`**: show `pattern_description` as a highlighted "New pattern found" callout. This is scored, so make it pop.
- **`written_to_graph: false`**: red warning banner. This should never happen in the final run, but don't hide it.
- **Long SAR narratives** (6–12 sentences): readable width, around 70ch.
- **Long device profile strings**: truncate with a tooltip.

---

## 5. Look and feel

- Dark theme by default (dashboard feel), with a light toggle. Base: slate/zinc greys. One accent (TigerGraph orange `#F68B1F` works well).
- Verdict colors: fraud **red-500**, legitimate **emerald-500**, uncertain **amber-500**.
- Route badges: `auto` green outline, `L1` amber, `L2` red solid.
- Mono font for IDs, refs and tool args (JetBrains Mono / `font-mono`).
- It must look right at 1440×900 (the demo video resolution). Mobile is nice to have only.

---

## 6. Milestones (times in IST)

| By | Deliverable |
|---|---|
| 2026-09-24 02:00 | Scaffold, types, mocks loaded, Overview table renders |
| 2026-09-24 09:00 | Case detail: header, Evidence, Actions, SAR, Raw JSON tabs |
| 2026-09-24 12:00 | Graph tab (Cytoscape) + Investigation tab (timeline + chart) |
| 2026-09-24 12:00 | **Naman drops real data** into `cases/` + `runs/latest/`. Run `sync-data` and fix mismatches |
| 2026-09-24 15:00 | How it works page, polish, deployed to Vercel with a public URL |
| 2026-09-24 18:00 | **Feature freeze.** Only bug fixes. Record demo screen captures |
| 2026-09-24 22:00 | Final data sync from the tagged submission commit, redeploy |

If behind schedule, cut in this order: Replay mode → light theme → filters → Graph tab toggles. **Never cut:** Actions initial/final, SAR, Evidence + retrieved memory, Graph view.

---

## 7. Acceptance checklist (Naman will check these)

- [ ] `npm run sync-data && npm run build` works from a clean clone. The site works from the static `dist/` output.
- [ ] All 20 cases open with no console errors, including ones with missing traces.
- [ ] Overview KPIs match `batch_summary.json` exactly.
- [ ] Every evidence entity chip that exists in the subgraph highlights the node.
- [ ] Initial vs final diff is correct for every case that has `actions_changed: true`.
- [ ] SAR renders for every `sar.file: true` and prints cleanly.
- [ ] `undocumented` pattern callout visible.
- [ ] How it works page lists all five required components.
- [ ] Public URL shared in the team chat.

---

## 8. Working agreement

- Don't change any file outside `ui/`. If the backend JSON looks wrong, tell Naman with the case ID and field. Don't patch around it in the UI.
- Contract changes happen only by agreement, recorded here as a version bump (`schema_version`).
- PRs go from `ui` into `tigergraph-fraud-agent`, small and often. Naman merges.
