"""
Database helpers — SQLite backend for local / on-premise deployment.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
SQLITE_DB_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "db" / "brs.db")))
SQLITE_SCHEMA_PATH = BASE_DIR / "db" / "schema.sqlite.sql"

_db_initialised = False


# ── JSON encoder ────────────────────────────────────────────────


class _SafeJsonEncoder(json.JSONEncoder):
    """Encoder that handles Decimal, date, and datetime for audit log payloads."""

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


# ── Cursor / Connection wrappers ────────────────────────────────


class DatabaseCursor:
    """Small cursor wrapper exposing a stable API."""

    def __init__(self, raw_cursor: Any):
        self._raw_cursor = raw_cursor

    @property
    def lastrowid(self) -> Any:
        return getattr(self._raw_cursor, "lastrowid", None)

    def fetchone(self):
        return self._raw_cursor.fetchone()

    def fetchall(self):
        return self._raw_cursor.fetchall()


class DatabaseConnection:
    """Thin wrapper around a raw SQLite connection."""

    def __init__(self, raw_connection: sqlite3.Connection):
        self._raw_connection = raw_connection
        self.backend = "sqlite"

    def execute(
        self, query: str, params: list[Any] | tuple[Any, ...] | None = None
    ) -> DatabaseCursor:
        bound_params = tuple(params or ())
        cursor = self._raw_connection.execute(query, bound_params)
        return DatabaseCursor(cursor)

    def commit(self):
        self._raw_connection.commit()

    def rollback(self):
        try:
            self._raw_connection.rollback()
        except Exception as exc:
            logger.warning("Rollback skipped: %s", exc)

    def close(self):
        try:
            self._raw_connection.close()
        except Exception as exc:
            logger.warning("Close skipped: %s", exc)


# ── Lifecycle ───────────────────────────────────────────────────


def get_db_path() -> str:
    """Return the active database file path."""
    return str(SQLITE_DB_PATH)


def init_db(force: bool = False):
    """Create tables from schema.sqlite.sql if they don't already exist."""

    global _db_initialised
    if _db_initialised and not force:
        return

    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with open(SQLITE_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())

    # ── Schema migrations for existing databases ────────────────
    _migrate(conn)

    conn.commit()
    conn.close()
    _db_initialised = True


def _migrate(conn: sqlite3.Connection):
    """Apply incremental schema changes to an existing database."""

    # Add bank_account_id to runs if missing
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "bank_account_id" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN bank_account_id INTEGER REFERENCES bank_accounts(id)")


@contextmanager
def get_connection():
    """Context manager that yields a DatabaseConnection."""

    init_db()
    raw = sqlite3.connect(SQLITE_DB_PATH)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys=ON")

    conn = DatabaseConnection(raw)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── CRUD helpers ────────────────────────────────────────────────

# ── Bank Accounts ───────────────────────────────────────────────


def get_bank_accounts(conn, active_only: bool = True) -> list[dict]:
    """Return all bank accounts, optionally limited to active ones."""
    query = "SELECT * FROM bank_accounts"
    if active_only:
        query += " WHERE is_active=1"
    query += " ORDER BY id"
    return [dict(row) for row in conn.execute(query).fetchall()]


def get_bank_account(conn, account_id: int) -> dict | None:
    """Return a single bank account by ID."""
    row = conn.execute("SELECT * FROM bank_accounts WHERE id=?", (account_id,)).fetchone()
    return dict(row) if row else None


def insert_bank_account(conn, account_no: str, bank_name: str, branch: str,
                        account_type: str, label: str) -> int:
    """Create a new bank account and return its ID."""
    cursor = conn.execute(
        """INSERT INTO bank_accounts (account_no, bank_name, branch, account_type, label)
           VALUES (?, ?, ?, ?, ?)""",
        (account_no, bank_name, branch, account_type, label),
    )
    return cursor.lastrowid


def update_bank_account(conn, account_id: int, **kwargs):
    """Update bank account fields dynamically."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{key}=?" for key in kwargs)
    values = list(kwargs.values()) + [account_id]
    conn.execute(f"UPDATE bank_accounts SET {set_clause} WHERE id=?", values)

def delete_bank_account(conn, account_id: int):
    """Permanently delete a bank account."""
    conn.execute("DELETE FROM bank_accounts WHERE id=?", (account_id,))

# ── Runs ────────────────────────────────────────────────────────


def insert_run(conn, period_start, period_end, bank_stmt_file=None,
               bank_book_file=None, prev_brs_file=None, created_by=None,
               bank_account_id=None):
    """Create a new reconciliation run and return its ID."""

    cursor = conn.execute(
        """INSERT INTO runs (period_start, period_end, bank_statement_file,
           bank_book_file, previous_brs_file, created_by, bank_account_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (period_start, period_end, bank_stmt_file, bank_book_file,
         prev_brs_file, created_by, bank_account_id),
    )
    return cursor.lastrowid


def insert_transaction(conn, run_id, source, txn_date, amount, direction,
                       references=None, narration=None, description=None,
                       voucher_type=None, voucher_no=None, cheque_no=None,
                       transaction_id=None, original_row=None, sha256_hash=""):
    """Insert a normalised transaction record."""

    refs_json = json.dumps(references) if references else "[]"
    cursor = conn.execute(
        """INSERT INTO transactions (run_id, source, transaction_date, amount,
           direction, references_json, narration, description, voucher_type,
           voucher_no, cheque_no, transaction_id, original_row, sha256_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, source, txn_date, amount, direction, refs_json, narration,
         description, voucher_type, voucher_no, cheque_no, transaction_id,
         original_row, sha256_hash),
    )
    return cursor.lastrowid


def insert_match(conn, run_id, pass_number, match_type, bank_stmt_ids,
                 bank_book_ids, matched_amount, confidence=1.0, notes=None):
    """Record a match between bank statement and bank book entries."""

    conn.execute(
        """INSERT INTO matches (run_id, pass_number, match_type, confidence,
           bank_stmt_txn_ids, bank_book_txn_ids, matched_amount, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, pass_number, match_type, confidence,
         json.dumps(bank_stmt_ids), json.dumps(bank_book_ids),
         matched_amount, notes),
    )


