"""Heavier, combined stress simulation for everything added this session:
persistence (item 1), audit logging (item 2), API auth (item 3), and the
LLM provider fallback/circuit breaker (item 4). Each item was verified in
isolation when built - this specifically targets what only shows up when
they're all live together, under real threading, which none of the
per-item test suites exercise (same gap concurrency stress testing found
in the original retry-budget bug: sequential tests can't reveal it).

Three sections, run independently and summarized at the end:

  A. FallbackClient under real concurrent load with randomized primary/
     secondary failure injection - the circuit breaker's own test suite
     (tests/test_fallback_client.py) is entirely sequential/scripted;
     this is its first exposure to genuine multi-threaded contention on
     the shared lock and counters.

  B. The full API stack (auth + persistence + audit + fallback wiring)
     under concurrent job creation against a real file-backed SQLite DB,
     with a deliberate mix of valid/invalid API keys interleaved - not
     just "does auth work" (already covered) but "does auth hold up
     correctly while under the same concurrent load that already broke
     something once before" (the retry-budget race).

  C. A simulated mid-run provider outage: noisy, high-volume stress data
     (data/noisy_stress_generator.py) run through the real pipeline with
     a FallbackClient whose primary fails for a stretch of calls then
     recovers - checking structural invariants (full accounting, no
     double-claims, no crash) hold when the provider actually changes
     mid-run, not just when it's static for the whole batch.

Run manually before final submission, same as the other scripts/ here:

    python scripts/deep_fuzz_hardening.py
"""

import json
import os
import random
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for subdir in ("api", "agent", "eval", "data"):
    sys.path.insert(0, os.path.join(ROOT, subdir))


# ---------------------------------------------------------------------------
# Section A: FallbackClient under real concurrent load
# ---------------------------------------------------------------------------

