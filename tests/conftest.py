"""Shared pytest fixtures for the BRS automation tests."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# Tests must not require real production secrets. Set ephemeral values
# *before* importing application code (config.py reads env at import time).
os.environ.setdefault("ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production")
os.environ.setdefault("CORS_ORIGINS", "http://testserver")

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


@pytest.fixture
def authed_client():
    """FastAPI TestClient with auth dependency overridden to a test user.

    All API routes require authentication in production; tests stub it out
    rather than minting real JWTs so the contract is exercised but the
    cryptographic path doesn't need real secrets.
    """
    from fastapi.testclient import TestClient

    import app as app_module
    from routes.auth import get_current_user

    def _fake_user():
        return {
            "id": 1,
            "username": "test-user",
            "full_name": "Test User",
            "role": "system_admin",
            "is_active": 1,
        }

    app_module.app.dependency_overrides[get_current_user] = _fake_user
    try:
        with TestClient(app_module.app) as client:
            yield client
    finally:
        app_module.app.dependency_overrides.pop(get_current_user, None)
