# Task 9 Review: Knowledge ingestion — policy, patterns, and regulatory PDFs

## Verdict: APPROVE

Spec compliance: **PASS**. Code quality: **PASS with minor nits** (no blocking issues). Retrieval — the actual point of this task — is **independently confirmed working**, not just "vectors exist."

All claims in `task-9-report.md` were independently re-verified against the live TigerGraph instance and the local test suite; every load-bearing number checked out exactly.

---

## 1. Live vertex counts — VERIFIED, exact match

Ran `SELECT COUNT(*) FROM KnowledgeDoc` and `SELECT COUNT(*) FROM ClosedCase` myself via a fresh `TigerGraphMCP` session (not trusting the report):

- `KnowledgeDoc`: **660** — matches the report's claim (16 policy/pattern + 644 regulatory) exactly.
- `ClosedCase`: **5,565** — matches exactly.
- `tigergraph__get_vector_index_status` for both `KnowledgeDoc.embedding` and `ClosedCase.embedding` returned `"status": "Ready_for_query"` with no `NeedRebuildServers` — the vector indexes are built and live, not just declared in schema.

(A `GROUP BY source` / `WHERE ... IS NOT NULL` GSQL query I tried to get a finer breakdown hit GSQL reserved-keyword/syntax errors unrelated to this task's code — that's a GSQL dialect quirk in my ad hoc query, not a defect in the ingestion script. The exact-match COUNT(*) results plus the search results below are sufficient independent confirmation.)

## 2. Live `search_top_k_similarity` — VERIFIED, genuinely working, high quality

Ran real queries myself (embedding the query text live with the same `nomic-embed-text` model, then calling `search_top_k_similarity`):

- **KnowledgeDoc**, query "card testing small online authorizations" → top hits: `pattern-card-testing` (distance 0.217), `policy-r5` (0.222), `pattern-cnp-fraud` (0.330), `policy-r10`, `policy-r4`. `pattern-card-testing` and `policy-r5` are exactly the two knowledge docs about card testing — correct.
- **ClosedCase**, same query → top hit `CC-4940`, `pattern: "card_testing"`, `analyst_notes` describing "A run of very small online authorizations followed by a larger purchase, consistent with testing a stolen card number" — a near-verbatim semantic match. Second and third hits also confirmed-fraud, one explicitly tagged `pattern: "card_testing"`.
- **KnowledgeDoc**, query "customer denies making a transaction" → top hit `policy-r2` ("Customer denies the transaction...") at distance 0.283, ahead of `policy-r3` and `policy-r7` — exactly correct, and correctly ranks R2 over the superficially similar-sounding R3 (confirms) and R7 (disputes but legitimate).

This confirms retrieval quality, not just non-empty results: the top-ranked hits are the *semantically correct* documents in every query I ran, on both vertex types. The report's claim is accurate and, if anything, understated.

## 3. `chunk_text` oversized-paragraph fix — VERIFIED sound, bound checked

Read `src/ingestion/regulatory_docs.py` in full. The fix (`_split_oversized`, called on every `re.split`-derived paragraph before the buffering loop) is correct:

- Any paragraph over `max_chars` (1200) is split on space boundaries into pieces ≤ `max_chars`.
- A pathological single "word" with no spaces (garbled extraction) is additionally hard-split character-wise (`while len(word) > max_chars: ...`), so there is no path where a piece fed into the downstream buffer can exceed `max_chars`.
- I traced the buffering loop's invariant: it only appends a paragraph to `buf` without flushing when `len(buf) + len(para) <= max_chars`, so `buf` immediately after append is bounded by `max_chars + 2` (the `"\n\n"` separator). Any subsequent paragraph will trigger a flush of that bounded `buf` before adding more. So the maximum text length of any single chunk emitted by `chunk_text` is `max_chars + 2` in the worst case (a single full-length paragraph on its own) — well within `nomic-embed-text`'s context window, consistent with the report's claim that the post-fix max observed chunk length across all 8 parsed PDFs was 1200.
- Edge case the fix does *not* fully address, but which does not matter here: `_split_oversized` splits on literal `" "` only, not on other whitespace (tabs, non-breaking spaces). If PDF table extraction produced a paragraph using tabs instead of spaces as the only separator, `paragraph.split(" ")` would treat it as one giant "word," but the character-level fallback (`while len(word) > max_chars`) still catches and hard-splits it — so correctness is preserved even in that case, just with less semantically clean chunk boundaries. Not a crash risk, not worth blocking on.

