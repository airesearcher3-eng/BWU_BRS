"""
Upload Routes — handles file upload for bank statement, bank book, and previous BRS.
"""
import os
import re
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException

import config
from models.database import get_connection, insert_audit_log
from routes.auth import get_current_user

router = APIRouter(prefix="/api/upload", tags=["Upload"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv"}
ALLOWED_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    "application/vnd.ms-excel",                                            # xls
    "application/octet-stream",                                            # some browsers
    "text/csv",
    "application/csv",
}
_FILENAME_SAFE = re.compile(r"[^\w\s\-.]")


def _validate_extension(filename: str) -> str:
    if not filename or "." not in filename:
        raise HTTPException(400, "Filename must include an extension")
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "File type not allowed. Use .xlsx, .xls, or .csv")
    return ext


def _secure_filename(filename: str) -> str:
    """Drop path components and unsafe characters."""
    base = os.path.basename(filename)
    cleaned = _FILENAME_SAFE.sub("_", base).strip(" ._")
    if not cleaned:
        raise HTTPException(400, "Invalid filename")
    return cleaned


async def _save_upload(file: UploadFile, subfolder: str, file_type: str, user: dict):
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            400, f"Unsupported content type: {file.content_type}"
        )
    _validate_extension(file.filename or "")
    filename = _secure_filename(file.filename or "")

    upload_dir = os.path.join(BASE_DIR, "uploads", subfolder)
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)

    # Stream to disk with a hard cap so we never buffer a giant file in RAM.
    max_bytes = config.MAX_UPLOAD_BYTES
    written = 0
    chunk_size = 1024 * 1024
    try:
        with open(filepath, "wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass
                    raise HTTPException(
                        413,
                        f"File exceeds maximum allowed size of {max_bytes} bytes",
                    )
                out.write(chunk)
    finally:
        await file.close()

    with get_connection() as conn:
        insert_audit_log(
            conn,
            "file_upload",
            user_id=user.get("id"),
            details={
                "file_type": file_type,
                "filename": filename,
                "filepath": filepath,
                "bytes": written,
            },
        )

    return {
        "message": f"{file_type} uploaded successfully",
        "filename": filename,
        "filepath": filepath,
        "size": written,
    }


@router.post("/bank-statement")
async def upload_bank_statement(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload and store bank statement file."""
    return await _save_upload(file, "bank_statements", "bank_statement", user)


@router.post("/bank-book")
async def upload_bank_book(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload and store bank book ledger file."""
    return await _save_upload(file, "bank_books", "bank_book", user)


@router.post("/previous-brs")
async def upload_previous_brs(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload and store previous month's BRS file."""
    return await _save_upload(file, "previous_brs", "previous_brs", user)
