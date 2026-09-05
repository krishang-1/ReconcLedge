"""Tests for api/app.py's GET /health endpoint - unauthenticated by
design (see api/auth.py's UNAUTHENTICATED_PATHS), checks real database
connectivity, not just process liveness."""

import app as api_app
from fastapi.testclient import TestClient

client = TestClient(api_app.app)


def test_health_returns_ok_when_database_reachable():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_health_bypasses_auth_even_when_api_keys_is_set(monkeypatch):
    monkeypatch.setenv("API_KEYS", "some-real-key")
    response = client.get("/health")
    assert response.status_code == 200  # no X-API-Key header sent at all


def test_other_routes_still_require_auth_when_api_keys_is_set(monkeypatch):
    """Control case for the test above - confirms the /health exemption
    is scoped to /health specifically, not a blanket auth bypass."""
    monkeypatch.setenv("API_KEYS", "some-real-key")
    response = client.get("/runs")
    assert response.status_code == 401


def test_health_reports_degraded_on_database_failure(monkeypatch):
    """Simulates a real database outage - a broken connection object -
    and confirms /health reports it as a clean 503 rather than crashing
    or silently reporting healthy."""
    import jobs

    class BrokenConn:
        def execute(self, *args, **kwargs):
            raise Exception("simulated database connection failure")

    monkeypatch.setattr(jobs, "_conn", BrokenConn())
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
