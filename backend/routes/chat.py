"""
Chat route — RAG-powered Q&A about reconciliation results (via ML service).
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from models.database import get_connection, get_run

router = APIRouter(prefix="/api/chat", tags=["Chat"])


class ChatRequest(BaseModel):
    question: str
    run_id: int | None = None


@router.post("")
async def chat(req: ChatRequest):
    """Answer a question about reconciliation data using the ML service."""

    async with get_connection() as conn:
        if req.run_id:
            run = await get_run(conn, req.run_id)
            if not run:
                raise HTTPException(404, "Run not found")
        else:
            row = await conn.fetchrow(
                "SELECT * FROM runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1"
            )
            if not row:
                raise HTTPException(404, "No completed runs found")
            run = dict(row)

        run_id = run["id"]

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

        unmatched = await conn.fetch(
            "SELECT * FROM transactions WHERE run_id=$1 AND match_status='unmatched' LIMIT 50",
            run_id,
        )
        if unmatched:
            context_parts.append("\n## Unmatched Transactions (up to 50)")
            for t in unmatched:
                t = dict(t)
                context_parts.append(
                    f"- [{t['source']}] Date={t.get('transaction_date')} "
                    f"Amt={t.get('amount')} Dir={t.get('direction')} "
                    f"Desc=\"{t.get('description') or t.get('narration') or ''}\""
                )

        exceptions = await conn.fetch(
            "SELECT * FROM exceptions WHERE run_id=$1 LIMIT 30", run_id
        )
        if exceptions:
            context_parts.append("\n## Exceptions")
            for e in exceptions:
                e = dict(e)
                context_parts.append(
                    f"- Type={e['exception_type']} Section={e.get('brs_section')} "
                    f"Status={e['status']} SLA={e.get('sla_days')} days"
                )

        matches_summary = await conn.fetch(
            "SELECT match_type, COUNT(*) as cnt, SUM(matched_amount) as total "
            "FROM matches WHERE run_id=$1 GROUP BY match_type",
            run_id,
        )
        if matches_summary:
            context_parts.append("\n## Matches by Type")
            for m in matches_summary:
                m = dict(m)
                context_parts.append(
                    f"- {m['match_type']}: {m['cnt']} matches, total ₹{m.get('total', 0):,.2f}"
                )

    context = "\n".join(context_parts)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{config.ML_SERVICE_URL}/ask",
                json={"question": req.question, "context": context},
            )
            if resp.status_code != 200:
                raise HTTPException(502, "ML service error")
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(503, "ML service unavailable")
