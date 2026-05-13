"""Pass 5: aggressive fallback matching for remaining unmatched items.

Runs after passes 1-4 with progressively relaxed constraints:
  - Wider date tolerance (up to 10 days)
  - Name-fragment matching (student names, payee names)
  - Enrollment ID / MR number cross-matching between statement and book
  - Amount-only matching for unique amounts within date windows
"""

from __future__ import annotations

import re

from typing import Any

from engine.matching.utils import iter_amount_matching_subsets, mark_match
from engine.reference_extractor import (
    compact_text,
    extract_enrollment_ids,
    extract_mr_numbers,
    extract_neft_payee,
    is_fd_description,
    is_neft_inft_description,
)


def run_pass5(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggressive fallback matching for items that survived passes 1-4."""

    matches: list[dict[str, Any]] = []

    # ── Strategy 0: NEFT ref-group → aggregated statement credit ─────
    # Multiple book entries sharing the same NEFT ref (from Tn.No field) were
    # deposited together and credited by the bank as a single entry.
    # Group unmatched book IN entries by their first ref, sum within each group,
    # then match to an unmatched statement IN entry of the same total amount.
    from collections import defaultdict
    ref_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for book_row in book_rows:
        if book_row["matched"] or book_row["direction"] != "IN":
            continue
        refs = book_row.get("refs", [])
        if len(refs) == 1 and refs[0].isdigit():
            ref_groups[refs[0]].append(book_row)

    for ref, group in ref_groups.items():
        if len(group) < 2:
            continue
        # All must still be unmatched (may have been consumed above).
        if any(row["matched"] for row in group):
            continue
        group_total = sum(row["amount"] for row in group)
        group_date = min(_row_date(row) for row in group)
        # Find statement entry with same total, direction=IN, close date.
        candidates = [
            row for row in statement_rows
            if not row["matched"]
            and row["direction"] == "IN"
            and row["amount"] == group_total
            and abs(_row_date(row).toordinal() - group_date.toordinal()) <= 5
        ]
        if len(candidates) == 1:
            matches.append(
                mark_match(
                    "neft_ref_group",
                    [candidates[0]],
                    group,
                    pass_number=5,
                    notes=f"Grouped {len(group)} book entries (ref {ref}) into one statement credit",
                )
            )
        elif len(candidates) > 1:
            # Pick closest date.
            g_ord = group_date.toordinal()
            candidates.sort(key=lambda r: abs(_row_date(r).toordinal() - g_ord))
            matches.append(
                mark_match(
                    "neft_ref_group",
                    [candidates[0]],
                    group,
                    pass_number=5,
                    notes=f"Grouped {len(group)} book entries (ref {ref}) into one statement credit",
                )
            )

    # ── Strategy A: Enrollment ID / MR number cross-match ────────────
    # If a statement description contains a student ID (MLM23003, BWU/BHM/23/008)
    # and the same ID appears in a book narration, match them.
    for statement_row in [row for row in statement_rows if not row["matched"]]:
        desc = statement_row["description"]
        enrollment_ids = extract_enrollment_ids(desc)
        mr_numbers = extract_mr_numbers(desc)

        if not enrollment_ids and not mr_numbers:
            continue

        candidates = []
        for row in book_rows:
            if row["matched"] or row["direction"] != statement_row["direction"]:
                continue
            if row["amount"] != statement_row["amount"]:
                continue
            if abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) > 10:
                continue

            ledger_text = f"{row.get('narration', '')} {row.get('particulars', '')}".upper()
            for eid in enrollment_ids:
                if eid in ledger_text or eid.replace("/", "") in compact_text(ledger_text):
                    candidates.append(row)
                    break
            else:
                for mr in mr_numbers:
                    if mr in ledger_text or mr.replace("/", "") in compact_text(ledger_text):
                        candidates.append(row)
                        break

        if len(candidates) == 1:
            matches.append(
                mark_match(
                    "enrollment_id",
                    [statement_row],
                    [candidates[0]],
                    pass_number=5,
                    notes=f"Enrollment/MR ID match ({enrollment_ids or mr_numbers})",
                )
            )

    # ── Strategy B: Name fragment matching ───────────────────────────
    # Extract payee/student name from the statement description and search
    # for it in the book narration. Works for NEFT, INF/INFT, etc.
    for statement_row in [row for row in statement_rows if not row["matched"]]:
        payee = extract_neft_payee(statement_row["description"])
        if not payee or len(payee) < 5:
            continue

        payee_compact = compact_text(payee)
        # Also try splitting concatenated names like "SHREYASHIGHOSH"
        # into fragments of 5+ characters for partial matching.

        candidates = []
        for row in book_rows:
            if row["matched"] or row["direction"] != statement_row["direction"]:
                continue
            if row["amount"] != statement_row["amount"]:
                continue
            if abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) > 10:
                continue

            ledger_text = compact_text(
                f"{row.get('narration', '')} {row.get('particulars', '')}"
            )
            if payee_compact in ledger_text:
                candidates.append(row)

        if len(candidates) == 1:
            matches.append(
                mark_match(
                    "name_match",
                    [statement_row],
                    [candidates[0]],
                    pass_number=5,
                    notes=f"Payee name match: {payee}",
                )
            )

    # ── Strategy C: Book narration → statement name match ────────────
    # Reverse direction: search book narration names in statement descriptions.
    for book_row in [row for row in book_rows if not row["matched"]]:
        narration = book_row.get("narration", "")
        particulars = book_row.get("particulars", "")
        ledger_text = f"{narration} {particulars}"

        # Extract enrollment IDs from book narration.
        book_enrollment = extract_enrollment_ids(ledger_text)
        book_mr = extract_mr_numbers(ledger_text)

        if not book_enrollment and not book_mr:
            continue

        candidates = []
        for row in statement_rows:
            if row["matched"] or row["direction"] != book_row["direction"]:
                continue
            if row["amount"] != book_row["amount"]:
                continue
            if abs(_row_date(row).toordinal() - _row_date(book_row).toordinal()) > 10:
                continue

            desc_text = row["description"].upper()
            desc_compact = compact_text(desc_text)
            for eid in book_enrollment:
                eid_no_slash = eid.replace("/", "")
                # Also try without BWU prefix (statement often has short form e.g. "BNC22120")
                eid_short = re.sub(r"^BWU/?", "", eid, flags=re.IGNORECASE).replace("/", "")
                if (
                    eid in desc_text
                    or eid_no_slash in desc_compact
                    or (eid_short != eid_no_slash and eid_short in desc_compact)
                ):
                    candidates.append(row)
                    break
            else:
                for mr in book_mr:
                    if mr in desc_text or mr.replace("/", "") in desc_compact:
                        candidates.append(row)
                        break

        if len(candidates) == 1:
            matches.append(
                mark_match(
                    "reverse_id_match",
                    [candidates[0]],
                    [book_row],
                    pass_number=5,
                    notes=f"Book enrollment/MR ID found in statement ({book_enrollment or book_mr})",
                )
            )

    # ── Strategy D: Wider-window unique amount matching ──────────────
    # For entries that still have no match, try unique amount within wider date windows.
    for tolerance in (3, 5, 7, 10, 13):
        for statement_row in [row for row in statement_rows if not row["matched"]]:
            # Skip FD descriptions to avoid false positives.
            if is_fd_description(statement_row["description"]):
                continue

            candidates = [
                row
                for row in book_rows
                if not row["matched"]
                and row["direction"] == statement_row["direction"]
                and row["amount"] == statement_row["amount"]
                and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= tolerance
            ]
            if len(candidates) != 1:
                continue

            # Ref-conflict guard: if both entries have references and they
            # don't overlap, this is likely a coincidental amount match.
            cand = candidates[0]
            s_refs = set(statement_row.get("refs", []))
            b_refs = set(cand.get("refs", []))
            if s_refs and b_refs and not s_refs & b_refs:
                continue

            matches.append(
                mark_match(
                    f"amount_date_wide_{tolerance}",
                    [statement_row],
                    [candidates[0]],
                    pass_number=5,
                    notes=f"Unique amount within {tolerance}-day window",
                )
            )

    # ── Strategy E: Many-to-one amount grouping ──────────────────────
    # Multiple book rows summing to one statement amount (salary batches, vendor batches).
    for statement_row in [row for row in statement_rows if not row["matched"]]:
        candidates = [
            row
            for row in book_rows
            if not row["matched"]
            and row["direction"] == statement_row["direction"]
            and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= 5
        ]
        if len(candidates) < 2 or len(candidates) > 12:
            continue

        for subset in iter_amount_matching_subsets(candidates, statement_row["amount"], max_size=8):
            matches.append(
                mark_match(
                    "amount_group_fallback",
                    [statement_row],
                    subset,
                    pass_number=5,
                    notes="Fallback amount grouping within date window",
                )
            )
            break

    # ── Strategy F: Reverse many-to-one (one book → many statement) ──
    # One book entry that should match multiple statement entries by amount sum.
    # Common for inter-bank transfers split across NEFT + RTGS.
    for book_row in [row for row in book_rows if not row["matched"]]:
        candidates = [
            row
            for row in statement_rows
            if not row["matched"]
            and row["direction"] == book_row["direction"]
            and abs(_row_date(row).toordinal() - _row_date(book_row).toordinal()) <= 5
        ]
        if len(candidates) < 2 or len(candidates) > 15:
            continue

        for subset in iter_amount_matching_subsets(candidates, book_row["amount"], max_size=10):
            matches.append(
                mark_match(
                    "reverse_amount_group",
                    subset,
                    [book_row],
                    pass_number=5,
                    notes="One book entry matched to multiple statement entries by amount sum",
                )
            )
            break

    # ── Strategy G: Pairwise same-amount matching by date proximity ──
    # When Strategy D skips entries because multiple candidates exist,
    # try greedily pairing by closest date.  Limited to entries from the
    # current reconciliation period (not carry-forward) to avoid false
    # positives between unrelated legacy items.
    for statement_row in [row for row in statement_rows if not row["matched"]]:
        # Skip carry-forward items — they are from prior periods and should
        # not be matched purely by amount coincidence.
        if statement_row.get("source") == "carry_forward":
            continue

        candidates = [
            row
            for row in book_rows
            if not row["matched"]
            and row["direction"] == statement_row["direction"]
            and row["amount"] == statement_row["amount"]
            and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= 15
            and row.get("source") != "carry_forward"
        ]
        if len(candidates) < 1:
            continue
        # Strategy D already handled the unique case.
        if len(candidates) == 1:
            continue
        # Pick the candidate closest by date.
        s_ord = _row_date(statement_row).toordinal()
        candidates.sort(key=lambda r: abs(_row_date(r).toordinal() - s_ord))
        best = candidates[0]
        # Only accept if the closest candidate is within 7 days.
        if abs(_row_date(best).toordinal() - s_ord) <= 7:
            matches.append(
                mark_match(
                    "amount_date_closest",
                    [statement_row],
                    [best],
                    pass_number=5,
                    notes=f"Closest date match among {len(candidates)} candidates",
                )
            )

    return matches


def _row_date(row: dict[str, Any]):
    """Return the canonical date field for mixed row types."""

    return row.get("value_date") or row.get("voucher_date")
