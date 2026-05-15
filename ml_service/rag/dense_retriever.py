"""Dense retrieval using OpenAI text-embedding-3-small (1536 dims).

Phase 3: Embeddings are generated in batches of 512 rows with at most
         3 concurrent API calls to avoid rate-limit errors.
Phase 5: An in-process cache keyed on normalised narration text avoids
         re-embedding the same description across monthly BRS runs.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import numpy as np
from openai import AsyncOpenAI

_EMBED_MODEL = "text-embedding-3-small"
_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Phase 3 tuning
_BATCH_SIZE = 512          # rows per API call
_MAX_CONCURRENT = 3        # simultaneous embedding calls
_embed_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

# Phase 5: in-process embedding cache  {normalised_text -> np.ndarray(1536,)}
_embed_cache: dict[str, np.ndarray] = {}

_WHITESPACE_RE = re.compile(r"\s+")


def _normalise_text(text: str) -> str:
    """Normalise narration text for cache key (upper, collapse whitespace)."""
    return _WHITESPACE_RE.sub(" ", text.strip().upper())


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


async def _embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Call OpenAI embeddings API for one batch, respecting the concurrency semaphore."""
    async with _embed_semaphore:
        resp = await _client.embeddings.create(model=_EMBED_MODEL, input=texts)
    items = sorted(resp.data, key=lambda x: x.index)
    return [np.array(item.embedding, dtype=np.float32) for item in items]


async def generate_embeddings(rows: list[dict[str, Any]]) -> np.ndarray:
    """Return (N, 1536) float32 embedding matrix.

    Phase 3: texts are chunked into _BATCH_SIZE groups and dispatched
             concurrently (up to _MAX_CONCURRENT simultaneous calls).
    Phase 5: results are cached by normalised text; cached rows skip the API.
    """
    texts = [transaction_to_text(r) for r in rows]
    keys = [_normalise_text(t) for t in texts]

    # Split into uncached / cached
    uncached_positions: list[int] = []
    uncached_texts: list[str] = []
    result: list[np.ndarray | None] = [None] * len(texts)

    for i, key in enumerate(keys):
        if key in _embed_cache:
            result[i] = _embed_cache[key]
        else:
            uncached_positions.append(i)
            uncached_texts.append(texts[i])

    if uncached_texts:
        # Build batches
        batches: list[list[str]] = [
            uncached_texts[start: start + _BATCH_SIZE]
            for start in range(0, len(uncached_texts), _BATCH_SIZE)
        ]

        # Dispatch all batches concurrently (semaphore limits to _MAX_CONCURRENT)
        batch_results: list[list[np.ndarray]] = await asyncio.gather(
            *[_embed_batch(batch) for batch in batches]
        )

        # Flatten and store
        flat: list[np.ndarray] = [vec for batch in batch_results for vec in batch]
        for pos, vec in zip(uncached_positions, flat):
            _embed_cache[keys[pos]] = vec
            result[pos] = vec

    return np.array(result, dtype=np.float32)


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
