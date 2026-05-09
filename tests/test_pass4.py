from copy import deepcopy

from engine.matching.pass4_fd import run_pass4


def test_fd_maturity_match(statement_result, book_result):
    statement_rows = deepcopy(
        [
            row
            for row in statement_result["transactions"]
            if row["row_number"] == 142
        ]
    )
    book_rows = deepcopy(
        [
            row
            for row in book_result["transactions"]
            if row["row_number"] == 134
        ]
    )

    matches = run_pass4(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["match_type"] == "fd_maturity"
    assert matches[0]["statement_rows"] == [142]
    assert matches[0]["book_rows"] == [134]
