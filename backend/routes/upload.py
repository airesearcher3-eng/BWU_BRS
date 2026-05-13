"""
Upload Routes — handles file upload for bank statement, bank book, and previous BRS.
"""
import os
import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

import config
from models.database import get_connection, insert_audit_log
from routes.auth import get_current_user

router = APIRouter(prefix="/api/upload", tags=["Upload"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv"}
ALLOWED_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",
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
    base = os.path.basename(filename)
    cleaned = _FILENAME_SAFE.sub("_", base).strip(" ._")
    if not cleaned:
        raise HTTPException(400, "Invalid filename")
    return cleaned


async def _save_upload(file: UploadFile, subfolder: str, file_type: str, user: dict) -> dict:
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"Unsupported content type: {file.content_type}")
    ext = _validate_extension(file.filename or "")
    safe_name = _secure_filename(file.filename or "")
    # Prefix with UUID to avoid collisions
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"

    upload_dir = os.path.join(BASE_DIR, config.UPLOAD_FOLDER, subfolder)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, unique_name)

    content = await file.read()
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds maximum size of {config.MAX_UPLOAD_BYTES // (1024*1024)} MB")

    with open(file_path, "wb") as f:
        f.write(content)

    async with get_connection() as conn:
        await insert_audit_log(
            conn, f"file_uploaded_{file_type}",
            user_id=user.get("id"),
            details={"filename": safe_name, "size": len(content), "path": file_path},
        )

    return {"path": file_path, "filename": safe_name, "size": len(content)}


@router.post("/bank-statement")
async def upload_bank_statement(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    return await _save_upload(file, "bank_statements", "bank_statement", user)


@router.post("/bank-book")
async def upload_bank_book(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    return await _save_upload(file, "bank_books", "bank_book", user)


@router.post("/previous-brs")
async def upload_previous_brs(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    return await _save_upload(file, "previous_brs", "previous_brs", user)
