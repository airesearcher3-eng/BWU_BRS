"""Shared normalisation helpers for the BRS automation engine."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
from typing import Any


TWOPLACES = Decimal("0.01")


def normalise_date(raw: Any) -> date | None:
    """Convert mixed Excel date values into a ``date`` instance."""

    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw

    text = str(raw).strip()
    if not text or text.lower() in {"none", "nan"}:
        return None

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def date_to_iso(value: date | None) -> str | None:
    """Render a ``date`` value as ISO text for storage or JSON."""

    return value.isoformat() if value else None


def format_brs_date(value: date | None) -> str:
    """Render a date in the workbook's ``DD/MM/YYYY`` display format."""

    return value.strftime("%d/%m/%Y") if value else ""


def normalise_amount(raw: Any) -> Decimal:
    """Convert raw numeric cells into a 2-decimal ``Decimal`` value."""

    if raw is None:
        return Decimal("0.00")

    text = str(raw).replace(",", "").strip()
    if not text or text == "-":
        return Decimal("0.00")

    # Strip common currency prefixes (e.g. "INR 109088408.79")
    text = re.sub(r"^[A-Z]{3}\s+", "", text)

    try:
        return Decimal(text).quantize(TWOPLACES)
    except InvalidOperation:
        return Decimal("0.00")


def decimal_to_float(value: Decimal) -> float:
    """Return a JSON-friendly float without changing internal precision usage."""

    return float(value.quantize(TWOPLACES))


def normalise_direction_stmt(cr_dr: str | None) -> str:
    """Normalise bank statement direction: ``CR`` -> ``IN``, otherwise ``OUT``."""

    return "IN" if str(cr_dr or "").strip().upper() == "CR" else "OUT"


def normalise_direction_book(debit: Any, credit: Any) -> str:
    """Normalise ledger direction where Debit means money received by BWU."""

    return "IN" if normalise_amount(debit) > 0 else "OUT"


def hash_bank_stmt_row(
    account_id: str,
    value_date_iso: str,
    amount_str: str,
    direction: str,
    description: str,
) -> str:
    """Build the deterministic statement row hash used for idempotency."""

    raw = "|".join(
        [account_id, value_date_iso, amount_str, direction, description.strip()]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_book_row(
    account_id: str,
    voucher_date_iso: str,
    voucher_no_str: str,
    amount_str: str,
    direction: str,
) -> str:
    """Build the deterministic bank book row hash used for idempotency."""

    raw = "|".join(
        [account_id, voucher_date_iso, voucher_no_str, amount_str, direction]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def amounts_equal(left: Decimal, right: Decimal) -> bool:
    """Compare 2-decimal values using the engine's canonical precision."""

    return left.quantize(TWOPLACES) == right.quantize(TWOPLACES)
