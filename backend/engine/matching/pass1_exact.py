"""Pass 1: exact structured-reference matching."""

from __future__ import annotations

import re
from typing import Any

from engine.matching.utils import mark_match, sum_amounts

# IFSC codes identify a bank branch, not a transaction.  They must not be used
# as the sole matching ref because multiple transactions can originate from the
# same branch.
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def _transaction_refs(refs: list[str]) -> set[str]:
    """Return refs that are genuine transaction identifiers (exclude IFSC codes)."""
    return {r for r in refs if not _IFSC_RE.match(r)}


def run_pass1(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match rows by exact structured references, including batched UPI many-to-one."""

    matches: list[dict[str, Any]] = []

    # Many statement rows -> one book row when a ledger row contains multiple refs.
    for book_row in [row for row in book_rows if not row["matched"] and len(row.get("refs", [])) > 1]:
        candidate_statements: list[dict[str, Any]] = []
        for ref in book_row["refs"]:
            statements = [
                row
                for row in statement_rows
                if not row["matched"]
                and row["direction"] == book_row["direction"]
                and ref in row.get("refs", [])
            ]
            if len(statements) != 1:
                candidate_statements = []
                break
            candidate_statements.extend(statements)

        if candidate_statements and sum_amounts(candidate_statements) == book_row["amount"]:
            matches.append(
                mark_match(
                    "exact_ref_multi_to_one",
                    candidate_statements,
                    [book_row],
                    pass_number=1,
                    notes="Batched structured references collapsed to one ledger row",
                )
            )

    # Standard one-to-one exact reference match.
    for statement_row in [row for row in statement_rows if not row["matched"] and row.get("refs")]:
        stmt_txn_refs = _transaction_refs(statement_row["refs"])
        if not stmt_txn_refs:
            continue
        candidates = [
            row
            for row in book_rows
            if not row["matched"]
            and row["direction"] == statement_row["direction"]
            and row["amount"] == statement_row["amount"]
            and stmt_txn_refs.intersection(_transaction_refs(row.get("refs", [])))
        ]
        if len(candidates) != 1:
            continue

        matches.append(
            mark_match(
                "exact_ref",
                [statement_row],
                [candidates[0]],
                pass_number=1,
                notes="Exact structured reference match",
            )
        )

    # Cheque number ↔ NEFT reference cross-match.
    for statement_row in [row for row in statement_rows if not row["matched"] and row.get("refs")]:
        for ref in statement_row["refs"]:
            candidates = [
                row
                for row in book_rows
                if not row["matched"]
                and row["direction"] == statement_row["direction"]
                and row["amount"] == statement_row["amount"]
                and row.get("cheque_no")
                and row["cheque_no"].strip() == ref
            ]
            if len(candidates) == 1:
                matches.append(
                    mark_match(
                        "cheque_ref",
                        [statement_row],
                        [candidates[0]],
                        pass_number=1,
                        notes=f"Cheque number matched NEFT ref {ref}",
                    )
                )
                break

    # Direct cheque number match: statement cheque_no ↔ book cheque_no.
    # Handles CLG (clearing house) entries where the ref extractor finds no refs
    # but the ChequeNo. column value matches the book's cheque number.
    for statement_row in [row for row in statement_rows if not row["matched"] and row.get("cheque_no")]:
        stmt_chq = _normalise_cheque_no(statement_row["cheque_no"])
        if not stmt_chq:
            continue
        candidates = [
            row
            for row in book_rows
            if not row["matched"]
            and row["direction"] == statement_row["direction"]
            and row["amount"] == statement_row["amount"]
            and row.get("cheque_no")
            and _normalise_cheque_no(row["cheque_no"]) == stmt_chq
        ]
        if len(candidates) == 1:
            matches.append(
                mark_match(
                    "cheque_direct",
                    [statement_row],
                    [candidates[0]],
                    pass_number=1,
                    notes=f"Direct cheque number match {stmt_chq}",
                )
            )

    return matches


def _normalise_cheque_no(raw: str) -> str:
    """Strip trailing '.0' and whitespace from cheque numbers."""
    s = str(raw).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s
