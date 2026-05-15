"""
ML Service — OpenAI Hybrid RAG matching + Q&A assistant.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any

from rag.hybrid_orchestrator import run_hybrid_rag_matching
from rag.openai_client import ask_question

_FMT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"


def _configure_logging() -> None:
    """Install a consistent single-line formatter on all active log handlers.

    Called once at startup (after uvicorn has initialised its own handlers)
    so that every logger — including uvicorn's — uses the same format.
    """
    formatter = logging.Formatter(_FMT, datefmt=_DATE)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Re-format any handlers uvicorn already installed on the root logger.
    for h in list(root.handlers):
        h.setFormatter(formatter)
    # If uvicorn left no handlers at all, add a stdout one.
    if not root.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(formatter)
        root.addHandler(h)
    # Align uvicorn's own loggers.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.propagate = True
        lg.handlers = []   # let them propagate to root with our format


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app_: FastAPI):
    # Reconfigure AFTER uvicorn has set up its own handlers.
    _configure_logging()
    logger.info("ML Service started (logging configured)")
    yield


app = FastAPI(title="BWU BRS ML Service", version="1.0.0", lifespan=lifespan)


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

    logger.info(
        "rag/match request: %d statement rows, %d book rows",
        len(req.statement_rows), len(req.book_rows),
    )
    result = await run_hybrid_rag_matching(req.statement_rows, req.book_rows)
    logger.info(
        "rag/match response: %d matches returned",
        len(result.get("matches", [])),
    )
    return result


@app.post("/ask")
async def ask(req: AskRequest):
    """Answer a question about reconciliation data using GPT-4o-mini."""
    if not req.question.strip():
        raise HTTPException(400, "Question is required")

    answer = await ask_question(req.question, req.context)
    return {"answer": answer}
