from copy import deepcopy

from engine.matching.pass1_exact import run_pass1


def _statement_by_ref(statement_result, ref):
    return deepcopy(
        [
            row
            for row in statement_result["transactions"]
            if ref in row.get("refs", [])
        ]
    )


def _book_by_ref(book_result, ref):
    return deepcopy(
        [
            row
            for row in book_result["transactions"]
            if ref in row.get("refs", [])
        ]
    )


def test_neft_ref_exact_match(statement_result, book_result):
    statement_rows = _statement_by_ref(statement_result, "HDFCH00783970725")
    book_rows = _book_by_ref(book_result, "HDFCH00783970725")

    matches = run_pass1(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["match_type"] == "exact_ref"


def test_batched_upi_individual_match(statement_result, book_result):
    statement_rows = deepcopy(
        [
            row
            for row in statement_result["transactions"]
            if row["row_number"] in {8, 9}
        ]
    )
    book_rows = deepcopy(
        [
            row
            for row in book_result["transactions"]
            if row["row_number"] == 11
        ]
    )

    matches = run_pass1(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["match_type"] == "exact_ref_multi_to_one"
    assert matches[0]["statement_rows"] == [8, 9]
    assert matches[0]["book_rows"] == [11]


def test_upi_ref_exact_match_with_late_numeric_token(statement_result, book_result):
    statement_rows = _statement_by_ref(statement_result, "640597872812")
    book_rows = _book_by_ref(book_result, "640597872812")

    matches = run_pass1(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["match_type"] == "exact_ref"
