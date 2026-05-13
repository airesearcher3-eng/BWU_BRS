"""Orchestrates carry-forward resolution and the four deterministic matching passes."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from engine.matching.carry_forward import resolve_carry_forward_items
from engine.matching.pass1_exact import run_pass1
from engine.matching.pass2_aggregate import run_pass2
from engine.matching.pass3_rules import run_pass3
from engine.matching.pass4_fd import run_pass4
from engine.matching.pass5_fallback import run_pass5


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


def run_matching_engine(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    carry_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the full reconciliation engine and return the final match state."""

    carry_items = carry_items or []
    all_matches: list[dict[str, Any]] = []

    # Pre-processing: pair NEFT-RETURN credits with their original outward NEFTs
    # so that the failed payment + return cancel out before ref-based matching
    # consumes the original outward NEFT.
    reversal_matches = _pair_neft_return_reversals(statement_rows)
    all_matches.extend(reversal_matches)

    carry_result = resolve_carry_forward_items(carry_items, statement_rows, book_rows)
    all_matches.extend(carry_result["resolved_matches"])

    # Inject unresolved carry-forward "less_cheque_deposit" items as synthetic
    # book rows so that settlement aggregation (pass 2) can group them with
    # regular book IN entries from the same or adjacent date.
    # Only inject items whose cleared_on date falls within or near the current
    # statement period (items that cleared in a prior month are already resolved).
    stmt_dates = [r.get("value_date") for r in statement_rows if r.get("value_date")]
    stmt_min = min(stmt_dates) if stmt_dates else None
    stmt_max = max(stmt_dates) if stmt_dates else None

    synthetic_book_rows: list[dict[str, Any]] = []
    for cf_item in carry_result["pending_items"]:
        if cf_item["section"] not in ("less_cheque_deposit", "add_bank_credit"):
            continue
        cleared = cf_item.get("cleared_on")
        if cleared and stmt_min and cleared < stmt_min:
            continue  # Cleared in a prior period — already resolved.
        synthetic = _make_synthetic_book_row(cf_item)
        book_rows.append(synthetic)
        synthetic_book_rows.append(synthetic)
        if cf_item["section"] == "less_cheque_deposit":
            cf_item["_injected"] = True

    pass_counts: dict[int, int] = {}
    import time as _time
    for pass_number, runner in (
        (1, run_pass1),
        (2, run_pass2),
        (3, run_pass3),
        (4, run_pass4),
        (5, run_pass5),
    ):
        _t0 = _time.time()
        matches = runner(statement_rows, book_rows)
        print(f"  Pass {pass_number}: {len(matches)} matches in {_time.time()-_t0:.1f}s", flush=True)
        all_matches.extend(matches)
        pass_counts[pass_number] = len(matches)

    # Post-pass: cancel out book entries where a MR was deposited (IN) and
    # later cancelled (OUT) for the same amount.  Neither entry will appear
    # in the bank statement, so they should net to zero in the BRS.
    cancel_matches = _pair_cancelled_mr_entries(book_rows)
    all_matches.extend(cancel_matches)

    unmatched_statement = [row for row in statement_rows if not row["matched"]]
    unmatched_book = [row for row in book_rows if not row["matched"]
                      and not row.get("_carry_forward")]

    # Remove CF items whose injected synthetic book rows were matched by a
    # later pass (e.g. settlement aggregation, amount_group_fallback).
    matched_cf_row_numbers = {
        r["row_number"]
        for r in synthetic_book_rows
        if r["matched"]
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
