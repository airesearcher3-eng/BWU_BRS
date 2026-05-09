"""
Superadmin routes — user management and database clearing.
"""
from __future__ import annotations

from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from models.database import (
    get_connection,
    get_bank_accounts,
    get_bank_account,
    insert_bank_account,
    update_bank_account,
    delete_bank_account,
    insert_audit_log,
)
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


# ── User CRUD ───────────────────────────────────────────────────

@router.get("/users")
async def list_users(user: dict = Depends(require_role("system_admin"))):
    """List all users including initial passwords (superadmin only)."""

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, username, full_name, role, email, is_active,
                      initial_password, created_at
               FROM users ORDER BY id"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/users")
async def create_user(req: CreateUserRequest, user: dict = Depends(require_role("system_admin"))):
    """Create a new user (superadmin only). Stores initial plaintext password for admin visibility."""

    if req.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username=?", (req.username,)
        ).fetchone()
        if existing:
            raise HTTPException(409, "Username already exists")

        cursor = conn.execute(
            """INSERT INTO users (username, password_hash, initial_password, full_name, role, email)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (req.username, password_hash, req.password, req.full_name, req.role, req.email),
        )
        new_id = cursor.lastrowid
        insert_audit_log(
            conn, "user_created",
            user_id=user["id"],
            entity_type="user",
            entity_id=new_id,
            details={"username": req.username, "role": req.role},
        )

    return {"message": "User created", "user_id": new_id}


@router.put("/users/{user_id}")
async def update_user(user_id: int, req: UpdateUserRequest,
                      admin: dict = Depends(require_role("system_admin"))):
    """Update a user's role, name, or active status (superadmin only)."""

    updates = {}
    if req.full_name is not None:
        updates["full_name"] = req.full_name
    if req.role is not None:
        if req.role not in VALID_ROLES:
            raise HTTPException(400, f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")
        updates["role"] = req.role
    if req.is_active is not None:
        updates["is_active"] = 1 if req.is_active else 0
    if req.email is not None:
        updates["email"] = req.email

    if not updates:
        raise HTTPException(400, "No fields to update")

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [user_id]

    with get_connection() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id=?", values)
        insert_audit_log(
            conn, "user_updated",
            user_id=admin["id"],
            entity_type="user",
            entity_id=user_id,
            details=updates,
        )

    return {"message": "User updated"}


@router.delete("/users/{user_id}")
async def deactivate_user(user_id: int, admin: dict = Depends(require_role("system_admin"))):
    """Deactivate a user (superadmin only). Does not delete data."""

    if user_id == admin["id"]:
        raise HTTPException(400, "Cannot deactivate your own account")

    with get_connection() as conn:
        conn.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))
        insert_audit_log(
            conn, "user_deactivated",
            user_id=admin["id"],
            entity_type="user",
            entity_id=user_id,
        )

    return {"message": "User deactivated"}


@router.delete("/users/{user_id}/permanent")
async def delete_user_permanently(user_id: int, admin: dict = Depends(require_role("system_admin"))):
    """Permanently delete a user (superadmin only)."""

    if user_id == admin["id"]:
        raise HTTPException(400, "Cannot delete your own account")

    with get_connection() as conn:
        row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        username = dict(row)["username"]
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        insert_audit_log(
            conn, "user_deleted",
            user_id=admin["id"],
            entity_type="user",
            entity_id=user_id,
            details={"username": username},
        )

    return {"message": f"User '{username}' permanently deleted"}


# ── Password Reset (admin resets user password) ─────────────────

class ResetPasswordRequest(BaseModel):
    new_password: str


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(user_id: int, req: ResetPasswordRequest,
                               admin: dict = Depends(require_role("system_admin"))):
    """Admin resets a user's password. Updates both hash and initial_password."""
    if len(req.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    new_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash=?, initial_password=? WHERE id=?",
            (new_hash, req.new_password, user_id),
        )
        insert_audit_log(
            conn, "password_reset_by_admin",
            user_id=admin["id"],
            entity_type="user",
            entity_id=user_id,
        )

    return {"message": "Password reset successfully"}


