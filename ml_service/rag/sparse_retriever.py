"""Sparse BM25 retrieval using rank_bm25."""
from __future__ import annotations

import re
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

_TOKEN_RE = re.compile(r"[A-Z0-9]+", re.IGNORECASE)


def _tokenise(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.upper())


def build_corpus(book_rows: list[dict[str, Any]]) -> BM25Okapi:
    """Build a BM25 index from bank-book rows."""
    from rag.dense_retriever import transaction_to_text
    tokenised = [_tokenise(transaction_to_text(r)) for r in book_rows]
    return BM25Okapi(tokenised)


def get_scores(query_text: str, bm25: BM25Okapi) -> np.ndarray:
    """Return raw BM25 scores (float32 array of length == corpus size)."""
    tokens = _tokenise(query_text)
    scores = bm25.get_scores(tokens)
    return scores.astype(np.float32)


def get_top_k(query_text: str, bm25: BM25Okapi, top_k: int = 10) -> list[tuple[int, float]]:
    """Return top-k (index, score) pairs."""
    scores = get_scores(query_text, bm25)
    top_indices = np.argsort(scores)[::-1][:top_k]
    return [(int(i), float(scores[i])) for i in top_indices]