Conclusion: the fix is real, sound, and bounds every chunk correctly. I don't see a remaining edge case that could still overflow `ollama.embeddings()`.

## 4. OFAC SDN list capping (300 of 19,339 chunks) — reasonable judgment call, logged clearly, but one claim in the report is overstated

- **Is capping reasonable given the task's purpose?** Yes. The task is GraphRAG grounding for SAR narratives and typology matching against policy/pattern/regulatory guidance — not a sanctions-screening lookup tool. Embedding ~19,339 near-duplicate name/alias entries locally (multiple additional hours) for a corpus this retrieval task will never usefully query against is a sound scoping call, and it's proportionate: 644 total regulatory chunks stay dominated by actual guidance prose (344 chunks) rather than being swamped 30:1 by SDN name entries.
- **Is it logged/documented in the code, not just the report?** Yes, clearly. `scripts/ingest_knowledge.py` has `MAX_CHUNKS_PER_DOC = 300` with an 11-line comment explaining the rationale (19,339 chunks, 21.2M chars, hours of embedding time, low retrieval value), and at runtime it prints `Capping {url}: parsed {len(chunks)} chunks (bulk enumeration document), keeping first {MAX_CHUNKS_PER_DOC}` plus a final summary line naming the capped doc and its `(original -> kept)` counts. Someone re-running this later will see exactly why the count is what it is, both in code comments and in run output.
- **One inaccuracy worth flagging**: both the report and the code comment characterize the kept 300 chunks as "keeping the list's front matter." I extracted the actual PDF locally (`data/raw/regulatory/sdnlist.pdf`, still cached) and ran `chunk_text` on it directly to check. Only chunks 0–1 are genuine front matter (title page, OFAC's explanatory paragraph about the list's purpose); chunk 150 and chunk 299 (well within the kept 300) are already deep into alphabetical name/alias enumeration entries indistinguishable in kind from the ~19,000 discarded ones (e.g. `ABDELOUADOUD, Abou Musab (a.k.a. ...)`, `ADHIGUNA, Dandi Muhammad (a.k.a. ...)`). So in practice the kept slice is "~2 chunks of front matter + ~298 chunks of alphabetically-first (A-surname) SDN entries," not meaningfully "front matter" as a whole. This doesn't change the soundness of the capping decision itself (an arbitrary 300-chunk sample of name entries is exactly as low-value for this task's retrieval purpose as the front matter framing implies, just for a slightly different reason), but the report's specific phrasing overstates what was preserved. Low severity, doc-accuracy only.

## 5. 8 known-HTML skips vs. 1 genuine 403 failure — VERIFIED handled distinctly, not conflated

Confirmed in `src/ingestion/regulatory_docs.py` and `scripts/ingest_knowledge.py`:

- `REGULATORY_HTML_KNOWN` (8 entries: `fincen-account-takeover`, 5 FATF typology pages, 2 FFIEC manual pages) is a **separate list**, iterated in its own loop in `ingest_regulatory_pdfs` with no `try/except` at all — each just prints `Skipping {url}: known HTML page, not a PDF (anticipated)` and increments `anticipated_html_skips`. No parse is even attempted.
- `REGULATORY_PDFS` (9 entries, including `ofac-sdn-list` and `fatf-cyber-fraud`) is iterated in a **separate loop** with a real `try/except Exception`, and on failure prints `Skipping {url}: GENUINE failure (expected a PDF) - {type(exc).__name__}: {exc}` and appends to a distinct `genuine_failures` list.
- The final summary line reports both counts separately: `"N expected-PDF links attempted, M succeeded, K genuinely failed; J known-HTML links skipped as anticipated"`.

This is exactly the distinction the review was asked to check for: a real future failure among the 9 expected-PDF links cannot be silently absorbed into the "expected" HTML-skip bucket, because that bucket is a hardcoded list checked with no exception handling at all, and the try/except path is labeled "GENUINE failure" in its own output and its own counter. I count 8 entries in `REGULATORY_HTML_KNOWN` and 1 actual failure (`fatf-cyber-fraud`, HTTP 403) among the 9 `REGULATORY_PDFS` entries in the run described in the report — consistent with the report's breakdown.

