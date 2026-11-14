"""Real embedding provider: OpenAI embeddings API. No mock fallback -- errors are raised."""
from __future__ import annotations

import os
from typing import Protocol

from openai import OpenAI

KNOWN_DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072,
                    "text-embedding-ada-002": 1536}
BATCH_SIZE = 100


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class OpenAIEmbedder:
    """OpenAI embeddings. Queries and documents are encoded the same way (the API has no prefixes)."""

    provider = "openai"

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small"
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set (put it in starter/.env)")
        if self.model_name not in KNOWN_DIMENSIONS:
            raise ValueError(f"Unknown dimensions for model {self.model_name!r}")
        self.dimensions = KNOWN_DIMENSIONS[self.model_name]
        # The SDK retries transient errors (rate limit, 5xx) itself; anything left is raised.
        self.client = OpenAI(max_retries=5, timeout=60)
        self.tokens_used = 0
        self.requests = 0

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]
            response = self.client.embeddings.create(model=self.model_name, input=batch)
            self.requests += 1
            self.tokens_used += response.usage.total_tokens
            ordered = sorted(response.data, key=lambda d: d.index)
            if len(ordered) != len(batch):
                raise RuntimeError(f"API returned {len(ordered)} vectors for {len(batch)} texts")
            for item in ordered:
                if len(item.embedding) != self.dimensions:
                    raise RuntimeError(f"Expected {self.dimensions} dims, got {len(item.embedding)}")
                vectors.append(item.embedding)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts) if texts else []

    def embed_query(self, question: str) -> list[float]:
        return self._embed([question])[0]
