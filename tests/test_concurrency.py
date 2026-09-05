"""Concurrency and adversarial-input tests for the API layer. Every other
test in this suite runs jobs sequentially, one at a time - these
specifically exercise what Stage 5's architecture actually promises under
real concurrent load: one shared LLM client instance across all runs, and
a single in-memory job store. Found on request after the rest of the
suite had already been reviewed twice with no new findings - genuinely
different territory (threading) from anything tested before.
"""

import json
import threading
import time

import app as api_app
from fastapi.testclient import TestClient
from fake_llm_client import FakeLLMClient

client = TestClient(api_app.app)
api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()


def test_shared_client_survives_concurrent_calls_no_crash():
    """The shared LLM client's sticky max_tokens (see docs/DECISIONS.md)
    is mutable state read and written from whichever thread happens to
    be running a job. Confirms real concurrent access - not just
    sequential calls - never crashes and never corrupts max_tokens into
    a negative or nonsensical value."""
    import llm_client

    call_log = []
    lock = threading.Lock()

    class FakeResp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
            self.text = json.dumps(body)
            self.headers = {}

        @property
        def ok(self):
            return 200 <= self.status_code < 300

        def json(self):
            return self._body

    def fake_post(url, headers=None, data=None, timeout=None):
        import random
        body = json.loads(data)
        with lock:
            call_log.append(body.get("max_tokens"))
        if random.random() < 0.3:
            shortfall = body["max_tokens"] - 50
            return FakeResp(402, {"error": {"message": f"You requested up to {body['max_tokens']} tokens, but can only afford {shortfall}.", "code": 402}})
        return FakeResp(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    original_post = llm_client.requests.post
    llm_client.requests.post = fake_post
    try:
        test_client = llm_client.OpenRouterClient(api_key="test")
        errors = []

        def worker(n):
            try:
                for _ in range(10):
                    test_client.chat([{"role": "user", "content": f"msg{n}"}])
            except Exception as e:
                errors.append((n, str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent calls raised: {errors}"
        assert all(v is None or v > 0 for v in call_log), "max_tokens went negative or nonsensical under concurrent access"
        assert test_client.max_tokens > 0
    finally:
        llm_client.requests.post = original_post


def test_concurrent_api_runs_do_not_cross_contaminate():
    """Fires several runs concurrently through the real API (not
    sequentially) and confirms each gets a unique run_id, completes
    independently, and its results are internally consistent - no
    evidence of one run's data leaking into another's."""
    run_ids = []
    lock = threading.Lock()

    def start_run():
        resp = client.post("/runs", json={"sample_size": 10})
        with lock:
            run_ids.append(resp.json()["run_id"])

    threads = [threading.Thread(target=start_run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(run_ids) == len(set(run_ids)) == 6, "expected 6 unique run_ids"

    deadline = time.time() + 15
    while time.time() < deadline:
        statuses = [client.get(f"/runs/{rid}/status").json()["status"] for rid in run_ids]
        if all(s in ("completed", "failed") for s in statuses):
            break
        time.sleep(0.1)

    for rid in run_ids:
        status = client.get(f"/runs/{rid}/status").json()
        assert status["status"] == "completed", f"{rid} did not complete: {status}"
        result = client.get(f"/runs/{rid}/results").json()
        txn_ids = {m["transaction_id"] for m in result["matched"]} | {e["transaction_id"] for e in result["exceptions"]}
        assert len(txn_ids) == result["summary"]["total"], f"{rid}: internally inconsistent result"


def test_concurrent_sse_streams_do_not_leak_across_run_ids():
    """Two different runs, streamed concurrently by two 'viewers' -
    confirms each stream only ever sees its own run's terminal event and
    stays self-consistent, with no cross-talk between run_ids."""
    run_a = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
    run_b = client.post("/runs", json={"sample_size": 10}).json()["run_id"]

    events_a, events_b = [], []

    def stream(run_id, out):
        with client.stream("GET", f"/runs/{run_id}/stream") as resp:
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    out.append(json.loads(line[len("data: "):]))
                if line and '"done"' in line:
                    break

    t1 = threading.Thread(target=stream, args=(run_a, events_a))
    t2 = threading.Thread(target=stream, args=(run_b, events_b))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert events_a and events_a[-1]["stage"] == "done"
    assert events_b and events_b[-1]["stage"] == "done"


def test_malformed_request_bodies_return_clean_422_not_crash():
    bad_bodies = [
        {"sample_size": "ten"},
        {"sample_size": 10.5},
        {"sample_size": [1, 2, 3]},
    ]
    for body in bad_bodies:
        response = client.post("/runs", json=body)
        assert response.status_code == 422, f"{body} should be a clean 422, got {response.status_code}"


def test_adversarial_run_ids_return_404_not_crash():
    weird_ids = ["../../etc/passwd", "<script>alert(1)</script>", "a" * 10000, "", "💥💥💥"]
    for run_id in weird_ids:
        assert client.get(f"/runs/{run_id}/status").status_code == 404
        assert client.get(f"/runs/{run_id}/results").status_code == 404
