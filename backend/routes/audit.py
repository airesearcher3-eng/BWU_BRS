"""
Audit Trail Routes — read-only access to the audit log.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.database import get_connection
from routes.auth import require_role

router = APIRouter(prefix="/api/audit", tags=["Audit"])

_mgr_or_admin = Depends(require_role("system_admin", "accounts_manager"))


@router.get("")
async def get_audit_log(
    limit: int = Query(100, ge=1, le=500),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    _user: dict = _mgr_or_admin,
):
    query = "SELECT * FROM audit_log WHERE 1=1"
    params: list = []

    if entity_type:
        params.append(entity_type)
        query += f" AND entity_type = ${len(params)}"
    if entity_id is not None:
        params.append(entity_id)
        query += f" AND entity_id = ${len(params)}"
    if action:
        params.append(action)
        query += f" AND action = ${len(params)}"

    params.append(limit)
    query += f" ORDER BY timestamp DESC LIMIT ${len(params)}"

    async with get_connection() as conn:
        rows = await conn.fetch(query, *params)

    return [dict(r) for r in rows]


@router.get("/run/{run_id}")
async def get_run_audit(run_id: int, _user: dict = _mgr_or_admin):
    async with get_connection() as conn:
        rows = await conn.fetch(
            "SELECT * FROM audit_log WHERE entity_type='run' AND entity_id=$1 ORDER BY timestamp",
            run_id,
        )
    return [dict(r) for r in rows]
