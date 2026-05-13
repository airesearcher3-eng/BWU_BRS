"""Pass 4: fixed-deposit and contra transfer matching."""

from __future__ import annotations

from typing import Any

from engine.matching.utils import mark_match
from engine.reference_extractor import extract_fd_number


def run_pass4(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match FD booking, FD maturity, and contra/CNT transfers."""

    matches: list[dict[str, Any]] = []

    for statement_row in [row for row in statement_rows if not row["matched"]]:
        description_upper = statement_row["description"].upper()
        fd_number = extract_fd_number(statement_row["description"])

        if fd_number and "TRF TO FD" in description_upper:
            candidates = [
                row
                for row in book_rows
                if not row["matched"]
                and row["voucher_type"] in {"CNT", "PMT"}
                and row["direction"] == statement_row["direction"]
                and row["amount"] == statement_row["amount"]
                and fd_number in f"{row['narration']} {row['particulars']}"
            ]
            if len(candidates) == 1:
                matches.append(
                    mark_match(
                        "fd_booking",
                        [statement_row],
                        [candidates[0]],
                        pass_number=4,
                        notes=f"FD booking {fd_number}",
                    )
                )
                continue

        # Ujjivan FD: "NEW FD BOOKING A/C 3314130340000045"
        if fd_number and "NEW FD BOOKING" in description_upper:
            candidates = [
                row
                for row in book_rows
                if not row["matched"]
                and row["direction"] == statement_row["direction"]
                and row["amount"] == statement_row["amount"]
                and fd_number in f"{row['narration']} {row['particulars']}"
            ]
            if len(candidates) == 1:
                matches.append(
                    mark_match(
                        "fd_booking",
                        [statement_row],
                        [candidates[0]],
                        pass_number=4,
                        notes=f"FD booking (Ujjivan) {fd_number}",
                    )
                )
                continue

        if fd_number and "FD CLOS" in description_upper:
            candidates = [
                row
                for row in book_rows
                if not row["matched"]
                and row["direction"] == statement_row["direction"]
                and row["amount"] == statement_row["amount"]
                and fd_number in f"{row['narration']} {row['particulars']}"
            ]
            if len(candidates) == 1:
                matches.append(
                    mark_match(
                        "fd_maturity",
                        [statement_row],
                        [candidates[0]],
                        pass_number=4,
                        notes=f"FD maturity {fd_number}",
                    )
                )
                continue

        if statement_row["direction"] == "OUT" and (
            "TRF" in description_upper or "TRANSFER" in description_upper or statement_row.get("is_fd")
        ):
            candidates = [
                row
                for row in book_rows
                if not row["matched"]
                and row["voucher_type"] == "CNT"
                and row["direction"] == statement_row["direction"]
                and row["amount"] == statement_row["amount"]
                and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= 2
            ]
            if len(candidates) == 1:
                matches.append(
                    mark_match(
                        "contra",
                        [statement_row],
                        [candidates[0]],
                        pass_number=4,
                        notes="Contra transfer",
                    )
                )

    return matches


def _row_date(row: dict[str, Any]):
    """Return the canonical date field for mixed row types."""

    return row.get("value_date") or row.get("voucher_date")