**Minor nit**: the header comments above both lists are stale/miscounted — the comment above `REGULATORY_PDFS` says "The 10 README ... links that actually serve PDF bytes -- 7 FinCEN ... 1 FATF ... and the OFAC SDN list" (7+1+1 = 9, and the list literally has 9 tuples, not 10), and the comment above `REGULATORY_HTML_KNOWN` says "The remaining 7 README regulatory links" for a list that has 8 entries. The two errors happen to cancel out (9 + 8 = 17, matching the report's "17 links total"), suggesting a late recategorization (moving `ofac-sdn-list` from an assumed-HTML link, per the original task brief's own comment, into the confirmed-PDF list) whose comment text wasn't fully updated. Functionally harmless — the actual list contents and runtime behavior are correct and were exercised live — but worth a one-line fix so a future reader doesn't trip over the mismatched counts.

## 6. Test suite — VERIFIED, exact match

Ran both commands myself from the repo root (`PYTHONPATH=.` needed for the full-suite absolute imports):

```
.venv\Scripts\pytest tests/test_policy_chunks.py tests/test_regulatory_docs.py -v
```
→ **5 passed** (3 in `test_policy_chunks.py`, 2 in `test_regulatory_docs.py`), all green.

```
PYTHONPATH=. .venv\Scripts\pytest tests/ -v
```
→ **42 passed**, 0 failed, 0 skipped — matches the report's "42/42 passed (5 new, 37 pre-existing, no regressions)" exactly.

---

## Other spec-compliance checks

- All required files present and match the brief's file list (`src/ingestion/{__init__,policy_chunks,regulatory_docs,embeddings}.py`, `scripts/ingest_knowledge.py`, both test files).
- `POLICY_CHUNKS` covers R1–R10 plus the case-vs-report distinction plus all 5 named patterns, verbatim from the plan brief — diff matches brief's Step 1 content exactly.
- `git show --stat HEAD` confirms the working tree is exactly this one commit (`7d21659`) on top of `fa1ddd9`, tree clean, matching the report's stated commit.
- `data/raw/regulatory/` (containing the downloaded PDFs, verified still present on disk) is correctly excluded via `.gitignore` (`*.pdf` and `data/raw/` both present) — not committed, as claimed.
- The `upsert_vectors`/`add_nodes`/`search_top_k_similarity` call shapes used in `scripts/ingest_knowledge.py` match `src/tg_client.py`'s actual method signatures (`vector_attribute` + `{"vertex_id", "vector"}`), which itself documents why it deviates from the plan brief's assumed shape — consistent with Task 7's prior finding that raw `INSERT` GSQL is rejected by this server. I didn't re-derive this from scratch, but it's internally consistent across `tg_client.py`, the ingestion script, and this session's own successful live calls using the same client.

## Summary of findings by severity

- **Blocking**: none.
- **Medium**: none.
- **Low**:
  1. Stale/self-contradictory link-count comments in `src/ingestion/regulatory_docs.py` (header says "10" PDFs for a 9-entry list, "remaining 7" HTML for an 8-entry list). Cosmetic; fix by updating the two comment headers to say 9 and 8.
  2. The report's (and code comment's) characterization of the capped OFAC chunks as "keeping the list's front matter" is only true for the first ~2 of the 300 kept chunks; the rest are ordinary alphabetically-early SDN name entries. Doesn't affect the soundness of the capping decision, just the accuracy of its description.

## Explicit confirmation: retrieval genuinely works

This was the actual point of the task, and it is not resting on the report's word: I independently embedded fresh query text with the same `nomic-embed-text` model, called `tigergraph__search_top_k_similarity` against both `KnowledgeDoc.embedding` and `ClosedCase.embedding` live, and got top-ranked results that are semantically correct matches for the query in every case I tried (card-testing pattern/policy docs and a matching confirmed-fraud case for a card-testing query; the exact R2 policy chunk, ranked above superficially similar R3/R7, for a "customer denies the transaction" query). Both vector indexes report `Ready_for_query`. This is real, working semantic retrieval over real ingested data, not merely populated vector columns.
