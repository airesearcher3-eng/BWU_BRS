"""In-memory vector store for transaction embeddings with cosine similarity search."""

from __future__ import annotations

import math
from typing import Any


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return _dot(a, b) / (na * nb)


class VectorStore:
    """Simple in-memory vector store for a single reconciliation run."""

    def __init__(self) -> None:
        self.embeddings: list[list[float]] = []
        self.metadata: list[dict[str, Any]] = []

    def add(self, embedding: list[float], metadata: dict[str, Any]) -> int:
        idx = len(self.embeddings)
        self.embeddings.append(embedding)
        self.metadata.append(metadata)
        return idx

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        filter_fn: Any = None,
    ) -> list[tuple[int, float, dict[str, Any]]]:
        """Return top-k most similar entries as (index, score, metadata) tuples."""

        scored: list[tuple[int, float, dict[str, Any]]] = []
        for i, emb in enumerate(self.embeddings):
            meta = self.metadata[i]
            if filter_fn and not filter_fn(meta):
                continue
            score = cosine_similarity(query_embedding, emb)
            scored.append((i, score, meta))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def __len__(self) -> int:
        return len(self.embeddings)
