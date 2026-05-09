"""Classification of unmatched items into final BRS sections and exceptions."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from engine.reference_extractor import compact_text


def _remove_offsetting_pairs(items: list[dict]) -> list[dict]:
    """Remove pairs of items whose amounts exactly cancel each other (+x and -x).

    For example, a bank statement entry of +141 and -141 in the same BRS
    section represent a transaction and its reversal; they net to zero and
    should not appear in the final output.
    """
    by_amount: dict = defaultdict(list)
    for i, item in enumerate(items):
        by_amount[item["amount"]].append(i)

    to_remove: set[int] = set()
    for amt, indices in by_amount.items():
        if amt <= 0:
            continue
        neg_amt = -amt
        if neg_amt in by_amount:
            neg_indices = by_amount[neg_amt]
            cancel_count = min(len(indices), len(neg_indices))
            to_remove.update(indices[:cancel_count])
            to_remove.update(neg_indices[:cancel_count])

    return [item for i, item in enumerate(items) if i not in to_remove]


SECTION_ORDER = (
    "add_cheque_issued",
    "add_bank_credit",
    "less_cheque_deposit",
    "less_bank_debit",
)

_SAL_RE = re.compile(r"^SAL\s+\S+\s+(.+)", re.IGNORECASE)


def _cross_section_salary_offset(sections: dict[str, list[dict[str, Any]]]) -> None:
    """Remove salary debit / NEFT credit return pairs across sections.

    When a SAL entry in less_bank_debit (duplicate salary debit) has the same
    amount as a NEFT credit in add_bank_credit from the same person (salary
    returned), both should be removed as they cancel each other out.
    """
    sal_items = [
        (i, item)
        for i, item in enumerate(sections["less_bank_debit"])
        if item["source"] == "statement" and _SAL_RE.match(item["remarks"])
    ]
    for sal_idx, sal_item in reversed(sal_items):
        sal_name_match = _SAL_RE.match(sal_item["remarks"])
        if not sal_name_match:
            continue
        sal_name = compact_text(sal_name_match.group(1))
        if len(sal_name) < 5:
            continue

        for neft_idx, neft_item in enumerate(sections["add_bank_credit"]):
            if neft_item["amount"] != sal_item["amount"]:
                continue
            if neft_item["source"] != "statement":
                continue
            neft_remarks_upper = neft_item["remarks"].upper()
            if "NEFT" not in neft_remarks_upper:
                continue
            neft_compact = compact_text(neft_remarks_upper)
            if sal_name in neft_compact:
                sections["less_bank_debit"].pop(sal_idx)
                sections["add_bank_credit"].pop(neft_idx)
                break


def build_brs_sections(
    unmatched_statement: list[dict[str, Any]],
    unmatched_book: list[dict[str, Any]],
    pending_carry_forward_items: list[dict[str, Any]],
    *,
    stale_after_days: int = 90,
    today: date | None = None,
    period_start: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Combine current-period unmatched rows with prior-period pending items."""

    today = today or date.today()
    sections = {section: [] for section in SECTION_ORDER}

    for item in pending_carry_forward_items:
        # Skip items that are already cleared/resolved in a prior period.
        # Items cleared within the current statement period are kept — their
        # clearing has not been verified by the reconciliation engine.
        cleared = item.get("cleared_on")
        if cleared is not None:
            if period_start is None or cleared < period_start:
                continue
        sections[item["section"]].append(
            {
                "date": item["original_date"],
                "remarks": item["remarks"],
                "cheque_no": item.get("cheque_no"),
                "amount": item["amount"],
                "cleared_on": item.get("cleared_on"),
                "source": "carry_forward",
                "stale": item["original_date"] < today - timedelta(days=stale_after_days),
            }
        )

    for row in unmatched_book:
        entry = {
            "date": row["voucher_date"],
            "remarks": row["narration"] or row["particulars"],
            "cheque_no": row.get("cheque_no"),
            "amount": row["amount"],
            "cleared_on": None,
            "source": "book",
            "row_number": row["row_number"],
        }
        if row["direction"] == "OUT":
            sections["add_cheque_issued"].append(entry)
        else:
            sections["less_cheque_deposit"].append(entry)

    for row in unmatched_statement:
        entry = {
            "date": row["value_date"],
            "remarks": row["description"],
            "cheque_no": row.get("cheque_no"),
            "amount": row["amount"],
            "cleared_on": None,
            "source": "statement",
            "row_number": row["row_number"],
        }
        if row["direction"] == "IN":
            sections["add_bank_credit"].append(entry)
        else:
            sections["less_bank_debit"].append(entry)

    for section in list(sections):
        sections[section] = _remove_offsetting_pairs(sections[section])

    # Cross-section offset: salary debit + corresponding NEFT credit return.
    _cross_section_salary_offset(sections)

    for section in list(sections):
        sections[section].sort(key=lambda item: (item["date"], item["remarks"]))

    return sections


def calculate_brs_totals(
    bank_book_balance: Decimal,
    bank_statement_balance: Decimal,
    sections: dict[str, list[dict[str, Any]]],
) -> dict[str, Decimal]:
    """Compute the BRS arithmetic totals and final difference."""

    totals = {
        section: sum((item["amount"] for item in items), Decimal("0.00"))
        for section, items in sections.items()
    }
    reconciled_balance = (
        bank_book_balance
        + totals["add_cheque_issued"]
        + totals["add_bank_credit"]
        - totals["less_cheque_deposit"]
        - totals["less_bank_debit"]
    )
    return {
        "bank_book_balance": bank_book_balance,
        "bank_statement_balance": bank_statement_balance,
        "reconciled_balance": reconciled_balance,
        "difference": reconciled_balance - bank_statement_balance,
        **totals,
    }


def build_exception_items(
    unmatched_statement: list[dict[str, Any]],
    unmatched_book: list[dict[str, Any]],
    pending_carry_forward_items: list[dict[str, Any]],
    *,
    stale_after_days: int = 90,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Generate lightweight exception records for the UI / API layer."""

    today = today or date.today()
    exceptions: list[dict[str, Any]] = []

    for row in unmatched_statement:
        category = "unknown_cr" if row["direction"] == "IN" else "unknown_dr"
        exceptions.append(
            {
                "source": "bank_statement",
                "row_number": row["row_number"],
                "category": category,
                "exception_type": category,
                "brs_section": "add_bank_credit" if row["direction"] == "IN" else "less_bank_debit",
                "amount": row["amount"],
                "direction": row["direction"],
                "date": row["value_date"],
                "description": row["description"],
            }
        )

    for row in unmatched_book:
        exceptions.append(
            {
                "source": "bank_book",
                "row_number": row["row_number"],
                "category": "timing_difference",
                "exception_type": "timing_difference",
                "brs_section": (
                    "less_cheque_deposit" if row["direction"] == "IN" else "add_cheque_issued"
                ),
                "amount": row["amount"],
                "direction": row["direction"],
                "date": row["voucher_date"],
                "description": row["narration"] or row["particulars"],
            }
        )

    for item in pending_carry_forward_items:
        if item["original_date"] >= today - timedelta(days=stale_after_days):
            continue
        exceptions.append(
            {
                "source": "carry_forward",
                "row_number": item["row_number"],
                "category": "stale_carry_forward",
                "exception_type": "stale_carry_forward",
                "brs_section": item["section"],
                "amount": item["amount"],
                "direction": item["direction"],
                "date": item["original_date"],
                "description": item["remarks"],
            }
        )

    return exceptions
