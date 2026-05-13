"""
BRS Automation System — Configuration
"""
import os
import secrets
import sys
from dotenv import load_dotenv

load_dotenv()

# ── Environment ───────────────────────────────────────────────────
ENV = os.getenv("ENV", "development").lower()
IS_PRODUCTION = ENV in ("production", "prod")
DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")


def _required_secret(name: str, *, dev_default: str | None = None) -> str:
    value = os.getenv(name)
    if value:
        return value
    if IS_PRODUCTION:
        sys.stderr.write(
            f"FATAL: {name} environment variable is required in production.\n"
        )
        sys.exit(1)
    if dev_default is not None:
        return dev_default
    return secrets.token_urlsafe(48)


# ── App settings ──────────────────────────────────────────────────
SECRET_KEY = _required_secret("SECRET_KEY")
JWT_SECRET = _required_secret("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "24"))

# ── Database (Supabase / PostgreSQL) ─────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/brs",
)

# ── Service URLs ──────────────────────────────────────────────────
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://ml:8001")

UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
OUTPUT_FOLDER = os.getenv("OUTPUT_FOLDER", "output")
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ── HTTP / Security ──────────────────────────────────────────────
_cors_raw = os.getenv("CORS_ORIGINS", "http://localhost:80,http://localhost:3000,http://localhost:8000")
CORS_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]
if IS_PRODUCTION and (not CORS_ORIGINS or "*" in CORS_ORIGINS):
    sys.stderr.write(
        "FATAL: CORS_ORIGINS must be set to explicit origins in production.\n"
    )
    sys.exit(1)

_hosts_raw = os.getenv("ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in _hosts_raw.split(",") if h.strip()]

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))  # 25 MB

# Auth
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "12"))
LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5/minute")
DEFAULT_RATE_LIMIT = os.getenv("DEFAULT_RATE_LIMIT", "120/minute")

# Cleanup
UPLOAD_MAX_AGE_DAYS = int(os.getenv("UPLOAD_MAX_AGE_DAYS", "60"))

# ── Bank Statement Parser (ICICI Excel) ──────────────────────────
BANK_STATEMENT = {
    "header_row": 6,
    "skip_rows": 6,
    "columns": {
        "transaction_id": "Transaction ID",
        "value_date": "Value Date",
        "txn_posted_date": "Txn Posted Date",
        "cheque_no": "ChequeNo.",
        "description": "Description",
        "cr_dr": "Cr/Dr",
        "amount": "Transaction Amount(INR)",
        "balance": "Available Balance(INR)",
    },
    "fd_prefix": "M",
    "regular_prefix": "S",
}

# ── Bank Book Parser (ERP Excel) ─────────────────────────────────
BANK_BOOK = {
    "columns": {
        "voucher_date": "Voucher Date",
        "particulars": "Particluars",
        "voucher_type": "Voucher Type",
        "debit": "Debit",
        "credit": "Credit",
        "cheque_no": "Cheque No",
        "narration": "Narration",
        "voucher_no": "Voucher No",
    },
}
