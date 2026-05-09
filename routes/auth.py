"""
Authentication routes — login, token validation, password change.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from models.database import get_connection

router = APIRouter(prefix="/api/auth", tags=["Auth"])

JWT_SECRET = os.getenv("JWT_SECRET", "dev-jwt-secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


# ── Schemas ─────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ── JWT helpers ─────────────────────────────────────────────────

def _create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired — please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token — please log in again")


def get_current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency that extracts and validates the JWT from the
    ``Authorization: Bearer <token>`` header."""

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")

    payload = _decode_token(auth_header[7:])
    try:
        user_id = int(payload.get("sub", 0))
    except (ValueError, TypeError):
        raise HTTPException(401, "Invalid token payload")
    if not user_id:
        raise HTTPException(401, "Invalid token payload")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, full_name, role, is_active FROM users WHERE id=?",
            (user_id,),
        ).fetchone()

    if not row:
        raise HTTPException(401, "User not found")
    user = dict(row)
    if not user["is_active"]:
        raise HTTPException(403, "Account disabled — contact the administrator")
    return user


def require_role(*roles: str):
    """Return a dependency that checks the current user has one of the
    specified roles."""

    def checker(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return checker


# ── Routes ──────────────────────────────────────────────────────

@router.post("/login")
async def login(req: LoginRequest):
    """Validate credentials and return a JWT token."""

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, full_name, role, is_active FROM users WHERE username=?",
            (req.username,),
        ).fetchone()

    if not row:
        raise HTTPException(401, "Invalid username or password")

    user = dict(row)
    if not user["is_active"]:
        raise HTTPException(403, "Account disabled — contact the administrator")

    if not bcrypt.checkpw(req.password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Invalid username or password")

    token = _create_token(user["id"], user["username"], user["role"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"],
        },
    }


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return the profile of the currently logged-in user."""
    return user


@router.post("/change-password")
async def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    """Let the logged-in user change their own password."""

    if len(req.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["id"],)
        ).fetchone()

    if not row or not bcrypt.checkpw(req.current_password.encode(), dict(row)["password_hash"].encode()):
        raise HTTPException(401, "Current password is incorrect")

    new_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash=?, initial_password=NULL WHERE id=?",
            (new_hash, user["id"]),
        )

    return {"message": "Password changed successfully"}
