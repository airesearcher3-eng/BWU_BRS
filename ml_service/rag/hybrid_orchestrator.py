"""
Hybrid RAG orchestrator — optimised pipeline.

Phase 2: BM25 index is built concurrently with dense embedding generation
         (no idle time between pre-processing and index availability).

Phase 3: Embeddings use batched API calls (512 rows/call, 3 concurrent) —
         see dense_retriever.py.

Phase 4: RRF auto-confirm threshold (0.85) skips the LLM for high-confidence
         matches.  Remaining items are batched to GPT-4o-mini in groups of 20
         with a semaphore limiting 5 simultaneous LLM calls.

Phase 5: Embedding cache — handled transparently in dense_retriever.py.

Overall timeout: 120 s hard cap for the entire Pass 6 call.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from rag.dense_retriever import generate_embeddings, search_similar, transaction_to_text
from rag.sparse_retriever import build_corpus, get_top_k
from rag.hybrid_fusion import fuse_rankings
from rag.openai_client import match_batch

logger = logging.getLogger(__name__)

_TOP_K = 10
_BATCH_SIZE = 20

# Phase 4 tuning
_RRF_AUTO_CONFIRM_THRESHOLD = 0.85   # skip LLM if top RRF score exceeds this
_LLM_SEMAPHORE = asyncio.Semaphore(5) # max 5 simultaneous GPT-4o-mini calls

# Pass 6 hard cap
_PASS6_TIMEOUT = 120.0  # seconds


async def _llm_batch_with_semaphore(
    batch_stmt: list[dict],
    batch_books: list[dict],
    stmt_offset: int,
) -> dict[str, Any]:
    """Acquire semaphore before calling GPT-4o-mini — Phase 4 concurrency control."""
    async with _LLM_SEMAPHORE:
        return await match_batch(batch_stmt, batch_books, stmt_offset=stmt_offset, book_offset=0)


async def run_hybrid_rag_matching(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Run Hybrid RAG matching (dense + sparse + LLM) on unmatched residuals.

    Returns:
        {
          "matches": [...],                    # pass_number=6
          "unmatched_statements": [...],
          "unmatched_books": [...],
        }
    """
    if not statement_rows or not book_rows:
        return {
            "matches": [],
            "unmatched_statements": list(range(len(statement_rows))),
            "unmatched_books": list(range(len(book_rows))),
        }

    try:
        result = await asyncio.wait_for(
            _run_rag_pipeline(statement_rows, book_rows),
            timeout=_PASS6_TIMEOUT,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning(
            "Pass 6 (RAG+LLM) timed out after %ss — all residuals left unmatched",
            _PASS6_TIMEOUT,
        )
        return {
            "matches": [],
            "unmatched_statements": list(range(len(statement_rows))),
            "unmatched_books": list(range(len(book_rows))),
        }


async def _run_rag_pipeline(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    import time
    t0 = time.perf_counter()
    logger.info(
        "Pass 6 RAG pipeline: %d stmt rows / %d book rows",
        len(statement_rows), len(book_rows),
    )
    loop = asyncio.get_running_loop()

    # ── Phase 2: Build FAISS-dense + BM25-sparse index concurrently ──────────
    logger.info("Pass 6: generating embeddings + building BM25 index in parallel …")
    t_embed = time.perf_counter()
    (stmt_embeddings, book_embeddings), bm25 = await asyncio.gather(
        asyncio.gather(
            generate_embeddings(statement_rows),
            generate_embeddings(book_rows),
        ),
        loop.run_in_executor(None, build_corpus, book_rows),
    )
    logger.info("Pass 6: embeddings + BM25 ready in %.2fs", time.perf_counter() - t_embed)

    # ── Per-statement RRF retrieval ───────────────────────────────────────────
    candidates_per_stmt: list[list[int]] = []
    top_rrf_scores: list[float] = []

    for stmt_idx, stmt_row in enumerate(statement_rows):
        dense_top = search_similar(stmt_embeddings[stmt_idx], book_embeddings, top_k=_TOP_K)
        sparse_top = get_top_k(transaction_to_text(stmt_row), bm25, top_k=_TOP_K)
        fused = fuse_rankings(dense_top, sparse_top, k=60)
        top_candidates = [idx for idx, _ in fused[:_TOP_K]]
        top_score = fused[0][1] if fused else 0.0
        candidates_per_stmt.append(top_candidates)
        top_rrf_scores.append(top_score)

    # ── Phase 4: Auto-confirm high-confidence matches (skip LLM) ─────────────
    all_matches: list[dict] = []
    matched_stmt: set[int] = set()
    matched_book: set[int] = set()

    for stmt_idx, (candidates, rrf_score) in enumerate(zip(candidates_per_stmt, top_rrf_scores)):
        if not candidates:
            continue
        if rrf_score < _RRF_AUTO_CONFIRM_THRESHOLD:
            continue  # needs LLM verification
        if stmt_idx in matched_stmt:
            continue

        top_book_idx = candidates[0]
        if top_book_idx in matched_book:
            continue

        stmt_row = statement_rows[stmt_idx]
        book_row = book_rows[top_book_idx]

        # Direction guard — auto-confirm only if directions match
        if stmt_row.get("direction") != book_row.get("direction"):
            continue

        matched_stmt.add(stmt_idx)
        matched_book.add(top_book_idx)
        amount = float(stmt_row.get("amount", 0))

        all_matches.append({
            "pass_number": 6,
            "match_type": "rag_auto_confirm",
            "statement_rows": [stmt_idx],
            "book_rows": [top_book_idx],
            "amount": amount,
            "confidence": round(rrf_score, 4),
            "notes": f"RRF score {rrf_score:.4f} exceeded auto-confirm threshold {_RRF_AUTO_CONFIRM_THRESHOLD}",
            "source": "rag",
        })

    auto_confirmed = len(all_matches)
    logger.info("Pass 6: %d auto-confirmed via RRF threshold %.2f", auto_confirmed, _RRF_AUTO_CONFIRM_THRESHOLD)

    # ── Phase 4: LLM verification for ambiguous residuals ────────────────────
    # Collect statement rows that were NOT auto-confirmed for LLM batching.
    llm_pending_stmt = [
        i for i in range(len(statement_rows))
        if i not in matched_stmt
        and candidates_per_stmt[i]  # has at least one candidate
    ]

    if llm_pending_stmt:
        # Build LLM batches of _BATCH_SIZE statement rows and dispatch concurrently.
        llm_tasks = []
        batch_meta = []  # (batch_start_idx_in_llm_pending, stmt_indices, book_indices_global)

        for batch_start in range(0, len(llm_pending_stmt), _BATCH_SIZE):
            batch_stmt_indices = llm_pending_stmt[batch_start: batch_start + _BATCH_SIZE]
            batch_stmt_rows = [statement_rows[i] for i in batch_stmt_indices]

            # Collect unique book-row candidates for this batch
            seen: set[int] = set()
            book_indices_global: list[int] = []
            for si in batch_stmt_indices:
                if si in matched_stmt:
                    continue
                for bi in candidates_per_stmt[si]:
                    if bi not in seen and bi not in matched_book:
                        seen.add(bi)
                        book_indices_global.append(bi)

            if not book_indices_global:
                continue

            batch_book_rows = [book_rows[bi] for bi in book_indices_global]
            local_to_global_book = {local: global_ for local, global_ in enumerate(book_indices_global)}

            llm_tasks.append(
                _llm_batch_with_semaphore(batch_stmt_rows, batch_book_rows, stmt_offset=0)
            )
            batch_meta.append((batch_stmt_indices, local_to_global_book))

        # Phase 4: run all LLM batches concurrently (semaphore limits to 5)
        logger.info("Pass 6: running %d LLM batch(es) with up to 5 concurrent …", len(llm_tasks))
        t_llm = time.perf_counter()
        llm_results = await asyncio.gather(*llm_tasks, return_exceptions=True)
        logger.info("Pass 6: LLM batches complete in %.2fs", time.perf_counter() - t_llm)

        for (batch_stmt_indices, local_to_global_book), llm_result in zip(batch_meta, llm_results):
            if isinstance(llm_result, Exception):
                logger.error("LLM batch failed: %s", llm_result)
                continue

            for grp in llm_result.get("matches", []):
                # Remap local batch indices to global
                global_stmt = [
                    batch_stmt_indices[s]
                    for s in grp.get("statement_indices", [])
                    if s < len(batch_stmt_indices)
                ]
                global_book = [
                    local_to_global_book[b]
                    for b in grp.get("book_indices", [])
                    if b in local_to_global_book
                ]

                if not global_stmt or not global_book:
                    continue
                if any(i in matched_stmt for i in global_stmt):
                    continue
                if any(i in matched_book for i in global_book):
                    continue

                confidence = grp.get("confidence", 0.0)
                if confidence < 0.50:
                    continue  # below minimum confidence floor

                matched_stmt.update(global_stmt)
                matched_book.update(global_book)

                amount = sum(float(statement_rows[i].get("amount", 0)) for i in global_stmt)
                all_matches.append({
                    "pass_number": 6,
                    "match_type": grp.get("match_type", "rag_hybrid"),
                    "statement_rows": global_stmt,
                    "book_rows": global_book,
                    "amount": amount,
                    "confidence": confidence,
                    "notes": grp.get("reasoning", ""),
                    "source": "rag",
                })

    llm_confirmed = len(all_matches) - auto_confirmed
    logger.info(
        "Pass 6 pipeline done in %.2fs: %d auto-confirmed + %d LLM-confirmed = %d total matches  "
        "(%d stmt / %d book unmatched)",
        time.perf_counter() - t0,
        auto_confirmed, llm_confirmed, len(all_matches),
        len([i for i in range(len(statement_rows)) if i not in matched_stmt]),
        len([i for i in range(len(book_rows)) if i not in matched_book]),
    )

    unmatched_stmts = [i for i in range(len(statement_rows)) if i not in matched_stmt]
    unmatched_books = [i for i in range(len(book_rows)) if i not in matched_book]

    return {
        "matches": all_matches,
        "unmatched_statements": unmatched_stmts,
        "unmatched_books": unmatched_books,
    }
