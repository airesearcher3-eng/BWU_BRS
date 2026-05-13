"""Reciprocal Rank Fusion (RRF) for combining dense and sparse rankings."""
from __future__ import annotations


def fuse_rankings(
    dense_ranking: list[tuple[int, float]],
    sparse_ranking: list[tuple[int, float]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """
    Merge two ranked lists using Reciprocal Rank Fusion.

    Args:
        dense_ranking:  List of (index, score) sorted by dense score descending.
        sparse_ranking: List of (index, score) sorted by sparse score descending.
        k:              Smoothing constant (default 60 per the original RRF paper).

    Returns:
        List of (index, rrf_score) sorted by RRF score descending.
    """
    scores: dict[int, float] = {}

    for rank, (idx, _score) in enumerate(dense_ranking):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)

    for rank, (idx, _score) in enumerate(sparse_ranking):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
