"""
BRS Automation System — Configuration
Config-driven parser settings and matching tolerances.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── App settings ──────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-jwt-secret")
DATABASE_PATH = os.getenv("DATABASE_PATH", "db/brs.db")
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
OUTPUT_FOLDER = os.getenv("OUTPUT_FOLDER", "output")

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
