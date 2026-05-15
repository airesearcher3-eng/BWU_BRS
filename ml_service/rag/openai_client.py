"""OpenAI client — chat (gpt-4o-mini) for LLM-based match verification and Q&A.

System prompts are loaded from .md files in rag/prompts/ so they can be reviewed,
versioned, and tuned without touching Python code.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
CHAT_MODEL = "gpt-4o-mini"

# ── Load prompts from .md files ──────────────────────────────────────────────
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Load prompt text from a .md file; fall back to empty string if missing."""
    path = _PROMPTS_DIR / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s", path)
        return ""


_MATCH_SYSTEM_PROMPT: str = _load_prompt("match_system.md")
_ASK_SYSTEM_PROMPT: str = _load_prompt("ask_system.md")

# ── User-message template for match verification ────────────────────────────
_MATCH_USER_TEMPLATE = """\
Match the UNMATCHED bank statement entries below against the bank book entries.

Return ONLY a JSON object (no markdown, no prose) in this exact schema:
{{
  "matches": [
    {{
      "statement_indices": [<int>, ...],
      "book_indices": [<int>, ...],
      "match_type": "<code>",
      "confidence": <0.0-1.0>,
      "reasoning": "<one concise sentence>"
    }}
  ],
  "unmatched_statements": [<int>, ...],
  "unmatched_books": [<int>, ...]
}}

Confidence floor: include a match only if confidence ≥ 0.50.
Match type codes: exact_ref, upi_batch, neft_batch, rtgs_batch, portal_settlement,
cheque, fd_booking, fd_maturity, contra, gib_tax, salary_neft, amount_date, rag_hybrid

STATEMENT entries (index → fields):
{statement_text}

BANK BOOK entries (index → fields):
{book_text}
"""


def _format_entry(idx: int, row: dict) -> str:
    """Serialise one transaction row into a compact, readable string for the LLM."""
    parts = [f"[{idx}]"]
    for key in ("transaction_date", "voucher_date", "amount", "direction",
                "description", "narration", "particulars", "cheque_no",
                "transaction_id", "voucher_no", "voucher_type"):
        v = row.get(key)
        if v:
            parts.append(f"{key}={v}")
    refs = row.get("refs") or row.get("references") or []
    if refs:
        parts.append(f"refs={','.join(str(r) for r in refs)}")
    return " | ".join(parts)


async def match_batch(
    statement_rows: list[dict],
    book_rows: list[dict],
    stmt_offset: int = 0,
    book_offset: int = 0,
) -> dict[str, Any]:
    """Call GPT-4o-mini to verify and finalise matches for one batch.

    Uses the match_system.md prompt for expert-level reconciliation reasoning.
    Returns a structured JSON dict; on parse failure returns empty match sets.
    """
    stmt_text = "\n".join(_format_entry(i + stmt_offset, r) for i, r in enumerate(statement_rows))
    book_text = "\n".join(_format_entry(i + book_offset, r) for i, r in enumerate(book_rows))

    try:
        resp = await _client.chat.completions.create(
            model=CHAT_MODEL,
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": _MATCH_SYSTEM_PROMPT},
                {"role": "user", "content": _MATCH_USER_TEMPLATE.format(
                    statement_text=stmt_text, book_text=book_text)},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("LLM returned invalid JSON: %s", exc)
    except Exception as exc:
        logger.error("LLM match_batch error: %s", exc)

    return {
        "matches": [],
        "unmatched_statements": list(range(len(statement_rows))),
        "unmatched_books": list(range(len(book_rows))),
    }


async def ask_question(question: str, context: str) -> str:
    """Answer a free-form question about reconciliation data using ask_system.md prompt."""
    try:
        resp = await _client.chat.completions.create(
            model=CHAT_MODEL,
            temperature=0.2,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": _ASK_SYSTEM_PROMPT},
                {"role": "user", "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}"},
            ],
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.error("LLM ask_question error: %s", exc)
        return "An error occurred while processing your question. Please try again."
