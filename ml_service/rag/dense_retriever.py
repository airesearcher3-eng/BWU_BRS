"""Dense retrieval using sentence-transformers/all-MiniLM-L6-v2 (384 dims)."""
from __future__ import annotations

import asyncio
from functools import lru_cache

import numpy as np
from typing import Any
from sentence_transformers import SentenceTransformer

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    """Load the model once and cache it for the lifetime of the process."""
    return SentenceTransformer(_MODEL_NAME)


def transaction_to_text(row: dict[str, Any]) -> str:
    """Convert a transaction dict to a descriptive text for embedding."""
    parts: list[str] = []

    date = row.get("transaction_date") or row.get("voucher_date") or ""
    if date:
        parts.append(f"Date: {date}")

    amt = row.get("amount")
    if amt is not None:
        parts.append(f"Amount: {amt}")

    direction = row.get("direction", "")
    if direction:
        parts.append(f"Direction: {'Credit' if direction == 'IN' else 'Debit'}")

    for key in ("description", "narration", "particulars"):
        v = row.get(key)
        if v:
            parts.append(f"Description: {v}")
            break

    cheque = row.get("cheque_no")
    if cheque:
        parts.append(f"Cheque: {cheque}")

    txn_id = row.get("transaction_id")
    if txn_id:
        parts.append(f"Ref: {txn_id}")

    refs = row.get("refs") or row.get("references") or []
    if refs:
        parts.append(f"References: {', '.join(str(r) for r in refs)}")

    vtypes = row.get("voucher_type")
    if vtypes:
        parts.append(f"Type: {vtypes}")

    return ". ".join(parts)


async def generate_embeddings(rows: list[dict[str, Any]]) -> np.ndarray:
    """Return (N, 384) float32 embedding matrix for a list of transaction rows."""
    texts = [transaction_to_text(r) for r in rows]
    model = _get_model()
    loop = asyncio.get_event_loop()
    arr: np.ndarray = await loop.run_in_executor(
        None,
        lambda: model.encode(texts, convert_to_numpy=True, normalize_embeddings=True),
    )
    return arr.astype(np.float32)


def cosine_similarity_matrix(query: np.ndarray, corpus: np.ndarray) -> np.ndarray:
    """Return cosine similarity of (1, D) query against (N, D) corpus → (N,)."""
    q_norm = query / (np.linalg.norm(query) + 1e-10)
    c_norms = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-10)
    return (c_norms @ q_norm).astype(float)


def search_similar(
    query_embedding: np.ndarray,
    book_embeddings: np.ndarray,
    top_k: int = 10,
) -> list[tuple[int, float]]:
    """Return top-k (index, score) pairs by cosine similarity."""
    sims = cosine_similarity_matrix(query_embedding, book_embeddings)
    top_indices = np.argsort(sims)[::-1][:top_k]
    return [(int(i), float(sims[i])) for i in top_indices]
