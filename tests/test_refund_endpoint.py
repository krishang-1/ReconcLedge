"""Tests for api/app.py's POST /refunds/reconcile - the standalone
refund reconciliation endpoint, deliberately separate from the /runs
pipeline (see refund_matcher.py's module docstring). Uses real
transaction_ids from the curated gateway dataset via
data/refund_generator.py rather than fabricated ones, so these tests
exercise the real jobs._load_data() lookup path, not a mock.
"""

import app as api_app
import refund_generator
from fastapi.testclient import TestClient

client = TestClient(api_app.app)


def test_reconcile_endpoint_returns_expected_shape():
    events = refund_generator.generate()
    response = client.post("/refunds/reconcile", json={"refund_events": events})
    assert response.status_code == 200
    body = response.json()
    assert "reconciliation" in body
    # generator produces events against 5 distinct transaction_ids (one full,
    # one partial, one over-refunded via duplicate, one split-into-full, one unknown)
    assert len(body["reconciliation"]) == 5


def test_reconcile_endpoint_classifies_real_generator_scenarios_correctly():
    events = refund_generator.generate()
    response = client.post("/refunds/reconcile", json={"refund_events": events})
    results = {r["transaction_id"]: r for r in response.json()["reconciliation"]}

    classifications = [r["classification"] for r in results.values() if r["known_transaction"]]
    assert classifications.count("full_refund") == 2   # the clean one + the split-into-full one
    assert classifications.count("partial_refund") == 1
    assert classifications.count("over_refunded") == 1

    unknown = [r for r in results.values() if not r["known_transaction"]]
    assert len(unknown) == 1
    assert unknown[0]["classification"] is None


def test_reconcile_endpoint_rejects_non_positive_refund_amount():
    response = client.post("/refunds/reconcile", json={
        "refund_events": [{"transaction_id": "whatever", "refund_amount": 0}]
    })
    assert response.status_code == 422  # pydantic validation, gt=0


def test_reconcile_endpoint_handles_empty_events_list():
    response = client.post("/refunds/reconcile", json={"refund_events": []})
    assert response.status_code == 200
    assert response.json()["reconciliation"] == []


def test_reconcile_endpoint_is_covered_by_auth(monkeypatch):
    """Spot-check that the app-level auth dependency (see auth.py,
    test_auth.py) actually covers this newly-added route too, not just
    the routes it was originally developed against."""
    monkeypatch.setenv("API_KEYS", "secret-1")
    response = client.post("/refunds/reconcile", json={"refund_events": []})
    assert response.status_code == 401
    response = client.post("/refunds/reconcile", json={"refund_events": []}, headers={"X-API-Key": "secret-1"})
    assert response.status_code == 200
