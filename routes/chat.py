"""Chat route — RAG-powered Q&A about reconciliation results."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engine.normaliser import decimal_to_float
from engine.rag.llm_matcher import ask_about_results
from models.database import get_connection, get_run, get_match_report

router = APIRouter(prefix="/api/chat", tags=["Chat"])


class ChatRequest(BaseModel):
    question: str
    run_id: int | None = None


@router.post("")
async def chat(req: ChatRequest):
    """Answer a question about reconciliation data using RAG."""

    # Build context from the most recent run (or specified run)
    with get_connection() as conn:
        if req.run_id:
            run = get_run(conn, req.run_id)
            if not run:
                raise HTTPException(404, "Run not found")
        else:
            row = conn.execute(
                "SELECT * FROM runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                raise HTTPException(404, "No completed runs found")
            run = dict(row)

        run_id = run["id"]

        # Gather run summary
        context_parts = [
            f"## Run #{run_id} Summary",
            f"- Period: {run.get('period_start')} to {run.get('period_end')}",
            f"- Status: {run.get('status')}",
            f"- Bank Statement Entries: {run.get('total_bank_stmt_entries', '?')}",
            f"- Bank Book Entries: {run.get('total_bank_book_entries', '?')}",
            f"- Total Matched: {run.get('total_matched', '?')}",
            f"- Total Unmatched: {run.get('total_unmatched', '?')}",
            f"- Pass 1 Matches: {run.get('pass1_matches', 0)}",
            f"- Pass 2 Matches: {run.get('pass2_matches', 0)}",
            f"- Pass 3 Matches: {run.get('pass3_matches', 0)}",
            f"- Pass 4 Matches: {run.get('pass4_matches', 0)}",
            f"- Bank Book Balance: {run.get('bank_book_balance', '?')}",
            f"- Bank Statement Balance: {run.get('bank_statement_balance', '?')}",
        ]

        # Gather unmatched transactions
        unmatched_txns = conn.execute(
            "SELECT * FROM transactions WHERE run_id=? AND match_status='unmatched' LIMIT 50",
            (run_id,),
        ).fetchall()
        if unmatched_txns:
            context_parts.append("\n## Unmatched Transactions (up to 50)")
            for t in unmatched_txns:
                t = dict(t)
                context_parts.append(
                    f"- [{t['source']}] Date={t.get('transaction_date')} "
                    f"Amt={t.get('amount')} Dir={t.get('direction')} "
                    f"Desc=\"{t.get('description') or t.get('narration') or ''}\" "
                    f"Cheque={t.get('cheque_no', '')}"
                )

        # Gather exceptions
        exceptions = conn.execute(
            "SELECT * FROM exceptions WHERE run_id=? LIMIT 30",
            (run_id,),
        ).fetchall()
        if exceptions:
            context_parts.append("\n## Exceptions")
            for e in exceptions:
                e = dict(e)
                context_parts.append(
                    f"- Type={e['exception_type']} Section={e.get('brs_section')} "
                    f"Status={e['status']} SLA={e.get('sla_days')} days"
                )

        # Match summary by type
        matches = conn.execute(
            "SELECT match_type, COUNT(*) as cnt, SUM(matched_amount) as total "
            "FROM matches WHERE run_id=? GROUP BY match_type",
            (run_id,),
        ).fetchall()
        if matches:
            context_parts.append("\n## Matches by Type")
            for m in matches:
                m = dict(m)
                context_parts.append(
                    f"- {m['match_type']}: {m['cnt']} matches, "
                    f"total ₹{m.get('total', 0):,.2f}"
                )

    context = "\n".join(context_parts)
    answer = ask_about_results(req.question, context)
    return {"answer": answer, "run_id": run_id}
