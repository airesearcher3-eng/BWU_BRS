"""
Async reconciliation wrapper for the FastAPI backend.

The core matching/parsing logic is CPU-bound and runs inside thread-pool executors
to avoid blocking the event loop.  RAG residuals are sent to the ML service via
httpx after the deterministic 5-pass run.
"""
from __future__ import annotations

import asyncio
import functools
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

import config
from engine.brs_output import generate_brs_excel
from engine.classifier import build_brs_sections, build_exception_items, calculate_brs_totals
from engine.matching.orchestrator import run_matching_engine
from engine.normaliser import date_to_iso, decimal_to_float
from engine.parsers.bank_book import parse_bank_book
from engine.parsers.bank_statement import parse_bank_statement
from engine.parsers.brs_previous import parse_previous_brs
from engine.parsers.hdfc_portal import enrich_portal_matches, parse_hdfc_portal


def _serialise_row_for_ml(row: dict) -> dict:
    """Convert Decimal/date values in a transaction row to JSON-safe primitives."""
    out: dict = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, (list, tuple)):
            out[k] = [str(i) for i in v]
        else:
            out[k] = v
    return out


def _apply_rag_results(rag_payload: dict, match_result: dict) -> None:
    """
    Merge RAG match groups back into match_result.

    The ML service returns:
        {
          "matches": [
              {"statement_rows": [..], "book_rows": [..],
               "match_type": "rag_llm", "confidence": 0.87, "notes": "...",
               "amount": ...},
              ...
          ],
          "unmatched_statements": [...],  # row numbers still unmatched
          "unmatched_books": [...],
        }
    """
    rag_stmt_matched = set()
    rag_book_matched = set()

    for grp in rag_payload.get("matches", []):
        grp["pass_number"] = 6
        grp["source"] = "rag"
        match_result["matches"].append(grp)
        rag_stmt_matched.update(grp.get("statement_rows", []))
        rag_book_matched.update(grp.get("book_rows", []))

    if "pass_counts" not in match_result:
        match_result["pass_counts"] = {}
    match_result["pass_counts"][6] = len(rag_payload.get("matches", []))

    # Remove newly-matched rows from the unmatched lists
    match_result["unmatched_statement"] = [
        t for t in match_result["unmatched_statement"]
        if t["row_number"] not in rag_stmt_matched
    ]
    match_result["unmatched_book"] = [
        t for t in match_result["unmatched_book"]
        if t["row_number"] not in rag_book_matched
    ]

    # Mark rows as matched in their original transaction dicts
    for t in match_result.get("statement", {}).get("transactions", []):
        if t["row_number"] in rag_stmt_matched:
            t["matched"] = True
    for t in match_result.get("bank_book", {}).get("transactions", []):
        if t["row_number"] in rag_book_matched:
            t["matched"] = True


