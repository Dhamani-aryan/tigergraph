from __future__ import annotations

import ollama


def embed(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    return [ollama.embeddings(model=model, prompt=t)["embedding"] for t in texts]
