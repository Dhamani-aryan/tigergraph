# TigerGraph Agentic Fraud Investigation Agent — Design

Date: 2026-09-22
Deadline: 2026-09-24 23:59 IST (~48 hours from design time)
Hackathon: TigerGraph Hacker House Goa — HHGOA_IEEE fraud investigation challenge

## 1. Goal

Build an agent that, for each of the 20 cases in `case_pack.csv`, investigates using
TigerGraph (graph + vector/GraphRAG) and closed-case memory, decides a fraud verdict,
pattern, and probability, recommends next-best actions under the bank's fraud policy
(with approval routing), writes a suspicious activity report when the policy requires
one, writes the case back into the graph as memory, and emits one answer JSON file per
case matching the README's exact schema. A Streamlit dashboard demonstrates case
progression, evidence, and recommendations for judges.

Full task spec, fraud policy (rules R1–R10), answer JSON schema, and worked example are
in [`README.md`](../../../README.md) — that document is the ground truth for output
format; this design covers **how we build the system that produces it**.

## 2. Constraints that shaped this design

- **~48 hours, one person doing the technical build.** Every design choice below
  favors "fast to build and debug" over "architecturally ideal."
- **No paid LLM API.** Reasoning runs on a local Ollama model (`qwen3:4b-instruct`,
  already pulled) on a laptop with 16GB RAM / 4GB VRAM (RTX 3050 Ti). This is a small,
  imperfect model — the architecture compensates by giving it narrow, structured
  tasks (classify, extract, write prose from given facts) instead of open-ended
  multi-step decision-making, and validates/retries its JSON output.
- **Embeddings are local** (`nomic-embed-text`, already pulled) — no API key needed.
- **TigerGraph Savanna**, empty workspace (not the pre-loaded fraud demo — different
  schema, unrelated to this dataset).
- Investigation accuracy + next-best-action quality are 50% of the score, so
  correctness of policy application matters more than architectural elegance anywhere
  else in the system.

## 3. Graph schema

Base schema is the README's suggested one; two vertex types added for case memory and
GraphRAG.

**Vertices**
| Vertex | Key attributes | Notes |
|---|---|---|
| `Customer` | `customer_id` | |
| `Card` | `card_id` | e.g. `C01234-K1` |
| `Transaction` | `transaction_id`, plus all ~393 original Vesta columns, `ts`, `channel`, `risk_score` | Attribute list generated programmatically from the CSV header at schema-creation time — not hand-typed |
| `DeviceProfile` | `device_id` (derived: hash of DeviceInfo+id_30+id_31+id_33), `device_info`, `os`, `browser`, `screen` | One profile can be shared by many transactions/cards |
| `EmailDomain` | `domain` | |
| `BillingRegion` | `addr1` | |
| `ClosedCase` | all `closed_cases_history.csv` columns | Historical memory, immutable |
| `Case` **(new)** | mirrors `ClosedCase` shape plus `fraud_probability`, `status`, `written_at`, `embedding` | Cases *this agent* creates during the run — structurally identical to `ClosedCase` so retrieval can't distinguish them |
| `KnowledgeDoc` **(new)** | `doc_id`, `source` (`policy`\|`pattern`\|`regulatory`), `section`, `text`, `embedding` | Chunked policy text, the 5 pattern descriptions, and regulatory PDF chunks |

**Edges**
`Customer-OWNS->Card`, `Card-MADE->Transaction`, `Transaction-FROM_DEVICE->DeviceProfile`
(online only), `Transaction-PURCHASER_EMAIL->EmailDomain`, `Transaction-BILLED_IN->BillingRegion`,
`Transaction-NEXT->Transaction` (per-card chronological chain), `ClosedCase-INVOLVES->Transaction`,
`ClosedCase-ON_CARD->Card`, `ClosedCase-CONNECTED_TO->Card`, and the same
`INVOLVES`/`ON_CARD`/`CONNECTED_TO` edges from `Case`.

## 4. Data loading

- Schema created via GSQL (`tigergraph__gsql` / `create_graph` MCP tools), attribute
  list for `Transaction` generated from the CSV header programmatically.
- Bulk load via GSQL `LOADING JOB` against the local CSVs (not row-by-row API calls —
  `transactions.csv` is 590K rows / 708MB, too slow otherwise).
- Derive `DeviceProfile` and `EmailDomain` and `BillingRegion` vertices and edges
  during/after load (dedup keys computed in the loading job or a post-load GSQL step).
- `closed_cases_history.csv` loaded as `ClosedCase` + edges from its `txn_ids` /
  `connected_card_ids` pipe-separated fields.

## 5. GraphRAG / vector store

One-time ingestion pass (before any case is processed):
1. Chunk and embed the fraud policy (by rule: R1–R10, each action, each approval-route
   row) and the 5 known-pattern descriptions → `KnowledgeDoc` (`source=policy`/`pattern`).
2. Download and chunk the 15 regulatory PDFs (FinCEN/FATF/FFIEC/OFAC) by section →
   `KnowledgeDoc` (`source=regulatory`). Parsed with a PDF text extraction library;
   chunked at a size sensible for the embedding model (roughly paragraph-level).
3. Embed every `ClosedCase.analyst_notes` (+ a synthesized structured summary: pattern,
   outcome, exposure) → stored as the `embedding` attribute on `ClosedCase`.

