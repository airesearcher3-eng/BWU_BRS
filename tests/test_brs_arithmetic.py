from decimal import Decimal


def test_february_2026_brs_balances_to_zero(reconciliation_result):
    totals = reconciliation_result["totals"]

    assert totals["bank_book_balance"] == Decimal("58068983.78")
    assert totals["add_cheque_issued"] == Decimal("10035473.00")
    assert totals["add_bank_credit"] == Decimal("279675.99")
    assert totals["less_cheque_deposit"] == Decimal("23750.00")
    assert totals["less_bank_debit"] == Decimal("129823.60")
    assert totals["reconciled_balance"] == Decimal("68230559.17")
    assert totals["bank_statement_balance"] == Decimal("68230559.17")
    assert totals["difference"] == Decimal("0.00")
