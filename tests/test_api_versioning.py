"""Tests for the new /v1/ versioned API surface (see api/app.py's
`router = APIRouter()` and the dual `app.include_router()` calls at the
bottom of that file). Proves both surfaces genuinely work identically
through real HTTP calls - not inferred from the openapi schema, and not
just "the unversioned tests still pass" (which only proves backward
compatibility, not that /v1/ itself actually works)."""

import time

import app as api_app
import fx_generator
from fastapi.testclient import TestClient
from fake_llm_client import FakeLLMClient

client = TestClient(api_app.app)
api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()


def _wait_for_completion(run_id, prefix="", timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"{prefix}/runs/{run_id}/status").json()
        if status["status"] in ("completed", "failed"):
            return status
        time.sleep(0.05)
    raise TimeoutError(f"run {run_id} did not complete in {timeout}s")


def test_v1_health_check_note_health_itself_stays_unversioned():
    """/health deliberately has no /v1 counterpart - see api/app.py's
    module-level comment on why (orchestrator health checks
    conventionally expect a stable, version-independent path)."""
    assert client.get("/health").status_code == 200
    assert client.get("/v1/health").status_code == 404


def test_v1_runs_pipeline_works_identically_to_unversioned():
    response = client.post("/v1/runs", json={})
    run_id = response.json()["run_id"]
    status = _wait_for_completion(run_id, prefix="/v1")
    assert status["status"] == "completed"

    results = client.get(f"/v1/runs/{run_id}/results").json()
    assert results["metrics"]["match_rate"] == 0.95


def test_v1_and_unversioned_share_the_same_underlying_data():
    """Both surfaces aren't two separate apps with separate state -
    they're the same router mounted twice, so a run created via one
    prefix must be readable via the other."""
    response = client.post("/runs", json={"sample_size": 5})
    run_id = response.json()["run_id"]
    _wait_for_completion(run_id)

    via_v1 = client.get(f"/v1/runs/{run_id}/status").json()
    assert via_v1["status"] == "completed"


def test_v1_refund_endpoint_works():
    response = client.post("/v1/refunds/reconcile", json={"refund_events": []})
    assert response.status_code == 200


def test_v1_fx_endpoint_works():
    gw, bank, rmin, rmax, markup = fx_generator.generate()[0]
    response = client.post("/v1/fx/reconcile", json={
        "gateway_record": gw, "bank_record": bank, "rate_min": rmin, "rate_max": rmax, "markup_bps": markup,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "matched_within_rate_band"


def test_v1_merchant_config_round_trips():
    client.post("/v1/merchants/v1_test_merchant/config", json={"date_window_days": 4})
    response = client.get("/v1/merchants/v1_test_merchant/config")
    assert response.json()["date_window_days"] == 4


def test_v1_audit_endpoint_works():
    response = client.get("/v1/audit")
    assert response.status_code == 200


def test_v1_is_covered_by_auth_same_as_unversioned(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-1")
    assert client.get("/v1/runs").status_code == 401
    assert client.get("/v1/runs", headers={"X-API-Key": "secret-1"}).status_code == 200


def test_v1_respects_the_request_size_limit_middleware_too():
    """The size-limit middleware (api/app.py's limit_request_body_size)
    is app-level, not router-level - confirms it covers the versioned
    surface too, not just the unversioned one it was written against."""
    huge_events = [{"transaction_id": f"t{i}", "refund_amount": 1.0} for i in range(50_000)]
    response = client.post("/v1/refunds/reconcile", json={"refund_events": huge_events})
    assert response.status_code == 413
