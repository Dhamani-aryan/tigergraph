from __future__ import annotations

import re
from pathlib import Path

import requests
from pypdf import PdfReader

# The 10 README "Regulatory references" links (README.md, lines 128-150) that
# actually serve PDF bytes -- 7 FinCEN advisories/guidance docs, 1 FATF
# report, and the OFAC SDN list (all URLs end in .pdf). Attempted via
# download_pdf + extract_pdf_text below.
REGULATORY_PDFS: list[tuple[str, str]] = [
    ("fincen-sar-faqs-2025", "https://www.fincen.gov/system/files/2025-10/SAR-FAQs-October-2025.pdf"),
    ("fincen-sar-narrative-guidance", "https://www.fincen.gov/system/files/shared/sar_guidance_narrative.pdf"),
    ("fincen-sar-narrative-complete", "https://www.fincen.gov/system/files/shared/sarnarrcompletguidfinal_112003.pdf"),
    ("fincen-sar-supporting-docs", "https://www.fincen.gov/system/files/shared/fin-2007-g003.pdf"),
    ("fincen-sar-trends-tips", "https://www.fincen.gov/sites/default/files/sar_report/sar_tti_19.pdf"),
    ("fincen-imposter-mule", "https://www.fincen.gov/system/files/advisory/2020-07-07/Advisory_%20Imposter_and_Money_Mule_COVID_19_508_FINAL.pdf"),
    ("fincen-identity-suspicious", "https://www.fincen.gov/system/files/shared/FTA_Identity_Final508.pdf"),
    ("fatf-cyber-fraud", "https://www.fatf-gafi.org/content/dam/fatf-gafi/reports/Illicit-financial-flows-cyber-enabled-fraud.pdf.coredownload.inline.pdf"),
    ("ofac-sdn-list", "https://www.treasury.gov/ofac/downloads/sdnlist.pdf"),
]

# The remaining 7 README regulatory links (README.md, lines 133, 139-143,
# 146-147) are HTML pages, not PDFs, despite living in the same "Regulatory
# references" section -- one FinCEN advisory rendered as an HTML page rather
# than a PDF, five FATF typology pages, and both FFIEC BSA/AML manual pages.
# Known in advance from inspecting the README's own links (not discovered by
# a failed pypdf parse), so the ingestion script skips these immediately with
# an "anticipated" message instead of attempting (and failing) a PDF parse.
REGULATORY_HTML_KNOWN: list[tuple[str, str]] = [
    ("fincen-account-takeover", "https://www.fincen.gov/resources/advisories/fincen-advisory-fin-2011-a016"),
    ("fatf-new-payment-methods", "https://www.fatf-gafi.org/en/publications/Methodsandtrends/Reportonnewpaymentmethods.html"),
    ("fatf-professional-ml", "https://www.fatf-gafi.org/en/publications/Methodsandtrends/Professional-money-laundering.html"),
    ("fatf-remittance-ml", "https://www.fatf-gafi.org/en/publications/Methodsandtrends/Moneylaunderingthroughmoneyremittanceandcurrencyexchangeproviders.html"),
    ("fatf-trade-based-ml", "https://www.fatf-gafi.org/en/publications/Methodsandtrends/Trade-based-money-laundering-trends-and-developments.html"),
    ("fatf-international-cooperation", "https://www.fatf-gafi.org/en/publications/Methodsandtrends/international-cooperation-against-money-laundering.html"),
    ("ffiec-red-flags", "https://bsaaml.ffiec.gov/manual/Appendices/07"),
    ("ffiec-sar-reporting", "https://bsaaml.ffiec.gov/manual/AssessingComplianceWithBSARegulatoryRequirements/04"),
]


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


def _split_oversized(paragraph: str, max_chars: int) -> list[str]:
    """Hard-split a single "paragraph" that itself exceeds max_chars, on
    whitespace boundaries. Needed because pypdf's text extraction sometimes
    produces a stretch of text with no blank-line break at all (e.g. a
    table-heavy page, or a long unbroken list like the OFAC SDN list) --
    live-observed on this ingestion run to produce single re.split "paragraphs"
    up to ~140,000 chars, which blew past nomic-embed-text's context window
    and made ollama.embeddings() fail outright with a 500 "input length
    exceeds the context length" error. Without this, chunk_text's max_chars
    cap only bounds *between*-paragraph buffering, never a single oversized
    paragraph, so every downstream chunk stayed silently oversized."""
    if len(paragraph) <= max_chars:
        return [paragraph]
    words = paragraph.split(" ")
    parts: list[str] = []
    buf = ""
    for word in words:
        # Guard the pathological case of a single "word" (no spaces at all,
        # e.g. garbled PDF extraction) that alone exceeds max_chars.
        while len(word) > max_chars:
            parts.append(word[:max_chars])
            word = word[max_chars:]
        if buf and len(buf) + 1 + len(word) > max_chars:
            parts.append(buf)
            buf = word
        else:
            buf = f"{buf} {word}" if buf else word
    if buf:
        parts.append(buf)
    return parts


def chunk_text(text: str, doc_id_prefix: str, max_chars: int = 1200) -> list[dict[str, str]]:
    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip() and len(p.strip()) > 40]
    paragraphs: list[str] = []
    for p in raw_paragraphs:
        paragraphs.extend(_split_oversized(p, max_chars))
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
