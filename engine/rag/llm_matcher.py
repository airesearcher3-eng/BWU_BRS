"""LLM-based transaction matching using Google Gemini with structured JSON output."""

from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

_client: genai.Client | None = None

SYSTEM_PROMPT = """You are a bank reconciliation expert for Brainware University (India).
Your job is to match bank STATEMENT entries against BANK BOOK (ERP ledger) entries.

## Rules for Matching

1. **Amount must match** — The statement amount must equal the book amount exactly,
   OR multiple book entries must sum exactly to the statement amount (one-to-many),
   OR multiple statement entries must sum to one book entry (many-to-one).

2. **Direction must match** — IN (credit/receipt) matches IN; OUT (debit/payment) matches OUT.

3. **Reference matching** — If a NEFT/UPI/IMPS reference code appears in both entries, they match.
   - Statement refs like NEFT-XXXXX, UPI/XXXXX should match book narration containing "Tn. No: XXXXX"
   - Cheque numbers should match across entries

4. **Date tolerance** — Entries can match within ±5 calendar days normally, ±15 days for edge cases.

5. **Special transaction types:**
   - **GIB (Govt Integration Bureau):** Statement GIB/DTAX, GIB/ESIC, GIB/EPFO, GIB/GST debits match
     book PMT vouchers with keywords TDS, ESIC, EPF, GST respectively.
   - **FD Transfers:** "TRF TO FD" or "FD CLOS" on statement → book CNT/PMT with FD number.
   - **BIL/ONL:** Bank bill payments → book PMT entries with matching payee.
   - **NEFT Returns:** Statement shows RETURN credit → cancels original outward NEFT debit.
   - **Settlement aggregation:** Payment gateways (PhonePe, Paytm, Pine Labs) settle as a single
     NEFT that sums multiple POS/UPI book entries.
   - **Salary batches:** One book entry lists multiple names → matches multiple statement entries.

6. **Text matching:**
   - Payee names in NEFT description should match book narration.
   - Enrollment IDs (MLM23003, BWU/BHM/23/008) and MR numbers (BWU2425/31788) link entries.
   - Bengali name spelling variations are common.

7. **RTGS grouping:** Multiple RTGS credits on same date can match one book CNT entry.

## Output Format

Return a JSON object:
```json
{
  "matches": [
    {
      "statement_indices": [0],
      "book_indices": [2],
      "match_type": "exact_ref",
      "confidence": 0.99,
      "reasoning": "Both entries share NEFT ref ABCD1234, same amount 50000, same direction IN"
    }
  ],
  "unmatched_statements": [1, 3],
  "unmatched_books": [0, 4]
}
```

**match_type** should be one of: exact_ref, cheque_match, amount_date, text_match, gib_tax,
fd_transfer, settlement_aggregate, salary_batch, neft_return, bil_payment, rtgs_group,
one_to_many, many_to_one, name_match, id_match, contra_transfer, fallback_amount.

**confidence** should be 0.0-1.0 where:
- 1.0 = exact reference match with matching amount
- 0.9+ = strong text/ID match
- 0.7-0.9 = reasonable amount+date+text match
- 0.5-0.7 = amount+date match only (could be coincidence)
- <0.5 = do not match (leave unmatched)

Only return matches with confidence >= 0.5. If unsure, leave unmatched.
NEVER force a match — accuracy is more important than match rate.

Respond with ONLY the JSON object, no markdown code fences."""


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set. Add it to your .env file.")
        _client = genai.Client(api_key=api_key)
    return _client


def _format_entry(idx: int, row: dict[str, Any], source: str) -> str:
    """Format a transaction entry for the LLM prompt."""

    parts = [f"[{idx}]"]
    date_field = "value_date" if source == "statement" else "voucher_date"
    parts.append(f"Date={row.get(date_field, '?')}")
    parts.append(f"Amt={row.get('amount', 0)}")
    parts.append(f"Dir={row.get('direction', '?')}")

    if source == "statement":
        parts.append(f"Desc=\"{row.get('description', '')}\"")
        if row.get("cheque_no"):
            parts.append(f"Chq={row['cheque_no']}")
    else:
        parts.append(f"Particulars=\"{row.get('particulars', '')}\"")
        narr = row.get("narration", "")
        if narr:
            parts.append(f"Narration=\"{narr}\"")
        if row.get("voucher_type"):
            parts.append(f"Voucher={row['voucher_type']}/{row.get('voucher_no', '')}")
        if row.get("cheque_no"):
            parts.append(f"Chq={row['cheque_no']}")

    refs = row.get("refs", [])
    if refs:
        parts.append(f"Refs=[{','.join(str(r) for r in refs)}]")

    return " | ".join(parts)


def match_batch(
    statement_entries: list[dict[str, Any]],
    book_candidates: list[dict[str, Any]],
    model: str = "gemini-2.5-flash",
) -> dict[str, Any]:
    """Send a batch of statement entries + book candidates to the LLM for matching.

    Returns parsed JSON with matches, unmatched_statements, unmatched_books.
    """
    client = _get_client()

    stmt_text = "\n".join(
        _format_entry(i, row, "statement")
        for i, row in enumerate(statement_entries)
    )
    book_text = "\n".join(
        _format_entry(i, row, "book")
        for i, row in enumerate(book_candidates)
    )

    user_prompt = (
        f"Match these {len(statement_entries)} STATEMENT entries against "
        f"{len(book_candidates)} BANK BOOK entries.\n\n"
        f"## STATEMENT ENTRIES:\n{stmt_text}\n\n"
        f"## BANK BOOK ENTRIES:\n{book_text}\n\n"
        "Return the JSON matching result."
    )

    response = None
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.0,
                    max_output_tokens=4096,
                ),
            )
            break
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait = min(2 ** attempt * 10, 60)
                log.warning("LLM rate limit hit, retrying in %ds…", wait)
                time.sleep(wait)
            else:
                raise
    else:
        return {"matches": [], "unmatched_statements": list(range(len(statement_entries))),
                "unmatched_books": list(range(len(book_candidates)))}

    content = response.text or "{}"
    # Strip markdown fences if present
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines)

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"matches": [], "unmatched_statements": list(range(len(statement_entries))),
                "unmatched_books": list(range(len(book_candidates)))}
