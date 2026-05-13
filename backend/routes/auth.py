"""
Authentication routes — login, token validation, password change.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import config
from models.database import get_connection, insert_audit_log

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def _create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def _decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired — please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token — please log in again")


async def get_current_user(request: Request) -> dict[str, Any]:
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

    async with get_connection() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, full_name, role, is_active FROM users WHERE id=$1",
            user_id,
        )

    if not row:
        raise HTTPException(401, "User not found")
    user = dict(row)
    if not user["is_active"]:
        raise HTTPException(403, "Account disabled — contact the administrator")
    return user


def require_role(*roles: str):
    def checker(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return checker


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    async with get_connection() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash, full_name, role, is_active FROM users WHERE username=$1",
            req.username,
        )

    if not row:
        raise HTTPException(401, "Invalid username or password")

    user = dict(row)
    if not user["is_active"]:
        raise HTTPException(403, "Account disabled")

    if not bcrypt.checkpw(req.password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Invalid username or password")

    token = _create_token(user["id"], user["username"], user["role"])
    async with get_connection() as conn:
        await insert_audit_log(
            conn, "login",
            user_id=user["id"],
            ip_address=request.client.host if request.client else None,
        )
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
async def me(user: dict = Depends(get_current_user)):
    return user


@router.post("/change-password")
async def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    if len(req.new_password) < config.PASSWORD_MIN_LENGTH:
        raise HTTPException(400, f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters")

    async with get_connection() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE id=$1", user["id"]
        )
        if not row or not bcrypt.checkpw(req.current_password.encode(), row["password_hash"].encode()):
            raise HTTPException(401, "Current password is incorrect")

        new_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
        await conn.execute(
            "UPDATE users SET password_hash=$1 WHERE id=$2",
            new_hash, user["id"],
        )
        await insert_audit_log(conn, "password_changed", user_id=user["id"])

    return {"message": "Password changed successfully"}
