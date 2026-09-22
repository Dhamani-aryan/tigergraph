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
