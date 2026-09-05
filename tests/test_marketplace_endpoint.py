"""Tests for api/app.py's POST /marketplace/reconcile - the standalone
Route-style multi-party settlement endpoint. Uses
data/marketplace_generator.py's four real scenarios."""

import app as api_app
import marketplace_generator
from fastapi.testclient import TestClient

client = TestClient(api_app.app)


def _body(scenario):
    gw, transfers, commission = scenario
    return {"gateway_record": gw, "transfers": transfers, "platform_commission": commission}


def test_clean_split_via_endpoint():
    response = client.post("/marketplace/reconcile", json=_body(marketplace_generator.generate()[0]))
    assert response.status_code == 200
    assert response.json()["status"] == "fully_reconciled"


def test_on_hold_via_endpoint():
    response = client.post("/marketplace/reconcile", json=_body(marketplace_generator.generate()[1]))
    assert response.json()["status"] == "pending_hold"


def test_reversal_via_endpoint():
    response = client.post("/marketplace/reconcile", json=_body(marketplace_generator.generate()[2]))
    assert response.json()["status"] == "reversal_accounted"


def test_mismatch_via_endpoint():
    response = client.post("/marketplace/reconcile", json=_body(marketplace_generator.generate()[3]))
    body = response.json()
    assert body["status"] == "mismatch"
    assert body["gap"] == 50.0


def test_unrecognized_status_returns_clean_422_not_raw_500():
    response = client.post("/marketplace/reconcile", json={
        "gateway_record": {"transaction_id": "x", "net_amount": 100.0},
        "transfers": [{"linked_account_id": "v1", "amount": 100.0, "status": "typo_status"}],
        "platform_commission": 0.0,
    })
    assert response.status_code == 422
    assert "typo_status" in response.json()["detail"]


def test_negative_commission_rejected_with_422():
    response = client.post("/marketplace/reconcile", json={
        "gateway_record": {"transaction_id": "x", "net_amount": 100.0},
        "transfers": [],
        "platform_commission": -5.0,
    })
    assert response.status_code == 422


def test_marketplace_endpoint_covered_by_auth(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-1")
    response = client.post("/marketplace/reconcile", json=_body(marketplace_generator.generate()[0]))
    assert response.status_code == 401
    response = client.post(
        "/marketplace/reconcile", json=_body(marketplace_generator.generate()[0]),
        headers={"X-API-Key": "secret-1"},
    )
    assert response.status_code == 200
