from engine.parsers.bank_book import parse_bank_book
from engine.parsers.bank_statement import parse_bank_statement
from engine.parsers.brs_previous import parse_previous_brs


def test_statement_parser_reads_137_rows(statement_result):
    assert statement_result["count"] == 137


def test_book_parser_reads_live_transaction_rows(book_result):
    # The live workbook currently contains 132 voucher rows plus the opening balance row.
    assert book_result["count"] == 132


def test_prev_brs_parser_extracts_all_pending_carry_forwards(previous_brs_result):
    assert len(previous_brs_result["pending_items"]) == 31


def test_prev_brs_parser_excludes_resolved_items(previous_brs_result):
    assert len(previous_brs_result["resolved_items"]) == 9
    assert all(item["cleared_on"] is not None for item in previous_brs_result["resolved_items"])


def test_prev_brs_parser_preserves_may_2025_items(previous_brs_result):
    pending_dates = {item["original_date"].isoformat() for item in previous_brs_result["pending_items"]}
    assert "2025-05-19" in pending_dates
    assert "2025-05-30" in pending_dates
