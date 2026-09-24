# Task 14 evidence and classification fix

Branch `fix/task14-evidence-classification`, from `codex/task14-all-fraud-diagnostic` @ `c4105be`.

The all-fraud batch (20 fraud, 0 legitimate, 0 uncertain) is archived unchanged under
`runs/diagnostic-all-fraud/` together with `TASK14_ALL_FRAUD_DIAGNOSTIC.md`. This branch corrects the
evidence and decision logic so that every signal means what the README says it means. It does not
target a verdict distribution, use case IDs, the README's "half are legitimate" statement, or any
external dataset.

## What changed

| Root cause | Fix | Where |
|---|---|---|
| CNP burst built from every nearby row, incl. in-person; silently capped at the 4 nearest | Flagged txn must be online; only online rows (normalized `strip().lower()`, missing != online) within 48h and <= cutoff; the documented 2-4 burst fires only when the complete set has 2-4; > 4 is "high-volume online", never relabelled; burst vs the card's own pre-episode baseline | `features.detect_cnp_burst`, `episode.build_episode` |
| Out-of-region fired on online txns and on regions used hundreds of times | Structured `RegionSignal` with all 8 conditions (card-present, addr1 present, zero prior use of the region, established home region >= 3 txns and >= 40%, home in-person activity within 48h, trip check) | `features.detect_out_of_region` |
| CNP burst / out-of-region overwrote the LLM pattern | Only the exact card-testing sequence and the strict out-of-region signal name the pattern; neither forces a verdict; legitimate forces `none` | `graph_flow._deterministic_pattern_override`, `decision.resolve_decision` |
| Simulator used the model's probability to manufacture a denial; $100 median | Simulator takes only the deterministic profile/signals; exact confirm / deny / no-reply matrix; every text starts `Simulated assumption:` with rule and metrics; median from real history | `simulator.simulate_customer_validation` |
| run_case called >= 0.70 fraud; stopping ignored the two-evidence rule | One resolver for stop, verdict, probability cap/floor, pattern and status; README Sec 6 literally | `decision.py` |
| Any ClosedCase on the card made `single_signal` false | Explicit independent evidence families; risk score, cluster priors, generic case existence and LLM prose never count | `features.compute_evidence_families` |
| Generic device fingerprints / old activity treated as shared origin | New `device_network` GSQL query (per-txn card, amount, risk, ts, confirmed-fraud cases); <= 20 cards, other-card activity within 48h, corroboration by confirmed fraud or tightly coordinated activity; only corroborated cards in `connected_card_ids` | `queries.device_network`, `features.evaluate_device_network` |
| Connected-component rate fired R6 on 1-case clusters and giant components | Context only, shown with cluster size and closed-case sample size (`ring_context` query); never sets shared origin | `graph_flow`, `queries.ring_context` |
| FraudCase vectors from earlier runs retrieved as evidence; live runs depended on a local embedding server | Retrieval re-architected (see below): TigerGraph structured ClosedCase/KnowledgeDoc candidates -> deterministic scoring -> balanced candidates -> GPT-5.5/Pi rerank. FraudCase is never retrieved (still written, without an embedding). | `vector_search.py`, `queries.closed_case_features`, `graph_flow._rerank_retrieval` |
| Amount-only recurrence matches in dense histories | `none` / `candidate` / `strong`; same channel + ProductCD, tolerance max($0.50, 1%), exactly one 25-35 day match, collision rate <= 1%, proxy note; only strong triggers R7 | `features.detect_recurring_charge` |
| Actions and verdict computed independently (closed_fraud + R7) | Policy engine consumes the resolved decision; R1 at literal 0.70; FILE_REPORT always behind CREATE_CASE; uncertain never blocks without a denial | `policy/engine.py` |
| Structural validation only | Separate semantic validator (status/verdict, legitimate shape, R7, CNP/out-of-region channel rules, decisive-stop evidence, simulated labels, collision-sourced connected cards) | `validate_outputs.validate_semantics` |

### Interpretation notes (documented, not tuned)

- **Recurrence collision rate.** The 1% rule is applied to occurrences of the amount band *outside* the
  recurrence slots (~30/60/90 days back). The slot matches are the hypothesised subscription, not
  coincidences; counting them would make "strong" impossible on any card with fewer than 100
  same-product transactions. Both the raw band share and the collision rate are reported.
- **Device corroboration (B).** "Tightly coordinated" is checked two ways: at least two other cards at
  near-identical amounts (or high risk and matching amounts) to the flagged transaction inside the 48h
  episode window, or at least two other cards coordinated with each other within 6 hours.
- **Customer-report cases.** The trigger text is a real statement on file, so it is applied as a
  denial unless strong recurrence applies (then R7). The same question is never asked again.
- **Behavioral baselines** (amount class, product class) need a stable history (>= 20 prior
  transactions); otherwise they are reported as unavailable.

## Retrieval architecture (intentional change)

`TigerGraph structured retrieval -> GPT-5.5/Pi reranking -> GPT-5.5/Pi assessment`

- **No embeddings and no vector search on the live path.** GPT-5.5 through Pi is a chat model, not an
  embeddings endpoint, and the stored ClosedCase/KnowledgeDoc vectors are only queryable with the
  local model that produced them. The live case path therefore makes no request to `localhost:11434`,
  never imports `ollama`, and never calls `embed()` (proved by
  `tests/test_offline_pipeline.py::test_case_path_makes_no_ollama_request_or_command`). The stored vector
  attributes remain in TigerGraph untouched.
- **Candidates.** `closed_case_features` (installed GSQL) returns every ClosedCase with the channel,
  ProductCD, amounts and New-device count of the transactions it INVOLVES; policy and pattern
  KnowledgeDocs come from `knowledge_docs_by_source`. Both are immutable and cached per process.
- **Deterministic scoring** against the corrected features: channel (hard filter), ProductCD, amount
  within 2x, device status, single vs multi-transaction episode, and pattern shape (for cleared cases,
  the templated reason: travel / new phone / unusual amount). Top 8 per outcome.
- **Rerank.** GPT-5.5/Pi selects up to 3 confirmed_fraud, up to 3 cleared cases and up to 5 documents.
  Returned ids are validated against the candidate set (the model cannot add a case) and recorded with
  its rationale in the trace (`retrieval.rerank`).
- **Evidence family.** The "closely matched prior case" family is set deterministically, never from
  the rerank: only when a candidate matches every applicable structured feature and no candidate of the
  opposite outcome does.
- **FraudCase memory** is still written (vertex + read-back); no vector is upserted without an embedding.

RESULTS_PLACEHOLDER
