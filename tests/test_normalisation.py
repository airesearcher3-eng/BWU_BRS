from datetime import datetime
from decimal import Decimal

from engine.normaliser import (
    normalise_amount,
    normalise_date,
    normalise_direction_book,
    normalise_direction_stmt,
)


def test_value_date_string_dd_mm_yyyy():
    assert normalise_date("01/02/2026").isoformat() == "2026-02-01"


def test_value_date_as_datetime():
    assert normalise_date(datetime(2026, 2, 3, 11, 25, 29)).isoformat() == "2026-02-03"


def test_amount_with_indian_comma_formatting():
    assert normalise_amount("1,08,44,213.00") == Decimal("10844213.00")


def test_bank_book_debit_is_direction_in():
    assert normalise_direction_book("35000", "0") == "IN"


def test_bank_book_credit_is_direction_out():
    assert normalise_direction_book("0", "2829.64") == "OUT"


def test_bank_stmt_cr_is_direction_in():
    assert normalise_direction_stmt("CR") == "IN"


def test_bank_stmt_dr_is_direction_out():
    assert normalise_direction_stmt("DR") == "OUT"
