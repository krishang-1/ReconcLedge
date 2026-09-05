"""Heavier variance on top of tests/test_concurrency.py: more threads,
more iterations, and a sustained sequential load test (many runs in
succession) checking for resource leaks or degradation in the in-memory
job store, which the smaller test suite never pushed hard enough to
reveal (only ever created a handful of jobs per test).
"""

import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "eval"))

from fastapi.testclient import TestClient
import app as api_app
from fake_llm_client import FakeLLMClient

client = TestClient(api_app.app)
api_app.app.dependency_overrides[api_app.get_llm_client] = lambda: FakeLLMClient()


def heavy_client_concurrency():
    """20 threads x 30 calls each against the shared client, with real
    jitter and a higher 402-injection rate than the permanent test suite
    uses, specifically to push harder on the retry-budget fix."""
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
        time.sleep(random.uniform(0, 0.005))
        body = json.loads(data)
        with lock:
            call_log.append(body.get("max_tokens"))
        if random.random() < 0.5:
            shortfall = body["max_tokens"] - 30
            return FakeResp(402, {"error": {"message": f"You requested up to {body['max_tokens']} tokens, but can only afford {shortfall}.", "code": 402}})
        return FakeResp(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    original_post = llm_client.requests.post
    llm_client.requests.post = fake_post
    try:
        test_client = llm_client.OpenRouterClient(api_key="test")
        errors = []

        def worker(n):
            try:
                for _ in range(30):
                    test_client.chat([{"role": "user", "content": f"msg{n}"}])
            except Exception as e:
                errors.append((n, str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start

        print(f"heavy concurrency: 20 threads x 30 calls = {len(call_log)} total calls in {elapsed:.2f}s")
        print(f"  errors: {len(errors)}")
        for e in errors[:10]:
            print(f"    {e}")
        print(f"  final max_tokens: {test_client.max_tokens} (sane: {test_client.max_tokens > 0})")
        return len(errors) == 0
    finally:
        llm_client.requests.post = original_post


def sustained_api_load():
    """50 sequential API runs in a row - checks for job-store growth
    issues or degrading response times, not something a handful of test
    runs would reveal."""
    timings = []
    errors = []
    status = None
    for i in range(50):
        start = time.time()
        try:
            run_id = client.post("/runs", json={"sample_size": 10}).json()["run_id"]
            deadline = time.time() + 10
            while time.time() < deadline:
                status = client.get(f"/runs/{run_id}/status").json()["status"]
                if status in ("completed", "failed"):
                    break
                time.sleep(0.02)
            assert status == "completed", f"run {i} ended as {status}"
            timings.append(time.time() - start)
        except Exception as e:
            errors.append((i, str(e)))

    all_runs = client.get("/runs").json()
    print(f"sustained load: 50 sequential runs, {len(errors)} errors")
    print(f"  job store now holds {len(all_runs)} total runs (across this whole test session)")
    if timings:
        print(f"  timing: first={timings[0]:.3f}s last={timings[-1]:.3f}s avg={sum(timings)/len(timings):.3f}s")
        degraded = timings[-1] > timings[0] * 5
        print(f"  meaningful degradation over 50 runs: {degraded}")
    for e in errors[:10]:
        print(f"    {e}")
    return len(errors) == 0


if __name__ == "__main__":
    ok1 = heavy_client_concurrency()
    print()
    ok2 = sustained_api_load()
    sys.exit(0 if (ok1 and ok2) else 1)
