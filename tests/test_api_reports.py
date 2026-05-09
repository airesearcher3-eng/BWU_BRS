from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_run_persists_matches_and_exceptions(authed_client):
    payload = {
        "bank_statement_path": str(ROOT / "uploads" / "bank_statements" / "BANK STATEMENT FEB_26.xlsx"),
        "bank_book_path": str(ROOT / "uploads" / "bank_books" / "BANK BOOK LEDGER FEB_26.xlsx"),
        "previous_brs_path": str(ROOT / "uploads" / "previous_brs" / "BRS JAN_26.xlsx"),
    }

    run_response = authed_client.post("/api/reconciliation/run", json=payload)
    assert run_response.status_code == 200
    run_data = run_response.json()

    matches_response = authed_client.get(f"/api/reconciliation/run/{run_data['run_id']}/matches")
    assert matches_response.status_code == 200
    matches_data = matches_response.json()
    assert matches_data["match_count"] > 0
    assert matches_data["statement_entry_count"] > 0

    exceptions_response = authed_client.get(f"/api/exceptions?run_id={run_data['run_id']}")
    assert exceptions_response.status_code == 200
    exceptions_data = exceptions_response.json()
    assert len(exceptions_data) == run_data["exception_count"]
    assert any(item["exception_type"] in {"unknown_cr", "unknown_dr", "timing_difference"} for item in exceptions_data)


def test_healthz(authed_client):
    r = authed_client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz(authed_client):
    r = authed_client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
