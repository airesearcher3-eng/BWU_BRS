"""
Database helpers — asyncpg pool for Supabase / PostgreSQL.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/brs")

_pool: asyncpg.Pool | None = None


# ── JSON encoder ─────────────────────────────────────────────────


class _SafeJsonEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


# ── Lifecycle ────────────────────────────────────────────────────


async def init_db() -> None:
    """Create the asyncpg connection pool. Schema is managed by Alembic."""
    global _pool
    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=60,
        statement_cache_size=0,          # required for Supabase pgBouncer (transaction mode)
        server_settings={"application_name": "brs-backend"},
    )
    logger.info("Database pool initialised (Supabase/PostgreSQL)")


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_connection():
    """Async context manager yielding an asyncpg connection with a transaction."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_db() first")
    async with _pool.acquire() as conn:
        async with conn.transaction():
            yield conn


# ── Bank Accounts ────────────────────────────────────────────────


async def get_bank_accounts(conn, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM bank_accounts"
    if active_only:
        query += " WHERE is_active = TRUE"
    query += " ORDER BY id"
    rows = await conn.fetch(query)
    return [dict(r) for r in rows]


async def get_bank_account(conn, account_id: int) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM bank_accounts WHERE id = $1", account_id)
    return dict(row) if row else None


async def insert_bank_account(conn, account_no: str, bank_name: str, branch: str,
                              account_type: str, label: str) -> int:
    return await conn.fetchval(
        """INSERT INTO bank_accounts (account_no, bank_name, branch, account_type, label)
           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
        account_no, bank_name, branch, account_type, label,
    )


async def update_bank_account(conn, account_id: int, **kwargs) -> None:
    if not kwargs:
        return
    parts = [f"{k} = ${i + 1}" for i, k in enumerate(kwargs)]
    values = list(kwargs.values()) + [account_id]
    await conn.execute(
        f"UPDATE bank_accounts SET {', '.join(parts)} WHERE id = ${len(values)}",
        *values,
    )


async def delete_bank_account(conn, account_id: int) -> None:
    await conn.execute("DELETE FROM bank_accounts WHERE id = $1", account_id)


# ── Runs ──────────────────────────────────────────────────────────


async def insert_run(conn, period_start, period_end, bank_stmt_file=None,
                     bank_book_file=None, prev_brs_file=None, created_by=None,
                     bank_account_id=None) -> int:
    def _to_date(v):
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v
    return await conn.fetchval(
        """INSERT INTO runs (period_start, period_end, bank_statement_path,
           bank_book_path, previous_brs_path, bank_account_id)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
        _to_date(period_start), _to_date(period_end), bank_stmt_file, bank_book_file,
        prev_brs_file, bank_account_id,
    )


async def update_run(conn, run_id: int, **kwargs) -> None:
    if not kwargs:
        return
    # DATE columns require date objects, not strings
    for key in ("period_start", "period_end"):
        if key in kwargs and isinstance(kwargs[key], str):
            kwargs[key] = date.fromisoformat(kwargs[key])
    # TIMESTAMP columns require datetime objects, not strings
    for key in ("completed_at", "started_at", "created_at", "updated_at"):
        if key in kwargs and isinstance(kwargs[key], str):
            kwargs[key] = datetime.fromisoformat(kwargs[key])
    parts = [f"{k} = ${i + 1}" for i, k in enumerate(kwargs)]
    values = list(kwargs.values()) + [run_id]
    await conn.execute(
        f"UPDATE runs SET {', '.join(parts)} WHERE id = ${len(values)}",
        *values,
    )


async def get_run(conn, run_id: int) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM runs WHERE id = $1", run_id)
    return dict(row) if row else None


# ── Transactions ──────────────────────────────────────────────────


async def insert_transaction(conn, run_id, source, txn_date, amount, direction,
                              references=None, narration=None, description=None,
                              voucher_type=None, voucher_no=None, cheque_no=None,
                              transaction_id=None, original_row=None,
                              sha256_hash="") -> int:
    refs_json = json.dumps(references) if references else "[]"
    return await conn.fetchval(
        """INSERT INTO transactions (run_id, source, transaction_date, amount,
           direction, references_json, narration, description, voucher_type,
           voucher_no, cheque_no, transaction_id, original_row, sha256_hash)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) RETURNING id""",
        run_id, source, txn_date, amount, direction, refs_json, narration,
        description, voucher_type, voucher_no, cheque_no, transaction_id,
        original_row, sha256_hash,
    )


async def update_transaction_status(conn, txn_id: int, status: str,
                                     pass_number: int | None = None) -> None:
    if pass_number:
        await conn.execute(
            "UPDATE transactions SET match_status=$1, pass_number=$2 WHERE id=$3",
            status, pass_number, txn_id,
        )
    else:
        await conn.execute(
            "UPDATE transactions SET match_status=$1 WHERE id=$2",
            status, txn_id,
        )


async def get_transactions(conn, run_id: int, source=None, status=None) -> list[dict]:
    query = "SELECT * FROM transactions WHERE run_id=$1"
    params: list[Any] = [run_id]
    if source:
        params.append(source)
        query += f" AND source=${len(params)}"
    if status:
        params.append(status)
        query += f" AND match_status=${len(params)}"
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


# ── Matches ───────────────────────────────────────────────────────


async def insert_match(conn, run_id, pass_number, match_type, bank_stmt_ids,
                       bank_book_ids, matched_amount, confidence=1.0,
                       notes=None) -> None:
    await conn.execute(
        """INSERT INTO matches (run_id, pass_number, match_type, confidence,
           statement_ids, book_ids, matched_amount, notes)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        run_id, pass_number, match_type, confidence,
        json.dumps(bank_stmt_ids), json.dumps(bank_book_ids),
        matched_amount, notes,
    )


async def get_match_report(conn, run_id: int) -> dict | None:
    run = await get_run(conn, run_id)
    if not run:
        return None

    match_rows = await conn.fetch(
        "SELECT * FROM matches WHERE run_id=$1 ORDER BY pass_number, id", run_id
    )

    matches = []
    total_stmt = 0
    total_book = 0
    total_amount = 0.0

    for row in match_rows:
        rd = dict(row)
        # JSONB columns are auto-decoded by asyncpg to Python objects
        raw_stmt = rd["statement_ids"]
        raw_book = rd["book_ids"]
        stmt_ids = raw_stmt if isinstance(raw_stmt, list) else json.loads(raw_stmt or "[]")
        book_ids = raw_book if isinstance(raw_book, list) else json.loads(raw_book or "[]")
        stmt_entries = await _get_transactions_by_ids(conn, stmt_ids)
        book_entries = await _get_transactions_by_ids(conn, book_ids)

        total_stmt += len(stmt_entries)
        total_book += len(book_entries)
        total_amount += float(rd["matched_amount"] or 0)

        matches.append({
            "id": rd["id"],
            "pass_number": rd["pass_number"],
            "match_type": rd["match_type"],
            "confidence": rd["confidence"],
            "matched_amount": rd["matched_amount"],
            "notes": rd["notes"] or "",
            "statement_entries": stmt_entries,
            "bank_book_entries": book_entries,
        })

    return {
        "run_id": run_id,
        "period_start": run.get("period_start"),
        "period_end": run.get("period_end"),
        "completed_at": run.get("completed_at"),
        "match_count": len(matches),
        "statement_entry_count": total_stmt,
        "bank_book_entry_count": total_book,
        "total_matched_amount": total_amount,
        "matches": matches,
    }


async def _get_transactions_by_ids(conn, transaction_ids: list) -> list[dict]:
    if not transaction_ids:
        return []
    rows = await conn.fetch(
        "SELECT * FROM transactions WHERE id = ANY($1::bigint[])",
        transaction_ids,
    )
    id_order = {tid: i for i, tid in enumerate(transaction_ids)}
    return [dict(r) for r in sorted(rows, key=lambda r: id_order.get(r["id"], 0))]


# ── Exceptions ────────────────────────────────────────────────────


async def insert_exception(conn, run_id, transaction_id, exception_type,
                            brs_section, sla_days=3, assigned_to=None) -> int:
    return await conn.fetchval(
        """INSERT INTO exceptions (run_id, transaction_id, exception_type,
           brs_section, sla_days, assigned_to)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        run_id, transaction_id, exception_type, brs_section, sla_days, assigned_to,
    )


async def get_exceptions(conn, run_id=None, status=None) -> list[dict]:
    query = """SELECT e.*, t.transaction_date, t.amount, t.direction,
                      t.narration, t.description, t.source, t.voucher_type
               FROM exceptions e
               JOIN transactions t ON e.transaction_id = t.id
               WHERE 1=1"""
    params: list[Any] = []
    if run_id is not None:
        params.append(run_id)
        query += f" AND e.run_id=${len(params)}"
    if status:
        params.append(status)
        query += f" AND e.status=${len(params)}"
    query += " ORDER BY e.created_at DESC"
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


# ── Audit Log ─────────────────────────────────────────────────────


async def insert_audit_log(conn, action: str, user_id=None, entity_type=None,
                            entity_id=None, details=None, ip_address=None) -> None:
    details_json = json.dumps(details, cls=_SafeJsonEncoder) if details else None
    await conn.execute(
        """INSERT INTO audit_log (user_id, action, entity_type, entity_id,
           details, ip_address)
           VALUES ($1,$2,$3,$4,$5,$6)""",
        user_id, action, entity_type, entity_id, details_json, ip_address,
    )
