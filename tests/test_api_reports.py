from pathlib import Path

from fastapi.testclient import TestClient

import app


ROOT = Path(__file__).resolve().parents[1]


def test_run_persists_matches_and_exceptions():
    client = TestClient(app.app)
    payload = {
        "bank_statement_path": str(ROOT / "uploads" / "bank_statements" / "BANK STATEMENT FEB_26.xlsx"),
        "bank_book_path": str(ROOT / "uploads" / "bank_books" / "BANK BOOK LEDGER FEB_26.xlsx"),
        "previous_brs_path": str(ROOT / "uploads" / "previous_brs" / "BRS JAN_26.xlsx"),
    }

    run_response = client.post("/api/reconciliation/run", json=payload)
    assert run_response.status_code == 200
    run_data = run_response.json()

    matches_response = client.get(f"/api/reconciliation/run/{run_data['run_id']}/matches")
    assert matches_response.status_code == 200
    matches_data = matches_response.json()
    assert matches_data["match_count"] > 0
    assert matches_data["statement_entry_count"] > 0

    exceptions_response = client.get(f"/api/exceptions?run_id={run_data['run_id']}")
    assert exceptions_response.status_code == 200
    exceptions_data = exceptions_response.json()
    assert len(exceptions_data) == run_data["exception_count"]
    assert any(item["exception_type"] in {"unknown_cr", "unknown_dr", "timing_difference"} for item in exceptions_data)
