"""
Admin routes — user management and database clearing.
"""
from __future__ import annotations

from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from models.database import (
    delete_bank_account,
    get_bank_account,
    get_bank_accounts,
    get_connection,
    insert_audit_log,
    insert_bank_account,
    update_bank_account,
)
import config
from routes.auth import get_current_user, require_role

router = APIRouter(prefix="/api/admin", tags=["Admin"])

VALID_ROLES = (
    "accounts_officer",
    "accounts_manager",
    "finance_controller",
    "internal_auditor",
    "system_admin",
)


class CreateUserRequest(BaseModel):
    username: str
    full_name: str
    role: str
    password: str
    email: Optional[str] = None


class UpdateUserRequest(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    email: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    new_password: str


class CreateBankAccountRequest(BaseModel):
    account_no: str
    bank_name: str
    branch: str = ""
    account_type: str = "Savings"
    label: str


class UpdateBankAccountRequest(BaseModel):
    bank_name: Optional[str] = None
    branch: Optional[str] = None
    account_type: Optional[str] = None
    label: Optional[str] = None
    is_active: Optional[bool] = None


# ── Users ────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(user: dict = Depends(require_role("system_admin"))):
    async with get_connection() as conn:
        rows = await conn.fetch(
            "SELECT id, username, full_name, role, email, is_active, created_at FROM users ORDER BY id"
        )
    return [dict(r) for r in rows]


@router.post("/users")
async def create_user(req: CreateUserRequest, admin: dict = Depends(require_role("system_admin"))):
    if req.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")
    if len(req.password) < config.PASSWORD_MIN_LENGTH:
        raise HTTPException(400, f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters")

    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()

    async with get_connection() as conn:
        existing = await conn.fetchrow("SELECT id FROM users WHERE username=$1", req.username)
        if existing:
            raise HTTPException(409, "Username already exists")

        new_id = await conn.fetchval(
            """INSERT INTO users (username, password_hash, full_name, role, email)
               VALUES ($1,$2,$3,$4,$5) RETURNING id""",
            req.username, password_hash, req.full_name, req.role, req.email,
        )
        await insert_audit_log(
            conn, "user_created",
            user_id=admin["id"],
            entity_type="user",
            entity_id=new_id,
            details={"username": req.username, "role": req.role},
        )

    return {"message": "User created", "user_id": new_id}


@router.put("/users/{user_id}")
async def update_user(user_id: int, req: UpdateUserRequest,
                      admin: dict = Depends(require_role("system_admin"))):
    updates: dict = {}
    if req.full_name is not None:
        updates["full_name"] = req.full_name
    if req.role is not None:
        if req.role not in VALID_ROLES:
            raise HTTPException(400, f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")
        updates["role"] = req.role
    if req.is_active is not None:
        updates["is_active"] = req.is_active
    if req.email is not None:
        updates["email"] = req.email

    if not updates:
        raise HTTPException(400, "No fields to update")

    parts = [f"{k} = ${i + 1}" for i, k in enumerate(updates)]
    values = list(updates.values()) + [user_id]

    async with get_connection() as conn:
        await conn.execute(
            f"UPDATE users SET {', '.join(parts)} WHERE id = ${len(values)}",
            *values,
        )
        await insert_audit_log(
            conn, "user_updated",
            user_id=admin["id"],
            entity_type="user",
            entity_id=user_id,
            details=updates,
        )

    return {"message": "User updated"}


@router.delete("/users/{user_id}")
async def deactivate_user(user_id: int, admin: dict = Depends(require_role("system_admin"))):
    if user_id == admin["id"]:
        raise HTTPException(400, "Cannot deactivate your own account")

    async with get_connection() as conn:
        await conn.execute("UPDATE users SET is_active = FALSE WHERE id = $1", user_id)
        await insert_audit_log(
            conn, "user_deactivated",
            user_id=admin["id"],
            entity_type="user",
            entity_id=user_id,
        )

    return {"message": "User deactivated"}


@router.delete("/users/{user_id}/permanent")
async def delete_user_permanently(user_id: int, admin: dict = Depends(require_role("system_admin"))):
    if user_id == admin["id"]:
        raise HTTPException(400, "Cannot delete your own account")

    async with get_connection() as conn:
        row = await conn.fetchrow("SELECT username FROM users WHERE id = $1", user_id)
        if not row:
            raise HTTPException(404, "User not found")
        await conn.execute("DELETE FROM users WHERE id = $1", user_id)
        await insert_audit_log(
            conn, "user_deleted",
            user_id=admin["id"],
            entity_type="user",
            entity_id=user_id,
            details={"username": row["username"]},
        )

    return {"message": f"User '{row['username']}' permanently deleted"}


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(user_id: int, req: ResetPasswordRequest,
                               admin: dict = Depends(require_role("system_admin"))):
    if len(req.new_password) < config.PASSWORD_MIN_LENGTH:
        raise HTTPException(400, f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters")

    new_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()

    async with get_connection() as conn:
        await conn.execute(
            "UPDATE users SET password_hash=$1 WHERE id=$2",
            new_hash, user_id,
        )
        await insert_audit_log(
            conn, "password_reset_by_admin",
            user_id=admin["id"],
            entity_type="user",
            entity_id=user_id,
        )

    return {"message": "Password reset successfully"}


# ── Database Clearing ────────────────────────────────────────────

@router.post("/clear-database")
async def clear_database(admin: dict = Depends(require_role("system_admin"))):
    tables = [
        "exception_comments", "exceptions", "matches",
        "carry_forward", "transactions", "approvals", "runs",
    ]
    async with get_connection() as conn:
        for table in tables:
            await conn.execute(f"DELETE FROM {table}")
        await insert_audit_log(
            conn, "database_cleared",
            user_id=admin["id"],
            details={"tables_cleared": tables},
        )

    return {"message": "All reconciliation data cleared", "tables_cleared": tables}


# ── Bank Accounts ────────────────────────────────────────────────

@router.get("/bank-accounts")
async def list_bank_accounts(user: dict = Depends(get_current_user)):
    async with get_connection() as conn:
        return await get_bank_accounts(conn, active_only=False)


@router.post("/bank-accounts")
async def create_bank_account(req: CreateBankAccountRequest,
                               admin: dict = Depends(require_role("system_admin"))):
    async with get_connection() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM bank_accounts WHERE account_no=$1", req.account_no
        )
        if existing:
            raise HTTPException(409, "Account number already exists")

        new_id = await insert_bank_account(
            conn, req.account_no, req.bank_name, req.branch,
            req.account_type, req.label,
        )
        await insert_audit_log(
            conn, "bank_account_created",
            user_id=admin["id"],
            entity_type="bank_account",
            entity_id=new_id,
            details={"account_no": req.account_no},
        )

    return {"message": "Bank account created", "account_id": new_id}


@router.put("/bank-accounts/{account_id}")
async def update_bank_account_route(account_id: int, req: UpdateBankAccountRequest,
                                     admin: dict = Depends(require_role("system_admin"))):
    updates: dict = {}
    for field in ("bank_name", "branch", "account_type", "label", "is_active"):
        val = getattr(req, field)
        if val is not None:
            updates[field] = val

    if not updates:
        raise HTTPException(400, "No fields to update")

    async with get_connection() as conn:
        if not await get_bank_account(conn, account_id):
            raise HTTPException(404, "Bank account not found")
        await update_bank_account(conn, account_id, **updates)
        await insert_audit_log(
            conn, "bank_account_updated",
            user_id=admin["id"],
            entity_type="bank_account",
            entity_id=account_id,
            details=updates,
        )

    return {"message": "Bank account updated"}


@router.delete("/bank-accounts/{account_id}")
async def delete_bank_account_route(account_id: int,
                                     admin: dict = Depends(require_role("system_admin"))):
    async with get_connection() as conn:
        if not await get_bank_account(conn, account_id):
            raise HTTPException(404, "Bank account not found")
        await delete_bank_account(conn, account_id)
        await insert_audit_log(
            conn, "bank_account_deleted",
            user_id=admin["id"],
            entity_type="bank_account",
            entity_id=account_id,
        )

    return {"message": "Bank account deleted"}
