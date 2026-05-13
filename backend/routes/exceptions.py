"""
Exception Management Routes.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from models.database import get_connection, get_exceptions, insert_audit_log

router = APIRouter(prefix="/api/exceptions", tags=["Exceptions"])

SUGGESTED_SOLUTIONS = {
    "unknown_dr": (
        "Verify if this debit is a bank charge, DD issuance, or auto-debit mandate. "
        "Check with ICICI for narration clarification. Cross-reference with the ERP "
        "expense register for matching entries."
    ),
    "unknown_cr": (
        "Check if this is a refund, interest credit, or returned cheque. "
        "Cross-reference with ERP pending receipts and fee collection records. "
        "Contact the bank if the narration is unclear."
    ),
    "timing_difference": (
        "This cheque/NEFT is likely in transit and has not yet cleared at the bank. "
        "It should appear in the next period's bank statement. Carry forward to the "
        "next BRS period — no action required unless stale (90+ days)."
    ),
    "stale_carry_forward": (
        "This item has been pending for 90+ days without clearing. Contact the bank "
        "or counterparty to confirm status. If the cheque is stale-dated, issue a "
        "fresh instrument. Consider write-off only after Finance Controller approval."
    ),
    "gib_unmatched": (
        "Check the GST/TDS portal for a matching challan or payment confirmation. "
        "Cross-reference with the university's tax payment register. The payment may "
        "have been processed with a different reference number."
    ),
    "amount_mismatch": (
        "Compare the bank and book amounts — the difference may be a bank charge, "
        "partial payment, TDS deduction, or rounding difference. Check if multiple "
        "book entries aggregate to the bank amount."
    ),
}


class CommentRequest(BaseModel):
    comment: str


class ResolveRequest(BaseModel):
    resolution_type: str = "manual_match"


@router.get("")
async def list_exceptions(
    run_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
):
    async with get_connection() as conn:
        exc_list = await get_exceptions(conn, run_id, status)

    for exc in exc_list:
        exc["suggested_solution"] = SUGGESTED_SOLUTIONS.get(
            exc.get("exception_type"),
            "Review this item manually and consult with the Accounts Manager.",
        )
    return exc_list


@router.get("/{exc_id}")
async def get_exception(exc_id: int):
    async with get_connection() as conn:
        row = await conn.fetchrow(
            """SELECT e.*, t.transaction_date, t.amount, t.direction,
                      t.narration, t.description, t.source, t.voucher_type,
                      t.voucher_no, t.cheque_no, t.transaction_id,
                      t.original_row, t.references_json,
                      u.full_name AS assigned_to_name
               FROM exceptions e
               JOIN transactions t ON e.transaction_id = t.id
               LEFT JOIN users u ON e.assigned_to = u.id
               WHERE e.id = $1""",
            exc_id,
        )
        if not row:
            raise HTTPException(404, "Exception not found")

        exc = dict(row)
        exc["suggested_solution"] = SUGGESTED_SOLUTIONS.get(
            exc.get("exception_type"),
            "Review this item manually and consult with the Accounts Manager.",
        )

        comments = await conn.fetch(
            """SELECT ec.*, u.full_name AS commenter_name
               FROM exception_comments ec
               LEFT JOIN users u ON ec.user_id = u.id
               WHERE ec.exception_id = $1 ORDER BY ec.created_at""",
            exc_id,
        )
        exc["comments"] = [dict(c) for c in comments]

    return exc


@router.post("/{exc_id}/comment")
async def add_comment(exc_id: int, req: CommentRequest):
    if not req.comment.strip():
        raise HTTPException(400, "Comment text is required")

    async with get_connection() as conn:
        await conn.execute(
            "INSERT INTO exception_comments (exception_id, comment_text) VALUES ($1,$2)",
            exc_id, req.comment.strip(),
        )
        await insert_audit_log(conn, "exception_comment_added",
                               entity_type="exception", entity_id=exc_id,
                               details={"comment": req.comment.strip()})

    return {"message": "Comment added"}


@router.post("/{exc_id}/resolve")
async def resolve_exception(exc_id: int, req: ResolveRequest):
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE exceptions SET status='resolved', resolution_type=$1, resolved_at=$2 WHERE id=$3",
            req.resolution_type, datetime.now().isoformat(), exc_id,
        )
        await insert_audit_log(conn, "exception_resolved",
                               entity_type="exception", entity_id=exc_id,
                               details={"resolution_type": req.resolution_type})

    return {"message": "Exception resolved"}


@router.post("/{exc_id}/escalate")
async def escalate_exception(exc_id: int):
    async with get_connection() as conn:
        manager = await conn.fetchrow(
            """SELECT id FROM users
               WHERE role IN ('accounts_manager','system_admin')
               AND is_active = TRUE
               ORDER BY CASE role WHEN 'accounts_manager' THEN 1 ELSE 2 END
               LIMIT 1"""
        )
        manager_id = manager["id"] if manager else None

        await conn.execute(
            "UPDATE exceptions SET status='escalated', assigned_to=$1 WHERE id=$2",
            manager_id, exc_id,
        )
        await insert_audit_log(conn, "exception_escalated",
                               entity_type="exception", entity_id=exc_id,
                               details={"assigned_to": manager_id})

    return {"message": "Exception escalated"}
