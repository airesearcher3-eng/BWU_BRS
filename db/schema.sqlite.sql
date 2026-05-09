-- BRS Automation System - SQLite fallback schema
-- All timestamps stored as ISO 8601 strings

CREATE TABLE IF NOT EXISTS bank_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_no  TEXT    NOT NULL UNIQUE,
    bank_name   TEXT    NOT NULL,
    branch      TEXT    NOT NULL DEFAULT '',
    account_type TEXT   NOT NULL DEFAULT 'Savings',
    label       TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Seed default ICICI account
INSERT OR IGNORE INTO bank_accounts (account_no, bank_name, branch, account_type, label)
    VALUES ('082401002764', 'ICICI Bank', 'Barasat Branch', 'Savings New', 'ICICI New Savings Ltd., Barasat Branch');

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    password_hash TEXT  NOT NULL,
    initial_password TEXT,
    full_name   TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK(role IN ('accounts_officer','accounts_manager','finance_controller','internal_auditor','system_admin')),
    email       TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT    NOT NULL DEFAULT (datetime('now')),
    period_start    TEXT    NOT NULL,
    period_end      TEXT    NOT NULL,
    account_no      TEXT    NOT NULL DEFAULT '082401002764',
    bank_account_id INTEGER REFERENCES bank_accounts(id),
    bank_statement_file TEXT,
    bank_book_file      TEXT,
    previous_brs_file   TEXT,
    bank_book_balance   REAL,
    bank_statement_balance REAL,
    total_bank_stmt_entries  INTEGER DEFAULT 0,
    total_bank_book_entries  INTEGER DEFAULT 0,
    pass1_matches   INTEGER DEFAULT 0,
    pass2_matches   INTEGER DEFAULT 0,
    pass3_matches   INTEGER DEFAULT 0,
    pass4_matches   INTEGER DEFAULT 0,
    total_matched   INTEGER DEFAULT 0,
    total_unmatched INTEGER DEFAULT 0,
    total_pending   INTEGER DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','failed','pending_review','approved','signed_off')),
    brs_output_path TEXT,
    created_by      INTEGER REFERENCES users(id),
    completed_at    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    source          TEXT    NOT NULL CHECK(source IN ('bank_statement','bank_book','carry_forward')),
    transaction_date TEXT   NOT NULL,
    amount          REAL    NOT NULL,
    direction       TEXT    NOT NULL CHECK(direction IN ('IN','OUT')),
    references_json TEXT,
    narration       TEXT,
    description     TEXT,
    voucher_type    TEXT,
    voucher_no      TEXT,
    cheque_no       TEXT,
    transaction_id  TEXT,
    original_row    INTEGER,
    sha256_hash     TEXT    NOT NULL,
    match_status    TEXT    NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched','pending_review','unmatched')),
    match_pass      INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_txn_run ON transactions(run_id);
CREATE INDEX IF NOT EXISTS idx_txn_hash ON transactions(sha256_hash);
CREATE INDEX IF NOT EXISTS idx_txn_source ON transactions(run_id, source);
CREATE INDEX IF NOT EXISTS idx_txn_status ON transactions(run_id, match_status);

CREATE TABLE IF NOT EXISTS matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    pass_number     INTEGER NOT NULL CHECK(pass_number IN (1,2,3,4)),
    match_type      TEXT    NOT NULL,
    confidence      REAL    NOT NULL DEFAULT 1.0,
    bank_stmt_txn_ids TEXT  NOT NULL,
    bank_book_txn_ids TEXT  NOT NULL,
    matched_amount  REAL    NOT NULL,
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_match_run ON matches(run_id);

CREATE TABLE IF NOT EXISTS exceptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id),
    exception_type  TEXT    NOT NULL,
    brs_section     TEXT    NOT NULL,
    sla_days        INTEGER NOT NULL DEFAULT 3,
    sla_due_date    TEXT,
    assigned_to     INTEGER REFERENCES users(id),
    status          TEXT    NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','resolved','escalated','closed')),
    resolution_type TEXT,
    resolved_by     INTEGER REFERENCES users(id),
    resolved_at     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_exc_run ON exceptions(run_id);
CREATE INDEX IF NOT EXISTS idx_exc_status ON exceptions(status);

CREATE TABLE IF NOT EXISTS exception_comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    exception_id    INTEGER NOT NULL REFERENCES exceptions(id),
    user_id         INTEGER REFERENCES users(id),
    comment_text    TEXT    NOT NULL,
    attachment_path TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approvals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    level           INTEGER NOT NULL CHECK(level IN (1,2,3)),
    role            TEXT    NOT NULL,
    user_id         INTEGER REFERENCES users(id),
    action          TEXT    NOT NULL CHECK(action IN ('submitted','approved','rejected','signed_off')),
    comments        TEXT,
    signed_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_approval_run ON approvals(run_id);

CREATE TABLE IF NOT EXISTS carry_forward (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    brs_section     TEXT    NOT NULL,
    original_date   TEXT    NOT NULL,
    remarks         TEXT,
    cheque_no       TEXT,
    amount          REAL    NOT NULL,
    cleared_date    TEXT,
    source_run_id   INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cf_run ON carry_forward(run_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),
    user_id         INTEGER REFERENCES users(id),
    action          TEXT    NOT NULL,
    entity_type     TEXT,
    entity_id       INTEGER,
    details_json    TEXT,
    ip_address      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);

INSERT OR IGNORE INTO users (username, password_hash, initial_password, full_name, role) VALUES
    ('admin', '$2b$12$t.OxZ2CpuQvcMdkS0EtLZ.z.6vRN/jIjnfpFvemV3i1SImAZgdroG', 'admin123', 'System Administrator', 'system_admin');