def update_transaction_status(conn, txn_id, status, pass_number=None):
    """Update match status of a transaction."""

    if pass_number:
        conn.execute(
            "UPDATE transactions SET match_status=?, match_pass=? WHERE id=?",
            (status, pass_number, txn_id),
        )
    else:
        conn.execute(
            "UPDATE transactions SET match_status=? WHERE id=?",
            (status, txn_id),
        )


def insert_exception(conn, run_id, transaction_id, exception_type,
                      brs_section, sla_days=3, assigned_to=None):
    """Create an exception for an unmatched item."""

    cursor = conn.execute(
        """INSERT INTO exceptions (run_id, transaction_id, exception_type,
           brs_section, sla_days, assigned_to)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_id, transaction_id, exception_type, brs_section,
         sla_days, assigned_to),
    )
    return cursor.lastrowid


def insert_audit_log(conn, action, user_id=None, entity_type=None,
                      entity_id=None, details=None, ip_address=None):
    """Append an entry to the immutable audit log."""

    details_json = json.dumps(details, cls=_SafeJsonEncoder) if details else None
    conn.execute(
        """INSERT INTO audit_log (user_id, action, entity_type, entity_id,
           details_json, ip_address) VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, action, entity_type, entity_id, details_json, ip_address),
    )


def update_run(conn, run_id, **kwargs):
    """Update run fields dynamically."""

    if not kwargs:
        return
    set_clause = ", ".join(f"{key}=?" for key in kwargs)
    values = list(kwargs.values()) + [run_id]
    conn.execute(f"UPDATE runs SET {set_clause} WHERE id=?", values)


def get_run(conn, run_id):
    """Get a run by ID."""

    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def get_transactions(conn, run_id, source=None, status=None):
    """Get transactions for a run, optionally filtered."""

    query = "SELECT * FROM transactions WHERE run_id=?"
    params: list[Any] = [run_id]
    if source:
        query += " AND source=?"
        params.append(source)
    if status:
        query += " AND match_status=?"
        params.append(status)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_exceptions(conn, run_id=None, status=None):
    """Get exceptions, optionally filtered."""

    query = """SELECT e.*, t.transaction_date, t.amount, t.direction,
                      t.narration, t.description, t.source, t.voucher_type
               FROM exceptions e
               JOIN transactions t ON e.transaction_id = t.id
               WHERE 1=1"""
    params: list[Any] = []
    if run_id:
        query += " AND e.run_id=?"
        params.append(run_id)
    if status:
        query += " AND e.status=?"
        params.append(status)
    query += " ORDER BY e.created_at DESC"
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_match_report(conn, run_id):
    """Return hydrated match groups with their linked statement and ledger entries."""

    run = get_run(conn, run_id)
    if not run:
        return None

    match_rows = conn.execute(
        "SELECT * FROM matches WHERE run_id=? ORDER BY pass_number, id",
        (run_id,),
    ).fetchall()

    matches = []
    total_statement_entries = 0
    total_bank_book_entries = 0
    total_matched_amount = 0.0

    for row in match_rows:
        row_dict = dict(row)
        bank_stmt_ids = json.loads(row_dict["bank_stmt_txn_ids"] or "[]")
        bank_book_ids = json.loads(row_dict["bank_book_txn_ids"] or "[]")
        statement_entries = _get_transactions_by_ids(conn, bank_stmt_ids)
        bank_book_entries = _get_transactions_by_ids(conn, bank_book_ids)

        total_statement_entries += len(statement_entries)
        total_bank_book_entries += len(bank_book_entries)
        total_matched_amount += float(row_dict["matched_amount"] or 0)

        matches.append({
            "id": row_dict["id"],
            "pass_number": row_dict["pass_number"],
            "match_type": row_dict["match_type"],
            "confidence": row_dict["confidence"],
            "matched_amount": row_dict["matched_amount"],
            "notes": row_dict["notes"] or "",
            "statement_entries": statement_entries,
            "bank_book_entries": bank_book_entries,
        })

    return {
        "run_id": run_id,
        "period_start": run.get("period_start"),
        "period_end": run.get("period_end"),
        "completed_at": run.get("completed_at"),
        "match_count": len(matches),
        "statement_entry_count": total_statement_entries,
        "bank_book_entry_count": total_bank_book_entries,
        "total_matched_amount": total_matched_amount,
        "matches": matches,
    }


def _get_transactions_by_ids(conn, transaction_ids):
    """Return transactions in the same order as the provided IDs."""

    if not transaction_ids:
        return []

    placeholder = ",".join("?" for _ in transaction_ids)
    rows = conn.execute(
        f"SELECT * FROM transactions WHERE id IN ({placeholder})",
        tuple(transaction_ids),
    ).fetchall()
    row_map = {row["id"]: dict(row) for row in rows}

    ordered = []
    for txn_id in transaction_ids:
        row = row_map.get(txn_id)
        if not row:
            continue
        row["references"] = json.loads(row.get("references_json") or "[]")
        ordered.append(row)
    return ordered
