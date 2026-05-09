"""RAG orchestrator — LLM-based matching engine using Gemini 2.5 Flash.

Flow:
  1. Resolve carry-forward items (deterministic)
  2. Send unmatched statement + book entries directly to Gemini 2.5 Flash in batches
  3. Aggregate results into the same format as the deterministic engine
"""

from __future__ import annotations

import hashlib
import time
from decimal import Decimal
from typing import Any

from engine.matching.carry_forward import resolve_carry_forward_items
from engine.matching.utils import sum_amounts
from engine.rag.llm_matcher import match_batch


def _make_synthetic_book_row(cf_item: dict[str, Any]) -> dict[str, Any]:
    """Build a synthetic book row from a carry-forward item."""
    remarks = cf_item.get("remarks", "")
    row_id = f"cf-{cf_item['row_number']}-{cf_item['amount']}"
    return {
        "row_number": -cf_item["row_number"],
        "voucher_date": cf_item["original_date"],
        "direction": "IN",
        "amount": cf_item["amount"],
        "particulars": remarks,
        "narration": remarks,
        "voucher_type": "",
        "voucher_no": "",
        "cheque_no": cf_item.get("cheque_no"),
        "row_hash": hashlib.sha256(row_id.encode()).hexdigest(),
        "matched": False,
        "match_state": "unmatched",
        "refs": cf_item.get("refs", []),
        "_carry_forward": True,
    }


def run_rag_matching(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    carry_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the RAG-based reconciliation engine.

    Returns the same structure as the deterministic ``run_matching_engine``.
    """
    carry_items = carry_items or []
    all_matches: list[dict[str, Any]] = []

    # ── 1. Carry-forward resolution (same deterministic logic) ──
    carry_result = resolve_carry_forward_items(carry_items, statement_rows, book_rows)
    all_matches.extend(carry_result["resolved_matches"])

    # Inject synthetic book rows for unresolved carry-forward items
    stmt_dates = [r.get("value_date") for r in statement_rows if r.get("value_date")]
    stmt_min = min(stmt_dates) if stmt_dates else None

    for cf_item in carry_result["pending_items"]:
        if cf_item["section"] not in ("less_cheque_deposit", "add_bank_credit"):
            continue
        cleared = cf_item.get("cleared_on")
        if cleared and stmt_min and cleared < stmt_min:
            continue
        synthetic = _make_synthetic_book_row(cf_item)
        book_rows.append(synthetic)
        if cf_item["section"] == "less_cheque_deposit":
            cf_item["_injected"] = True

    # ── 2. Get unmatched entries ──
    unmatched_stmt = [r for r in statement_rows if not r.get("matched")]
    unmatched_book = [r for r in book_rows if not r.get("matched")]

    if not unmatched_stmt or not unmatched_book:
        return _build_result(all_matches, statement_rows, book_rows, carry_result)

    # ── 3. Send to Gemini 2.5 Flash in batches ──
    BATCH_SIZE = 20  # statement entries per LLM call

    t0 = time.time()
    print(f"  RAG: Matching {len(unmatched_stmt)} stmt × {len(unmatched_book)} book "
          f"via Gemini 2.5 Flash (batches of {BATCH_SIZE})...", flush=True)

    rag_pass_matches: list[dict[str, Any]] = []
    processed = set()

    for batch_start in range(0, len(unmatched_stmt), BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, len(unmatched_stmt))
        batch_stmt_indices = [
            i for i in range(batch_start, batch_end) if i not in processed
        ]
        if not batch_stmt_indices:
            continue

        # Filter out already-matched book entries
        available_book_indices = [
            i for i in range(len(unmatched_book))
            if not unmatched_book[i].get("matched")
        ]
        if not available_book_indices:
            break

        batch_stmt_rows = [unmatched_stmt[i] for i in batch_stmt_indices]
        batch_book_rows = [unmatched_book[i] for i in available_book_indices]

        llm_result = match_batch(batch_stmt_rows, batch_book_rows)

        for m in llm_result.get("matches", []):
            confidence = m.get("confidence", 0.5)
            if confidence < 0.5:
                continue

            # Map LLM-local indices back to our unmatched_* indices
            global_stmt_indices = [batch_stmt_indices[i] for i in m["statement_indices"]]
            global_book_indices = [available_book_indices[i] for i in m["book_indices"]]

            # Verify indices are valid and not already matched
            valid = True
            for gi in global_stmt_indices:
                if gi >= len(unmatched_stmt) or unmatched_stmt[gi].get("matched"):
                    valid = False
                    break
            for gi in global_book_indices:
                if gi >= len(unmatched_book) or unmatched_book[gi].get("matched"):
                    valid = False
                    break
            if not valid:
                continue

            # Mark matched
            matched_stmt_rows = [unmatched_stmt[i] for i in global_stmt_indices]
            matched_book_rows = [unmatched_book[i] for i in global_book_indices]

            for row in matched_stmt_rows:
                row["matched"] = True
                row["match_state"] = "matched"
            for row in matched_book_rows:
                row["matched"] = True
                row["match_state"] = "matched"

            processed.update(global_stmt_indices)

            match_record = {
                "match_type": m.get("match_type", "rag_match"),
                "pass_number": 5,  # RAG is treated as a single pass
                "statement_rows": [r["row_number"] for r in matched_stmt_rows],
                "book_rows": [r["row_number"] for r in matched_book_rows],
                "amount": max(
                    sum_amounts(matched_stmt_rows),
                    sum_amounts(matched_book_rows),
                ),
                "confidence": confidence,
                "notes": f"RAG: {m.get('reasoning', '')}",
            }
            rag_pass_matches.append(match_record)

    all_matches.extend(rag_pass_matches)
    print(f"  RAG: {len(rag_pass_matches)} matches found in {time.time() - t0:.1f}s",
          flush=True)

    return _build_result(all_matches, statement_rows, book_rows, carry_result)


def _build_result(
    all_matches: list[dict[str, Any]],
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    carry_result: dict[str, Any],
) -> dict[str, Any]:
    """Build the final result dict matching the deterministic engine's structure."""

    unmatched_statement = [row for row in statement_rows if not row.get("matched")]
    unmatched_book = [
        row for row in book_rows
        if not row.get("matched") and not row.get("_carry_forward")
    ]

    # RAG uses a single pass, but we expose pass breakdown for compatibility
    rag_count = sum(1 for m in all_matches if m.get("notes", "").startswith("RAG:"))
    cf_count = sum(1 for m in all_matches if m.get("match_type") == "carry_forward_cleared")
    other_count = len(all_matches) - rag_count - cf_count

    pass_counts = {
        1: cf_count + other_count,  # Carry-forward and pre-processing
        2: 0,
        3: 0,
        4: 0,
        5: rag_count,               # RAG matches shown as pass 5
    }

    return {
        "matches": all_matches,
        "pass_counts": pass_counts,
        "resolved_carry_forward_matches": carry_result["resolved_matches"],
        "pending_carry_forward_items": carry_result["pending_items"],
        "unmatched_statement": unmatched_statement,
        "unmatched_book": unmatched_book,
    }
