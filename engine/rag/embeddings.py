"""Transaction text representation and Google Gemini embedding generation."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from google import genai

log = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY not set. Add it to your .env file."
            )
        _client = genai.Client(api_key=api_key)
    return _client


def transaction_to_text(row: dict[str, Any], source: str) -> str:
    """Convert a transaction dict into a descriptive text string for embedding."""

    parts: list[str] = [f"Source: {source}"]

    date_field = "value_date" if source == "statement" else "voucher_date"
    date_val = row.get(date_field)
    if date_val:
        parts.append(f"Date: {date_val}")

    parts.append(f"Amount: {row.get('amount', 0)}")
    parts.append(f"Direction: {row.get('direction', '')}")

    if source == "statement":
        desc = row.get("description", "")
        if desc:
            parts.append(f"Description: {desc}")
        cheque = row.get("cheque_no", "")
        if cheque:
            parts.append(f"Cheque: {cheque}")
    else:
        particulars = row.get("particulars", "")
        if particulars:
            parts.append(f"Particulars: {particulars}")
        narration = row.get("narration", "")
        if narration:
            parts.append(f"Narration: {narration}")
        vtype = row.get("voucher_type", "")
        vno = row.get("voucher_no", "")
        if vtype:
            parts.append(f"Voucher: {vtype} {vno}")
        cheque = row.get("cheque_no", "")
        if cheque:
            parts.append(f"Cheque: {cheque}")

    refs = row.get("refs", [])
    if refs:
        parts.append(f"References: {', '.join(str(r) for r in refs)}")

    return " | ".join(parts)


def generate_embeddings(texts: list[str], model: str = "gemini-embedding-001") -> list[list[float]]:
    """Generate embeddings for a batch of texts via Google Gemini API.

    Handles batching to stay within API limits (max 100 per request)
    and retries with exponential backoff on 429 RESOURCE_EXHAUSTED.
    """
    client = _get_client()
    all_embeddings: list[list[float]] = []
    batch_size = 100  # Gemini batch limit
    max_retries = 5

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        for attempt in range(max_retries):
            try:
                result = client.models.embed_content(
                    model=model,
                    contents=batch,
                )
                for emb in result.embeddings:
                    all_embeddings.append(emb.values)
                break
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    wait = min(2 ** attempt * 10, 60)
                    log.warning("Embedding rate limit hit, retrying in %ds…", wait)
                    time.sleep(wait)
                else:
                    raise
        else:
            raise RuntimeError(f"Embedding API failed after {max_retries} retries (rate limit).")

    return all_embeddings
