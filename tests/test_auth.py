"""Tests for api/auth.py's app-level API-key dependency. Uses monkeypatch
(pytest builtin) for API_KEYS so each test's environment change auto-
reverts afterward - this matters here specifically, because a value left
set would silently change every OTHER test file's requests from
unauthenticated-and-passing to unauthenticated-and-401, since the app
instance (and its dependency) is shared process-wide across the whole
test session.

Hits GET /runs rather than POST /runs for the auth checks themselves -
/runs doesn't depend on the LLM client, so these tests don't need to
touch the FakeLLMClient override test_api.py sets up.
"""

import app as api_app
from fastapi.testclient import TestClient

client = TestClient(api_app.app)


def test_disabled_by_default_allows_unauthenticated_request(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    response = client.get("/runs")
    assert response.status_code == 200


def test_missing_key_returns_401_when_configured(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key-1,secret-key-2")
    response = client.get("/runs")
    assert response.status_code == 401


def test_wrong_key_returns_403_when_configured(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key-1,secret-key-2")
    response = client.get("/runs", headers={"X-API-Key": "not-a-real-key"})
    assert response.status_code == 403


def test_correct_key_allows_request_when_configured(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-key-1,secret-key-2")
    response = client.get("/runs", headers={"X-API-Key": "secret-key-2"})
    assert response.status_code == 200


def test_keys_are_whitespace_trimmed(monkeypatch):
    monkeypatch.setenv("API_KEYS", " secret-key-1 , secret-key-2 ")
    response = client.get("/runs", headers={"X-API-Key": "secret-key-1"})
    assert response.status_code == 200


def test_empty_api_keys_value_is_treated_as_disabled(monkeypatch):
    monkeypatch.setenv("API_KEYS", "")
    response = client.get("/runs")
    assert response.status_code == 200


def test_auth_covers_other_routes_too(monkeypatch):
    """Spot-check that the app-level wiring actually covers a route
    other than /runs, confirming this isn't accidentally scoped to a
    single endpoint."""
    monkeypatch.setenv("API_KEYS", "secret-key-1")
    assert client.get("/audit").status_code == 401
    assert client.get("/runs/does_not_exist/status").status_code == 401
    assert client.get("/audit", headers={"X-API-Key": "secret-key-1"}).status_code == 200


def test_only_health_bypasses_auth_every_other_real_route_still_gated(monkeypatch):
    """Full route-by-route audit, not a spot-check: enumerates every
    real registered route programmatically and confirms exactly one
    (GET /health) bypasses auth when API_KEYS is set, and every other
    route still returns 401 with no key. Written after adding the
    /health exemption to api/auth.py's UNAUTHENTICATED_PATHS, to prove
    that change didn't accidentally widen further than intended - same
    discipline as the full auth audit done after the /health work in
    docs/DECISIONS.md.

    Enumerates via app.openapi()'s generated schema, not app.routes -
    found during the API-versioning work (see docs/DECISIONS.md) that
    app.routes doesn't flatten routes mounted via include_router() in
    this Starlette version (they show up as an internal
    "_IncludedRouter" wrapper with no stable public attribute to
    recurse into). openapi() is FastAPI's own stable, public, documented
    way to get the full real route list regardless of how routes were
    mounted - the correct fix, not a version-specific workaround."""
    monkeypatch.setenv("API_KEYS", "secret-key-1")

    schema = api_app.app.openapi()
    route_set = sorted(
        (method.upper(), path)
        for path, methods in schema["paths"].items()
        for method in methods
        if method.upper() in ("GET", "POST")
    )
    assert len(route_set) >= 26  # sanity: 13 unversioned + 13 /v1 + didn't accidentally enumerate zero

    bodies = {
        "POST /runs": {},
        "POST /refunds/reconcile": {"refund_events": []},
        "POST /batches/reconcile": {"gateway_records": [], "bank_batch_records": []},
        "POST /fx/reconcile": {
            "gateway_record": {"transaction_id": "x", "amount": 1, "currency": "USD"},
            "bank_record": {"settled_amount": 1, "currency": "INR"},
            "rate_min": 1, "rate_max": 2,
        },
        "POST /merchants/{merchant_id}/config": {"date_window_days": 5},
        "POST /marketplace/reconcile": {
            "gateway_record": {"transaction_id": "x", "net_amount": 100},
            "transfers": [], "platform_commission": 100,
        },
        "POST /chargebacks/reconcile": {
            "gateway_record": {"transaction_id": "x", "net_amount": 100},
            "chargeback_event": {"status": "open", "disputed_amount": 50, "chargeback_fee": 10, "initiated_by": "customer"},
        },
    }

    for method, path in route_set:
        concrete_path = path.replace("{merchant_id}", "test_m").replace("{run_id}", "fake_id")
        body = bodies.get(f"{method} {path}")
        response = client.get(concrete_path) if method == "GET" else client.post(concrete_path, json=body or {})
        expected = 200 if path == "/health" else 401
        assert response.status_code == expected, f"{method} {concrete_path} returned {response.status_code}, expected {expected}"
