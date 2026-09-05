"""Integration tests for merchant-specific configuration wired into the
real /runs pipeline - proving a registered merchant's settlement window
and escalation threshold genuinely change a real run's outcome, and that
an unconfigured/omitted merchant_id is byte-for-byte identical to the
pre-merchant-config baseline. Uses FastAPI's TestClient against the real
app.py/jobs.py code path, same pattern as test_api.py, not just the
merchant_config.py unit tests in isolation."""

import time

import app as api_app
import merchant_config
from fastapi.testclient import TestClient
from fake_llm_client import FakeLLMClient

client = TestClient(api_app.app)
api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()


def _wait_for_completion(run_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/runs/{run_id}/status").json()
        if status["status"] in ("completed", "failed"):
            return status
        time.sleep(0.05)
    raise TimeoutError(f"run {run_id} did not complete in {timeout}s")


def test_omitted_merchant_id_matches_the_known_baseline():
    """No merchant_id at all - must reproduce the exact 37/3/12-derived
    baseline (0.95 match rate) this whole project's submission number
    depends on. This is the single most important test in this file."""
    response = client.post("/runs", json={})
    run_id = response.json()["run_id"]
    _wait_for_completion(run_id)
    results = client.get(f"/runs/{run_id}/results").json()
    assert results["metrics"]["match_rate"] == 0.95


def test_unregistered_merchant_id_also_matches_the_baseline():
    """A merchant_id that was never registered must behave identically
    to omitting it entirely - get_merchant_config()'s fallback, exercised
    through the real endpoint, not just the unit-level registry."""
    response = client.post("/runs", json={"merchant_id": "totally_unregistered_merchant"})
    run_id = response.json()["run_id"]
    _wait_for_completion(run_id)
    results = client.get(f"/runs/{run_id}/results").json()
    assert results["metrics"]["match_rate"] == 0.95


def test_registered_merchant_config_actually_changes_a_real_run():
    """The real proof: a tight date window registered for a specific
    merchant must route MORE records through the agent stage (visible in
    the audit log's method field) than the default - not just be
    accepted and silently ignored. Checking the audit log's per-record
    method rather than the final matched count, because the agent stage
    can successfully recover many of the extra records a tighter window
    pushes to it - the final matched count alone doesn't reliably prove
    the parameter took effect, but which STAGE resolved each record does."""
    client.post("/merchants/tight_window_merchant/config", json={"date_window_days": 0})

    baseline = client.post("/runs", json={})
    baseline_id = baseline.json()["run_id"]
    _wait_for_completion(baseline_id)

    configured = client.post("/runs", json={"merchant_id": "tight_window_merchant"})
    configured_id = configured.json()["run_id"]
    _wait_for_completion(configured_id)

    baseline_audit = client.get("/audit", params={"run_id": baseline_id}).json()
    configured_audit = client.get("/audit", params={"run_id": configured_id}).json()

    baseline_deterministic = sum(1 for row in baseline_audit if row["method"] == "deterministic")
    configured_deterministic = sum(1 for row in configured_audit if row["method"] == "deterministic")
    assert configured_deterministic < baseline_deterministic  # tighter window -> fewer deterministic resolutions


def test_registered_escalation_threshold_actually_changes_a_real_run():
    """A near-zero threshold for a specific merchant should flag nearly
    every matched/exception record for human review, not just accept
    the value and ignore it."""
    client.post("/merchants/low_threshold_merchant/config", json={"escalation_threshold": 1.0})

    response = client.post("/runs", json={"merchant_id": "low_threshold_merchant"})
    run_id = response.json()["run_id"]
    _wait_for_completion(run_id)
    results = client.get(f"/runs/{run_id}/results").json()
    assert results["requires_human_review"] > 10  # well above the default config's count (10)


def test_merchant_config_registration_round_trips():
    client.post("/merchants/roundtrip_merchant/config", json={"date_window_days": 5, "escalation_threshold": 500.0})
    response = client.get("/merchants/roundtrip_merchant/config")
    body = response.json()
    assert body["date_window_days"] == 5
    assert body["escalation_threshold"] == 500.0
    assert body["known_merchant"] is True


def test_unregistered_merchant_config_get_reports_known_merchant_false():
    response = client.get("/merchants/never_touched_merchant/config")
    body = response.json()
    assert body["known_merchant"] is False
    assert body["date_window_days"] == merchant_config.DATE_WINDOW_DAYS
    assert body["escalation_threshold"] == merchant_config.HIGH_VALUE_THRESHOLD


def test_partial_config_update_falls_back_to_global_default_not_previous_value():
    client.post("/merchants/partial_update_merchant/config", json={"date_window_days": 2, "escalation_threshold": 100.0})
    client.post("/merchants/partial_update_merchant/config", json={"date_window_days": 9})
    response = client.get("/merchants/partial_update_merchant/config")
    body = response.json()
    assert body["date_window_days"] == 9
    assert body["escalation_threshold"] == merchant_config.HIGH_VALUE_THRESHOLD  # not 100.0


def test_merchant_config_endpoints_covered_by_auth(monkeypatch):
    monkeypatch.setenv("API_KEYS", "secret-1")
    response = client.post("/merchants/auth_check_merchant/config", json={"date_window_days": 5})
    assert response.status_code == 401
    response = client.get("/merchants/auth_check_merchant/config")
    assert response.status_code == 401


def test_merchant_config_and_confidence_gating_compose_correctly_together():
    """Cross-feature interaction never explicitly tested before, found
    worth checking during a later audit: a merchant with BOTH a custom
    date window AND a custom escalation threshold, run through the full
    pipeline including confidence gating (agent/confidence.py, added
    after merchant config already existed). Confirms all layers compose
    without breaking core invariants - full accounting, correct metrics,
    every record still carries a confidence tier regardless of which
    merchant's config produced it."""
    client.post("/merchants/full_custom_merchant/config", json={"date_window_days": 3, "escalation_threshold": 20000.0})

    response = client.post("/runs", json={"merchant_id": "full_custom_merchant"})
    run_id = response.json()["run_id"]
    status = _wait_for_completion(run_id)
    assert status["status"] == "completed"

    results = client.get(f"/runs/{run_id}/results").json()
    assert results["metrics"]["match_rate"] == 0.95  # core metric untouched by any of this
    total = len(results["matched"]) + len(results["exceptions"])
    assert total == 52  # full accounting held across all three composed layers
    assert all("confidence" in r for r in results["matched"] + results["exceptions"])

    audit = client.get("/audit", params={"run_id": run_id}).json()
    assert len(audit) == 52
    confidences_seen = {row["detail"].get("confidence") for row in audit}
    assert confidences_seen <= {"high", "medium", "low"}
