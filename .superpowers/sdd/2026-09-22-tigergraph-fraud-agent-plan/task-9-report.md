# Task 9 Report: Knowledge ingestion — policy, patterns, and regulatory PDFs

## Status: DONE

## Commit

`7d21659` — `feat: knowledge ingestion (policy, patterns, regulatory PDFs, closed-case
embeddings)`, on branch `tigergraph-fraud-agent`, local only (not pushed). Adds:
- `src/ingestion/__init__.py`
- `src/ingestion/policy_chunks.py`
- `src/ingestion/regulatory_docs.py`
- `src/ingestion/embeddings.py`
- `scripts/ingest_knowledge.py`
- `tests/test_policy_chunks.py`
- `tests/test_regulatory_docs.py`

Working tree clean after commit. Downloaded PDFs landed in `data/raw/regulatory/`,
which is already covered by `.gitignore` (`*.pdf`, `data/raw/`) — not committed, as
intended (a local cache, not source).

## Live-verified final counts

- **`KnowledgeDoc`: 660** vertices (16 policy/pattern + 644 regulatory chunks), all
  with populated 768-dim `embedding` vectors.
- **`ClosedCase`: 5,565** vertices (all pre-existing from Task 7/8's loading job),
  all now with populated 768-dim `embedding` vectors from `analyst_notes`.
- Confirmed via `tigergraph__gsql` `SELECT COUNT(*)` for both vertex types, and via
  live `search_top_k_similarity` calls on both `KnowledgeDoc.embedding` and
  `ClosedCase.embedding` that return semantically correct top matches (e.g. a
  "customer denies making a transaction" query's top hit is `policy-r2`; a "card
  testing small online purchases" query's top hits are confirmed-fraud closed cases
  matching that exact pattern) — proof the vectors are real and searchable, not
  just present as zero/garbage data.

## Regulatory-chunk ingestion: honest breakdown

The README's "Regulatory references" section (lines 123–150) lists 17 links total:
9 that serve actual PDF bytes, 8 that are HTML pages despite living in the same
section. `REGULATORY_PDFS` (9 entries) and `REGULATORY_HTML_KNOWN` (8 entries) in
`src/ingestion/regulatory_docs.py` reflect this split, determined by inspecting the
README's own links up front — not discovered via failed parse attempts.

- **8 of 9 PDFs succeeded** (download + pypdf extraction + chunking):
  fincen-sar-faqs-2025, fincen-sar-narrative-guidance, fincen-sar-narrative-complete,
  fincen-sar-supporting-docs, fincen-sar-trends-tips, fincen-imposter-mule,
  fincen-identity-suspicious, ofac-sdn-list.
- **1 genuine failure**: `fatf-cyber-fraud`
  (fatf-gafi.org/.../Illicit-financial-flows-cyber-enabled-fraud.pdf...) — `HTTP 403
  Forbidden`. This is real request-blocking by the site against an automated client,
  not the anticipated HTML-vs-PDF issue; reported as a genuine failure, not silently
  folded into the "expected" skip count.
- **8 known-HTML links skipped as anticipated**, without attempting a PDF parse:
  `fincen-account-takeover` (the FinCEN advisory renders as HTML despite living
  under `/resources/advisories/`, not a `.pdf` URL), 5 FATF typology pages, and both
  FFIEC BSA/AML manual pages.
- **1 document capped, not silently truncated**: `ofac-sdn-list` downloaded and
  parsed successfully — it is a genuine, well-formed PDF — but it is a bulk
  enumeration of the Treasury's Specially Designated Nationals list: 21.2M
  characters of extracted text, producing 19,339 chunks (vs. 344 chunks total from
  the other 7 successful PDFs combined). This is structurally unlike the other
  regulatory documents (guidance prose) and is a list of names/aliases, not
  narrative content a semantic query about fraud policy would usefully match
  against. Embedding all ~19,339 chunks locally would have taken multiple
  additional hours for negligible retrieval benefit, so `scripts/ingest_knowledge.py`
  caps any single document at `MAX_CHUNKS_PER_DOC = 300` (keeping the list's front
  matter), logs the cap explicitly (`Capping ...: parsed 19339 chunks ..., keeping
  first 300`), and reports it as a distinct category from both the anticipated-HTML
  skips and the genuine failure. This was a deliberate scoping call, made
  transparently, not a quiet drop — flagging it here per the explicit instruction to
  report the actual ingestion count honestly.
- **Total regulatory chunks ingested: 644** (344 from the 7 uncapped PDFs + 300
  capped from `ofac-sdn-list`).

## Bug found and fixed during the run: unbounded chunk size

The first live attempt at `ingest_regulatory_pdfs` failed outright:
`ollama._types.ResponseError: the input length exceeds the context length (status
code: 500)`. Root cause: `chunk_text`'s paragraph-buffering logic (`re.split(r"\n\s*\n",
text)` then buffer-until-`max_chars`) only bounds chunk size *between* paragraphs —
it never bounds a single paragraph that is itself oversized. pypdf's text extraction
produced single "paragraphs" (no blank-line break at all) up to ~140,000 characters
on some pages (table-heavy layouts, and the OFAC list's dense entries), which blew
straight past `nomic-embed-text`'s context window.

Fixed by adding `_split_oversized()` to `src/ingestion/regulatory_docs.py`: any
paragraph exceeding `max_chars` is hard-split on whitespace boundaries (with a
character-level fallback for a single pathologically long "word") before the
existing buffering loop runs. Re-verified after the fix that the max chunk length
across all 8 successfully-parsed PDFs is exactly 1200 (the `max_chars` cap), not
140,000. Existing tests (`tests/test_regulatory_docs.py`) still pass unchanged since
their fixture paragraphs are all well under `max_chars`.

## What ran, live, in order

1. `ingest_policy_and_patterns` — 16 chunks embedded and upserted in seconds.
   Smoke-tested first, in isolation, before committing to the long runs.
2. `ingest_regulatory_pdfs` — first attempt failed on the oversized-chunk bug above;
   fixed, PDFs re-used from the local download cache (no re-download needed), second
   attempt succeeded: 644 chunks embedded and upserted.
3. `ingest_closed_case_narratives` — 5,565 narratives embedded in 28 batches of 200,
   **720 seconds (12 minutes)** wall-clock, well within the brief's 10-30+ minute
   estimate. Ran to completion with no errors.

All vertex/vector writes used `tigergraph__add_nodes` (batches of 500) +
`tg.upsert_vectors` with the `{"vertex_id": ..., "vector": [...]}` shape (not raw
`tg.gsql("INSERT ...")`, and not the plan brief's `{"id", "embedding"}` shape —
both corrected per the task context notes, and both confirmed working live).

## Verification

- `PYTHONPATH=. .venv\Scripts\pytest tests/` — 42/42 passed (5 new: 3 in
  `tests/test_policy_chunks.py`, 2 in `tests/test_regulatory_docs.py`; 37
  pre-existing, no regressions).
- Ollama daemon confirmed running before starting (`ollama.list()` returned both
  `nomic-embed-text:latest` and `qwen3:4b-instruct` with no connection error).
- `nomic-embed-text` confirmed live to produce 768-dim vectors, matching
  `KnowledgeDoc`/`ClosedCase`'s declared vector attribute dimension.
- Live `SELECT COUNT(*)` on both vertex types, and live `search_top_k_similarity`
  calls returning semantically correct results on both, as described above.

## Concerns

None blocking. Two items worth flagging for whoever builds Task 10's
`retrieve_knowledge` tool:
1. `KnowledgeDoc.source` values are `"policy"`, `"pattern"`, or `"regulatory"` —
   useful if Task 10 wants to filter/weight retrieval by source type.
2. The `ofac-sdn-list` capping decision (300 of 19,339 chunks) means the knowledge
   base has only a small, front-matter-weighted sample of the SDN list, not the full
   sanctions roster — fine for this agent's GraphRAG policy/narrative retrieval use
   case, but worth knowing if a later task assumes full SDN-list coverage for
   sanctions-screening lookups (it does not have that).
