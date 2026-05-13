"""Pass 3: rule-based reconciliation for GIB, BIL, NEFT/INFT, and reversal patterns."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from engine.matching.pass1_exact import _IFSC_RE
from engine.matching.utils import alias_score, iter_amount_matching_subsets, mark_match, mark_partial_match, sum_amounts
from engine.reference_extractor import (
    GIB_KEYWORD_MAP,
    compact_text,
    extract_enrollment_ids,
    extract_gib_tax_type,
    extract_neft_payee,
    extract_neft_payee_from_narration,
    is_neft_inft_description,
)

# ---------------------------------------------------------------------------
# Portal settlement constants
# ---------------------------------------------------------------------------
# Bank book narrations for portal-collected fees embed the settlement date as:
#   "Portal Settled on DD-MM-YYYY" or "Portal Settled on DD-MM-YY"
_PORTAL_SETTLED_ON_RE = re.compile(
    r"Portal\s+Settled\s+on\s+(\d{2}-\d{2}-\d{2,4})", re.IGNORECASE
)

# Corresponding bank statement description patterns by payment type:
#   Rule 1 – Debit/Credit Card  → settled next working day
#   Rule 2 – UPI               → settled next working day
#   Rule 3 – NEFT              → settled after 2 days (via PAYU PAYMENTS)
#   Rule 4 – POS               → settled next working day
PORTAL_STMT_KEYWORDS: tuple[str, ...] = (
    "76017672TERMINAL 1 CARDS SETTL",  # D/C Card portal settlement
    "UPI SETTLEMENT -I78231",           # UPI portal settlement
    "PAYU PAYMENTS",                    # NEFT portal settlement
    "99857247TERMINAL 1 CARDS SETTL",   # POS terminal 99857247 settlement
    "99863812TERMINAL 1 CARDS SETTL",   # POS terminal 99863812 settlement
)


def _parse_portal_settled_date(narration: str):
    """Return the settlement date from a 'Portal Settled on …' bank-book narration."""
    m = _PORTAL_SETTLED_ON_RE.search(narration)
    if not m:
        return None
    date_str = m.group(1)
    for fmt in ("%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass
    return None


def _is_portal_settlement(description: str) -> bool:
    """Return True if a bank statement description is a portal settlement credit."""
    desc_upper = description.upper()
    return any(kw.upper() in desc_upper for kw in PORTAL_STMT_KEYWORDS)


def run_pass3(
    statement_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply deterministic keyword- and pattern-driven matching rules."""

    matches: list[dict[str, Any]] = []

    # ── RTGS many-to-one: multiple RTGS statement credits → one CNT book entry ──
    # Group unmatched RTGS statement rows by date, then check if any single book
    # CNT/REC row's narration contains the RTGS ref codes and amounts sum up.
    rtgs_rows = [
        row for row in statement_rows
        if not row["matched"] and (
            row["description"].upper().startswith("RTGS-")
            or row["description"].upper().startswith("RTGS CR-")
        )
    ]
    if rtgs_rows:
        # Group by (date, direction)
        rtgs_groups: dict[tuple, list] = defaultdict(list)
        for row in rtgs_rows:
            rtgs_groups[(_row_date(row), row["direction"])].append(row)

        for (dt, direction), group in rtgs_groups.items():
            group_total = sum_amounts(group)
            # Extract RTGS ref codes from descriptions (e.g. BDBLR62026010622200458)
            rtgs_refs = []
            for row in group:
                parts = row["description"].split("-")
                if len(parts) >= 2:
                    rtgs_refs.append(parts[1].strip())

            # Find book CNT/REC entries with matching amount and RTGS refs in narration
            for book_row in book_rows:
                if book_row["matched"] or book_row["direction"] != direction:
                    continue
                if book_row["amount"] != group_total:
                    continue
                if abs(_row_date(book_row).toordinal() - dt.toordinal()) > 3:
                    continue
                # Check if any RTGS ref appears in the book narration
                narr = compact_text(book_row.get("narration", ""))
                narr_upper = (book_row.get("narration", "") or "").upper()
                ref_match = any(compact_text(ref) in narr for ref in rtgs_refs if ref)
                # Also match if the book narration mentions RTGS/transfer and
                # the RTGS entries come from an identifiable source bank.
                keyword_match = (
                    ("RTGS" in narr_upper or "TRANSFER" in narr_upper)
                    and len(group) >= 2
                )
                if ref_match or keyword_match:
                    matches.append(
                        mark_match(
                            "rule_rtgs_group",
                            group,
                            [book_row],
                            pass_number=3,
                            notes=f"Grouped {len(group)} RTGS credits into one CNT entry",
                        )
                    )
                    break

    # ── Inter-bank transfer: book entry mentions account number fragments that ──
    # appear in NEFT/RTGS statement descriptions.  E.g. book narration has
    # "transfer from HDFC BANK - 4321 and Ujjivan SFB - 2414" while statement
    # has separate NEFT and RTGS credits referencing those account numbers.
    _ACCT_FRAG_RE = re.compile(r"[-–]\s*(\d{4,})")
    for book_row in [row for row in book_rows if not row["matched"]]:
        narr = book_row.get("narration", "") or ""
        narr_upper = narr.upper()
        if "TRANSFER" not in narr_upper and "TRFR" not in narr_upper:
            continue
        # Extract 4+ digit account number fragments from the narration.
        acct_frags = _ACCT_FRAG_RE.findall(narr)
        if not acct_frags:
            continue

        candidates: list[dict[str, Any]] = []
        for s_row in statement_rows:
            if s_row["matched"]:
                continue
            if s_row["direction"] != book_row["direction"]:
                continue
            if abs(_row_date(book_row).toordinal() - _row_date(s_row).toordinal()) > 5:
                continue
            desc = s_row.get("description", "") or ""
            if any(frag in desc for frag in acct_frags):
                candidates.append(s_row)

        if not candidates:
            continue
        # Check if all candidates sum to book amount.
        if sum_amounts(candidates) == book_row["amount"]:
            matches.append(
                mark_match(
                    "rule_interbank_transfer",
                    candidates,
                    [book_row],
                    pass_number=3,
                    notes=f"Inter-bank transfer matched via account fragments {acct_frags}",
                )
            )
            continue
        # Try subsets.
        for subset in iter_amount_matching_subsets(candidates, book_row["amount"]):
            matches.append(
                mark_match(
                    "rule_interbank_transfer",
                    subset,
                    [book_row],
                    pass_number=3,
                    notes=f"Inter-bank transfer matched via account fragments {acct_frags}",
                )
            )
            break

    # Aggregate same-day GIB rows into the corresponding PMT voucher.
    for book_row in [row for row in book_rows if not row["matched"] and row["voucher_type"] == "PMT"]:
        ledger_text = compact_text(f"{book_row['narration']} {book_row['particulars']}")
        tax_type = next(
            (
                tax
                for tax, keywords in GIB_KEYWORD_MAP.items()
                if any(compact_text(keyword) in ledger_text for keyword in keywords)
            ),
            None,
        )
        if not tax_type:
            continue

        candidates = [
            row
            for row in statement_rows
            if not row["matched"]
            and row["direction"] == "OUT"
            and _row_date(row) == _row_date(book_row)
            and extract_gib_tax_type(row["description"]) == tax_type
        ]
        if not candidates:
            continue

        if sum(row["amount"] for row in candidates) == book_row["amount"]:
            matches.append(
                mark_match(
                    "rule_gib",
                    candidates,
                    [book_row],
                    pass_number=3,
                    notes=f"Grouped {tax_type} debits into one PMT voucher",
                )
            )
            continue

        for candidate in candidates:
            if candidate["amount"] == book_row["amount"]:
                matches.append(
                    mark_match(
                        "rule_gib",
                        [candidate],
                        [book_row],
                        pass_number=3,
                        notes=f"Direct {tax_type} amount match",
                    )
                )
                break

    # BIL rows: direct payee match or grouped payment plus bank charges.
    for statement_row in [
        row
        for row in statement_rows
        if not row["matched"] and row["description"].upper().startswith(("BIL/ONL/", "BIL/INFT/"))
    ]:
        candidates = [
            row
            for row in book_rows
            if not row["matched"]
            and row["direction"] == "OUT"
            and row["voucher_type"] == "PMT"
            and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= 4
            and (
                alias_score(statement_row["description"], row) > 0
                or (
                    "COUNCIL" in statement_row["description"].upper()
                    and "BANK CHARGES" in row["particulars"].upper()
                )
            )
        ]

        exact = [row for row in candidates if row["amount"] == statement_row["amount"]]
        if len(exact) == 1:
            matches.append(
                mark_match(
                    "rule_bil",
                    [statement_row],
                    [exact[0]],
                    pass_number=3,
                    notes="Exact BIL payee/amount match",
                )
            )
            continue

        for subset in iter_amount_matching_subsets(candidates, statement_row["amount"]):
            matches.append(
                mark_match(
                    "rule_bil_group",
                    [statement_row],
                    subset,
                    pass_number=3,
                    notes="Grouped BIL payment and related charges",
                )
            )
            break

    # NEFT / INFT outward and inward rows: match by amount + date + payee/cheque cross-ref.
    for statement_row in [
        row
        for row in statement_rows
        if not row["matched"] and is_neft_inft_description(row["description"])
    ]:
        neft_refs = statement_row.get("refs", [])
        payee = extract_neft_payee(statement_row["description"])
        payee_compact = compact_text(payee) if payee else ""
        # Extract enrollment IDs embedded in the NEFT description.
        enrollment_ids = extract_enrollment_ids(statement_row["description"])

        candidates = []
        for row in book_rows:
            if row["matched"] or row["direction"] != statement_row["direction"]:
                continue
            days_diff = abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal())
            if days_diff > 35:
                continue

            ledger_text = compact_text(
                f"{row.get('narration', '')} {row.get('particulars', '')}"
            )

            # Strategy 1: cheque number matches the NEFT reference code.
            cheque = (row.get("cheque_no") or "").strip()
            if cheque and neft_refs and cheque in neft_refs:
                candidates.append(row)
                continue

            # Strategy 2: payee name appears in narration or particulars.
            if payee_compact and len(payee_compact) >= 5:
                if payee_compact in ledger_text:
                    candidates.append(row)
                    continue

            # Strategy 3: alias-based text scoring.
            if alias_score(statement_row["description"], row) > 0:
                candidates.append(row)
                continue

            # Strategy 4: enrollment/registration ID from NEFT description in ledger text.
            if enrollment_ids:
                for eid in enrollment_ids:
                    if compact_text(eid) in ledger_text:
                        candidates.append(row)
                        break
                if row in candidates:
                    continue

            # Strategy 5: NEFT ref code appears in the book narration text.
            for ref in neft_refs:
                if _IFSC_RE.match(ref):
                    continue
                if ref in ledger_text:
                    candidates.append(row)
                    break

        # Exact unique match.
        exact = [row for row in candidates if row["amount"] == statement_row["amount"]]
        if len(exact) == 1:
            matches.append(
                mark_match(
                    "rule_neft",
                    [statement_row],
                    [exact[0]],
                    pass_number=3,
                    notes=f"NEFT/INFT match via payee/cheque ({payee or 'ref'})",
                )
            )
            continue

        # Aggregate: multiple book rows summing to the NEFT amount.
        for subset in iter_amount_matching_subsets(candidates, statement_row["amount"]):
            matches.append(
                mark_match(
                    "rule_neft_group",
                    [statement_row],
                    subset,
                    pass_number=3,
                    notes="Grouped NEFT/INFT payment entries",
                )
            )
            break

    # Narration-based NEFT payee matching: book narration says "Neft to X a/c"
    # or "favour of X A/c" — match statement NEFT entries by the alternate payee
    # name with a wider date window (vendor payments may be booked at month-end).
    for book_row in [row for row in book_rows if not row["matched"] and row["direction"] == "OUT"]:
        neft_payee = extract_neft_payee_from_narration(book_row.get("narration", ""))
        if not neft_payee:
            continue
        neft_payee_compact = compact_text(neft_payee)
        if len(neft_payee_compact) < 5:
            continue

        candidates = [
            row
            for row in statement_rows
            if not row["matched"]
            and row["direction"] == "OUT"
            and row["amount"] == book_row["amount"]
            and abs(_row_date(row).toordinal() - _row_date(book_row).toordinal()) <= 35
            and is_neft_inft_description(row["description"])
            and neft_payee_compact in compact_text(row["description"])
        ]
        if len(candidates) == 1:
            matches.append(
                mark_match(
                    "rule_neft_narration_payee",
                    [candidates[0]],
                    [book_row],
                    pass_number=3,
                    notes=f"Book narration NEFT payee '{neft_payee}' matched statement",
                )
            )

    # Return / reversal handling (NEFT-RETURN, incorrect account number, etc.).
    for statement_row in [row for row in statement_rows if not row["matched"]]:
        description_upper = statement_row["description"].upper()
        if "RETURN" not in description_upper and "INCORRECT ACCOUNT NUMBER" not in description_upper:
            continue

        neft_refs = statement_row.get("refs", [])
        candidates = [
            row
            for row in book_rows
            if not row["matched"]
            and row["direction"] == statement_row["direction"]
            and row["amount"] == statement_row["amount"]
            and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= 7
            and (
                "REVERSE" in row["narration"].upper()
                or "INCORRECT" in row["narration"].upper()
                or "NEFT-RETURN" in row["narration"].upper()
                or "RETURN" in row["narration"].upper()
                or any(ref in compact_text(row["narration"]) for ref in neft_refs)
            )
        ]
        if len(candidates) != 1:
            continue

        matches.append(
            mark_match(
                "rule_return",
                [statement_row],
                [candidates[0]],
                pass_number=3,
                notes="Return / incorrect account reversal",
            )
        )

    # Statement-only reversal pairs such as BIL debit followed by CMS reversal.
    for statement_row in [
        row
        for row in statement_rows
        if not row["matched"] and row["direction"] == "OUT" and "BIL/" in row["description"].upper()
    ]:
        candidates = [
            row
            for row in statement_rows
            if not row["matched"]
            and row["direction"] == "IN"
            and row["amount"] == statement_row["amount"]
            and _row_date(row) >= _row_date(statement_row)
            and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= 7
            and ("REV" in row["description"].upper() or "RETURN" in row["description"].upper())
        ]
        if len(candidates) != 1:
            continue

        reversal = candidates[0]
        statement_row["matched"] = True
        statement_row["match_state"] = "matched"
        reversal["matched"] = True
        reversal["match_state"] = "matched"
        matches.append(
            {
                "match_type": "rule_statement_reversal",
                "pass_number": 3,
                "statement_rows": [statement_row["row_number"], reversal["row_number"]],
                "book_rows": [],
                "amount": statement_row["amount"],
                "notes": "Bank-only reversal pair",
            }
        )

    # CHQ DEP / CHQ DEP RTN reversal pairs: a cheque deposit credited by bank
    # (IN) followed by a cheque deposit return debited (OUT). These cancel out.
    # Pair by closest date when multiple candidates exist.
    chq_rtn_rows = [
        row
        for row in statement_rows
        if not row["matched"]
        and row["direction"] == "OUT"
        and "CHQ DEP RTN" in row["description"].upper()
    ]
    for statement_row in chq_rtn_rows:
        # Extract cheque number from CHQ DEP RTN description.
        chq_match = re.search(r"CHQ DEP RTN/0*(\d+)/", statement_row["description"])
        chq_no = chq_match.group(1) if chq_match else None

        candidates = [
            row
            for row in statement_rows
            if not row["matched"]
            and row["direction"] == "IN"
            and row["amount"] == statement_row["amount"]
            and "CHQ DEP" in row["description"].upper()
            and "RTN" not in row["description"].upper()
            and abs(_row_date(row).toordinal() - _row_date(statement_row).toordinal()) <= 10
            and (
                chq_no is None
                or chq_no in row["description"]
                or f"0{chq_no}" in row["description"]
                or f"00{chq_no}" in row["description"]
                or f"000{chq_no}" in row["description"]
            )
        ]
        if not candidates:
            continue

        # Pick the closest candidate by date, then by row number.
        deposit = min(
            candidates,
            key=lambda r: (abs(_row_date(r).toordinal() - _row_date(statement_row).toordinal()), r["row_number"]),
        )
        statement_row["matched"] = True
        statement_row["match_state"] = "matched"
        deposit["matched"] = True
        deposit["match_state"] = "matched"
        matches.append(
            {
                "match_type": "rule_chq_dep_return",
                "pass_number": 3,
                "statement_rows": [deposit["row_number"], statement_row["row_number"]],
                "book_rows": [],
                "amount": statement_row["amount"],
                "notes": f"Cheque deposit/return reversal pair (chq {chq_no})",
            }
        )

    # ── Portal settlement matching ──────────────────────────────────────────
    # The university collects fees through its payment portal (Debit/Credit
    # Card, UPI, NEFT, POS).  The bank aggregates and settles these collections
    # back to the university account on the next working day (Card/UPI/POS) or
    # after 2 days (NEFT).  The bank book records individual fee receipts whose
    # narration contains "Portal Settled on DD-MM-YYYY", giving the exact date
    # on which the corresponding bank statement credit will appear.
    #
    # Settlement entry patterns in the bank statement:
    #   1. 76017672TERMINAL 1 CARDS SETTL  → D/C Card  (next working day)
    #   2. UPI SETTLEMENT -I78231           → UPI       (next working day)
    #   3. PAYU PAYMENTS                    → NEFT      (after 2 days)
    #   4. 99857247TERMINAL 1 CARDS SETTL   → POS T1    (next working day)
    #   5. 99863812TERMINAL 1 CARDS SETTL   → POS T2    (next working day)

    portal_book_by_date: dict[Any, list] = defaultdict(list)
    for row in book_rows:
        if row["matched"] or row["direction"] != "IN":
            continue
        settled_date = _parse_portal_settled_date(row.get("narration", "") or "")
        if settled_date is not None:
            portal_book_by_date[settled_date].append(row)

    portal_stmt_by_date: dict[Any, list] = defaultdict(list)
    for row in statement_rows:
        if row["matched"] or row["direction"] != "IN":
            continue
        if _is_portal_settlement(row["description"]):
            portal_stmt_by_date[_row_date(row)].append(row)

    for settle_date, book_group in portal_book_by_date.items():
        stmt_group = [r for r in portal_stmt_by_date.get(settle_date, []) if not r["matched"]]
        book_group_active = [r for r in book_group if not r["matched"]]
        if not stmt_group or not book_group_active:
            continue

        book_total = sum_amounts(book_group_active)
        stmt_total = sum_amounts(stmt_group)

        if book_total == stmt_total:
            # Full batch: all book entries for this settlement date match all
            # statement settlement entries on the same date.
            matches.append(
                mark_match(
                    "rule_portal_settlement",
                    stmt_group,
                    book_group_active,
                    pass_number=3,
                    notes=(
                        f"Portal settlement batch {settle_date}: "
                        f"{len(book_group_active)} book → {len(stmt_group)} stmt entries"
                    ),
                )
            )
            continue

        # Partial match: try to pair each statement entry individually.
        for stmt_row in stmt_group:
            if stmt_row["matched"]:
                continue
            remaining_book = [r for r in book_group_active if not r["matched"]]
            if not remaining_book:
                break

            remaining_total = sum_amounts(remaining_book)
            if remaining_total == stmt_row["amount"]:
                # All remaining book entries for this date sum to one stmt entry.
                matches.append(
                    mark_match(
                        "rule_portal_settlement",
                        [stmt_row],
                        remaining_book,
                        pass_number=3,
                        notes=f"Portal settlement {settle_date}: {len(remaining_book)} book → 1 stmt",
                    )
                )
            else:
                # Single book entry matches this statement entry exactly.
                exact = [r for r in remaining_book if r["amount"] == stmt_row["amount"]]
                if len(exact) == 1:
                    matches.append(
                        mark_match(
                            "rule_portal_settlement",
                            [stmt_row],
                            [exact[0]],
                            pass_number=3,
                            notes=f"Portal settlement {settle_date}: 1:1 exact match",
                        )
                    )
                elif remaining_total < stmt_row["amount"]:
                    # Stmt settlement amount exceeds book total for this date.
                    # The bank batches multiple settlement types (UPI + Card)
                    # into one credit.  Partially match the book entries and
                    # leave the remainder as an unmatched stmt IN entry.
                    matches.append(
                        mark_partial_match(
                            "rule_portal_settlement",
                            stmt_row,
                            remaining_book,
                            pass_number=3,
                            notes=(
                                f"Portal settlement {settle_date}: partial "
                                f"{remaining_total} of {stmt_row['amount']} "
                                f"({len(remaining_book)} book entries)"
                            ),
                        )
                    )

    return matches

def _row_date(row: dict[str, Any]):
    """Return the canonical date field for mixed row types."""

    return row.get("value_date") or row.get("voucher_date")
