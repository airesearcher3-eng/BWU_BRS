"""
Hybrid RAG orchestrator.

Flow:
1. Generate dense embeddings for all statement + book rows (OpenAI).
2. Build BM25 index on book rows (sparse).
3. For each statement row, fuse dense + sparse rankings via RRF → top-K candidates.
4. Batch candidates + statement rows to GPT-4o-mini for verification (batches of 20).
5. Mark matched rows and return result dict.
"""
from __future__ import annotations

import asyncio
from typing import Any

from rag.dense_retriever import generate_embeddings, search_similar, transaction_to_text
from rag.sparse_retriever import build_corpus, get_top_k
from rag.hybrid_fusion import fuse_rankings
from rag.openai_client import match_batch

_TOP_K = 10
_BATCH_SIZE = 20


async def run_hybrid_rag_matching(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Run Hybrid RAG matching (dense + sparse + LLM) on unmatched residuals.

    Returns a dict compatible with the route layer:
        {
          "matches": [...],          # match groups, each with pass_number=6
          "unmatched_statements": [0, 2, ...],   # original indices
          "unmatched_books": [1, 3, ...],
        }
    """
    if not statement_rows or not book_rows:
        return {
            "matches": [],
            "unmatched_statements": list(range(len(statement_rows))),
            "unmatched_books": list(range(len(book_rows))),
        }

    # ── 1. Dense embeddings ───────────────────────────────────────────────────
    stmt_embeddings, book_embeddings = await asyncio.gather(
        generate_embeddings(statement_rows),
        generate_embeddings(book_rows),
    )

    # ── 2. Sparse BM25 ───────────────────────────────────────────────────────
    bm25 = build_corpus(book_rows)

    # ── 3. Per-statement candidate retrieval ─────────────────────────────────
    candidates_per_stmt: list[list[int]] = []
    for stmt_idx, stmt_row in enumerate(statement_rows):
        dense_top = search_similar(stmt_embeddings[stmt_idx], book_embeddings, top_k=_TOP_K)
        sparse_top = get_top_k(transaction_to_text(stmt_row), bm25, top_k=_TOP_K)
        fused = fuse_rankings(dense_top, sparse_top, k=60)
        candidates_per_stmt.append([idx for idx, _ in fused[:_TOP_K]])

    # ── 4. Batch GPT-4o-mini verification ────────────────────────────────────
    all_matches: list[dict] = []
    matched_stmt: set[int] = set()
    matched_book: set[int] = set()

    # Process in batches: each batch covers up to BATCH_SIZE statement rows
    for batch_start in range(0, len(statement_rows), _BATCH_SIZE):
        batch_end = min(batch_start + _BATCH_SIZE, len(statement_rows))
        batch_stmt = statement_rows[batch_start:batch_end]

        # Collect unique book-row candidates for this batch
        book_indices_in_batch: list[int] = []
        seen: set[int] = set()
        for stmt_idx in range(batch_start, batch_end):
            for bi in candidates_per_stmt[stmt_idx]:
                if bi not in seen:
                    seen.add(bi)
                    book_indices_in_batch.append(bi)

        if not book_indices_in_batch:
            continue

        batch_books = [book_rows[bi] for bi in book_indices_in_batch]
        # Build a local-index→global-index map for book rows
        local_to_global_book: dict[int, int] = {
            local: global_ for local, global_ in enumerate(book_indices_in_batch)
        }

        llm_result = await match_batch(
            batch_stmt, batch_books,
            stmt_offset=batch_start,
            book_offset=0,
        )

        for grp in llm_result.get("matches", []):
            # Translate local book indices back to global
            global_stmt = [s for s in grp.get("statement_indices", [])
                           if batch_start <= s < batch_end]
            global_book = [local_to_global_book[b] for b in grp.get("book_indices", [])
                           if b in local_to_global_book]

            if not global_stmt or not global_book:
                continue

            # Skip if any member is already matched
            if any(i in matched_stmt for i in global_stmt):
                continue
            if any(i in matched_book for i in global_book):
                continue

            matched_stmt.update(global_stmt)
            matched_book.update(global_book)

            # Compute matched amount from statement side
            amount = sum(
                float(statement_rows[i].get("amount", 0)) for i in global_stmt
            )

            all_matches.append({
                "pass_number": 6,
                "match_type": grp.get("match_type", "rag_hybrid"),
                "statement_rows": global_stmt,
                "book_rows": global_book,
                "amount": amount,
                "confidence": grp.get("confidence", 0.0),
                "notes": grp.get("reasoning", ""),
                "source": "rag",
            })

    unmatched_stmts = [i for i in range(len(statement_rows)) if i not in matched_stmt]
    unmatched_books = [i for i in range(len(book_rows)) if i not in matched_book]

    return {
        "matches": all_matches,
        "unmatched_statements": unmatched_stmts,
        "unmatched_books": unmatched_books,
    }
