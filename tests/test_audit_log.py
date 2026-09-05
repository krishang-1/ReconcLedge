"""Tests for the audit_log table (see docs/DECISIONS.md, "Audit
logging"). Distinct from tests/test_persistence.py, which proves the
job store survives a restart - these prove the audit trail itself is
complete, correctly queryable, and structurally append-only.
"""

import app as api_app
import jobs
import time
from fastapi.testclient import TestClient
from fake_llm_client import FakeLLMClient

client = TestClient(api_app.app)
api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()


def _wait_for_completion(run_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/runs/{run_id}/status").json()
        if status["status"] in ("completed", "failed"):
            return status
        time.sleep(0.05)
    raise TimeoutError(f"run {run_id} did not complete within {timeout}s")


def test_audit_log_has_one_entry_per_decision():
    """Every matched and every excepted transaction from a completed run
    must have a corresponding audit_log entry - the whole point of the
    table is completeness, not a sample."""
    run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    _wait_for_completion(run_id)
    results = client.get(f"/runs/{run_id}/results").json()

    audit_entries = client.get(f"/audit?run_id={run_id}").json()
    expected_txn_ids = {m["transaction_id"] for m in results["matched"]} | {e["transaction_id"] for e in results["exceptions"]}
    audited_txn_ids = {a["transaction_id"] for a in audit_entries}

    assert audited_txn_ids == expected_txn_ids, "audit log doesn't cover every decision the run actually made"
    assert len(audit_entries) == len(expected_txn_ids), "expected exactly one audit entry per transaction, no duplicates or gaps"


def test_audit_log_detail_matches_the_real_decision():
    """The audit entry for a matched transaction must carry the same
    utrs/method the actual result did - not a placeholder or a summary,
    the real decision detail."""
    run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    _wait_for_completion(run_id)
    results = client.get(f"/runs/{run_id}/results").json()

    if not results["matched"]:
        return  # nothing to check this run, sample_size=10 is small and demo-biased
    sample_match = results["matched"][0]

    audit_entries = client.get(f"/audit?run_id={run_id}&transaction_id={sample_match['transaction_id']}").json()
    assert len(audit_entries) == 1
    entry = audit_entries[0]
    assert entry["decision_type"] == "matched"
    assert entry["method"] == sample_match["method"]
    assert entry["detail"]["utrs"] == sample_match["utrs"]


def test_audit_log_queryable_by_transaction_id_across_runs():
    """The real audit-review question - 'what happened to transaction X'
    - must work by transaction_id alone, without needing to already know
    which run_id it was part of. Two runs against the same fixed dataset
    (build_demo_sample is deterministic) will touch overlapping
    transaction IDs - querying by transaction_id alone must find entries
    from both."""
    run_a = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    _wait_for_completion(run_a)
    run_b = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    _wait_for_completion(run_b)

    results_a = client.get(f"/runs/{run_a}/results").json()
    if not results_a["matched"]:
        return
    txn_id = results_a["matched"][0]["transaction_id"]

    entries = client.get(f"/audit?transaction_id={txn_id}").json()
    run_ids_seen = {e["run_id"] for e in entries}
    assert run_a in run_ids_seen
    assert run_b in run_ids_seen, "querying by transaction_id alone should find it across every run it appeared in, not just the most recent one"


def test_audit_log_covers_full_run_including_deterministic_stage():
    """A full run (not a demo sample) must also produce complete audit
    coverage, including the deterministic-stage matches that never went
    through the agent - not just agent-verified decisions."""
    run_id = client.post("/runs", json={}).json()["run_id"]
    _wait_for_completion(run_id, timeout=20)
    results = client.get(f"/runs/{run_id}/results").json()

    audit_entries = client.get(f"/audit?run_id={run_id}").json()
    expected_total = len(results["matched"]) + len(results["exceptions"])
    assert len(audit_entries) == expected_total

    deterministic_entries = [a for a in audit_entries if a["method"] == "deterministic"]
    assert deterministic_entries, "full run should include deterministic-stage matches in the audit trail"


def test_no_mutation_function_exists_for_audit_log():
    """Structural guard: no function in jobs.py issues a real SQL UPDATE
    or DELETE against audit_log - append-only by construction, not by
    convention. Static check, not a runtime one: confirms the module
    genuinely doesn't expose a way to do it, rather than trusting nobody
    calls one that exists. Checks for actual SQL statement syntax
    ("UPDATE audit_log" / "DELETE FROM audit_log"), not just the two
    words appearing near each other - a naive substring check would
    trip on this very docstring, which describes the guarantee in
    prose."""
    import inspect
    import re
    source = inspect.getsource(jobs)
    assert not re.search(r"UPDATE\s+audit_log", source, re.IGNORECASE), "found a real UPDATE statement against audit_log"
    assert not re.search(r"DELETE\s+FROM\s+audit_log", source, re.IGNORECASE), "found a real DELETE statement against audit_log"
