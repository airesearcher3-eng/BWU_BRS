"""
Approval and Sign-off Routes — 3-level approval chain.
Migrated to FastAPI.
"""
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from models.database import get_connection, insert_audit_log, update_run

router = APIRouter(prefix="/api/approval", tags=["Approval"])


class ApprovalRequest(BaseModel):
    comments: Optional[str] = ""


@router.post("/{run_id}/submit")
async def submit_for_approval(run_id: int):
    """Accounts Officer submits the BRS for review."""
    with get_connection() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run not found")

        if dict(run)["status"] not in ("completed",):
            raise HTTPException(400, "Run must be completed before submission")

        conn.execute(
            """INSERT INTO approvals (run_id, level, role, action)
               VALUES (?, 1, 'accounts_officer', 'submitted')""",
            (run_id,)
        )
        update_run(conn, run_id, status="pending_review")
        insert_audit_log(conn, "approval_submitted", entity_type="run",
                          entity_id=run_id, details={"level": 1})

    return {"message": "BRS submitted for manager review"}


@router.post("/{run_id}/approve")
async def manager_approve(run_id: int, req: ApprovalRequest):
    """Accounts Manager approves the BRS."""
    with get_connection() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run not found")

        if dict(run)["status"] not in ("pending_review",):
            raise HTTPException(400, "Run must be pending review for approval")

        conn.execute(
            """INSERT INTO approvals (run_id, level, role, action, comments)
               VALUES (?, 2, 'accounts_manager', 'approved', ?)""",
            (run_id, req.comments)
        )
        update_run(conn, run_id, status="approved")
        insert_audit_log(conn, "approval_approved", entity_type="run",
                          entity_id=run_id, details={"level": 2, "comments": req.comments})

    return {"message": "BRS approved by Accounts Manager"}


@router.post("/{run_id}/signoff")
async def controller_signoff(run_id: int, req: ApprovalRequest):
    """Finance Controller provides final sign-off."""
    with get_connection() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run not found")

        if dict(run)["status"] not in ("approved",):
            raise HTTPException(400, "Run must be approved before final sign-off")

        conn.execute(
            """INSERT INTO approvals (run_id, level, role, action, comments)
               VALUES (?, 3, 'finance_controller', 'signed_off', ?)""",
            (run_id, req.comments)
        )
        update_run(conn, run_id, status="signed_off")
        insert_audit_log(conn, "approval_signoff", entity_type="run",
                          entity_id=run_id, details={"level": 3, "comments": req.comments})

    return {"message": "BRS signed off by Finance Controller. Run is now complete."}


@router.get("/{run_id}/status")
async def get_approval_status(run_id: int):
    """Get the current approval status for a run."""
    with get_connection() as conn:
        approvals = conn.execute(
            "SELECT * FROM approvals WHERE run_id=? ORDER BY level",
            (run_id,)
        ).fetchall()
        run = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()

    return {
        "run_status": dict(run)["status"] if run else "unknown",
        "approvals": [dict(a) for a in approvals],
    }
