"""High-level reconciliation workflow used by tests and the API layer."""

from __future__ import annotations

from pathlib import Path
from decimal import Decimal
from typing import Any

from engine.brs_output import generate_brs_excel
from engine.classifier import build_brs_sections, build_exception_items, calculate_brs_totals
from engine.matching.orchestrator import run_matching_engine
from engine.normaliser import decimal_to_float
from engine.parsers.bank_book import parse_bank_book
from engine.parsers.bank_statement import parse_bank_statement
from engine.parsers.brs_previous import parse_previous_brs


def reconcile_workbooks(
    *,
    statement_path: str | Path,
    bank_book_path: str | Path,
    previous_brs_path: str | Path | None = None,
    previous_brs_sheet: str | None = None,
    output_path: str | Path | None = None,
    bank_account: dict | None = None,
    use_rag: bool = False,
) -> dict[str, Any]:
    """Run the reconciliation pipeline (deterministic or RAG-based)."""

    statement_result = parse_bank_statement(statement_path)
    bank_book_result = parse_bank_book(bank_book_path)
    previous_brs_result = (
        parse_previous_brs(previous_brs_path, previous_brs_sheet)
        if previous_brs_path
        else {"items": [], "pending_items": [], "resolved_items": []}
    )

    if use_rag:
        from engine.rag import run_rag_matching
        match_result = run_rag_matching(
            statement_result["transactions"],
            bank_book_result["transactions"],
            previous_brs_result["items"],
        )
    else:
        match_result = run_matching_engine(
            statement_result["transactions"],
            bank_book_result["transactions"],
            previous_brs_result["items"],
        )
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

    output_file = None
    if output_path:
        output_file = generate_brs_excel(
            output_path,
            as_on_date=statement_result["period_end"],
            bank_book_balance=bank_book_result["closing_balance"],
            bank_statement_balance=statement_result["closing_balance"],
            sections=sections,
            totals=totals,
            bank_account=bank_account,
        )

    return {
        "statement": statement_result,
        "bank_book": bank_book_result,
        "previous_brs": previous_brs_result,
        "matching": match_result,
        "sections": sections,
        "totals": totals,
        "exceptions": exceptions,
        "output_file": output_file,
    }


def serialise_result(result: dict[str, Any]) -> dict[str, Any]:
    """Convert Decimals and dates in the reconciliation result into JSON-friendly values."""

    totals = {
        key: decimal_to_float(value) if hasattr(value, "quantize") else value
        for key, value in result["totals"].items()
    }
    section_summary = {
        section: {
            "count": len(items),
            "total": decimal_to_float(
                sum((item["amount"] for item in items), Decimal("0.00"))
            ),
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
        "output_file": result["output_file"],
    }
