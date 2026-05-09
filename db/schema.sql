-- Primary schema file for PostgreSQL / Supabase deployments.
-- For local offline fallback the app now uses db/schema.sqlite.sql automatically.

CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('accounts_officer','accounts_manager','finance_controller','internal_auditor','system_admin')),
    email           TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runs (
    id                      BIGSERIAL PRIMARY KEY,
    run_date                TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    period_start            TEXT NOT NULL,
    period_end              TEXT NOT NULL,
    account_no              TEXT NOT NULL DEFAULT '082401002764',
    bank_statement_file     TEXT,
    bank_book_file          TEXT,
    previous_brs_file       TEXT,
    bank_book_balance       NUMERIC(18, 2),
    bank_statement_balance  NUMERIC(18, 2),
    total_bank_stmt_entries INTEGER DEFAULT 0,
    total_bank_book_entries INTEGER DEFAULT 0,
    pass1_matches           INTEGER DEFAULT 0,
    pass2_matches           INTEGER DEFAULT 0,
    pass3_matches           INTEGER DEFAULT 0,
    pass4_matches           INTEGER DEFAULT 0,
    total_matched           INTEGER DEFAULT 0,
    total_unmatched         INTEGER DEFAULT 0,
    total_pending           INTEGER DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed','pending_review','approved','signed_off')),
    brs_output_path         TEXT,
    created_by              BIGINT REFERENCES users(id),
    completed_at            TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source          TEXT NOT NULL CHECK (source IN ('bank_statement','bank_book','carry_forward')),
    transaction_date TEXT NOT NULL,
    amount          NUMERIC(18, 2) NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
    references_json TEXT,
    narration       TEXT,
    description     TEXT,
    voucher_type    TEXT,
    voucher_no      TEXT,
    cheque_no       TEXT,
    transaction_id  TEXT,
    original_row    INTEGER,
    sha256_hash     TEXT NOT NULL,
    match_status    TEXT NOT NULL DEFAULT 'unmatched' CHECK (match_status IN ('matched','pending_review','unmatched')),
    match_pass      INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_txn_run ON transactions(run_id);
CREATE INDEX IF NOT EXISTS idx_txn_hash ON transactions(sha256_hash);
CREATE INDEX IF NOT EXISTS idx_txn_source ON transactions(run_id, source);
CREATE INDEX IF NOT EXISTS idx_txn_status ON transactions(run_id, match_status);

CREATE TABLE IF NOT EXISTS matches (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    pass_number         INTEGER NOT NULL CHECK (pass_number IN (1,2,3,4)),
    match_type          TEXT NOT NULL,
    confidence          NUMERIC(5, 2) NOT NULL DEFAULT 1.0,
    bank_stmt_txn_ids   TEXT NOT NULL,
    bank_book_txn_ids   TEXT NOT NULL,
    matched_amount      NUMERIC(18, 2) NOT NULL,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_match_run ON matches(run_id);

CREATE TABLE IF NOT EXISTS exceptions (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    transaction_id  BIGINT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    exception_type  TEXT NOT NULL,
    brs_section     TEXT NOT NULL,
    sla_days        INTEGER NOT NULL DEFAULT 3,
    sla_due_date    TEXT,
    assigned_to     BIGINT REFERENCES users(id),
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','resolved','escalated','closed')),
    resolution_type TEXT,
    resolved_by     BIGINT REFERENCES users(id),
    resolved_at     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_exc_run ON exceptions(run_id);
CREATE INDEX IF NOT EXISTS idx_exc_status ON exceptions(status);

CREATE TABLE IF NOT EXISTS exception_comments (
    id              BIGSERIAL PRIMARY KEY,
    exception_id    BIGINT NOT NULL REFERENCES exceptions(id) ON DELETE CASCADE,
    user_id         BIGINT REFERENCES users(id),
    comment_text    TEXT NOT NULL,
    attachment_path TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS approvals (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    level           INTEGER NOT NULL CHECK (level IN (1,2,3)),
    role            TEXT NOT NULL,
    user_id         BIGINT REFERENCES users(id),
    action          TEXT NOT NULL CHECK (action IN ('submitted','approved','rejected','signed_off')),
    comments        TEXT,
    signed_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_approval_run ON approvals(run_id);

CREATE TABLE IF NOT EXISTS carry_forward (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    brs_section     TEXT NOT NULL,
    original_date   TEXT NOT NULL,
    remarks         TEXT,
    cheque_no       TEXT,
    amount          NUMERIC(18, 2) NOT NULL,
    cleared_date    TEXT,
    source_run_id   BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cf_run ON carry_forward(run_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id         BIGINT REFERENCES users(id),
    action          TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       BIGINT,
    details_json    TEXT,
    ip_address      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);

INSERT INTO users (username, password_hash, full_name, role)
VALUES ('admin', 'pbkdf2:sha256:600000$placeholder$hash', 'System Administrator', 'system_admin')
ON CONFLICT (username) DO NOTHING;
