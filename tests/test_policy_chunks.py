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
