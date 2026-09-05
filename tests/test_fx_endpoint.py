"""Tests for api/app.py's POST /fx/reconcile - the standalone FX
reconciliation endpoint. Uses data/fx_generator.py's five real
scenarios, same approach as test_refund_endpoint.py and
test_batch_endpoint.py."""

import app as api_app
import fx_generator
from fastapi.testclient import TestClient

client = TestClient(api_app.app)


def _body(scenario):
    gw, bank, rmin, rmax, markup = scenario
    return {
        "gateway_record": gw,
        "bank_record": bank,
        "rate_min": rmin,
        "rate_max": rmax,
        "markup_bps": markup,
    }


def test_clean_match_scenario_via_endpoint():
    response = client.post("/fx/reconcile", json=_body(fx_generator.generate()[0]))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "matched_within_rate_band"
    assert body["requires_human_review"] is True


def test_implausible_scenario_via_endpoint():
    response = client.post("/fx/reconcile", json=_body(fx_generator.generate()[1]))
    body = response.json()
    assert body["status"] == "rate_implausible"


def test_same_currency_scenario_via_endpoint():
    response = client.post("/fx/reconcile", json=_body(fx_generator.generate()[2]))
    body = response.json()
    assert body["status"] == "not_a_currency_mismatch"


def test_markup_scenario_via_endpoint():
    response = client.post("/fx/reconcile", json=_body(fx_generator.generate()[4]))
    body = response.json()
    assert body["status"] == "matched_within_rate_band"


def test_invalid_rate_band_via_endpoint():
    gw, bank, _, _, markup = fx_generator.generate()[0]
    response = client.post("/fx/reconcile", json={
        "gateway_record": gw, "bank_record": bank,
        "rate_min": 90.0, "rate_max": 80.0, "markup_bps": markup,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "invalid_rate_band"


def test_non_positive_amount_rejected_with_422():
    response = client.post("/fx/reconcile", json={
        "gateway_record": {"transaction_id": "x", "amount": 0, "currency": "USD"},
        "bank_record": {"settled_amount": 100.0, "currency": "INR"},
        "rate_min": 80.0, "rate_max": 85.0,
    })
    assert response.status_code == 422


def test_fx_endpoint_covered_by_auth(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-1")
    response = client.post("/fx/reconcile", json=_body(fx_generator.generate()[0]))
    assert response.status_code == 401
    response = client.post("/fx/reconcile", json=_body(fx_generator.generate()[0]), headers={"X-API-Key": "secret-1"})
    assert response.status_code == 200
