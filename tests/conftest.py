"""Shared pytest fixtures for the BRS automation tests."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from engine.parsers.bank_book import parse_bank_book
from engine.parsers.bank_statement import parse_bank_statement
from engine.parsers.brs_previous import parse_previous_brs
from engine.reconciliation import reconcile_workbooks


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "BWU ICICI 2764.xlsx"


@lru_cache(maxsize=1)
def _statement_result():
    return parse_bank_statement(WORKBOOK)


@lru_cache(maxsize=1)
def _book_result():
    return parse_bank_book(WORKBOOK)


@lru_cache(maxsize=1)
def _previous_brs_result():
    return parse_previous_brs(WORKBOOK, "BRS JAN'26")


@lru_cache(maxsize=1)
def _reconciliation_result():
    return reconcile_workbooks(
        statement_path=WORKBOOK,
        bank_book_path=WORKBOOK,
        previous_brs_path=WORKBOOK,
        previous_brs_sheet="BRS JAN'26",
    )


@pytest.fixture
def workbook_path() -> Path:
    """Return the live workbook path used for regression testing."""

    return WORKBOOK


@pytest.fixture
def statement_result():
    """Parsed current-month bank statement data."""

    return _statement_result()


@pytest.fixture
def book_result():
    """Parsed current-month bank book data."""

    return _book_result()


@pytest.fixture
def previous_brs_result():
    """Parsed previous-month BRS carry-forward data."""

    return _previous_brs_result()


@pytest.fixture
def reconciliation_result():
    """Full February 2026 reconciliation result."""

    return _reconciliation_result()