async def reconcile_workbooks(
    *,
    statement_path: str | Path,
    bank_book_path: str | Path,
    previous_brs_path: str | Path | None = None,
    previous_brs_sheet: str | None = None,
    portal_data_path: str | Path | None = None,
    output_path: str | Path | None = None,
    bank_account: dict | None = None,
    use_rag: bool = True,
) -> dict[str, Any]:
    """Async wrapper — runs CPU-bound parsing + matching in thread pool."""

    loop = asyncio.get_running_loop()

    # ── Parallel parsing (CPU-bound in executor) ────────────────────
    statement_result, bank_book_result = await asyncio.gather(
        loop.run_in_executor(None, parse_bank_statement, statement_path),
        loop.run_in_executor(None, parse_bank_book, bank_book_path),
    )

    if previous_brs_path:
        previous_brs_result = await loop.run_in_executor(
            None, parse_previous_brs, previous_brs_path, previous_brs_sheet
        )
    else:
        previous_brs_result = {"items": [], "pending_items": [], "resolved_items": []}

    # ── Opening balance cross-validation ────────────────────────────
    # The bank-book opening balance for the current period must equal the
    # reconciled balance from the previous BRS.  A mismatch indicates either
    # a data-entry error in the ERP or that the previous BRS was not finalised.
    balance_warnings: list[str] = []
    if previous_brs_path:
        prev_reconciled = previous_brs_result.get("reconciled_balance")
        book_opening = bank_book_result.get("opening_balance")
        if prev_reconciled is not None and book_opening is not None:
            from decimal import Decimal
            diff = book_opening - prev_reconciled
            if abs(diff) > Decimal("0.01"):
                balance_warnings.append(
                    f"Opening balance mismatch: Bank Book opening balance is "
                    f"{book_opening:,.2f} but previous BRS reconciled balance was "
                    f"{prev_reconciled:,.2f} (difference: {diff:+,.2f}). "
                    f"Verify that the previous month's BRS was finalised correctly."
                )

    # ── Deterministic 5-pass matching ───────────────────────────────
    match_result: dict = await loop.run_in_executor(
        None,
        run_matching_engine,
        statement_result["transactions"],
        bank_book_result["transactions"],
        previous_brs_result["items"],
    )

    # ── Optional Hybrid RAG for residuals ───────────────────────────
    if match_result["unmatched_statement"] or match_result["unmatched_book"]:
        stmt_rows = [_serialise_row_for_ml(r) for r in match_result["unmatched_statement"]]
        book_rows = [_serialise_row_for_ml(r) for r in match_result["unmatched_book"]]
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{config.ML_SERVICE_URL}/rag/match",
                    json={"statement_rows": stmt_rows, "book_rows": book_rows},
                )
            if resp.status_code == 200:
                _apply_rag_results(resp.json(), match_result)
        except (httpx.ConnectError, httpx.TimeoutException):
            # ML service unavailable — continue with deterministic results only
            pass

    # ── HDFC portal data enrichment ──────────────────────────────────
    # Parse the portal report (if supplied) and annotate portal settlement
    # match groups with individual student payment details.
    portal_result: dict = {"payments": [], "by_settlement_date": {}, "count": 0}
    if portal_data_path:
        portal_result = await loop.run_in_executor(
            None, parse_hdfc_portal, portal_data_path
        )
        if portal_result.get("count", 0) > 0:
            enrich_portal_matches(match_result, portal_result)

    # ── BRS sections & totals ────────────────────────────────────────
    sections = build_brs_sections(
        match_result["unmatched_statement"],
        match_result["unmatched_book"],
        match_result["pending_carry_forward_items"],
        period_start=statement_result.get("period_start"),
    )
    totals = calculate_brs_totals(
        bank_book_result["closing_balance"],
        statement_result["closing_balance"],
        sections,
    )
    exceptions = build_exception_items(
        match_result["unmatched_statement"],
        match_result["unmatched_book"],
        match_result["pending_carry_forward_items"],
    )

    # ── Excel output (CPU-bound in executor) ─────────────────────────
    output_file = None
    if output_path:
        output_file = await loop.run_in_executor(
            None,
            functools.partial(
                generate_brs_excel,
                output_path,
                as_on_date=statement_result["period_end"],
                bank_book_balance=bank_book_result["closing_balance"],
                bank_statement_balance=statement_result["closing_balance"],
                sections=sections,
                totals=totals,
                bank_account=bank_account,
            ),
        )

    return {
        "statement": statement_result,
        "bank_book": bank_book_result,
        "previous_brs": previous_brs_result,
        "portal_data": portal_result,
        "matching": match_result,
        "sections": sections,
        "totals": totals,
        "exceptions": exceptions,
        "output_file": output_file,
        "balance_warnings": balance_warnings,
    }


def serialise_result(result: dict[str, Any]) -> dict[str, Any]:
    """Convert Decimals and dates in the reconciliation result into JSON-friendly values."""
    totals = {
        k: decimal_to_float(v) if isinstance(v, Decimal) else v
        for k, v in result["totals"].items()
    }
    section_summary = {
        section: {
            "count": len(items),
            "total": decimal_to_float(sum((i["amount"] for i in items), Decimal("0.00"))),
        }
        for section, items in result["sections"].items()
    }
    return {
        "statement_count": result["statement"]["count"],
        "bank_book_count": result["bank_book"]["count"],
        "pending_carry_forward_count": len(result["matching"]["pending_carry_forward_items"]),
        "pass_counts": result["matching"]["pass_counts"],
        "section_summary": section_summary,
        "totals": totals,
        "output_file": str(result["output_file"]) if result["output_file"] else None,
        "balance_warnings": result.get("balance_warnings", []),
    }
