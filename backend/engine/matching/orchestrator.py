"""Orchestrates carry-forward resolution and the five deterministic matching passes.

Phase 1: Pass 1 + Pass 4 run in parallel (disjoint domains: refs vs FD numbers).
         Pass 2 + Pass 3 run in parallel on residuals (refs vs domain rules).
         Pass 5 runs last on the deduplicated survivor set.
Phase 7: Every pass is wrapped with an asyncio timeout.  If a pass exceeds its
         deadline the warning is logged and survivors flow to the next pass.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import re
import time
from typing import Any

from engine.matching.carry_forward import resolve_carry_forward_items
from engine.matching.pass1_exact import run_pass1
from engine.matching.pass2_aggregate import run_pass2
from engine.matching.pass3_rules import run_pass3
from engine.matching.pass4_fd import run_pass4
from engine.matching.pass5_fallback import run_pass5

logger = logging.getLogger(__name__)

# ── Phase 7: per-pass timeout budgets (seconds) ────────────────────────────
_PASS_TIMEOUTS: dict[int, float] = {
    1: 30.0,
    2: 30.0,
    3: 30.0,
    4: 30.0,
    5: 60.0,
}


def _make_synthetic_book_row(cf_item: dict[str, Any]) -> dict[str, Any]:
    """Build a synthetic book row from a carry-forward item."""
    remarks = cf_item.get("remarks", "")
    row_id = f"cf-{cf_item['row_number']}-{cf_item['amount']}"
    # Use negative row_number to avoid collision with real book entries that
    # share the same row_number (from a different Excel file).
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


async def _timed_pass(
    loop: asyncio.AbstractEventLoop,
    executor: concurrent.futures.ThreadPoolExecutor,
    pass_number: int,
    runner,
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run a sync pass runner in a thread pool with a per-pass timeout guard.

    Phase 7: If the pass exceeds its deadline, log a warning and return an empty
    match list — survivors flow naturally to the next pass.
    """
    deadline = _PASS_TIMEOUTS.get(pass_number, 30.0)
    t0 = time.perf_counter()
    try:
        matches: list[dict[str, Any]] = await asyncio.wait_for(
            loop.run_in_executor(executor, runner, statement_rows, book_rows),
            timeout=deadline,
        )
        elapsed = time.perf_counter() - t0
        logger.info("Pass %d: %d matches in %.2fs", pass_number, len(matches), elapsed)
        return matches
    except asyncio.TimeoutError:
        logger.warning(
            "Pass %d exceeded %ss deadline — survivors passed to subsequent passes",
            pass_number,
            deadline,
        )
        return []
    except Exception as exc:
        logger.error("Pass %d raised %s: %s — continuing", pass_number, type(exc).__name__, exc)
        return []


