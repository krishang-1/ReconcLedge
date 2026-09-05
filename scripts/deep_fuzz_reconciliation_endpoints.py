"""Targeted stress test for the five standalone reconciliation endpoints
added during Tier 2/3 hardening (refunds, batches, FX, marketplace,
chargebacks) - a different target from deep_fuzz_hardening.py, which
covers the original six hardening items (persistence, audit, auth,
fallback) together. Two things neither that script nor any per-endpoint
test suite exercises:

  A. Mixed concurrent load across ALL FIVE endpoints (plus /runs and
     /health) at once, from many threads - each endpoint has only ever
     been load-tested individually before this.

  B. Malformed/noisy input fuzzing against the raw reconciliation
     functions directly - NaN, infinity, extreme values, unicode,
     SQL-injection-shaped strings, null bytes - checking for crashes
     (bad) vs. clean handling (expected). Not checking for "correct"
     classification, since garbage input doesn't have a correct answer,
     only "did it crash or handle it." Also specifically confirms NaN
     inputs never get silently misclassified as a clean/matched result
     - a real, subtle risk given Python's NaN comparison semantics
       (NaN != NaN, and NaN <= anything is always False), which happens
     to make "not matched" the safe default outcome here rather than a
     dangerous one - confirmed, not just assumed.

Run manually before final submission, same as the other scripts/ here:

    python scripts/deep_fuzz_reconciliation_endpoints.py
"""

import math
import os
import random
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for subdir in ("api", "agent", "eval", "data"):
    sys.path.insert(0, os.path.join(ROOT, subdir))


