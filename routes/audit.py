"""
Audit Trail Routes — read-only access to the audit log.
Migrated to FastAPI.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from models.database import get_connection
from routes.auth import require_role

router = APIRouter(prefix="/api/audit", tags=["Audit"])

_mgr_or_admin = Depends(require_role("system_admin", "accounts_manager"))


@router.get("")
async def get_audit_log(limit: int = Query(100, ge=1, le=500),
                         entity_type: Optional[str] = Query(None),
                         entity_id: Optional[int] = Query(None),
                         action: Optional[str] = Query(None),
                         _user: dict = _mgr_or_admin):
    """Get audit log entries with optional filters."""
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []

    if entity_type:
        query += " AND entity_type=?"
        params.append(entity_type)
    if entity_id:
        query += " AND entity_id=?"
        params.append(entity_id)
    if action:
        query += " AND action=?"
        params.append(action)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]


@router.get("/run/{run_id}")
async def get_run_audit(run_id: int, _user: dict = _mgr_or_admin):
    """Get all audit entries for a specific run."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE entity_type='run' AND entity_id=? ORDER BY timestamp",
            (run_id,)
        ).fetchall()

    return [dict(r) for r in rows]
