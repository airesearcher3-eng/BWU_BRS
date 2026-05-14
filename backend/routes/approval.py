"""
Approval and Sign-off Routes — 3-level approval chain.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from models.database import get_connection, insert_audit_log, update_run
from routes.auth import require_role

router = APIRouter(prefix="/api/approval", tags=["Approval"])


class ApprovalRequest(BaseModel):
    comments: Optional[str] = ""


@router.post("/{run_id}/submit")
async def submit_for_approval(run_id: int):
    async with get_connection() as conn:
        run = await conn.fetchrow("SELECT * FROM runs WHERE id = $1", run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        if dict(run)["status"] not in ("completed",):
            raise HTTPException(400, "Run must be completed before submission")

        await conn.execute(
            "INSERT INTO approvals (run_id, level, role, action) VALUES ($1,$2,$3,$4)",
            run_id, 1, "accounts_officer", "submitted",
        )
        await update_run(conn, run_id, status="pending_review")
        await insert_audit_log(conn, "approval_submitted", entity_type="run",
                               entity_id=run_id, details={"level": 1})

    return {"message": "BRS submitted for manager review"}


@router.post("/{run_id}/approve")
async def manager_approve(run_id: int, req: ApprovalRequest,
                          _user: dict = Depends(require_role("accounts_manager", "system_admin"))):
    async with get_connection() as conn:
        run = await conn.fetchrow("SELECT * FROM runs WHERE id = $1", run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        if dict(run)["status"] != "pending_review":
            raise HTTPException(400, "Run must be pending review for approval")

        await conn.execute(
            "INSERT INTO approvals (run_id, level, role, action, comments) VALUES ($1,$2,$3,$4,$5)",
            run_id, 2, "accounts_manager", "approved", req.comments,
        )
        await update_run(conn, run_id, status="approved")
        await insert_audit_log(conn, "approval_approved", entity_type="run",
                               entity_id=run_id, details={"level": 2, "comments": req.comments})

    return {"message": "BRS approved by Accounts Manager"}


@router.post("/{run_id}/signoff")
async def controller_signoff(run_id: int, req: ApprovalRequest,
                             _user: dict = Depends(require_role("finance_controller", "system_admin"))):
    async with get_connection() as conn:
        run = await conn.fetchrow("SELECT * FROM runs WHERE id = $1", run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        if dict(run)["status"] != "approved":
            raise HTTPException(400, "Run must be approved before final sign-off")

        await conn.execute(
            "INSERT INTO approvals (run_id, level, role, action, comments) VALUES ($1,$2,$3,$4,$5)",
            run_id, 3, "finance_controller", "signed_off", req.comments,
        )
        await update_run(conn, run_id, status="signed_off")
        await insert_audit_log(conn, "approval_signoff", entity_type="run",
                               entity_id=run_id, details={"level": 3, "comments": req.comments})

    return {"message": "BRS signed off by Finance Controller"}


@router.get("/{run_id}/status")
async def get_approval_status(run_id: int):
    async with get_connection() as conn:
        approvals = await conn.fetch(
            "SELECT * FROM approvals WHERE run_id = $1 ORDER BY level", run_id
        )
        run = await conn.fetchrow("SELECT status FROM runs WHERE id = $1", run_id)

    return {
        "run_status": run["status"] if run else "unknown",
        "approvals": [dict(a) for a in approvals],
    }
