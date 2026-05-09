from datetime import date

from engine.classifier import build_brs_sections


def test_carry_forward_pending_items_preserved(previous_brs_result):
    pending = previous_brs_result["pending_items"]
    sections = build_brs_sections([], [], pending, today=date(2026, 2, 28))

    assert len(sections["add_cheque_issued"]) == 2
    assert len(sections["add_bank_credit"]) == 29


def test_carry_forward_resolved_items_excluded(previous_brs_result):
    pending_rows = {item["row_number"] for item in previous_brs_result["pending_items"]}
    resolved_rows = {item["row_number"] for item in previous_brs_result["resolved_items"]}

    assert pending_rows.isdisjoint(resolved_rows)


def test_carry_forward_items_retain_original_date(previous_brs_result):
    first_pending = previous_brs_result["pending_items"][0]
    sections = build_brs_sections([], [], [first_pending], today=date(2026, 2, 28))

    assert sections["add_cheque_issued"][0]["date"] == first_pending["original_date"]


def test_stale_items_flagged_not_removed(previous_brs_result):
    sections = build_brs_sections([], [], previous_brs_result["pending_items"], today=date(2026, 2, 28))
    stale_items = [item for item in sections["add_bank_credit"] if item.get("stale")]

    assert stale_items
    assert any(item["amount"] == 1 for item in stale_items)