# ── Database Clearing ───────────────────────────────────────────

@router.post("/clear-database")
async def clear_database(admin: dict = Depends(require_role("system_admin"))):
    """Truncate all data tables. Preserves users and audit log."""

    tables_to_clear = [
        "exception_comments",
        "exceptions",
        "matches",
        "carry_forward",
        "transactions",
        "approvals",
        "runs",
    ]

    with get_connection() as conn:
        for table in tables_to_clear:
            conn.execute(f"DELETE FROM {table}")

        insert_audit_log(
            conn, "database_cleared",
            user_id=admin["id"],
            details={"tables_cleared": tables_to_clear},
        )

    return {"message": "All reconciliation data has been cleared", "tables_cleared": tables_to_clear}


# ── Bank Accounts ───────────────────────────────────────────────

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


@router.get("/bank-accounts")
async def list_bank_accounts(user: dict = Depends(get_current_user)):
    """List all active bank accounts."""
    with get_connection() as conn:
        return get_bank_accounts(conn, active_only=True)


@router.post("/bank-accounts")
async def create_bank_account(req: CreateBankAccountRequest,
                               admin: dict = Depends(require_role("system_admin", "accounts_manager"))):
    """Create a new bank account (superadmin only)."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM bank_accounts WHERE account_no=?", (req.account_no,)
        ).fetchone()
        if existing:
            raise HTTPException(409, "Account number already exists")
        new_id = insert_bank_account(
            conn, req.account_no, req.bank_name, req.branch,
            req.account_type, req.label,
        )
        insert_audit_log(
            conn, "bank_account_created",
            user_id=admin["id"],
            entity_type="bank_account",
            entity_id=new_id,
            details={"account_no": req.account_no, "bank_name": req.bank_name},
        )
    return {"message": "Bank account created", "id": new_id}


@router.put("/bank-accounts/{account_id}")
async def update_bank_account_route(account_id: int,
                                     req: UpdateBankAccountRequest,
                                     admin: dict = Depends(require_role("system_admin", "accounts_manager"))):
    """Update a bank account's details (superadmin only)."""
    updates = {}
    if req.bank_name is not None:
        updates["bank_name"] = req.bank_name
    if req.branch is not None:
        updates["branch"] = req.branch
    if req.account_type is not None:
        updates["account_type"] = req.account_type
    if req.label is not None:
        updates["label"] = req.label
    if req.is_active is not None:
        updates["is_active"] = 1 if req.is_active else 0
    if not updates:
        raise HTTPException(400, "No fields to update")
    with get_connection() as conn:
        update_bank_account(conn, account_id, **updates)
        insert_audit_log(
            conn, "bank_account_updated",
            user_id=admin["id"],
            entity_type="bank_account",
            entity_id=account_id,
            details=updates,
        )
    return {"message": "Bank account updated"}


@router.delete("/bank-accounts/{account_id}")
async def delete_bank_account_route(
    account_id: int,
    admin: dict = Depends(require_role("system_admin", "accounts_manager")),
):
    """Permanently delete a bank account."""
    with get_connection() as conn:
        acct = get_bank_account(conn, account_id)
        if not acct:
            raise HTTPException(404, "Bank account not found")
        # Prevent deletion if runs reference this account
        linked = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE bank_account_id=?", (account_id,)
        ).fetchone()[0]
        if linked:
            raise HTTPException(
                409,
                f"Cannot delete: {linked} reconciliation run(s) reference this account. Deactivate it instead.",
            )
        delete_bank_account(conn, account_id)
        insert_audit_log(
            conn,
            "bank_account_deleted",
            user_id=admin["id"],
            entity_type="bank_account",
            entity_id=account_id,
            details={"account_no": acct["account_no"], "bank_name": acct["bank_name"]},
        )
    return {"message": "Bank account deleted"}
