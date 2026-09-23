## Task 9: Knowledge ingestion — policy, patterns, and regulatory PDFs

**Files:**
- Create: `src/ingestion/__init__.py` (empty)
- Create: `src/ingestion/policy_chunks.py`
- Create: `src/ingestion/regulatory_docs.py`
- Create: `src/ingestion/embeddings.py`
- Create: `scripts/ingest_knowledge.py`
- Test: `tests/test_policy_chunks.py`
- Test: `tests/test_regulatory_docs.py`

**Interfaces:**
- Produces: `POLICY_CHUNKS: list[dict]` (each `{"doc_id", "source": "policy"|"pattern", "section", "text"}`), `chunk_pdf_text(text: str, source_name: str) -> list[dict]`, `embed(texts: list[str]) -> list[list[float]]` (calls Ollama `nomic-embed-text`). Task 10 (`retrieve_knowledge` tool) queries the `KnowledgeDoc` vertices this task creates.

- [ ] **Step 1: Write `src/ingestion/policy_chunks.py`** — the policy text, hand-chunked by rule (this is short enough to just write out; it's the actual R1–R10 text from README.md, not paraphrased).

```python
from __future__ import annotations

POLICY_CHUNKS: list[dict[str, str]] = [
    {"doc_id": "policy-r1", "source": "policy", "section": "R1", "text": "R1. Verify before you block on a weak signal. If the case rests on a single signal (including a risk score alone) and your assessed fraud probability is below 0.70, recommend VERIFY_WITH_CUSTOMER or STEP_UP_AUTH before any block. Blocking a legitimate customer on one signal is a policy breach."},
    {"doc_id": "policy-r2", "source": "policy", "section": "R2", "text": "R2. Customer denies the transaction. Recommend BLOCK_CARD and CREATE_CASE. Add FILE_REPORT if exposure exceeds $1,000 or the case connects to a shared device profile or another card's fraud."},
    {"doc_id": "policy-r3", "source": "policy", "section": "R3", "text": "R3. Customer confirms the transaction. Recommend CLOSE_NO_FRAUD. Note the confirmation in the case file."},
    {"doc_id": "policy-r4", "source": "policy", "section": "R4", "text": "R4. No reply within 24 hours. Recommend MONITOR_CARD and DECLINE_TRANSACTION for pending authorizations. Escalate if exposure exceeds $500."},
    {"doc_id": "policy-r5", "source": "policy", "section": "R5", "text": "R5. Card testing. Three or more small online authorizations on one card within an hour, followed by a larger purchase: recommend DECLINE_TRANSACTION and STEP_UP_AUTH. If a purchase over $100 has already cleared, recommend BLOCK_CARD."},
    {"doc_id": "policy-r6", "source": "policy", "section": "R6", "text": "R6. Shared origin. When several cards show fraud from the same device profile, the same billing region, or the same recipient email in one window, name the shared element, recommend CREATE_CASE and FILE_REPORT, and MONITOR_CONNECTED_CARDS for every card that shares it."},
    {"doc_id": "policy-r7", "source": "policy", "section": "R7", "text": "R7. Disputed but legitimate. When the customer disputes a charge that matches their own recurring pattern (same merchant, same amount, monthly), recommend CREATE_CASE, VERIFY_WITH_CUSTOMER, and WARN_CUSTOMER. Do not block."},
    {"doc_id": "policy-r8", "source": "policy", "section": "R8", "text": "R8. Escalate when uncertain and exposed. If the verdict is uncertain and exposure exceeds $500, or the evidence conflicts, recommend ESCALATE_TO_ANALYST."},
    {"doc_id": "policy-r9", "source": "policy", "section": "R9", "text": "R9. Undocumented patterns. When activity fits none of the known patterns but the evidence shows coordinated or repeated abuse across customers, recommend CREATE_CASE, FILE_REPORT, and ESCALATE_TO_ANALYST, and describe the pattern in your own words. Do not force it into a known category."},
    {"doc_id": "policy-r10", "source": "policy", "section": "R10", "text": "R10. Never BLOCK_ALL_CARDS unless at least two of the customer's cards show confirmed fraud or the customer's credentials are confirmed compromised."},
    {"doc_id": "policy-case-vs-report", "source": "policy", "section": "3a", "text": "A case (CREATE_CASE) is the bank's internal record of an investigation. Open one whenever fraud probability reaches 0.30, whenever you request evidence, or whenever a customer disputes a charge. A suspicious activity report (FILE_REPORT) is a regulatory filing sent outside the bank. File one when fraud is confirmed or strongly suspected and at least one of: exposure exceeds $1,000; the activity connects to a shared device profile, a shared region cluster, or another customer's fraud; the pattern is coordinated or undocumented (R9)."},
    {"doc_id": "pattern-card-testing", "source": "pattern", "section": "1", "text": "Card testing. A stolen card number is checked before use: three or more tiny online authorizations, often under $5, then a larger purchase. Confirmed by the sequence itself. Policy R5."},
    {"doc_id": "pattern-cnp-fraud", "source": "pattern", "section": "2", "text": "Card-not-present fraud. The number is used online without the card. Amounts and products that don't fit the cardholder's history, often in a burst of two to four within 48 hours. On its own, one unusual online purchase is ambiguous: verify. Policy R1 to R4."},
    {"doc_id": "pattern-cnp-new-device", "source": "pattern", "section": "3", "text": "Card-not-present fraud from a new device. Same as above, with the identity record marking the device as New for this account, sometimes behind a proxy. Stronger than pattern 2, still not proof: people buy new phones."},
    {"doc_id": "pattern-out-of-region", "source": "pattern", "section": "4", "text": "Out-of-region use. Card-present purchases in a billing region the cardholder has no history in, while their normal activity continues at home. Several days of purchases in one new region is a trip, not a clone. Policy R2, R3."},
    {"doc_id": "pattern-account-takeover", "source": "pattern", "section": "5", "text": "Account takeover. Mixed-channel activity inconsistent with the cardholder, often with device and match-flag anomalies, pointing to stolen credentials rather than a stolen number."},
]
```

- [ ] **Step 2: Write `tests/test_policy_chunks.py`**

```python
from src.ingestion.policy_chunks import POLICY_CHUNKS


def test_covers_all_ten_rules():
    sections = {c["section"] for c in POLICY_CHUNKS if c["source"] == "policy"}
    for rule in (f"R{i}" for i in range(1, 11)):
        assert rule in sections, f"missing {rule}"


def test_covers_all_five_patterns():
    patterns = [c for c in POLICY_CHUNKS if c["source"] == "pattern"]
    assert len(patterns) == 5


def test_every_chunk_has_required_fields():
    for chunk in POLICY_CHUNKS:
        assert chunk["doc_id"] and chunk["source"] and chunk["section"] and chunk["text"]
```

- [ ] **Step 3: Run and verify passing**

```bash
.venv\Scripts\pytest tests/test_policy_chunks.py -v
```

Expected: 3 passed (this test needs no network/TigerGraph — it's checking the static data structure).

- [ ] **Step 4: Write `src/ingestion/regulatory_docs.py`**

```python
from __future__ import annotations

import re
from pathlib import Path

import requests
from pypdf import PdfReader

REGULATORY_PDFS: list[tuple[str, str]] = [
    ("fincen-sar-faqs-2025", "https://www.fincen.gov/system/files/2025-10/SAR-FAQs-October-2025.pdf"),
    ("fincen-sar-narrative-guidance", "https://www.fincen.gov/system/files/shared/sar_guidance_narrative.pdf"),
    ("fincen-sar-narrative-complete", "https://www.fincen.gov/system/files/shared/sarnarrcompletguidfinal_112003.pdf"),
    ("fincen-sar-supporting-docs", "https://www.fincen.gov/system/files/shared/fin-2007-g003.pdf"),
    ("fincen-sar-trends-tips", "https://www.fincen.gov/sites/default/files/sar_report/sar_tti_19.pdf"),
    ("fincen-account-takeover", "https://www.fincen.gov/resources/advisories/fincen-advisory-fin-2011-a016"),
    ("fincen-imposter-mule", "https://www.fincen.gov/system/files/advisory/2020-07-07/Advisory_%20Imposter_and_Money_Mule_COVID_19_508_FINAL.pdf"),
    ("fincen-identity-suspicious", "https://www.fincen.gov/system/files/shared/FTA_Identity_Final508.pdf"),
    ("fatf-cyber-fraud", "https://www.fatf-gafi.org/content/dam/fatf-gafi/reports/Illicit-financial-flows-cyber-enabled-fraud.pdf.coredownload.inline.pdf"),
]
# Note: the README also lists several FATF/FFIEC/OFAC pages that are HTML, not PDF
# (money-laundering typology pages, the FFIEC manual, the OFAC SDN list). Those need
# get_page_text-style HTML extraction instead of pypdf -- handle in Step 6 below with
# a second, smaller list and a `requests.get(...).text` + a simple tag-stripping regex,
# since pulling in a full HTML-parsing dependency for ~6 pages isn't worth it.


def download_pdf(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = dest_dir / (url.split("/")[-1].split("?")[0] or "doc.pdf")
    if not filename.exists():
        resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        filename.write_bytes(resp.content)
    return filename


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def chunk_text(text: str, doc_id_prefix: str, max_chars: int = 1200) -> list[dict[str, str]]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip() and len(p.strip()) > 40]
    chunks: list[dict[str, str]] = []
    buf = ""
    idx = 0
    for para in paragraphs:
        if len(buf) + len(para) > max_chars and buf:
            chunks.append({
                "doc_id": f"{doc_id_prefix}-{idx}",
                "source": "regulatory",
                "section": str(idx),
                "text": buf.strip(),
            })
            idx += 1
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        chunks.append({
            "doc_id": f"{doc_id_prefix}-{idx}",
            "source": "regulatory",
            "section": str(idx),
            "text": buf.strip(),
        })
    return chunks
```

- [ ] **Step 5: Write `tests/test_regulatory_docs.py`** (tests the pure chunking function only — no network call in the test).

```python
from src.ingestion.regulatory_docs import chunk_text


def test_chunk_text_splits_on_paragraphs_within_max_chars():
    text = "\n\n".join([f"Paragraph number {i} with some real content padding here." * 3 for i in range(10)])
    chunks = chunk_text(text, "test-doc", max_chars=500)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["text"]) <= 600  # allows the last paragraph to slightly exceed max_chars
        assert c["source"] == "regulatory"


def test_chunk_text_drops_short_noise_paragraphs():
    text = "Real paragraph with enough content to survive the length filter here.\n\nshort\n\nAnother real paragraph with plenty of content in it too."
    chunks = chunk_text(text, "test-doc")
    joined = " ".join(c["text"] for c in chunks)
    assert "short" not in joined
```

- [ ] **Step 6: Run and verify passing**

```bash
.venv\Scripts\pytest tests/test_regulatory_docs.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Write `src/ingestion/embeddings.py`** — Ollama embedding wrapper.

```python
from __future__ import annotations

import ollama


def embed(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    return [ollama.embeddings(model=model, prompt=t)["embedding"] for t in texts]
```

- [ ] **Step 8: Write `scripts/ingest_knowledge.py`** — orchestrates the full one-time ingestion pass: policy/pattern chunks, regulatory PDFs (best-effort per URL — some of the README's regulatory links are HTML pages, not PDFs; catch and skip those with a printed warning rather than failing the whole run), and `ClosedCase` narrative embeddings.

```python
import asyncio
from pathlib import Path

import pandas as pd

from src.ingestion.embeddings import embed
from src.ingestion.policy_chunks import POLICY_CHUNKS
from src.ingestion.regulatory_docs import REGULATORY_PDFS, chunk_text, download_pdf, extract_pdf_text
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


async def ingest_regulatory_pdfs(tg: TigerGraphMCP) -> None:
    all_chunks: list[dict] = []
    for doc_id_prefix, url in REGULATORY_PDFS:
        try:
            pdf_path = download_pdf(url, Path("data/raw/regulatory"))
            text = extract_pdf_text(pdf_path)
            all_chunks.extend(chunk_text(text, doc_id_prefix))
        except Exception as exc:  # noqa: BLE001 -- best-effort ingestion, log and continue
            print(f"Skipping {url}: {exc}")
    if not all_chunks:
        return
    vectors = embed([c["text"] for c in all_chunks])
    entries = [{**chunk, "embedding": vec} for chunk, vec in zip(all_chunks, vectors)]
    await _upsert_knowledge_docs(tg, entries)
    print(f"Ingested {len(entries)} regulatory chunks")


async def ingest_closed_case_narratives(tg: TigerGraphMCP, closed_cases_csv: str) -> None:
    df = pd.read_csv(closed_cases_csv)
    texts = df["analyst_notes"].fillna("").tolist()
    case_ids = df["case_id"].tolist()
    batch_size = 200
    for start in range(0, len(texts), batch_size):
        batch_texts = texts[start : start + batch_size]
        batch_ids = case_ids[start : start + batch_size]
        vectors = embed(batch_texts)
        await tg.upsert_vectors(
            "ClosedCase",
            "embedding",
            [{"vertex_id": cid, "vector": vec} for cid, vec in zip(batch_ids, vectors)],
        )
    print(f"Embedded {len(texts)} closed case narratives")


async def _upsert_knowledge_docs(tg: TigerGraphMCP, entries: list[dict]) -> None:
    # Task 7 confirmed live against this server: raw tg.gsql("INSERT INTO
    # VERTEX/EDGE ...") is rejected outright (the /gsql/v1/statements endpoint's
    # parser never accepts "insert" as a top-level statement). Use the
    # tigergraph__add_nodes MCP tool (REST++ batch upsert) instead, batched at
    # 500 like the rest of this ingestion script's batches.
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
```

- [ ] **Step 9: Run it**

```bash
.venv\Scripts\python scripts\ingest_knowledge.py
```

Expected: policy/pattern chunks ingest quickly; regulatory PDFs take a few minutes (network + PDF parsing) and some may print "Skipping ... " for the HTML-page README links — that's expected per the Step 4 note, not a bug; closed-case embedding of 5,565 narratives via local Ollama takes the longest (likely 10-30+ minutes depending on hardware) — let it run, this is a one-time cost.

- [ ] **Step 10: Verify**

```bash
.venv\Scripts\python -c "
import asyncio
from src.tg_client import TigerGraphMCP
async def main():
    async with TigerGraphMCP() as tg:
        print(await tg.gsql('USE GRAPH FraudInvestigation SELECT COUNT(*) FROM KnowledgeDoc'))
asyncio.run(main())
"
```

Expected: at least 16 (10 policy + 5 pattern + case-vs-report = 16 from Step 1, plus however many regulatory chunks succeeded).

- [ ] **Step 11: Commit**

```bash
git add src/ingestion scripts/ingest_knowledge.py tests/test_policy_chunks.py tests/test_regulatory_docs.py
git commit -m "feat: knowledge ingestion (policy, patterns, regulatory PDFs, closed-case embeddings)"
```

---