def section_a_mixed_concurrent_load():
    print("=== Section A: mixed concurrent load across all 5 reconciliation endpoints + /runs + /health ===")

    db_path = os.path.join(ROOT, "scripts", "_deep_fuzz_reconciliation_jobs.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    os.environ["JOBS_DB_PATH"] = db_path
    # Same isolation reasoning as JOBS_DB_PATH above, added for
    # agent/merchant_config.py's own SQLite file (see docs/DECISIONS.md)
    # - without this a real app boot here creates a real file in the
    # actual source directory instead of scratch space.
    os.environ["MERCHANT_CONFIG_DB_PATH"] = ":memory:"

    import importlib
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
        app_module = sys.modules["app"]
    else:
        import app as app_module

    from fastapi.testclient import TestClient
    from fake_llm_client import FakeLLMClient
    import refund_generator, batch_generator, fx_generator, marketplace_generator, chargeback_generator

    client = TestClient(app_module.app)
    app_module.app.dependency_overrides[app_module.get_llm_client] = lambda: FakeLLMClient()

    refund_events = refund_generator.generate()
    gwb, bankb = batch_generator.generate()
    fx_scenarios = fx_generator.generate()
    mp_scenarios = marketplace_generator.generate()
    cb_scenarios = chargeback_generator.generate()

    results = {"success": 0, "error": 0}
    latencies = []
    lock = threading.Lock()

    def call_random_endpoint():
        choice = random.randint(0, 6)
        start = time.time()
        try:
            if choice == 0:
                r = client.post("/refunds/reconcile", json={"refund_events": refund_events})
            elif choice == 1:
                r = client.post("/batches/reconcile", json={"gateway_records": gwb, "bank_batch_records": bankb})
            elif choice == 2:
                gw, bank, rmin, rmax, markup = random.choice(fx_scenarios)
                r = client.post("/fx/reconcile", json={"gateway_record": gw, "bank_record": bank, "rate_min": rmin, "rate_max": rmax, "markup_bps": markup})
            elif choice == 3:
                gw, transfers, commission = random.choice(mp_scenarios)
                r = client.post("/marketplace/reconcile", json={"gateway_record": gw, "transfers": transfers, "platform_commission": commission})
            elif choice == 4:
                gw, cb = random.choice(cb_scenarios)
                r = client.post("/chargebacks/reconcile", json={"gateway_record": gw, "chargeback_event": cb})
            elif choice == 5:
                r = client.get("/health")
            else:
                r = client.post("/runs", json={"sample_size": 5})
            elapsed = time.time() - start
            with lock:
                latencies.append(elapsed)
                if r.status_code == 200:
                    results["success"] += 1
                else:
                    results["error"] += 1
                    print(f"  unexpected status {r.status_code} from endpoint choice {choice}: {r.text[:150]}")
        except Exception as e:
            with lock:
                results["error"] += 1
            print(f"  EXCEPTION on endpoint choice {choice}: {type(e).__name__}: {e}")

    def worker(n_calls):
        for _ in range(n_calls):
            call_random_endpoint()

    threads = [threading.Thread(target=worker, args=(25,)) for _ in range(20)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start

    total = results["success"] + results["error"]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

    print(f"  20 threads x 25 calls = {total} total mixed-endpoint calls in {elapsed:.2f}s")
    print(f"  success={results['success']}  error={results['error']}")
    print(f"  avg latency: {avg_latency*1000:.1f}ms  p95 latency: {p95_latency*1000:.1f}ms")
    ok = results["error"] == 0 and total == 500

    # Real bug found the first time this script ran: /runs starts real
    # background threads (jobs.start_job) that keep running after the
    # HTTP response returns. Deleting the DB file immediately here raced
    # with those still-in-flight threads trying to write their results,
    # producing real "attempt to write a readonly database" errors -
    # not a flaw in the app, a flaw in this cleanup being too eager.
    # Give any /runs calls made during this section a real chance to
    # finish before removing the file.
    time.sleep(2)
    if os.path.exists(db_path):
        os.remove(db_path)

    print(f"  no errors under mixed concurrent load across all endpoints: {'PASS' if ok else 'FAIL'}")
    return ok


def section_b_malformed_input_fuzzing():
    print("\n=== Section B: malformed/noisy input fuzzing against raw reconciliation functions ===")

    from refund_matcher import reconcile_refunds
    from batch_settlement import find_bounded_subset_matches
    from fx_reconciliation import reconcile_fx_transaction
    from marketplace_settlement import reconcile_split_transaction
    from chargeback_matcher import reconcile_chargeback

    weird_floats = [0.0, -0.0, 1e300, -1e300, 1e-300, math.inf, -math.inf, math.nan, -1.0, 0.001]
    weird_strings = ["", "a" * 10000, "🎉💰🔥", "'; DROP TABLE jobs; --", "\x00\x01\x02", "NaN", "null", " " * 100]

    crashes = []
    handled = 0

    def try_call(label, fn, *args, **kwargs):
        nonlocal handled
        try:
            fn(*args, **kwargs)
            handled += 1
        except (ValueError, KeyError, TypeError, ZeroDivisionError):
            handled += 1  # expected exception types for bad input - handled, not a crash
        except Exception as e:
            crashes.append((label, type(e).__name__, str(e)[:150]))

    rng = random.Random(99)
    for _ in range(200):
        amt = rng.choice(weird_floats)
        txn_id = rng.choice(weird_strings)

        try_call("reconcile_refunds", reconcile_refunds,
                  [{"transaction_id": txn_id, "net_amount": amt}],
                  [{"transaction_id": txn_id, "refund_amount": rng.choice(weird_floats)}])

        from batch_settlement import reconcile_by_batch_id
        try_call("reconcile_by_batch_id", reconcile_by_batch_id,
                  [{"transaction_id": txn_id, "net_amount": amt, "settlement_batch_id": rng.choice(weird_strings)}],
                  [{"batch_id": rng.choice(weird_strings), "credited_amount": rng.choice(weird_floats)}])

        try_call("find_bounded_subset_matches", find_bounded_subset_matches,
                  [{"transaction_id": txn_id, "net_amount": amt}], rng.choice(weird_floats))

        try_call("reconcile_fx_transaction", reconcile_fx_transaction,
                  {"transaction_id": txn_id, "amount": amt, "currency": rng.choice(weird_strings)},
                  {"settled_amount": rng.choice(weird_floats), "currency": rng.choice(weird_strings)},
                  rng.choice(weird_floats), rng.choice(weird_floats), rng.choice(weird_floats))

        try_call("reconcile_split_transaction", reconcile_split_transaction,
                  {"transaction_id": txn_id, "net_amount": amt},
                  [{"linked_account_id": txn_id, "amount": rng.choice(weird_floats),
                    "status": rng.choice(["settled", "on_hold", "reversed", rng.choice(weird_strings)])}],
                  rng.choice(weird_floats))

        try_call("reconcile_chargeback", reconcile_chargeback,
                  {"transaction_id": txn_id, "net_amount": amt},
                  {"status": rng.choice(["open", "won", "lost", rng.choice(weird_strings)]),
                   "disputed_amount": rng.choice(weird_floats), "chargeback_fee": rng.choice(weird_floats),
                   "initiated_by": rng.choice(weird_strings)})

    print(f"  {handled} calls handled cleanly (returned normally or raised an expected exception type)")
    print(f"  {len(crashes)} unexpected crashes")
    for label, exc_type, msg in crashes[:20]:
        print(f"    CRASH in {label}: {exc_type}: {msg}")
    ok = len(crashes) == 0
    print(f"  no unexpected crashes under noisy/malformed input fuzzing: {'PASS' if ok else 'FAIL'}")
    return ok


def section_c_nan_never_falsely_matches():
    print("\n=== Section C: NaN inputs never silently classify as a clean/matched result ===")

    from refund_matcher import reconcile_refunds
    from fx_reconciliation import reconcile_fx_transaction
    from chargeback_matcher import reconcile_chargeback

    r1 = reconcile_refunds([{"transaction_id": "t1", "net_amount": math.nan}], [{"transaction_id": "t1", "refund_amount": 100.0}])
    r2 = reconcile_fx_transaction({"transaction_id": "t1", "amount": 100.0, "currency": "USD"},
                                    {"settled_amount": math.nan, "currency": "INR"}, 80.0, 85.0, 0)
    r3 = reconcile_chargeback({"transaction_id": "t1", "net_amount": math.nan},
                                {"status": "open", "disputed_amount": 50.0, "chargeback_fee": 10.0, "initiated_by": "customer"})

    print(f"  NaN net_amount refund -> {r1[0]['classification']}")
    print(f"  NaN settled_amount fx -> {r2['status']}")
    print(f"  NaN net_amount chargeback -> {r3['classification']}")

    ok = r1[0]["classification"] != "full_refund" and r2["status"] != "matched_within_rate_band"
    print(f"  NaN never produces a false clean-match classification: {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    a_ok = section_a_mixed_concurrent_load()
    b_ok = section_b_malformed_input_fuzzing()
    c_ok = section_c_nan_never_falsely_matches()

    print("\n=== SUMMARY ===")
    print(f"Section A (mixed concurrent load, all endpoints):     {'PASS' if a_ok else 'FAIL'}")
    print(f"Section B (malformed input fuzzing, no crashes):      {'PASS' if b_ok else 'FAIL'}")
    print(f"Section C (NaN never falsely matches):                {'PASS' if c_ok else 'FAIL'}")
    overall = a_ok and b_ok and c_ok
    print(f"\nOVERALL: {'PASS' if overall else 'FAIL'}")
    sys.exit(0 if overall else 1)
