"""Tests for api/app.py. Uses FastAPI's TestClient (synchronous, no
running server needed) and overrides the LLM client dependency with
FakeLLMClient - same pattern as the rest of this test suite, no real API
key required.
"""

import time

import app as api_app
import jobs
from fastapi.testclient import TestClient
from fake_llm_client import FakeLLMClient

client = TestClient(api_app.app)
api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()


def _wait_for_completion(run_id, timeout=10):
    """Polls /status until the run leaves 'pending'/'running', or times out."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/runs/{run_id}/status").json()
        if status["status"] in ("completed", "failed"):
            return status
        time.sleep(0.05)
    raise TimeoutError(f"run {run_id} did not complete within {timeout}s")


def test_create_run_returns_run_id_immediately():
    response = client.post("/runs", json={"sample_size": 10})
    assert response.status_code == 200
    body = response.json()
    assert "run_id" in body
    assert body["status"] == "pending"


def test_unknown_run_id_returns_404():
    assert client.get("/runs/does_not_exist/status").status_code == 404
    assert client.get("/runs/does_not_exist/results").status_code == 404


def test_results_not_ready_returns_409():
    run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    # results endpoint hit immediately, before the background thread has
    # necessarily finished - should be a clear 409, not a crash or a
    # silently empty/wrong body
    response = client.get(f"/runs/{run_id}/results")
    assert response.status_code in (200, 409)  # 200 only if it happened to finish instantly


def test_demo_run_completes_and_returns_results():
    run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    status = _wait_for_completion(run_id)
    assert status["status"] == "completed"

    results = client.get(f"/runs/{run_id}/results").json()
    assert results["mode"] == "demo_sample"
    assert results["summary"]["total"] == results["summary"]["matched"] + results["summary"]["exceptions"]


def test_demo_run_includes_at_least_one_agent_routed_record():
    """The whole point of build_demo_sample() (see jobs.py) is biasing the
    sample toward agent-routed records, not a naive slice that might miss
    them. Confirm the demo run's results actually show agent-method
    matches or exceptions, not just instant deterministic ones."""
    run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    _wait_for_completion(run_id)
    results = client.get(f"/runs/{run_id}/results").json()

    agent_methods = [m for m in results["matched"] if m.get("method") == "agent_verified"]
    assert agent_methods or results["exceptions"], "demo sample should include at least one agent-routed outcome"


def test_full_run_computes_real_eval_metrics():
    """sample_size omitted should run the complete pipeline, including the
    real held-out eval-split metrics - not the demo summary shape."""
    run_id = client.post("/runs", json={}).json()["run_id"]
    status = _wait_for_completion(run_id, timeout=15)
    assert status["status"] == "completed"

    results = client.get(f"/runs/{run_id}/results").json()
    assert results["mode"] == "full_run"
    assert "metrics" in results
    assert results["metrics"]["eval_set_size"] == 20  # regression guard for the ORPHAN_BANK fix (error #11)


def test_list_runs_shows_created_jobs():
    run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    all_runs = client.get("/runs").json()
    assert any(r["run_id"] == run_id for r in all_runs)


def test_missing_api_key_returns_clean_503_not_a_raw_traceback():
    """Regression guard: without GROQ_API_KEY set, the API must return a
    clear, actionable error - not an unhandled RuntimeError surfacing as
    a raw stack trace and a generic 500. This is a near-certain first
    mistake for anyone setting the API up."""
    import os as _os
    saved_key = _os.environ.pop("GROQ_API_KEY", None)
    saved_override = api_app.app.dependency_overrides.pop(api_app.get_llm_client, None)
    try:
        response = client.post("/runs", json={"sample_size": 10})
        assert response.status_code == 503
        assert "GROQ_API_KEY" in response.json()["detail"]
    finally:
        if saved_key is not None:
            _os.environ["GROQ_API_KEY"] = saved_key
        if saved_override is not None:
            api_app.app.dependency_overrides[api_app.get_llm_client] = saved_override


def test_stream_emits_events_and_terminates():
    """SSE stream should emit progress events and a final 'done' event,
    then the connection should actually close - not hang forever."""
    run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    with client.stream("GET", f"/runs/{run_id}/stream") as response:
        assert response.status_code == 200
        lines = []
        for line in response.iter_lines():
            if line:
                lines.append(line)
            if len(lines) > 200:  # safety valve - fail loudly instead of hanging if it never terminates
                break
    assert any('"done"' in line for line in lines), "stream never emitted a terminal done event"


def test_deterministic_progress_actually_increments():
    """Regression guard: the progress counter for deterministic-stage
    events was found to always report a constant (the total, not the
    running index) on a real run - every event said 'current: 7, total: 7'
    instead of counting 1/7, 2/7, ..., 7/7. Confirm the fix: the sequence
    of 'current' values across deterministic events must actually
    increment, not repeat the same number."""
    import json as _json

    run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    with client.stream("GET", f"/runs/{run_id}/stream") as response:
        det_currents = []
        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            event = _json.loads(line[len("data: "):])
            if event.get("stage") == "deterministic":
                det_currents.append(event["progress"]["current"])
            if event.get("stage") == "done":
                break

    assert len(det_currents) >= 2, "need at least 2 deterministic events to check incrementing"
    assert det_currents == sorted(set(det_currents)), (
        f"deterministic progress did not increment cleanly: {det_currents}"
    )
    assert det_currents == list(range(1, len(det_currents) + 1)), (
        f"expected 1, 2, 3... but got {det_currents} - this is the exact bug found on a real run"
    )


def test_full_run_streams_deterministic_exceptions_too():
    """Regression guard for a real gap found on review: a full run's
    deterministic-stage DUPLICATE exceptions never appeared in the live
    SSE stream, only the matches did - meaning the most compelling
    'honest exception reporting' story was invisible to anyone actually
    watching the demo live, even though it showed up fine in the final
    results.json."""
    import json as _json

    run_id = client.post("/runs", json={}).json()["run_id"]  # full run, not a demo sample
    det_events = []
    with client.stream("GET", f"/runs/{run_id}/stream") as response:
        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            event = _json.loads(line[len("data: "):])
            if event.get("stage") == "deterministic":
                det_events.append(event)
            if event.get("stage") == "done":
                break

    exception_events = [e for e in det_events if e["status"] == "exception"]
    assert exception_events, "full run should stream at least one deterministic exception (the known DUPLICATE cases)"
    # progress total must reflect ALL deterministic-stage outcomes, not just matches
    assert det_events[-1]["progress"]["current"] == det_events[-1]["progress"]["total"]


def test_stream_includes_error_in_terminal_event_on_failure():
    """Regression guard: a real run that failed mid-agent-stage terminated
    with a bare {"stage": "done", "status": "failed"} SSE event - no error
    message, forcing a second call to /status to learn why. Confirm the
    terminal event now carries the error directly."""
    import json as _json

    class AlwaysBrokenClient:
        """Raises immediately on every call - simulates a hard pipeline
        failure (e.g. a real network error) to exercise the failed path."""
        def chat(self, messages, tools=None, tool_choice="auto"):
            raise RuntimeError("simulated hard failure for this test")

    api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: AlwaysBrokenClient()
    try:
        run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
        with client.stream("GET", f"/runs/{run_id}/stream") as response:
            done_event = None
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                event = _json.loads(line[len("data: "):])
                if event.get("stage") == "done":
                    done_event = event
                    break
        assert done_event is not None, "stream never terminated"
        assert done_event["status"] == "failed"
        assert "simulated hard failure" in done_event.get("error", ""), (
            "terminal SSE event on failure must include the actual error message"
        )
    finally:
        api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()


def test_build_demo_sample_biases_toward_agent_routed_records():
    """Unit-level check on the sampling logic itself, not just the
    end-to-end API behavior above."""
    import json as _json
    import os as _os
    data_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "data")
    with open(_os.path.join(data_dir, "gateway_transactions.json")) as f:
        gw = _json.load(f)
    with open(_os.path.join(data_dir, "bank_settlement.json")) as f:
        bank = _json.load(f)

    det_matched, sampled_agent, unclaimed = jobs.build_demo_sample(gw, bank, sample_size=10)
    assert len(sampled_agent) >= 2, "demo sample should always include at least 2 agent-routed records when available"
    assert len(det_matched) + len(sampled_agent) <= 10


def test_build_demo_sample_never_exceeds_requested_size():
    """Regression guard for a real bug found on review: the 'at least 2
    agent-routed records' floor could return MORE than sample_size when
    sample_size was 1 or 0 - e.g. sample_size=1 could silently return 2
    records. Total returned must never exceed what was requested."""
    import json as _json
    import os as _os
    data_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "data")
    with open(_os.path.join(data_dir, "gateway_transactions.json")) as f:
        gw = _json.load(f)
    with open(_os.path.join(data_dir, "bank_settlement.json")) as f:
        bank = _json.load(f)

    for size in (0, 1, 2, 3, 5):
        det_matched, sampled_agent, unclaimed = jobs.build_demo_sample(gw, bank, sample_size=size)
        total = len(det_matched) + len(sampled_agent)
        assert total <= size, f"sample_size={size} but got {total} records back"


def test_sample_size_zero_or_negative_rejected_with_422():
    """API-level guard: a 0 or negative sample_size should get a clean
    validation error, not silently produce a degenerate or confusing
    result."""
    assert client.post("/runs", json={"sample_size": 0}).status_code == 422
    assert client.post("/runs", json={"sample_size": -5}).status_code == 422


def test_llm_usage_reports_only_this_runs_own_calls_not_the_shared_clients_combined_total():
    """The whole reason api/jobs.py computes a before/after DELTA rather
    than reading the shared llm_client's raw cumulative totals directly
    (see docs/DECISIONS.md) - get_llm_client() is a long-lived singleton
    reused across every run, not recreated per run. Proves this
    concretely: run two SEPARATE runs against the SAME shared client
    instance and confirm each run's own llm_usage reflects only its own
    calls, never the other run's, and never the running total.

    Also restores the module's own default override in a finally block
    - a real, self-inflicted instance of an already-known bug class
    (see the sibling test below, which actually broke a different test
    file when this wasn't done): app.dependency_overrides is shared
    PROCESS-WIDE across the whole test session, so leaving a custom
    override in place here would leak into every test that runs after
    this one."""
    shared_client = FakeLLMClient()
    api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: shared_client

    try:
        run_id_1 = client.post("/runs", json={"sample_size": 3}).json()["run_id"]
        _wait_for_completion(run_id_1)
        results_1 = client.get(f"/runs/{run_id_1}/results").json()

        run_id_2 = client.post("/runs", json={"sample_size": 3}).json()["run_id"]
        _wait_for_completion(run_id_2)
        results_2 = client.get(f"/runs/{run_id_2}/results").json()

        assert "llm_usage" in results_1 and "llm_usage" in results_2
        # The real, meaningful assertion: run 2's own reported usage must
        # NOT equal the shared client's combined total after both runs -
        # if the delta logic were broken (e.g. reading raw totals instead
        # of a before/after difference), run 2 would incorrectly report
        # its own usage PLUS run 1's, inflating every run after the first.
        assert results_2["llm_usage"]["calls"] < shared_client.total_calls
        assert results_1["llm_usage"]["calls"] + results_2["llm_usage"]["calls"] == shared_client.total_calls
    finally:
        api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()


def test_llm_usage_falls_back_gracefully_for_a_client_without_the_tracking_interface():
    """Real bug found and fixed while building this feature (see
    docs/DECISIONS.md): an unconditional attribute read broke an
    unrelated test using a deliberately-minimal/broken client stub for
    a different purpose. An additive observability feature must never
    be able to break a correctness-critical path just because some
    client implementation doesn't expose these extra attributes.

    A second, self-inflicted instance of an already-known bug class
    caught immediately by re-running the full suite (not trusted from
    this file's own tests passing in isolation): this test's first
    version set app.dependency_overrides[get_llm_client] to the
    MinimalClient below and never restored it - app and its
    dependency_overrides are shared PROCESS-WIDE across the whole test
    session (same caveat as the CORS test regression found earlier this
    session, see docs/DECISIONS.md), so every test running afterward
    would have silently gotten MinimalClient's hardcoded
    always-report-exception stub instead of the real FakeLLMClient this
    whole file's module-level override sets up. Fixed with a real
    try/finally restoring the module's own default, not a bare
    assignment left in place."""

    class MinimalClient:
        """Deliberately does NOT implement total_prompt_tokens etc. -
        simulates any current or future llm_client implementation that
        predates or doesn't care about this feature."""

        def chat(self, messages, tools=None, tool_choice="auto"):
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "report_exception", "arguments": '{"exception_type": "NO_CANDIDATE_FOUND", "reasoning": "minimal client"}'},
            }]}

    api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: MinimalClient()
    try:
        run_id = client.post("/runs", json={"sample_size": 3}).json()["run_id"]
        status = _wait_for_completion(run_id)
        assert status["status"] == "completed"
        results = client.get(f"/runs/{run_id}/results").json()
        assert results["llm_usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "latency_seconds": 0.0, "calls": 0}
    finally:
        # Restore this file's own module-level default - never leave a
        # shared dependency_override pointing at a one-off test stub for
        # every subsequent test in the whole session to silently inherit.
        api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()
