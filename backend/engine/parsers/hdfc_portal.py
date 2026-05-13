"""Parser for HDFC portal settlement Excel / CSV report.

The HDFC payment portal (SmartHub / PayZapp) provides a downloadable report
listing individual student fee payments that were aggregated and settled to
the university bank account.  This report is used to:

  1. Trace which students contributed to a given portal settlement batch.
  2. Identify which student's payment is missing when a portal settlement
     credit on the bank statement cannot be fully matched in the bank book.
  3. Verify partial portal settlement mismatches (e.g. one student's
     transaction failed but the ERP recorded it anyway).

Supported column layouts (auto-detected, case-insensitive):
  - HDFC SmartHub export:
      Date | Transaction ID | Student Code | Student Name |
      Amount | Payment Mode | Status | Settlement Date
  - Generic portal report with columns containing recognisable keywords.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any
import re

import openpyxl

from engine.normaliser import normalise_amount, normalise_date


# Header scan depth (rows examined before data starts)
_HEADER_SCAN_LIMIT = 25

# ── Column keyword groups (case-insensitive substring match) ──────────────
_COL_AMOUNT       = ("amount", "paid", "fee", "payment amount", "net amount")
_COL_DATE         = ("txn date", "transaction date", "payment date", "date",
                     "trans date")
_COL_SETTLE_DATE  = ("settlement date", "settle date", "credit date",
                     "bank credit date", "settlement")
_COL_STUDENT_ID   = ("student code", "student id", "student no",
                     "application no", "application id", "app no", "app id",
                     "roll no", "enrollment", "registration no", "reg no",
                     "admission no")
_COL_NAME         = ("student name", "applicant name", "candidate name",
                     "payer name", "name")
_COL_TXN_ID       = ("transaction id", "txn id", "transaction no", "txn no",
                     "reference no", "ref no", "utr", "receipt no",
                     "rrn", "approval no")
_COL_MODE         = ("payment mode", "mode of payment", "payment type",
                     "payment method", "mode", "type")
_COL_STATUS       = ("status", "txn status", "transaction status")


# ── Public API ────────────────────────────────────────────────────────────

def parse_hdfc_portal(filepath: str | Path) -> dict[str, Any]:
    """Parse HDFC portal settlement report.

    Returns a dict with:
    - ``payments``:  list of individual payment records
    - ``by_settlement_date``:  dict mapping ISO settlement date → list of records
    - ``by_payment_date``:     dict mapping ISO payment date → list of records
    - ``count``:      total number of valid payment rows
    - ``total_amount``: sum of all payment amounts (float, for quick sanity check)
    - ``error``:      present only when the file could not be parsed
    """
    suffix = Path(filepath).suffix.lower()

    if suffix == ".csv":
        rows = _read_csv(filepath)
    else:
        rows = _read_excel(filepath)

    if not rows:
        return _empty("Empty file")

    header_idx, col_map = _detect_header(rows)
    if header_idx is None:
        return _empty(
            "Could not detect column headers — need at least 'amount' and 'date' columns"
        )

    payments: list[dict[str, Any]] = []
    for row_idx, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        record = _parse_row(row, col_map, row_idx)
        if record is None:
            continue
        payments.append(record)

    # Index by settlement date (fallback: payment date) for O(1) lookup
    by_settlement_date: dict[str, list[dict]] = {}
    by_payment_date: dict[str, list[dict]] = {}
    for p in payments:
        s_key = str(p.get("settlement_date") or "")
        if s_key:
            by_settlement_date.setdefault(s_key, []).append(p)

        p_key = str(p.get("payment_date") or "")
        if p_key:
            by_payment_date.setdefault(p_key, []).append(p)

    total = sum(p["amount"] for p in payments)
    return {
        "payments": payments,
        "by_settlement_date": by_settlement_date,
        "by_payment_date": by_payment_date,
        "count": len(payments),
        "total_amount": float(total),
    }


def enrich_portal_matches(match_result: dict, portal_result: dict) -> None:
    """Annotate portal settlement match groups with student-level payment details.

    Called in-place: each ``rule_portal_settlement`` match group gains a
    ``portal_students`` key listing the individual students whose payments
    contributed to that settlement batch.
    """
    by_date = portal_result.get("by_settlement_date", {})
    if not by_date:
        return

    _DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

    for grp in match_result.get("matches", []):
        if grp.get("match_type") != "rule_portal_settlement":
            continue
        notes = grp.get("notes", "")
        m = _DATE_RE.search(notes)
        if not m:
            continue
        settle_key = m.group(1)
        students = by_date.get(settle_key, [])
        if students:
            grp["portal_students"] = [
                {
                    "student_id":    s.get("student_id"),
                    "student_name":  s.get("student_name"),
                    "amount":        float(s["amount"]),
                    "txn_id":        s.get("txn_id"),
                    "payment_mode":  s.get("payment_mode"),
                    "payment_date":  str(s["payment_date"]) if s.get("payment_date") else None,
                }
                for s in students
            ]


# ── Internal helpers ──────────────────────────────────────────────────────

def _read_excel(filepath: str | Path) -> list[tuple]:
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    return rows


def _read_csv(filepath: str | Path) -> list[tuple]:
    import csv
    rows: list[tuple] = []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(tuple(row))
    return rows


def _detect_header(rows: list) -> tuple[int | None, dict[str, int]]:
    """Return (header_row_index, col_map) or (None, {}) if not found."""
    for i, row in enumerate(rows[:_HEADER_SCAN_LIMIT]):
        if not row:
            continue
        headers = [str(cell or "").strip().lower() for cell in row]
        col_map: dict[str, int] = {}

        for group_key, candidates in (
            ("amount",        _COL_AMOUNT),
            ("date",          _COL_DATE),
            ("settlement_date", _COL_SETTLE_DATE),
            ("student_id",    _COL_STUDENT_ID),
            ("name",          _COL_NAME),
            ("txn_id",        _COL_TXN_ID),
            ("mode",          _COL_MODE),
            ("status",        _COL_STATUS),
        ):
            for j, h in enumerate(headers):
                if any(c in h for c in candidates):
                    col_map.setdefault(group_key, j)
                    break

        # Require at minimum: amount + at least one date column
        if "amount" in col_map and ("date" in col_map or "settlement_date" in col_map):
            return i, col_map

    return None, {}


def _parse_row(row: tuple, col_map: dict[str, int], row_number: int) -> dict[str, Any] | None:
    """Parse a single data row into a payment record dict."""

    def _get(key: str):
        idx = col_map.get(key)
        return row[idx] if idx is not None and idx < len(row) else None

    amount = normalise_amount(_get("amount"))
    if amount <= Decimal("0"):
        return None

    # Accept row if at least one date column is parseable
    payment_date = normalise_date(_get("date"))
    settlement_date = normalise_date(_get("settlement_date"))
    if payment_date is None and settlement_date is None:
        return None

    # Skip clearly cancelled/failed transactions
    status = _clean(_get("status"))
    if status and status.upper() in ("FAILED", "CANCELLED", "REVERSED", "REFUNDED"):
        return None

    return {
        "row_number":       row_number,
        "payment_date":     payment_date,
        "settlement_date":  settlement_date,
        "student_id":       _clean(_get("student_id")),
        "student_name":     _clean(_get("name")),
        "txn_id":           _clean(_get("txn_id")),
        "payment_mode":     _clean(_get("mode")),
        "status":           status,
        "amount":           amount,
    }


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s and s.lower() not in ("none", "nan", "-", "n/a", "") else None


def _empty(error: str) -> dict[str, Any]:
    return {
        "payments": [],
        "by_settlement_date": {},
        "by_payment_date": {},
        "count": 0,
        "total_amount": 0.0,
        "error": error,
    }
