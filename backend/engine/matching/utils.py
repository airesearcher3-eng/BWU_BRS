"""Shared helpers used by the reconciliation matching passes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import combinations
from typing import Any, Iterable

from engine.normaliser import amounts_equal
from engine.reference_extractor import compact_text, extract_bill_aliases


def days_apart(left: date | None, right: date | None) -> int:
    """Return the absolute day difference between 2 optional dates."""

    if left is None or right is None:
        return 10_000
    return abs((left - right).days)


def sum_amounts(items: Iterable[dict[str, Any]]) -> Decimal:
    """Sum the ``amount`` field across a collection of transaction dicts."""

    total = Decimal("0.00")
    for item in items:
        total += item["amount"]
    return total


def iter_amount_matching_subsets(
    items: list[dict[str, Any]],
    target: Decimal,
    *,
    min_size: int = 2,
    max_size: int = 6,
) -> Iterable[list[dict[str, Any]]]:
    """Yield the FIRST candidate subset whose summed amount matches the target exactly.

    Phase 6 guards:
    - Hard cap: candidate set is trimmed to 20 rows before any search begins.
      This prevents exponential blowup when many book rows have close amounts.
    - Early exit: search stops on the first valid combination found.  The first
      match is sufficient for reconciliation purposes.
    """
    # Phase 6: hard cap — trim to 20 rows before exponential search begins.
    if len(items) > 20:
        items = items[:20]

    # Fast-path: if ALL items sum to the target, yield the full set immediately.
    if len(items) >= min_size and amounts_equal(sum_amounts(items), target):
        yield list(items)
        return

    # Near-full-set: for large sets try removing 1–2 items instead of O(2^n) search.
    if len(items) > max_size and len(items) >= min_size + 1:
        total = sum_amounts(items)
        excess = total - target
        if excess > 0:
            for i, item in enumerate(items):
                if amounts_equal(item["amount"], excess):
                    yield [x for j, x in enumerate(items) if j != i]
                    return
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    if amounts_equal(items[i]["amount"] + items[j]["amount"], excess):
                        yield [x for k, x in enumerate(items) if k != i and k != j]
                        return

    upper = min(max_size, len(items))
    for size in range(min_size, upper + 1):
        for combo in combinations(items, size):
            if amounts_equal(sum_amounts(combo), target):
                yield list(combo)
                return  # Phase 6: exit on first valid combination found


def mark_match(
    name: str,
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
    *,
    pass_number: int,
    notes: str | None = None,
) -> dict[str, Any]:
    """Mark matched transactions in place and return a match descriptor."""

    for row in statement_rows:
        row["matched"] = True
        row["match_state"] = "matched"
    for row in book_rows:
        row["matched"] = True
        row["match_state"] = "matched"

    return {
        "match_type": name,
        "pass_number": pass_number,
        "statement_rows": [row["row_number"] for row in statement_rows],
        "book_rows": [row["row_number"] for row in book_rows],
        "amount": max(sum_amounts(statement_rows), sum_amounts(book_rows)),
        "notes": notes or "",
    }


def mark_partial_match(
    name: str,
    statement_row: dict[str, Any],
    book_rows: list[dict[str, Any]],
    *,
    pass_number: int,
    notes: str | None = None,
) -> dict[str, Any]:
    """Match book rows against part of a statement amount, leaving the remainder unmatched."""

    book_total = sum_amounts(book_rows)
    original_amount = statement_row["amount"]
    remainder = original_amount - book_total

    for row in book_rows:
        row["matched"] = True
        row["match_state"] = "matched"

    # Reduce statement amount to the unmatched remainder; keep it unmatched
    statement_row["amount"] = remainder
    statement_row["original_amount"] = original_amount
    statement_row["partial_match"] = True

    return {
        "match_type": name,
        "pass_number": pass_number,
        "statement_rows": [statement_row["row_number"]],
        "book_rows": [row["row_number"] for row in book_rows],
        "amount": book_total,
        "notes": notes or "",
    }


def alias_score(description: str, book_row: dict[str, Any]) -> int:
    """Score how strongly a statement description matches a ledger text block."""

    aliases = [compact_text(alias) for alias in extract_bill_aliases(description)]
    if not aliases:
        return 0

    ledger_text = compact_text(
        " ".join(filter(None, [book_row.get("narration"), book_row.get("particulars")]))
    )
    return sum(1 for alias in aliases if alias and alias in ledger_text)