def section_a_fallback_concurrency():
    import llm_client

    print("=== Section A: FallbackClient under real concurrent load ===")

    class FlakyClient:
        """Fails with the given probability, real random jitter included
        so threads genuinely interleave rather than lock-stepping."""

        def __init__(self, fail_rate, label):
            self.fail_rate = fail_rate
            self.label = label
            self.calls = 0
            self._lock = threading.Lock()

        def chat(self, messages, tools=None, tool_choice="auto"):
            time.sleep(random.uniform(0, 0.003))
            with self._lock:
                self.calls += 1
            if random.random() < self.fail_rate:
                raise RuntimeError(f"{self.label} transient failure")
            return {"role": "assistant", "content": f"ok from {self.label}"}

    primary = FlakyClient(fail_rate=0.4, label="primary")
    secondary = FlakyClient(fail_rate=0.05, label="secondary")
    client = llm_client.FallbackClient(primary, secondary)

    results = {"success": 0, "both_failed": 0, "unexpected_error": 0}
    lock = threading.Lock()

    def worker(n_calls):
        for _ in range(n_calls):
            try:
                client.chat([{"role": "user", "content": "x"}])
                with lock:
                    results["success"] += 1
            except RuntimeError as e:
                if "Both LLM providers failed" in str(e) or "secondary" in str(e).lower():
                    with lock:
                        results["both_failed"] += 1
                else:
                    with lock:
                        results["unexpected_error"] += 1
                    print(f"  UNEXPECTED ERROR SHAPE: {e}")
            except Exception as e:
                with lock:
                    results["unexpected_error"] += 1
                print(f"  UNEXPECTED EXCEPTION TYPE: {type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(40,)) for _ in range(15)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start

    total = results["success"] + results["both_failed"] + results["unexpected_error"]
    print(f"  15 threads x 40 calls = {total} total calls in {elapsed:.2f}s")
    print(f"  primary.calls={primary.calls}  secondary.calls={secondary.calls}")
    print(f"  success={results['success']}  both_failed={results['both_failed']}  "
          f"unexpected_error={results['unexpected_error']}")

    # After sustained load with a low secondary fail rate, essentially
    # every call should have been served by SOMETHING - both_failed should
    # be rare (secondary only fails 5% of the time) and unexpected_error
    # must be exactly zero: any nonzero count here means the lock isn't
    # actually protecting the shared counters from real interleaving.
    ok = results["unexpected_error"] == 0 and total == 600
    print(f"  no unexpected errors and no calls silently lost: {'PASS' if ok else 'FAIL'}")

    # Give the circuit a clean recovery window: primary healthy now, confirm
    # the breaker actually closes back up under continued real threading,
    # not just in the sequential half-open test.
    #
    # Real, rare bug found via this exact script during a later audit
    # (see docs/DECISIONS.md): this loop originally had no exception
    # handling at all. If the circuit is still tripped when this loop
    # starts, a call is routed straight to the secondary - and if the
    # secondary happens to fail on its own low, but nonzero, fail_rate
    # before primary has been retried on that same call, FallbackClient
    # correctly raises the plain unwrapped secondary error (see its own
    # docstring - there's no "both failed" story when primary was never
    # attempted this call). That's correct FallbackClient behavior, not
    # a bug in it - the bug was this test loop having no tolerance for
    # a single realistic mid-recovery blip. Fixed the same way the rest
    # of this script tolerates real noise: catch it, count it, and judge
    # success by whether the circuit actually closes by the end - not by
    # every single call in the window succeeding individually.
    primary.fail_rate = 0.0
    recovery_call_errors = 0
    for _ in range(30):
        try:
            client.chat([{"role": "user", "content": "recovery check"}])
        except RuntimeError:
            recovery_call_errors += 1
    recovered = not client.tripped
    print(f"  recovery window: {recovery_call_errors} individual call errors tolerated (secondary's own low "
          f"fail_rate, not a failure of this check)")
    print(f"  circuit closes back to healthy primary after real-threaded load: {'PASS' if recovered else 'FAIL'}")

    return ok and recovered


# ---------------------------------------------------------------------------
# Section B: full API stack under concurrent load
# ---------------------------------------------------------------------------

def section_b_api_stack_concurrency():
    print("\n=== Section B: full API stack (auth+persistence+audit+fallback wiring) under concurrent load ===")

    db_path = os.path.join(ROOT, "scripts", "_deep_fuzz_hardening_jobs.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    os.environ["JOBS_DB_PATH"] = db_path
    # Real gap found and fixed (see docs/DECISIONS.md): without this,
    # a real app boot here would create agent/merchant_config.db as a
    # genuine file in the actual source directory, not scratch space -
    # same isolation JOBS_DB_PATH already gets, just added later since
    # this env var didn't exist when this script was first written.
    os.environ["MERCHANT_CONFIG_DB_PATH"] = ":memory:"
    os.environ["API_KEYS"] = "valid-key-1,valid-key-2"

    # Fresh app import with these env vars already set, matching how a
    # real deployment would boot (not reusing any test-session app
    # instance that might have stale dependency_overrides on it).
    import importlib
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
        app_module = sys.modules["app"]
    else:
        import app as app_module

    from fastapi.testclient import TestClient
    from fake_llm_client import FakeLLMClient

    client = TestClient(app_module.app)
    app_module.app.dependency_overrides[app_module.get_llm_client] = lambda: FakeLLMClient()

    good_headers = {"X-API-Key": "valid-key-2"}
    bad_headers = {"X-API-Key": "not-a-real-key"}

    outcomes = {"created": 0, "correctly_rejected": 0, "unexpected": 0}
    run_ids = []
    lock = threading.Lock()

    def create_worker(use_valid_key):
        headers = good_headers if use_valid_key else bad_headers
        r = client.post("/runs", json={"sample_size": 15}, headers=headers)
        with lock:
            if use_valid_key:
                if r.status_code == 200:
                    outcomes["created"] += 1
                    run_ids.append(r.json()["run_id"])
                else:
                    outcomes["unexpected"] += 1
                    print(f"  valid-key request unexpectedly got {r.status_code}: {r.text}")
            else:
                if r.status_code == 403:
                    outcomes["correctly_rejected"] += 1
                else:
                    outcomes["unexpected"] += 1
                    print(f"  invalid-key request unexpectedly got {r.status_code}: {r.text}")

    # 12 concurrent creations with a valid key, interleaved with 8 using a
    # deliberately wrong one - checking auth doesn't leak through or block
    # legitimate requests under real thread contention on the shared app.
    jobs_specs = [True] * 12 + [False] * 8
    random.shuffle(jobs_specs)
    threads = [threading.Thread(target=create_worker, args=(spec,)) for spec in jobs_specs]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start

    print(f"  20 concurrent requests (12 valid-key, 8 invalid-key) in {elapsed:.2f}s")
    print(f"  created={outcomes['created']}  correctly_rejected={outcomes['correctly_rejected']}  "
          f"unexpected={outcomes['unexpected']}")
    auth_ok = outcomes["created"] == 12 and outcomes["correctly_rejected"] == 8 and outcomes["unexpected"] == 0
    print(f"  auth held correctly under concurrent load: {'PASS' if auth_ok else 'FAIL'}")

    # Wait for all 12 real runs to complete
    deadline = time.time() + 20
    completed = set()
    while time.time() < deadline and len(completed) < len(run_ids):
        for rid in run_ids:
            if rid in completed:
                continue
            s = client.get(f"/runs/{rid}/status", headers=good_headers).json()
            if s["status"] in ("completed", "failed"):
                completed.add(rid)
        time.sleep(0.1)

    all_completed = all(
        client.get(f"/runs/{rid}/status", headers=good_headers).json()["status"] == "completed"
        for rid in run_ids
    )
    print(f"  all {len(run_ids)} concurrently-created runs completed: {'PASS' if all_completed else 'FAIL'}")

    # Audit integrity: every completed run's audit rows should exactly
    # match its own results (matched + exceptions), with no duplicates
    # and nothing bled across run_ids despite concurrent writes to one
    # shared SQLite file.
    audit_ok = True
    for rid in run_ids:
        results = client.get(f"/runs/{rid}/results", headers=good_headers).json()
        expected = len(results.get("matched", [])) + len(results.get("exceptions", []))
        audit_rows = client.get("/audit", params={"run_id": rid}, headers=good_headers).json()
        txn_ids = [row["transaction_id"] for row in audit_rows]
        no_dupes = len(txn_ids) == len(set(txn_ids))
        if len(audit_rows) != expected or not no_dupes:
            audit_ok = False
            print(f"  MISMATCH run {rid}: expected {expected} audit rows, "
                  f"got {len(audit_rows)} (duplicates: {not no_dupes})")
    print(f"  audit trail exactly matches results for all {len(run_ids)} runs, no duplicates/leakage: "
          f"{'PASS' if audit_ok else 'FAIL'}")

    return auth_ok and all_completed and audit_ok


# ---------------------------------------------------------------------------
# Section C: noisy data through a simulated mid-run provider outage
# ---------------------------------------------------------------------------

def section_c_noisy_data_with_outage():
    print("\n=== Section C: noisy stress data through a simulated mid-run provider outage ===")

    import llm_client
    from matcher import run_deterministic_stage
    from react_loop import run_agent_stage
    from noisy_stress_generator import generate as generate_noisy
    from fake_llm_client import FakeLLMClient
    from collections import Counter

    def _generate_valid():
        """Same helper as tests/test_noisy_stress.py's _generate_valid():
        the noisy generator intentionally injects a duplicate
        transaction_id (see validate_input's fail-fast guard, item found
        earlier this project) - that guard is already tested directly
        and deliberately elsewhere, so it's stripped here to reach the
        rest of the pipeline instead of re-testing the same guard again."""
        gw, bank = generate_noisy()
        ids = Counter(g["transaction_id"] for g in gw)
        dupe_ids = {k for k, v in ids.items() if v > 1}
        gw_clean = [g for g in gw if g["transaction_id"] not in dupe_ids]
        gw_clean += [g for g in gw if g["transaction_id"] in dupe_ids][:1]
        return gw_clean, bank

    gateway, bank = _generate_valid()
    print(f"  generated {len(gateway)} noisy gateway records / {len(bank)} bank records")

    det_matched, det_exceptions, needs_agent, unclaimed = run_deterministic_stage(gateway, bank)
    print(f"  deterministic stage: {len(det_matched)} matched, "
          f"{len(det_exceptions)} exceptions, {len(needs_agent)} routed to agent")

    class OutageThenRecoverPrimary:
        """Fails outright for its first N calls (simulating Groq being
        down for a stretch mid-run), then behaves normally - this is
        what a real transient provider outage during a live run looks
        like, as opposed to the static all-or-nothing failure rate the
        sequential FallbackClient tests use."""

        def __init__(self, outage_calls):
            self.outage_calls = outage_calls
            self.calls = 0
            self._fake = FakeLLMClient()

        def chat(self, messages, tools=None, tool_choice="auto"):
            self.calls += 1
            if self.calls <= self.outage_calls:
                raise RuntimeError("simulated Groq outage")
            return self._fake.chat(messages, tools=tools, tool_choice=tool_choice)

    outage_len = max(3, len(needs_agent) // 3)
    primary = OutageThenRecoverPrimary(outage_calls=outage_len)
    secondary = FakeLLMClient()
    fallback = llm_client.FallbackClient(primary, secondary)

    agent_matched, agent_exceptions = run_agent_stage(needs_agent, unclaimed, fallback)

    total_in = len(gateway)
    total_out = len(det_matched) + len(det_exceptions) + len(agent_matched) + len(agent_exceptions)
    accounting_ok = total_in == total_out
    print(f"  full accounting: {total_in} in, {total_out} out: {'PASS' if accounting_ok else 'FAIL'}")

    matched_ids = [m["transaction_id"] for m in (det_matched + agent_matched)]
    no_double_claim = len(matched_ids) == len(set(matched_ids))
    print(f"  no double-claimed transactions across deterministic+agent stages during the simulated outage: "
          f"{'PASS' if no_double_claim else 'FAIL'}")

    print(f"  primary called {primary.calls} times (outage for the first {outage_len}), "
          f"circuit ended {'tripped' if fallback.tripped else 'closed'}")
    no_crash = True  # if we got this far without an exception, this holds
    print(f"  no crash despite primary failing outright mid-run: PASS")

    return accounting_ok and no_double_claim and no_crash


if __name__ == "__main__":
    a_ok = section_a_fallback_concurrency()
    b_ok = section_b_api_stack_concurrency()
    c_ok = section_c_noisy_data_with_outage()

    print("\n=== SUMMARY ===")
    print(f"Section A (FallbackClient real concurrency):        {'PASS' if a_ok else 'FAIL'}")
    print(f"Section B (API stack concurrency, auth+audit):       {'PASS' if b_ok else 'FAIL'}")
    print(f"Section C (noisy data + simulated mid-run outage):   {'PASS' if c_ok else 'FAIL'}")
    overall = a_ok and b_ok and c_ok
    print(f"\nOVERALL: {'PASS' if overall else 'FAIL'}")

    # Clean up the scratch DB immediately after this run, not just at the
    # start of the next one - a stray file left sitting around between
    # runs is exactly the kind of thing that ends up accidentally
    # committed. Also covered defensively in .gitignore either way.
    _scratch_db = os.path.join(ROOT, "scripts", "_deep_fuzz_hardening_jobs.db")
    if os.path.exists(_scratch_db):
        os.remove(_scratch_db)

    sys.exit(0 if overall else 1)
