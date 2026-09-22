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
- **No paid LLM API — but free hosted APIs are fine.** Reasoning runs on **Groq's
  free tier** (`llama-3.3-70b-versatile`, OpenAI-compatible endpoint, no cost, rate-limited
  not metered) rather than a local model — materially stronger reasoning at zero cost,
  which matters because investigation accuracy + next-best-action are 50% of the score.
  A local Ollama model (`qwen3:4b-instruct`, already pulled) remains as a config-toggle
  fallback (`LLM_BACKEND=ollama`) if Groq's free-tier rate limits prove too tight for a
  full 20-case run. Either way, the architecture still gives the LLM narrow, structured
  tasks (classify, extract, write prose from given facts) rather than open-ended
  multi-step decision-making, and validates/retries its JSON output — this discipline
  pays off regardless of which backend is faster.
- **Embeddings are local** (`nomic-embed-text`, already pulled) — no API key needed,
  and embedding 5,565+ closed-case narratives locally avoids burning any hosted API's
  rate limit on a bulk one-time job.
- **TigerGraph Savanna**, empty workspace (not the pre-loaded fraud demo — different
  schema, unrelated to this dataset).
- Investigation accuracy + next-best-action quality are 50% of the score, so
  correctness of policy application matters more than architectural elegance anywhere
  else in the system.
- **Git workflow:** local commits only, one per completed task, as the plan proceeds.
  No push to a remote until the full 20-case run is validated and the submission
  checklist (§11) is otherwise ready.

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
`INVOLVES`/`ON_CARD`/`CONNECTED_TO` edges from `Case`. Plus **`Card-SHARES_ORIGIN-Card`**
(undirected, both directions inserted): a projected edge between two `Card`s that share
a `DeviceProfile`, `BillingRegion`, or `EmailDomain`, built in a post-load pass —
this is what the graph algorithm in §3a runs over. `Card` also gets two extra
attributes: `ring_cluster_id` and `cluster_prior_fraud_rate` (both written by that pass).

### 3a. Graph algorithms (required component, not optional)

The README's required-components list names *"GSQL and TigerGraph graph algorithms"*
explicitly — point queries alone don't satisfy that. We run **Connected Components**
once, in batch, over the `SHARES_ORIGIN` projection: every `Card` gets a `ring_cluster_id`
identifying which cluster of cards it belongs to (cards linked by any chain of shared
device/region/email), and every cluster gets a `cluster_prior_fraud_rate` — the fraction
of `ClosedCase` rows on cards in that cluster that were `confirmed_fraud`. This turns
"does this card connect to fraud elsewhere" from a live multi-hop traversal repeated per
case into an O(1) lookup, and gives a genuinely new signal (a cluster-level prior) that
point queries alone don't produce — directly useful for R6 (shared origin) and R9
(undocumented, coordinated abuse), and a real differentiator versus an agent that only
does point lookups. Implemented as a GSQL query using standard label-propagation
(iteratively take the minimum `ring_cluster_id` across `SHARES_ORIGIN` neighbors until
no card's label changes); TigerGraph's packaged GDS algorithm library is tried first if
available on this Savanna instance, with the hand-written query as the fallback either
way — see the plan's graph-algorithms task for the concrete GSQL.

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
2. **Gather evidence (deterministic core)** — Python always runs a fixed set of graph
   queries for every case: `card_window`, `customer_cards`, `device_neighbors`,
   `region_neighbors`, `closed_case_lookup`, `ring_membership` (the §3a cluster
   lookup), and `retrieve_knowledge(query)` (vector search over `KnowledgeDoc` +
   `ClosedCase` + `Case`, i.e. including cases this run has already written — see §6
   step 8). This is not LLM-driven tool selection; it's the same evidence pass every
   time, which is what makes a small/rate-limited model's job tractable.
2a. **Gather evidence (bounded agentic round)** — after the deterministic pass, the
   LLM is given one real function-calling turn (native tool-calling against the
   hosted model, not prose-parsing) with a small menu of the same query functions,
   parameterized differently (e.g. a wider `region_neighbors` window, or
   `closed_case_lookup` keyed on a device instead of a card). It may call **at most
   one** additional tool, only when it judges the deterministic evidence ambiguous —
   this is the genuinely agentic piece of the flow: the LLM decides *whether* and
   *what* to look up next, bounded so it can't loop indefinitely.
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
8. **Write to graph + memory** — create the `Case` vertex + edges, **and embed and
   upsert its summary as a vector immediately** (not deferred to a later batch job),
   so a later case-pack case in the *same run* can retrieve it via `retrieve_knowledge`
   exactly like a pre-loaded `ClosedCase` — this is what makes "case memory" real
   within the 20-case run itself, not just against pre-existing history.

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
2. Derived entities (`DeviceProfile`, pipe-separated edges) + **graph algorithm pass**
   (§3a Connected Components → `ring_cluster_id`/`cluster_prior_fraud_rate`).
3. Investigate **one case by hand** via direct GSQL/MCP queries before writing agent
   code (README's own advice) — this validates the schema is actually queryable the
   way the agent will need, including the new ring-cluster lookup.
4. Knowledge ingestion pass (policy, patterns, regulatory PDFs, closed-case
   embeddings).
5. Policy engine (pure Python, unit-testable against the README's worked example and
   rules R1–R10 independent of the LLM/graph).
6. LangGraph agent + tools (deterministic evidence pass, bounded agentic tool-choice
   round, Groq-backed assessment/explanation), tested end-to-end on the one
   hand-investigated case first.
7. Run all 20 cases, validate output schema, spot-check a few against the policy by
   hand.
8. Streamlit dashboard.
9. Demo video, blog post, social post (can run in parallel with later build steps).

## 10. Explicit scope cuts / risks (for the blog post's "what we'd improve")

- LLM reasoning runs on Groq's free tier rather than a paid frontier model —
  materially better than a local small model at zero cost, but still not the
  strongest available model, and subject to free-tier rate limits during a full
  20-case run. Mitigated architecturally (narrow LLM tasks, deterministic policy
  layer, schema validation+retry, rate-limit backoff) and with a local Ollama
  fallback (`qwen3:4b-instruct`) if Groq's limits prove too tight.
- The bounded agentic tool-choice round caps at one additional tool call per case
  by design — a deliberate reliability/ambition tradeoff, not an oversight; an
  unbounded ReAct loop was judged too risky against a free-tier rate limit and a
  48-hour clock.
- Customer/analyst evidence-request responses are simulated by a rule-based module
  against real graph data, not a second LLM persona or real humans — reasonable per
  README §5, but the simulator's assumptions are a modeling choice worth stating
  plainly in the blog post.
- Regulatory PDF ingestion is automated text extraction, not manually curated
  summaries — chunk quality depends on how cleanly each PDF's text extracts.
- Connected Components runs once after initial load, not incrementally as new
  `Case` vertices are written during the run — a new case's own card is added to
  the graph but doesn't retroactively update `ring_cluster_id` for the rest of its
  cluster mid-run. Worth noting as a known limitation, not silently glossing over it.

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
