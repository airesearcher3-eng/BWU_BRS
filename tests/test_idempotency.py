from engine.parsers.bank_book import parse_bank_book
from engine.parsers.bank_statement import parse_bank_statement


def test_re_upload_same_file_no_duplicates(workbook_path):
    first_statement = parse_bank_statement(workbook_path)["transactions"]
    second_statement = parse_bank_statement(workbook_path)["transactions"]
    first_book = parse_bank_book(workbook_path)["transactions"]
    second_book = parse_bank_book(workbook_path)["transactions"]

    first_statement_hashes = {row["row_hash"] for row in first_statement}
    second_statement_hashes = {row["row_hash"] for row in second_statement}
    first_book_hashes = {row["row_hash"] for row in first_book}
    second_book_hashes = {row["row_hash"] for row in second_book}

    assert len(first_statement_hashes) == len(first_statement)
    assert len(first_book_hashes) == len(first_book)
    assert first_statement_hashes == second_statement_hashes
    assert first_book_hashes == second_book_hashes
