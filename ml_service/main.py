"""
ML Service — OpenAI Hybrid RAG matching + Q&A assistant.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any

from rag.hybrid_orchestrator import run_hybrid_rag_matching
from rag.openai_client import ask_question

app = FastAPI(title="BWU BRS ML Service", version="1.0.0")


class MatchRequest(BaseModel):
    statement_rows: list[dict[str, Any]]
    book_rows: list[dict[str, Any]]


class AskRequest(BaseModel):
    question: str
    context: str = ""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/rag/match")
async def rag_match(req: MatchRequest):
    """Hybrid RAG matching: dense (OpenAI embeddings) + sparse (BM25) + GPT-4o-mini verification."""
    if not req.statement_rows or not req.book_rows:
        return {"matches": [], "unmatched_statements": [], "unmatched_books": []}

    result = await run_hybrid_rag_matching(req.statement_rows, req.book_rows)
    return result


@app.post("/ask")
async def ask(req: AskRequest):
    """Answer a question about reconciliation data using GPT-4o-mini."""
    if not req.question.strip():
        raise HTTPException(400, "Question is required")

    answer = await ask_question(req.question, req.context)
    return {"answer": answer}
