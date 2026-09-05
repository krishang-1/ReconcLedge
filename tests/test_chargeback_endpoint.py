"""Tests for api/app.py's POST /chargebacks/reconcile - the standalone
chargeback/dispute reconciliation endpoint."""

import app as api_app
import chargeback_generator
from fastapi.testclient import TestClient

client = TestClient(api_app.app)


def _body(scenario):
    gw, cb = scenario
    return {"gateway_record": gw, "chargeback_event": cb}


def test_in_flight_via_endpoint():
    response = client.post("/chargebacks/reconcile", json=_body(chargeback_generator.generate()[0]))
    assert response.status_code == 200
    assert response.json()["classification"] == "in_flight"


def test_won_via_endpoint():
    response = client.post("/chargebacks/reconcile", json=_body(chargeback_generator.generate()[1]))
    assert response.json()["classification"] == "reversed"


def test_lost_via_endpoint():
    response = client.post("/chargebacks/reconcile", json=_body(chargeback_generator.generate()[2]))
    assert response.json()["classification"] == "finalized_debit"


def test_arbitration_via_endpoint():
    response = client.post("/chargebacks/reconcile", json=_body(chargeback_generator.generate()[3]))
    assert response.json()["classification"] == "in_flight"


def test_invalid_dispute_via_endpoint():
    response = client.post("/chargebacks/reconcile", json=_body(chargeback_generator.generate()[4]))
    assert response.json()["classification"] == "invalid_dispute"


def test_unrecognized_status_returns_clean_422_not_raw_500():
    response = client.post("/chargebacks/reconcile", json={
        "gateway_record": {"transaction_id": "x", "net_amount": 100.0},
        "chargeback_event": {"status": "typo_status", "disputed_amount": 50.0, "chargeback_fee": 10.0, "initiated_by": "customer"},
    })
    assert response.status_code == 422
    assert "typo_status" in response.json()["detail"]


def test_negative_amount_rejected_with_422():
    response = client.post("/chargebacks/reconcile", json={
        "gateway_record": {"transaction_id": "x", "net_amount": -100.0},
        "chargeback_event": {"status": "open", "disputed_amount": 50.0, "chargeback_fee": 10.0, "initiated_by": "customer"},
    })
    assert response.status_code == 422


def test_chargeback_endpoint_covered_by_auth(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-1")
    response = client.post("/chargebacks/reconcile", json=_body(chargeback_generator.generate()[0]))
    assert response.status_code == 401
    response = client.post(
        "/chargebacks/reconcile", json=_body(chargeback_generator.generate()[0]),
        headers={"X-API-Key": "secret-1"},
    )
    assert response.status_code == 200