async def run_matching_engine_async(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    carry_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the full reconciliation engine and return the final match state.

    Phase 1: Pass 1 + Pass 4 dispatched concurrently (disjoint domains).
             Pass 2 + Pass 3 dispatched concurrently on residuals.
             Pass 5 runs last on the deduplicated survivor set.
    Phase 7: Each pass is wrapped with an asyncio timeout.
    """
    carry_items = carry_items or []
    all_matches: list[dict[str, Any]] = []

    # Pre-processing: pair NEFT-RETURN credits with their original outward NEFTs.
    reversal_matches = _pair_neft_return_reversals(statement_rows)
    all_matches.extend(reversal_matches)

    carry_result = resolve_carry_forward_items(carry_items, statement_rows, book_rows)
    all_matches.extend(carry_result["resolved_matches"])

    stmt_dates = [r.get("value_date") for r in statement_rows if r.get("value_date")]
    stmt_min = min(stmt_dates) if stmt_dates else None

    synthetic_book_rows: list[dict[str, Any]] = []
    for cf_item in carry_result["pending_items"]:
        if cf_item["section"] not in ("less_cheque_deposit", "add_bank_credit"):
            continue
        cleared = cf_item.get("cleared_on")
        if cleared and stmt_min and cleared < stmt_min:
            continue
        synthetic = _make_synthetic_book_row(cf_item)
        book_rows.append(synthetic)
        synthetic_book_rows.append(synthetic)
        if cf_item["section"] == "less_cheque_deposit":
            cf_item["_injected"] = True

    pass_counts: dict[int, int] = {}
    loop = asyncio.get_running_loop()

    # Dedicated thread pool for pass execution — 4 workers covers 2 parallel pairs.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="brs_pass")

    try:
        t_start = time.perf_counter()

        # ── Phase 1: Pass 1 (exact refs) + Pass 4 (FD/contra) — fully independent ──
        # Pass 1 targets rows with structured refs (UTR/UPI/cheque).
        # Pass 4 targets rows with FD account numbers or contra voucher types.
        # No row can belong to both domains, so concurrent mutation is safe under CPython GIL.
        pass1_matches, pass4_matches = await asyncio.gather(
            _timed_pass(loop, executor, 1, run_pass1, statement_rows, book_rows),
            _timed_pass(loop, executor, 4, run_pass4, statement_rows, book_rows),
        )
        all_matches.extend(pass1_matches)
        all_matches.extend(pass4_matches)
        pass_counts[1] = len(pass1_matches)
        pass_counts[4] = len(pass4_matches)

        # ── Phase 1: Pass 2 (aggregate refs) + Pass 3 (domain rules) — on residuals ──
        # Pass 2 and Pass 3 operate on different matching domains.  In the rare case
        # that both passes select the same row, deduplication below resolves it.
        pass2_matches, pass3_matches = await asyncio.gather(
            _timed_pass(loop, executor, 2, run_pass2, statement_rows, book_rows),
            _timed_pass(loop, executor, 3, run_pass3, statement_rows, book_rows),
        )
        # Deduplicate: if a row was matched by both passes keep the lower-number pass.
        pass2_matches, pass3_matches = _dedup_parallel_matches(pass2_matches, pass3_matches)
        all_matches.extend(pass2_matches)
        all_matches.extend(pass3_matches)
        pass_counts[2] = len(pass2_matches)
        pass_counts[3] = len(pass3_matches)

        # ── Pass 5: aggressive fallback on deduplicated survivors ──────────────────
        pass5_matches = await _timed_pass(loop, executor, 5, run_pass5, statement_rows, book_rows)
        all_matches.extend(pass5_matches)
        pass_counts[5] = len(pass5_matches)

        total_matches = sum(pass_counts.values())
        logger.info(
            "Matching engine complete: %d total matches in %.2fs  (P1=%d P2=%d P3=%d P4=%d P5=%d)",
            total_matches, time.perf_counter() - t_start,
            pass_counts.get(1, 0), pass_counts.get(2, 0),
            pass_counts.get(3, 0), pass_counts.get(4, 0), pass_counts.get(5, 0),
        )

    finally:
        executor.shutdown(wait=False)

    # Post-pass: cancel out book MR deposit/cancellation pairs.
    cancel_matches = _pair_cancelled_mr_entries(book_rows)
    all_matches.extend(cancel_matches)

    unmatched_statement = [row for row in statement_rows if not row["matched"]]
    unmatched_book = [row for row in book_rows if not row["matched"]
                      and not row.get("_carry_forward")]

    matched_cf_row_numbers = {
        r["row_number"] for r in synthetic_book_rows if r["matched"]
    }
    still_pending = [
        item for item in carry_result["pending_items"]
        if -item["row_number"] not in matched_cf_row_numbers
    ]

    return {
        "matches": all_matches,
        "pass_counts": pass_counts,
        "resolved_carry_forward_matches": carry_result["resolved_matches"],
        "pending_carry_forward_items": still_pending,
        "unmatched_statement": unmatched_statement,
        "unmatched_book": unmatched_book,
    }


def _dedup_parallel_matches(
    lower_pass: list[dict[str, Any]],
    higher_pass: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove match groups from higher_pass whose rows were already claimed by lower_pass.

    When two passes run in parallel, the same row may appear in both result sets.
    The lower-numbered pass wins; the higher-numbered pass groups are filtered.
    Rows already un-matched (reset) here so the higher pass match is dropped cleanly.
    """
    claimed_stmt: set[int] = set()
    claimed_book: set[int] = set()
    for grp in lower_pass:
        claimed_stmt.update(grp.get("statement_rows", []))
        claimed_book.update(grp.get("book_rows", []))

    clean_higher: list[dict[str, Any]] = []
    for grp in higher_pass:
        if any(r in claimed_stmt for r in grp.get("statement_rows", [])):
            continue
        if any(r in claimed_book for r in grp.get("book_rows", [])):
            continue
        clean_higher.append(grp)

    return lower_pass, clean_higher



def _pair_neft_return_reversals(
    statement_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair NEFT-RETURN inward credits with their original outward NEFTs.

    When a NEFT payment is returned (e.g. incorrect account number), the bank
    shows both the original debit and the return credit on the statement.  These
    two entries cancel each other out and should not match any book entries.
    """
    matches: list[dict[str, Any]] = []

    return_rows = [
        row for row in statement_rows
        if not row["matched"]
        and row["direction"] == "IN"
        and "RETURN" in row["description"].upper()
        and row.get("refs")
    ]

    for return_row in return_rows:
        return_refs = set(return_row["refs"])
        # Find the original outward NEFT with the same ref and amount
        original_candidates = [
            row for row in statement_rows
            if not row["matched"]
            and row["direction"] == "OUT"
            and row["amount"] == return_row["amount"]
            and return_refs & set(row.get("refs", []))
        ]
        if len(original_candidates) != 1:
            continue

        original = original_candidates[0]
        original["matched"] = True
        original["match_state"] = "matched"
        return_row["matched"] = True
        return_row["match_state"] = "matched"
        matches.append(
            {
                "match_type": "neft_return_reversal",
                "pass_number": 0,
                "statement_rows": [original["row_number"], return_row["row_number"]],
                "book_rows": [],
                "amount": return_row["amount"],
                "notes": f"NEFT-RETURN reversal pair (ref {return_refs})",
            }
        )

    return matches


_MR_NO_RE = re.compile(r"BWU\d{4}/\d{4,6}", re.IGNORECASE)


def _pair_cancelled_mr_entries(
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair cancelled MR book entries (OUT) with their original deposits (IN).

    When a Money Receipt is cancelled, the book shows both the original deposit
    (direction=IN) and a cancellation entry (direction=OUT, narration contains
    "(cancelled)") for the same amount.  Neither ever appears on the bank
    statement, so they should cancel each other in the BRS.
    """
    matches: list[dict[str, Any]] = []

    cancelled_rows = [
        row for row in book_rows
        if not row["matched"]
        and row["direction"] == "OUT"
        and "(cancelled)" in (row.get("narration", "") or "").lower()
    ]

    for cancel_row in cancelled_rows:
        mr_match = _MR_NO_RE.search(cancel_row.get("narration", "") or "")
        if not mr_match:
            mr_match = _MR_NO_RE.search(cancel_row.get("particulars", "") or "")
        if not mr_match:
            continue
        mr_no = mr_match.group(0).upper()

        # Find the original IN entry with the same MR number and amount.
        original_candidates = [
            row for row in book_rows
            if not row["matched"]
            and row["direction"] == "IN"
            and row["amount"] == cancel_row["amount"]
            and mr_no in (f"{row.get('narration', '')} {row.get('particulars', '')}").upper()
        ]
        if len(original_candidates) != 1:
            continue

        original = original_candidates[0]
        original["matched"] = True
        original["match_state"] = "matched"
        cancel_row["matched"] = True
        cancel_row["match_state"] = "matched"
        matches.append(
            {
                "match_type": "cancelled_mr_pair",
                "pass_number": 6,
                "statement_rows": [],
                "book_rows": [original["row_number"], cancel_row["row_number"]],
                "amount": cancel_row["amount"],
                "notes": f"Cancelled MR pair: {mr_no}",
            }
        )

    # Fallback: cancelled entries without MR match → pair by amount + same date.
    for cancel_row in cancelled_rows:
        if cancel_row["matched"]:
            continue
        cancel_date = cancel_row.get("voucher_date")
        candidates = [
            row for row in book_rows
            if not row["matched"]
            and row["direction"] == "IN"
            and row["amount"] == cancel_row["amount"]
            and row.get("voucher_date") == cancel_date
        ]
        if len(candidates) != 1:
            continue

        original = candidates[0]
        original["matched"] = True
        original["match_state"] = "matched"
        cancel_row["matched"] = True
        cancel_row["match_state"] = "matched"
        matches.append(
            {
                "match_type": "cancelled_mr_pair",
                "pass_number": 6,
                "statement_rows": [],
                "book_rows": [original["row_number"], cancel_row["row_number"]],
                "amount": cancel_row["amount"],
                "notes": "Cancelled entry paired by amount+date",
            }
        )

    return matches
