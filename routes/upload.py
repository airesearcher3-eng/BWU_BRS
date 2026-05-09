"""
Upload Routes — handles file upload for bank statement, bank book, and previous BRS.
Migrated to FastAPI.
"""
import os
from fastapi import APIRouter, UploadFile, File, HTTPException
from models.database import get_connection, insert_audit_log

router = APIRouter(prefix="/api/upload", tags=["Upload"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv"}


def _validate_extension(filename: str):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400,
                            detail="File type not allowed. Use .xlsx, .xls, or .csv")


def _secure_filename(filename: str) -> str:
    """Simple secure filename — keep alphanumeric, dots, hyphens, underscores."""
    import re
    return re.sub(r"[^\w\s\-.]", "_", filename).strip()


async def _save_upload(file: UploadFile, subfolder: str, file_type: str):
    _validate_extension(file.filename)
    filename = _secure_filename(file.filename)
    upload_dir = os.path.join(BASE_DIR, "uploads", subfolder)
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    with get_connection() as conn:
        insert_audit_log(conn, "file_upload", details={
            "file_type": file_type,
            "filename": filename,
            "filepath": filepath,
        })

    return {"message": f"{file_type} uploaded successfully",
            "filename": filename, "filepath": filepath}


@router.post("/bank-statement")
async def upload_bank_statement(file: UploadFile = File(...)):
    """Upload and store bank statement file."""
    return await _save_upload(file, "bank_statements", "bank_statement")


@router.post("/bank-book")
async def upload_bank_book(file: UploadFile = File(...)):
    """Upload and store bank book ledger file."""
    return await _save_upload(file, "bank_books", "bank_book")


@router.post("/previous-brs")
async def upload_previous_brs(file: UploadFile = File(...)):
    """Upload and store previous month's BRS file."""
    return await _save_upload(file, "previous_brs", "previous_brs")
