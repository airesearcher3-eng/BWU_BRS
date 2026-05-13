"""Initial schema migration — full PostgreSQL DDL for BWU BRS.

Revision: 0001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector extension (needed for dense retrieval)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── bank_accounts ─────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS bank_accounts (
        id            BIGSERIAL PRIMARY KEY,
        account_no    TEXT NOT NULL UNIQUE,
        bank_name     TEXT NOT NULL,
        branch        TEXT NOT NULL DEFAULT '',
        account_type  TEXT NOT NULL DEFAULT 'Savings',
        label         TEXT NOT NULL,
        is_active     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    # ── users ─────────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id               BIGSERIAL PRIMARY KEY,
        username         TEXT NOT NULL UNIQUE,
        password_hash    TEXT NOT NULL,
        initial_password TEXT,
        full_name        TEXT NOT NULL,
        role             TEXT NOT NULL CHECK (role IN (
                            'accounts_officer','accounts_manager',
                            'finance_controller','internal_auditor','system_admin')),
        email            TEXT,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    # ── runs ──────────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS runs (
        id                      BIGSERIAL PRIMARY KEY,
        period_start            DATE,
        period_end              DATE,
        bank_statement_path     TEXT,
        bank_book_path          TEXT,
        previous_brs_path       TEXT,
        bank_account_id         BIGINT REFERENCES bank_accounts(id) ON DELETE SET NULL,
        status                  TEXT NOT NULL DEFAULT 'running'
                                    CHECK (status IN (
                                        'running','completed','failed',
                                        'pending_review','approved','signed_off')),
        bank_book_balance        NUMERIC(18,2),
        bank_statement_balance   NUMERIC(18,2),
        total_bank_stmt_entries  INTEGER DEFAULT 0,
        total_bank_book_entries  INTEGER DEFAULT 0,
        pass1_matches            INTEGER DEFAULT 0,
        pass2_matches            INTEGER DEFAULT 0,
        pass3_matches            INTEGER DEFAULT 0,
        pass4_matches            INTEGER DEFAULT 0,
        pass5_matches            INTEGER DEFAULT 0,
        total_matched            INTEGER DEFAULT 0,
        total_unmatched          INTEGER DEFAULT 0,
        total_pending            INTEGER DEFAULT 0,
        brs_output_path          TEXT,
        completed_at             TIMESTAMPTZ,
        created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    # ── transactions ──────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id               BIGSERIAL PRIMARY KEY,
        run_id           BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        source           TEXT NOT NULL CHECK (source IN (
                            'bank_statement','bank_book','carry_forward')),
        transaction_date TEXT NOT NULL,
        amount           NUMERIC(18,2) NOT NULL,
        direction        TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
        match_status     TEXT NOT NULL DEFAULT 'unmatched'
                            CHECK (match_status IN ('unmatched','matched','exception')),
        pass_number      INTEGER,
        references_json  JSONB DEFAULT '[]',
        description      TEXT,
        narration        TEXT,
        voucher_type     TEXT,
        voucher_no       TEXT,
        cheque_no        TEXT,
        transaction_id   TEXT,
        original_row     INTEGER,
        sha256_hash      TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_txn_run_id ON transactions(run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_txn_status  ON transactions(match_status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_txn_hash    ON transactions(sha256_hash)")

    # ── matches ───────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        id               BIGSERIAL PRIMARY KEY,
        run_id           BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        pass_number      INTEGER NOT NULL CHECK (pass_number BETWEEN 1 AND 6),
        match_type       TEXT NOT NULL,
        statement_ids    JSONB DEFAULT '[]',
        book_ids         JSONB DEFAULT '[]',
        matched_amount   NUMERIC(18,2),
        confidence       REAL DEFAULT 1.0,
        notes            TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_match_run_id ON matches(run_id)")

    # ── carry_forward ─────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS carry_forward (
        id              BIGSERIAL PRIMARY KEY,
        run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        brs_section     TEXT NOT NULL,
        original_date   TEXT NOT NULL,
        remarks         TEXT,
        cheque_no       TEXT,
        amount          NUMERIC(18,2) NOT NULL,
        cleared_date    TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    # ── exceptions ────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS exceptions (
        id              BIGSERIAL PRIMARY KEY,
        run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        transaction_id  BIGINT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        exception_type  TEXT NOT NULL,
        brs_section     TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open','resolved','escalated','waived')),
        resolution_type TEXT,
        assigned_to     BIGINT REFERENCES users(id) ON DELETE SET NULL,
        sla_days        INTEGER DEFAULT 3,
        resolved_at     TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_exc_run_id ON exceptions(run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_exc_status  ON exceptions(status)")

    # ── exception_comments ────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS exception_comments (
        id              BIGSERIAL PRIMARY KEY,
        exception_id    BIGINT NOT NULL REFERENCES exceptions(id) ON DELETE CASCADE,
        user_id         BIGINT REFERENCES users(id) ON DELETE SET NULL,
        comment_text    TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    # ── approvals ─────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS approvals (
        id          BIGSERIAL PRIMARY KEY,
        run_id      BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        level       INTEGER NOT NULL,
        role        TEXT NOT NULL,
        action      TEXT NOT NULL,
        comments    TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    # ── audit_log ─────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id          BIGSERIAL PRIMARY KEY,
        action      TEXT NOT NULL,
        user_id     BIGINT REFERENCES users(id) ON DELETE SET NULL,
        entity_type TEXT,
        entity_id   BIGINT,
        details     JSONB DEFAULT '{}',
        ip_address  TEXT,
        timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_log(timestamp DESC)")

    # ── txn_embeddings (pgvector) ──────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS txn_embeddings (
        id          BIGSERIAL PRIMARY KEY,
        run_id      BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        row_number  INTEGER NOT NULL,
        source      TEXT NOT NULL DEFAULT 'bank_statement',
        embedding   vector(384),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_emb_run_id ON txn_embeddings(run_id)")

    # ── Seed: default admin user (password: admin123) ──────────────────────────
    op.execute("""
    INSERT INTO users (username, password_hash, initial_password, full_name, role)
    VALUES (
        'admin',
        '$2b$12$Xwb6rR5WO/2BjdgDfhYQh.V5G74u/l.H3cIPgQaCYyjTFpzZny7n.',
        'admin123',
        'System Administrator',
        'system_admin'
    )
    ON CONFLICT (username) DO NOTHING
    """)

    # ── Seed: default ICICI bank account ──────────────────────────────────────
    op.execute("""
    INSERT INTO bank_accounts (account_no, bank_name, branch, account_type, label)
    VALUES ('628005500177', 'ICICI Bank', 'Barasat', 'Current', 'BWU ICICI Current')
    ON CONFLICT (account_no) DO NOTHING
    """)


def downgrade() -> None:
    for tbl in [
        "txn_embeddings", "audit_log", "approvals",
        "exception_comments", "exceptions", "carry_forward",
        "matches", "transactions", "runs", "users", "bank_accounts",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
