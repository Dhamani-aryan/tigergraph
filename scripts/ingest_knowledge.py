import asyncio
import time
from pathlib import Path

import pandas as pd

from src.ingestion.embeddings import embed
from src.ingestion.policy_chunks import POLICY_CHUNKS
from src.ingestion.regulatory_docs import (
    REGULATORY_HTML_KNOWN,
    REGULATORY_PDFS,
    chunk_text,
    download_pdf,
    extract_pdf_text,
)
from src.tg_client import TigerGraphMCP

GRAPH_NAME = "FraudInvestigation"


async def ingest_policy_and_patterns(tg: TigerGraphMCP) -> None:
    texts = [c["text"] for c in POLICY_CHUNKS]
    vectors = embed(texts)
    entries = [
        {**chunk, "embedding": vec} for chunk, vec in zip(POLICY_CHUNKS, vectors)
    ]
    await _upsert_knowledge_docs(tg, entries)
    print(f"Ingested {len(entries)} policy/pattern chunks")


# Live-observed on this ingestion run: the OFAC SDN list PDF is a genuine,
# successfully-downloaded-and-parsed PDF, but it is a bulk enumeration of
# ~20,000 sanctioned individuals/entities (21.2M chars of extracted text),
# structurally unlike the other 7 regulatory PDFs (guidance prose, which
# together produced only 344 chunks total). Embedding all ~19,339 of its
# chunks locally would take hours and would not meaningfully improve
# retrieval quality for this agent's fraud-policy questions (near-duplicate
# short name/alias entries dominating the KnowledgeDoc vector index). Capped
# per-document at MAX_CHUNKS_PER_DOC, keeping the first N chunks (which
# include the list's front matter) -- documented and logged, not a silent
# drop, and distinct from both the "anticipated HTML" and "genuine failure"
# categories below.
MAX_CHUNKS_PER_DOC = 300


async def ingest_regulatory_pdfs(tg: TigerGraphMCP) -> None:
    all_chunks: list[dict] = []
    anticipated_html_skips = 0
    genuine_failures: list[tuple[str, str]] = []
    capped_docs: list[tuple[str, int, int]] = []  # (doc_id_prefix, original_count, kept_count)

    # Known-HTML README links: skip up front, don't even try a PDF parse.
    for doc_id_prefix, url in REGULATORY_HTML_KNOWN:
        print(f"Skipping {url}: known HTML page, not a PDF (anticipated)")
        anticipated_html_skips += 1

    # Links that should genuinely be PDF bytes -- attempt download + parse,
    # and report any failure honestly rather than assuming it's the known
    # HTML-vs-PDF issue.
    for doc_id_prefix, url in REGULATORY_PDFS:
        try:
            pdf_path = download_pdf(url, Path("data/raw/regulatory"))
            text = extract_pdf_text(pdf_path)
            chunks = chunk_text(text, doc_id_prefix)
            if not chunks:
                raise ValueError("PDF parsed but produced zero usable chunks (empty/unextractable text)")
            if len(chunks) > MAX_CHUNKS_PER_DOC:
                print(
                    f"Capping {url}: parsed {len(chunks)} chunks (bulk enumeration document), "
                    f"keeping first {MAX_CHUNKS_PER_DOC} -- see MAX_CHUNKS_PER_DOC note above"
                )
                capped_docs.append((doc_id_prefix, len(chunks), MAX_CHUNKS_PER_DOC))
                chunks = chunks[:MAX_CHUNKS_PER_DOC]
            all_chunks.extend(chunks)
        except Exception as exc:  # noqa: BLE001 -- best-effort ingestion, log and continue
            print(f"Skipping {url}: GENUINE failure (expected a PDF) - {type(exc).__name__}: {exc}")
            genuine_failures.append((url, str(exc)))

    print(
        f"Regulatory link summary: {len(REGULATORY_PDFS)} expected-PDF links attempted, "
        f"{len(REGULATORY_PDFS) - len(genuine_failures)} succeeded, "
        f"{len(genuine_failures)} genuinely failed; "
        f"{anticipated_html_skips} known-HTML links skipped as anticipated; "
        f"{len(capped_docs)} document(s) capped: "
        + (", ".join(f"{d} ({orig} -> {kept})" for d, orig, kept in capped_docs) or "none")
    )

    if not all_chunks:
        print("No regulatory chunks produced; skipping upsert.")
        return
    vectors = embed([c["text"] for c in all_chunks])
    entries = [{**chunk, "embedding": vec} for chunk, vec in zip(all_chunks, vectors)]
    await _upsert_knowledge_docs(tg, entries)
    print(f"Ingested {len(entries)} regulatory chunks (from {len(REGULATORY_PDFS) - len(genuine_failures)} PDFs)")


async def ingest_closed_case_narratives(tg: TigerGraphMCP, closed_cases_csv: str) -> None:
    df = pd.read_csv(closed_cases_csv)
    texts = df["analyst_notes"].fillna("").tolist()
    case_ids = df["case_id"].tolist()
    batch_size = 200
    total = len(texts)
    start_time = time.monotonic()
    for start in range(0, total, batch_size):
        batch_texts = texts[start : start + batch_size]
        batch_ids = case_ids[start : start + batch_size]
        vectors = embed(batch_texts)
        await tg.upsert_vectors(
            "ClosedCase",
            "embedding",
            [{"vertex_id": cid, "vector": vec} for cid, vec in zip(batch_ids, vectors)],
        )
        done = min(start + batch_size, total)
        elapsed = time.monotonic() - start_time
        print(f"  embedded {done}/{total} closed-case narratives ({elapsed:.0f}s elapsed)")
    print(f"Embedded {len(texts)} closed case narratives")


async def _upsert_knowledge_docs(tg: TigerGraphMCP, entries: list[dict]) -> None:
    # Task 7 confirmed live against this server: raw tg.gsql("INSERT INTO
    # VERTEX/EDGE ...") is rejected outright (the /gsql/v1/statements
    # endpoint's parser never accepts "insert" as a top-level statement). Use
    # the tigergraph__add_nodes MCP tool (REST++ batch upsert) instead,
    # batched at 500 like the rest of this ingestion script's batches.
    for i in range(0, len(entries), 500):
        batch = entries[i : i + 500]
        await tg.call(
            "tigergraph__add_nodes",
            {
                "vertex_type": "KnowledgeDoc",
                "vertex_id": "doc_id",
                "vertices": [
                    {
                        "doc_id": e["doc_id"],
                        "source": e["source"],
                        "section": e["section"],
                        "text": e["text"],
                    }
                    for e in batch
                ],
            },
        )
    # upsert_vectors takes vector_attribute + {"vertex_id", "vector"} dicts
    # (see src/tg_client.py) -- not the {"id", "embedding"} shape the
    # original plan brief assumed.
    await tg.upsert_vectors(
        "KnowledgeDoc",
        "embedding",
        [{"vertex_id": e["doc_id"], "vector": e["embedding"]} for e in entries],
    )


async def main() -> None:
    async with TigerGraphMCP() as tg:
        await ingest_policy_and_patterns(tg)
        await ingest_regulatory_pdfs(tg)
        await ingest_closed_case_narratives(tg, "closed_cases_history.csv")


if __name__ == "__main__":
    asyncio.run(main())
