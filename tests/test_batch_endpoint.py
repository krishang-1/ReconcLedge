"""Tests for api/app.py's POST /batches/reconcile - the standalone N-way
batch settlement endpoint. Uses data/batch_generator.py's five real
scenarios rather than fabricated ones, same approach as
test_refund_endpoint.py."""

import app as api_app
import batch_generator
from fastapi.testclient import TestClient

client = TestClient(api_app.app)


def _request_body():
    gw, bank = batch_generator.generate()
    return {"gateway_records": gw, "bank_batch_records": bank}


def test_reconcile_endpoint_returns_expected_shape():
    response = client.post("/batches/reconcile", json=_request_body())
    assert response.status_code == 200
    body = response.json()
    assert "batch_id_reconciliation" in body
    assert "bounded_fallback_reconciliation" in body
    # 3 batch_ids (CLEAN, GAP, PENDING) + 2 unbatched credit lines
    assert len(body["batch_id_reconciliation"]) == 3
    assert len(body["bounded_fallback_reconciliation"]) == 2


def test_reconcile_endpoint_classifies_all_five_scenarios_correctly():
    response = client.post("/batches/reconcile", json=_request_body())
    body = response.json()

    by_batch_id = {r["batch_id"]: r for r in body["batch_id_reconciliation"]}
    assert by_batch_id["BATCH_CLEAN_01"]["matched"] is True
    assert by_batch_id["BATCH_GAP_01"]["matched"] is False
    assert by_batch_id["BATCH_PENDING_01"]["credited_amount"] is None

    statuses = [r["status"] for r in body["bounded_fallback_reconciliation"]]
    assert "candidate_match" in statuses
    assert "ambiguous" in statuses


def test_reconcile_endpoint_handles_empty_input():
    response = client.post("/batches/reconcile", json={"gateway_records": [], "bank_batch_records": []})
    assert response.status_code == 200
    body = response.json()
    assert body["batch_id_reconciliation"] == []
    assert body["bounded_fallback_reconciliation"] == []


def test_reconcile_endpoint_is_covered_by_auth(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-1")
    response = client.post("/batches/reconcile", json={"gateway_records": [], "bank_batch_records": []})
    assert response.status_code == 401
    response = client.post(
        "/batches/reconcile", json={"gateway_records": [], "bank_batch_records": []},
        headers={"X-API-Key": "secret-1"},
    )
    assert response.status_code == 200


def test_reconcile_endpoint_oversized_unbatched_pool_refuses_not_crashes():
    gateway_records = [{"transaction_id": f"t{i}", "net_amount": float(i)} for i in range(20)]
    bank_batch_records = [{"batch_id": None, "credited_amount": 99999.0}]
    response = client.post("/batches/reconcile", json={
        "gateway_records": gateway_records, "bank_batch_records": bank_batch_records,
    })
    assert response.status_code == 200
    result = response.json()["bounded_fallback_reconciliation"][0]
    assert result["status"] == "pool_too_large"


def test_reconcile_endpoint_duplicate_batch_id_returns_clean_422_not_raw_500():
    """Real end-to-end regression guard: reconcile_by_batch_id() raises
    ValueError on a duplicate batch_id (see test_batch_settlement.py),
    but that alone doesn't prove the real API surfaces it cleanly - found
    via a full end-to-end check that the first version of this endpoint
    let the ValueError bubble up as a raw, unhandled 500 traceback
    instead of a clean 422. Same class of gap as the earlier missing-
    GROQ_API_KEY-raw-traceback fix."""
    gateway_records = [{"transaction_id": "dt1", "net_amount": 100.0, "settlement_batch_id": "DUP1"}]
    bank_batch_records = [
        {"batch_id": "DUP1", "credited_amount": 100.0},
        {"batch_id": "DUP1", "credited_amount": 999.0},
    ]
    response = client.post("/batches/reconcile", json={
        "gateway_records": gateway_records, "bank_batch_records": bank_batch_records,
    })
    assert response.status_code == 422
    assert "DUP1" in response.json()["detail"]
