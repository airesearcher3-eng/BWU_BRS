from copy import deepcopy

from engine.matching.pass3_rules import run_pass3


def test_gib_dtax_matches_grouped_tds_pmt(statement_result, book_result):
    statement_rows = deepcopy(
        [
            row
            for row in statement_result["transactions"]
            if row["row_number"] in {18, 19, 20, 21, 22, 23, 24}
        ]
    )
    book_rows = deepcopy(
        [
            row
            for row in book_result["transactions"]
            if row["row_number"] == 12
        ]
    )

    matches = run_pass3(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["match_type"] == "rule_gib"
    assert matches[0]["book_rows"] == [12]


def test_gib_esic_matches_esic_pmt(statement_result, book_result):
    statement_rows = deepcopy(
        [
            row
            for row in statement_result["transactions"]
            if row["row_number"] == 60
        ]
    )
    book_rows = deepcopy(
        [
            row
            for row in book_result["transactions"]
            if row["row_number"] == 40
        ]
    )

    matches = run_pass3(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["match_type"] == "rule_gib"
    assert matches[0]["book_rows"] == [40]


def test_bil_onl_bharti_air_match(statement_result, book_result):
    statement_rows = deepcopy(
        [
            row
            for row in statement_result["transactions"]
            if row["row_number"] == 15
        ]
    )
    book_rows = deepcopy(
        [
            row
            for row in book_result["transactions"]
            if row["row_number"] == 8
        ]
    )

    matches = run_pass3(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["match_type"] == "rule_bil"


def test_two_pharmacy_c_entries_no_cross_contamination(statement_result, book_result):
    statement_rows = deepcopy(
        [
            row
            for row in statement_result["transactions"]
            if row["row_number"] in {28, 73, 128}
        ]
    )
    book_rows = deepcopy(
        [
            row
            for row in book_result["transactions"]
            if row["row_number"] == 18
        ]
    )

    matches = run_pass3(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["statement_rows"] == [28]
    assert matches[0]["book_rows"] == [18]
