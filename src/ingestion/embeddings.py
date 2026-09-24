from __future__ import annotations

# Ingestion-time only. The live investigation path never embeds (Task 14):
# `ollama` is imported lazily here so importing this module -- or anything
# that imports it -- does not require an Ollama install or a server on
# localhost:11434. Only the knowledge-ingestion scripts call embed().


def embed(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    import ollama

    return [ollama.embeddings(model=model, prompt=t)["embedding"] for t in texts]