All embeddings computed via `nomic-embed-text` locally, upserted through the MCP
`upsert_vectors` tool. Retrieval is hybrid: `search_top_k_similarity` for narrative/text
matches (similar past cases, relevant policy/regulatory passages) combined with direct
graph traversal for structural evidence (shared device/region/card connections) — the
GraphRAG requirement is satisfied by *combining* both, not vector search alone.

## 6. Agent architecture

LangGraph state machine, one run per case pack row, following the README's 8-step flow:

1. **Trigger** — load the case-pack row.
2. **Gather evidence** — bounded tool-calling loop (hard cap ~6-8 iterations, since a
   small model can loop). Tools: `card_window`, `customer_cards`, `device_neighbors`,
   `region_neighbors`, `closed_case_lookup` (structural graph queries), and
   `retrieve_knowledge(query)` (vector search over `KnowledgeDoc` + `ClosedCase`).
3. **Assess** — LLM call constrained to a Pydantic schema (pattern, probability,
   evidence claims, `similar_prior_cases`); validated and retried on schema failure.
4. **Stopping check** — deterministic code applying README §6 (probability ≥0.85 or
   ≤0.15 with ≥2 independent evidence pieces → stop; else continue).
5. **Evidence request + simulated response** — when R1 or another rule calls for it,
   request `customer_validation`/`step_up_auth`/`analyst_info`. A separate,
   rule-based **simulator module** (not the investigating agent) produces
   `assumed_response` from the actual graph data (e.g. does the flagged transaction
   fit this customer's historical pattern?) — logged plainly as an assumption per
   README §5. Then re-run step 3 with the new evidence.
6. **Policy engine** — deterministic Python encoding the full action table, approval
   routes, and rules R1–R10 (including §3a case-vs-report and §3b initial-vs-final
   tracking). Takes structured findings in, returns exact action identifiers +
   routes + rule citations out. The LLM never invents action names.
7. **Explain** — LLM writes `summary` and, when required, the SAR `narrative`, from
   the already-decided structured facts (a safer task for a small model than deciding
   the facts themselves).
8. **Write to graph + memory** — create the `Case` vertex + edges, upsert its
   embedding, so later case-pack cases can retrieve it exactly like a `ClosedCase`.

## 7. Output generation

- Pydantic models mirror the README's answer schema exactly (top level + `case` +
  `sar` + `next_best_actions`), used both to constrain LLM output and to validate the
  final JSON before writing `cases/<case_id>.json`.
- `tool_calls`, `tokens`, `latency_s` tracked by instrumenting the LangGraph run.
- A validation script checks all 20 output files against the schema and cross-checks
  that every referenced ID actually exists in the dataset before submission.

## 8. UI

Streamlit dashboard (pure Python, fastest to build solo): case list (20 cases) with
verdict/pattern/risk; per-case detail view showing the evidence timeline, retrieved
similar prior cases, initial vs. final recommended actions with rule citations, and
the SAR narrative when filed. Reads the generated JSON files directly (source of
truth for judging) plus live TigerGraph queries for graph snippets. No auth, no
persistence beyond the JSON files and the graph itself — this is a demo surface, not
a production app.

## 9. Build order (rough time budget)

1. Graph schema + bulk load (transactions, identity, closed cases) — get counts
   verified.
2. Investigate **one case by hand** via direct GSQL/MCP queries before writing agent
   code (README's own advice) — this validates the schema is actually queryable the
   way the agent will need.
3. Knowledge ingestion pass (policy, patterns, regulatory PDFs, closed-case
   embeddings).
4. Policy engine (pure Python, unit-testable against the README's worked example and
   rules R1–R10 independent of the LLM/graph).
5. LangGraph agent + tools, tested end-to-end on the one hand-investigated case first.
6. Run all 20 cases, validate output schema, spot-check a few against the policy by
   hand.
7. Streamlit dashboard.
8. Demo video, blog post, social post (can run in parallel with later build steps).

## 10. Explicit scope cuts / risks (for the blog post's "what we'd improve")

- LLM is a small local model chosen for zero cost, not the best available reasoning
  model — mitigated architecturally (narrow LLM tasks, deterministic policy layer,
  schema validation+retry) but real accuracy risk remains on nuanced pattern
  judgment calls. Escalation path if this proves insufficient during testing:
  pull a larger local model (e.g. `qwen2.5:7b-instruct`) or move inference to a
  free-tier Colab GPU.
- Customer/analyst evidence-request responses are simulated by a rule-based module
  against real graph data, not a second LLM persona or real humans — reasonable per
  README §5, but the simulator's assumptions are a modeling choice worth stating
  plainly in the blog post.
- Regulatory PDF ingestion is automated text extraction, not manually curated
  summaries — chunk quality depends on how cleanly each PDF's text extracts.

## 11. Deliverables checklist (mapped to submission requirements)

- [ ] Working agent (this repo)
- [ ] `cases/HHG-001.json` … `cases/HHG-020.json`, schema-validated
- [ ] Cases written into the graph (`written_to_graph`/`graph_case_id` populated)
- [ ] SAR included where policy requires
- [ ] Initial vs. final next-best-action recorded for every case
- [ ] 3–5 min demo video
- [ ] Technical blog post (architecture, TigerGraph usage, agentic capabilities,
      learnings, improvements)
- [ ] Social post tagging @TigerGraphDB
