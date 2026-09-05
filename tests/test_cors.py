"""Tests for the CORS configuration in api/app.py - a real, severe gap
found via a comprehensive line-by-line audit, not a routine hardening
item. Before this fix, zero CORS configuration existed at all: a
browser's preflight OPTIONS request got back a bare 405 with no CORS
headers, meaning every cross-origin request (exactly what the
frontend's VITE_API_BASE_URL config and the Docker "separate services"
deployment story anticipate) would be silently blocked by the browser.
See docs/DECISIONS.md for the full narrative, including the middleware-
ordering bug found and fixed along the way (CORS headers were initially
missing specifically on error responses like 413/401, because
CORSMiddleware was registered before, not after, the body-size-limit
middleware - Starlette's real behavior is that the LAST-registered
middleware becomes outermost, opposite to what was first assumed).
"""

import app as api_app
from fastapi.testclient import TestClient

client = TestClient(api_app.app)


def test_preflight_options_returns_cors_headers():
    response = client.options(
        "/v1/runs",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Key",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"
    assert "GET" in response.headers.get("access-control-allow-methods", "")


def test_actual_cross_origin_request_carries_allow_origin_header():
    response = client.get("/v1/runs", headers={"Origin": "http://localhost:5174"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"


def test_rejected_oversized_request_still_carries_cors_headers():
    """Regression guard for the middleware-ordering bug specifically:
    a browser rejected by the body-size-limit middleware must still see
    CORS headers on that rejection, or it shows a confusing CORS error
    instead of the real, informative 413."""
    huge_events = [{"transaction_id": f"t{i}", "refund_amount": 1.0} for i in range(50_000)]
    response = client.post(
        "/v1/refunds/reconcile",
        json={"refund_events": huge_events},
        headers={"Origin": "http://localhost:5174"},
    )
    assert response.status_code == 413
    assert response.headers.get("access-control-allow-origin") == "*"


def test_rejected_auth_request_still_carries_cors_headers(monkeypatch):
    monkeypatch.setenv("API_KEYS", "cors-test-key")
    response = client.get("/v1/runs", headers={"Origin": "http://localhost:5174"})
    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == "*"


def test_streaming_response_carries_cors_headers():
    """SSE responses are a genuinely different response type (chunked,
    text/event-stream) from a normal JSON response - confirms CORS
    middleware applies to them too, not just the common case.

    Real bug caught in this test's own first version, not shipped:
    it originally did `del app.dependency_overrides[get_llm_client]`
    at the end to "clean up" - but `app` and its `dependency_overrides`
    are shared process-wide across the ENTIRE test session (same
    caveat already documented elsewhere in this suite), and other test
    files (e.g. test_merchant_config_integration.py) set this same
    override once at module level and rely on it staying set for every
    one of their own tests, regardless of import/run order. Deleting
    it here broke five tests in a completely different file - caught
    immediately by re-running the full suite after adding this test,
    not assumed passing from this file's own tests alone. Fixed by
    following the same established pattern every other test file in
    this suite already uses: set it once, never delete it."""
    from fake_llm_client import FakeLLMClient

    api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()
    create_response = client.post("/v1/runs", json={"sample_size": 3})
    run_id = create_response.json()["run_id"]

    with client.stream("GET", f"/v1/runs/{run_id}/stream", headers={"Origin": "http://localhost:5174"}) as response:
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "*"


def test_cors_allowed_origins_is_configurable_via_env_var():
    """CORS_ALLOWED_ORIGINS is read once at module import time (same
    pattern as MAX_REQUEST_BODY_BYTES/STALE_JOB_TIMEOUT_SECONDS) - a
    real subprocess test, not a same-process one, since the value is
    baked in at import time and every other test in this file already
    exercises the default "*" behavior against the already-imported
    module. Confirms a real, non-default configuration actually takes
    effect: with CORS_ALLOWED_ORIGINS restricted to one specific
    origin, a request from THAT origin gets it echoed back, and the
    middleware genuinely reflects the configured value rather than
    always defaulting to "*" regardless of the env var."""
    import os
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = f"""
import sys, os
sys.path.insert(0, {root + "/api"!r})
sys.path.insert(0, {root + "/agent"!r})
sys.path.insert(0, {root + "/eval"!r})
sys.path.insert(0, {root + "/data"!r})
import app
from fastapi.testclient import TestClient
client = TestClient(app.app)
response = client.get("/v1/runs", headers={{"Origin": "https://my-real-frontend.example.com"}})
print(response.headers.get("access-control-allow-origin"))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "JOBS_DB_PATH": ":memory:",
            "CORS_ALLOWED_ORIGINS": "https://my-real-frontend.example.com,https://staging.example.com",
        },
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    assert result.stdout.strip() == "https://my-real-frontend.example.com"


def test_cors_rejects_a_non_allowed_origin_when_restricted():
    """The negative case for the test above - genuinely never covered
    before, found as a gap during a deep verification pass (see
    docs/DECISIONS.md): confirms that when CORS_ALLOWED_ORIGINS is
    restricted, a request from an origin NOT in that list gets no
    Access-Control-Allow-Origin header at all - the actual mechanism
    that makes the restriction real. The request itself still succeeds
    at the HTTP level (this is correct, standard CORS behavior - the
    server doesn't reject the request outright, it just omits the
    permissive header; enforcement happens client-side, in the
    browser). Manually verified once via curl during the same pass
    this test was written for; this locks it in permanently."""
    import os
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = f"""
import sys, os
sys.path.insert(0, {root + "/api"!r})
sys.path.insert(0, {root + "/agent"!r})
sys.path.insert(0, {root + "/eval"!r})
sys.path.insert(0, {root + "/data"!r})
import app
from fastapi.testclient import TestClient
client = TestClient(app.app)
response = client.get("/v1/runs", headers={{"Origin": "https://attacker.example.com"}})
print(response.status_code)
print(response.headers.get("access-control-allow-origin"))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "JOBS_DB_PATH": ":memory:",
            "CORS_ALLOWED_ORIGINS": "https://my-real-frontend.example.com,https://staging.example.com",
        },
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    lines = result.stdout.strip().split("\n")
    assert lines[0] == "200"  # the HTTP request itself still succeeds - correct CORS behavior
    assert lines[1] == "None"  # but no permissive header is present for a non-allowed origin
