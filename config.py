"""
BRS Automation System — Configuration
Config-driven parser settings and matching tolerances.
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
    """Return a secret env var. Required in production; in development falls
    back to dev_default if provided, else generates an ephemeral random value
    so the app still boots locally but tokens won't survive restarts."""
    value = os.getenv(name)
    if value:
        return value
    if IS_PRODUCTION:
        sys.stderr.write(
            f"FATAL: {name} environment variable is required in production. "
            "Set it in /etc/brs/.env or your container environment.\n"
        )
        sys.exit(1)
    if dev_default is not None:
        return dev_default
    # Dev fallback: a random per-process secret. Tokens won't outlive a restart,
    # but at least we never ship a known value.
    return secrets.token_urlsafe(48)


# ── App settings ──────────────────────────────────────────────────
SECRET_KEY = _required_secret("SECRET_KEY")
JWT_SECRET = _required_secret("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "24"))

DATABASE_PATH = os.getenv("DATABASE_PATH", "db/brs.db")
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
OUTPUT_FOLDER = os.getenv("OUTPUT_FOLDER", "output")
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ── HTTP / Security ──────────────────────────────────────────────
# Comma-separated list of allowed origins for CORS. In production this MUST
# be set to the real frontend origin(s); a wildcard is rejected when paired
# with credentials.
_cors_raw = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
CORS_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]
if IS_PRODUCTION and (not CORS_ORIGINS or "*" in CORS_ORIGINS):
    sys.stderr.write(
        "FATAL: CORS_ORIGINS must be set to explicit origins in production "
        "(wildcard '*' is not allowed with credentialed requests).\n"
    )
    sys.exit(1)

# Comma-separated list of trusted Host headers. Defaults are permissive in dev.
_hosts_raw = os.getenv("ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in _hosts_raw.split(",") if h.strip()]

# Upload limits
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))  # 25 MB

# Auth
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "12"))
LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5/minute")
DEFAULT_RATE_LIMIT = os.getenv("DEFAULT_RATE_LIMIT", "120/minute")

# Cleanup
UPLOAD_MAX_AGE_DAYS = int(os.getenv("UPLOAD_MAX_AGE_DAYS", "60"))

# ── Google Gemini RAG settings ────────────────────────────────────
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
RAG_LLM_MODEL = os.getenv("RAG_LLM_MODEL", "gemini-2.5-flash")

# ── Bank Statement Parser (ICICI Excel) ──────────────────────────
BANK_STATEMENT = {
    "header_row": 6,          # 0-indexed row containing column headers
    "skip_rows": 6,           # rows 0-5 are metadata
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
    "fd_prefix": "M",         # FD transfer rows use 'M' prefix
    "regular_prefix": "S",    # Regular rows use 'S' prefix
}

# ── Bank Book Parser (ERP Excel) ─────────────────────────────────
BANK_BOOK = {
    "header_row": 0,
    "columns": {
        "voucher_date": "Voucher Date",
        "voucher_type": "Voucher Type",
        "voucher_no": "Voucher No.",
        "particulars": "Particluars",     # Note: ERP typo preserved
        "debit": "Debit",                 # Debit = money IN to BWU
        "credit": "Credit",              # Credit = money OUT of BWU
        "narration": "Narration",
    },
    "voucher_types": ["REC", "PMT", "CNT"],
}

# ── Previous BRS Parser ──────────────────────────────────────────
PREVIOUS_BRS = {
    "sections": {
        "add_cheque_issued": "Cheque issued but not debited",
        "add_bank_credit": "Credit in Bank Statement but not entered",
        "less_cheque_deposited": "Cheque deposited but not credited",
        "less_bank_debit": "Debit in Bank Statement but not entered",
    },
    "cleared_column": "E",    # Column E = 'Cleared/Cancelled on'
}

# ── Matching Engine Tolerances ────────────────────────────────────
DATE_TOLERANCE_DAYS = 3       # ±3 calendar days for Pass 1 (UPI/NEFT)
GIB_DATE_TOLERANCE_DAYS = 1   # ±1 day for Pass 3 (GIB/tax)
BIL_DATE_TOLERANCE_DAYS = 1   # ±1 day for Pass 3 (BIL/ONL)

# ── Reference Extraction Patterns ────────────────────────────────
# Bank book: Tn. No extraction from Narration
BANK_BOOK_REF_PATTERN = r"Tn\.\s*No\s*:\s*([^\s,;]+)"

# Bank statement: reference extraction prefixes
BANK_STATEMENT_REF_PREFIXES = [
    "NEFT-",
    "UPI/",
    "INF/NEFT/",
    "MMT/IMPS/",
    "INF/INFT/",
]

# ── GIB/Tax Keywords ─────────────────────────────────────────────
GIB_TAX_KEYWORDS = {
    "GIB/DTAX": ["TDS", "tds", "Tax Deducted"],
    "GIB/ESIC": ["ESIC", "esic", "ESI"],
    "GIB/EPFO": ["EPF", "epf", "EPFO", "PF"],
    "GIB/GST":  ["GST", "gst", "Goods and Service"],
}

# ── BRS Output Format ────────────────────────────────────────────
BRS_OUTPUT = {
    "institution": "Brainware University",
    "bank_name": "ICICI Bank",
    "branch": "Barasat Branch",
    "account_no": "082401002764",
    "account_type": "Savings New",
}

# ── Exception SLA (business days) ────────────────────────────────
EXCEPTION_SLA = {
    "unknown_dr": 1,
    "amount_mismatch": 1,
    "gib_unmatched": 3,
    "unknown_cr": 3,
    "stale_threshold_days": 90,
}

# ── Approval Settings ────────────────────────────────────────────
DUAL_APPROVAL_THRESHOLD = 50000  # ₹50,000 — items above require dual approval
