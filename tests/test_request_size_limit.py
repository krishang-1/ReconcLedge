"""Tests for api/app.py's limit_request_body_size middleware - a real
gap found in a production-readiness audit: none of the reconciliation
endpoints had any protection against an oversized request body before
this. See docs/DECISIONS.md."""

import app as api_app
from fastapi.testclient import TestClient

client = TestClient(api_app.app)


def test_small_request_passes_through_normally():
    response = client.post("/refunds/reconcile", json={"refund_events": []})
    assert response.status_code == 200


def test_oversized_request_body_rejected_with_413():
    huge_events = [{"transaction_id": f"t{i}", "refund_amount": 1.0} for i in range(50_000)]
    response = client.post("/refunds/reconcile", json={"refund_events": huge_events})
    assert response.status_code == 413
    assert "exceeds" in response.json()["detail"]


def test_request_just_under_the_limit_is_not_rejected_by_this_middleware(monkeypatch):
    """Confirms the check is a genuine size threshold, not something
    that rejects every POST regardless of size."""
    monkeypatch.setattr(api_app, "MAX_REQUEST_BODY_BYTES", 10_000_000)
    huge_events = [{"transaction_id": f"t{i}", "refund_amount": 1.0} for i in range(50_000)]
    response = client.post("/refunds/reconcile", json={"refund_events": huge_events})
    assert response.status_code != 413


def test_limit_applies_globally_not_just_to_one_endpoint():
    """Wired as app-level middleware (not per-route), so it should cover
    a second, unrelated endpoint too - spot-checked the same way the
    auth-coverage tests spot-check a second route."""
    huge_gateway_records = [{"transaction_id": f"t{i}", "net_amount": 1.0} for i in range(50_000)]
    response = client.post("/batches/reconcile", json={
        "gateway_records": huge_gateway_records, "bank_batch_records": [],
    })
    assert response.status_code == 413


def test_limit_covers_endpoints_added_after_the_middleware_was_written():
    """Found worth re-checking during a later audit: /marketplace/reconcile
    and /chargebacks/reconcile were both added AFTER this middleware
    already existed - confirms the app-level wiring genuinely covers
    endpoints it never knew about at write time, not just the ones it
    was originally tested against."""
    huge_transfers = [{"linked_account_id": f"v{i}", "amount": 1.0, "status": "settled"} for i in range(50_000)]
    response = client.post("/marketplace/reconcile", json={
        "gateway_record": {"transaction_id": "x", "net_amount": 100},
        "transfers": huge_transfers, "platform_commission": 0,
    })
    assert response.status_code == 413


def test_malformed_content_length_does_not_crash_the_middleware():
    """A malformed Content-Length header should fall through to normal
    request handling rather than crash the middleware itself."""
    response = client.post(
        "/refunds/reconcile", json={"refund_events": []},
        headers={"Content-Length": "not-a-number"},
    )
    # Exact status depends on how the underlying test transport handles
    # a manually-overridden Content-Length, but the middleware itself
    # must not raise an unhandled exception either way.
    assert response.status_code in (200, 400, 411, 422)
