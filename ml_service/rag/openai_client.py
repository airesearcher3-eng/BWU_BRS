"""OpenAI client — chat (gpt-4o-mini) for LLM-based match verification and Q&A."""
from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CHAT_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """\
You are a financial reconciliation expert assistant for Brainware University (BWU).
You help match bank statement entries against ERP (bank book) entries for
Bank Reconciliation Statement (BRS) purposes.

Rules:
- "IN" direction transactions are credits (money received).
- "OUT" direction transactions are debits (money paid).
- Matches must have the SAME direction.
- Amount differences within ±2% may be valid (bank charges, rounding).
- Reference numbers (cheque/UTR/NEFT/RTGS/IMPS) are strong matching signals.
- Date tolerance: ±5 days for statement vs book (clearing delay).
- Multiple book entries may aggregate to one statement entry (batch payments).
- Return ONLY valid JSON — no markdown, no prose.
"""

MATCH_USER_TEMPLATE = """\
Match these UNMATCHED bank statement entries against bank book entries.
Return JSON: {{"matches":[{{"statement_indices":[...],"book_indices":[...],"match_type":"...","confidence":0.0-1.0,"reasoning":"..."}}],"unmatched_statements":[...],"unmatched_books":[...]}}

STATEMENT entries (index → entry):
{statement_text}

BANK BOOK entries (index → entry):
{book_text}
"""

ASK_SYSTEM = """\
You are a financial assistant helping with Bank Reconciliation Statements (BRS) at \
Brainware University. Answer clearly and concisely using the provided context. \
If the answer is not in the context, say so honestly.
"""


def _format_entry(idx: int, row: dict) -> str:
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
    """Call GPT-4o-mini to verify and finalize matches for a batch."""
    stmt_text = "\n".join(_format_entry(i + stmt_offset, r) for i, r in enumerate(statement_rows))
    book_text = "\n".join(_format_entry(i + book_offset, r) for i, r in enumerate(book_rows))

    resp = await _client.chat.completions.create(
        model=CHAT_MODEL,
        response_format={"type": "json_object"},
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": MATCH_USER_TEMPLATE.format(
                statement_text=stmt_text, book_text=book_text)},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"matches": [], "unmatched_statements": list(range(len(statement_rows))),
                "unmatched_books": list(range(len(book_rows)))}


async def ask_question(question: str, context: str) -> str:
    """Answer a free-form question about reconciliation data."""
    resp = await _client.chat.completions.create(
        model=CHAT_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": ASK_SYSTEM},
            {"role": "user", "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}"},
        ],
    )
    return resp.choices[0].message.content or ""
