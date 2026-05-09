from copy import deepcopy
from datetime import date
from decimal import Decimal

from engine.matching.pass2_aggregate import run_pass2


def test_neft_split_one_to_many(statement_result, book_result):
    statement_rows = deepcopy(
        [
            row
            for row in statement_result["transactions"]
            if "HDFCH00787812754" in row.get("refs", [])
        ]
    )
    book_rows = deepcopy(
        [
            row
            for row in book_result["transactions"]
            if "HDFCH00787812754" in row.get("refs", [])
        ]
    )

    matches = run_pass2(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["match_type"] == "one_to_many_ref"
    assert matches[0]["statement_rows"] == [33]
    assert matches[0]["book_rows"] == [25, 26]


def test_don_bosco_neft_pair_matches_single_book_entry(statement_result, book_result):
    statement_rows = deepcopy(
        [
            row
            for row in statement_result["transactions"]
            if row["row_number"] in {10, 11}
        ]
    )
    book_rows = deepcopy(
        [
            row
            for row in book_result["transactions"]
            if row["row_number"] == 9
        ]
    )

    matches = run_pass2(statement_rows, book_rows)

    assert len(matches) == 1
    assert matches[0]["match_type"] == "narration_ref_group"
    assert matches[0]["statement_rows"] == [10, 11]
    assert matches[0]["book_rows"] == [9]


def test_partial_ref_match_leaves_remainder():
    """When book rows sharing a ref cover only PART of the statement amount,
    the covered portion should match and the remainder should stay unmatched."""
    stmt = [
        {
            "row_number": 1,
            "matched": False,
            "direction": "IN",
            "amount": Decimal("62342.00"),
            "refs": ["HDFCH00746380881"],
            "description": "NEFT-HDFCH00746380881",
            "value_date": date(2026, 2, 10),
        }
    ]
    book = [
        {
            "row_number": 10,
            "matched": False,
            "direction": "IN",
            "amount": Decimal("52900.00"),
            "refs": ["HDFCH00746380881"],
            "narration": "NEFT HDFCH00746380881",
            "particulars": "",
            "voucher_date": date(2026, 2, 10),
            "voucher_type": "REC",
            "cheque_no": None,
        },
        {
            "row_number": 11,
            "matched": False,
            "direction": "IN",
            "amount": Decimal("1500.00"),
            "refs": ["HDFCH00746380881"],
            "narration": "NEFT HDFCH00746380881",
            "particulars": "",
            "voucher_date": date(2026, 2, 10),
            "voucher_type": "REC",
            "cheque_no": None,
        },
    ]

    matches = run_pass2(stmt, book)

    # Should produce a partial match
    assert len(matches) >= 1
    partial = [m for m in matches if m["match_type"] == "one_to_many_ref_partial"]
    assert len(partial) == 1

    # Both book rows consumed
    assert book[0]["matched"] is True
    assert book[1]["matched"] is True

    # Statement row stays unmatched with reduced amount (remainder)
    assert stmt[0]["matched"] is False
    assert stmt[0]["amount"] == Decimal("7942.00")
    assert stmt[0]["original_amount"] == Decimal("62342.00")
    assert stmt[0]["partial_match"] is True
