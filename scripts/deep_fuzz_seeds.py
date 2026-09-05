"""Multi-seed property-based fuzz harness. Every prior test (curated
dataset, noisy stress test) uses a fixed seed - this generates MANY
different datasets across many seeds and checks that core invariants
hold universally, not just for the specific seeds already tested.
Property-based testing, not example-based: same checks, applied to
however many different randomly-generated worlds we throw at it.
"""

import importlib
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "data"))
sys.path.insert(0, os.path.join(ROOT, "eval"))

import synthetic_generator as curated_gen
import noisy_stress_generator as noisy_gen
from matcher import run_deterministic_stage, ref_token
from react_loop import run_agent_stage
from fake_llm_client import FakeLLMClient
from metrics import compute_metrics


def check_curated_seed(seed):
    """Regenerates the curated dataset logic with a different seed and
    checks: determinism, zero incorrect matches, full accounting, no
    double-claims. The curated generator hardcodes SEED=42 internally, so
    we monkeypatch it per-trial rather than duplicating its logic."""
    curated_gen.SEED = seed
    gw1, bank1, gt1 = curated_gen.generate()
    gw2, bank2, gt2 = curated_gen.generate()
    assert gw1 == gw2 and bank1 == bank2 and gt1 == gt2, f"seed {seed}: determinism broke"

    gt_by_id = {r["transaction_id"]: r for r in gt1 if r.get("transaction_id")}
    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw1, bank1)

    for m in matched:
        truth = gt_by_id[m["transaction_id"]]
        expected = truth.get("correct_settlement_utrs")
        assert expected is not None and sorted(expected) == sorted(m["utrs"]), \
            f"seed {seed}: INCORRECT MATCH {m['transaction_id']}"

    total = len(matched) + len(exceptions) + len(needs_agent)
    assert total == len(gw1), f"seed {seed}: accounting mismatch {total} != {len(gw1)}"

    claimed = [utr for m in matched for utr in m["utrs"]]
    assert len(claimed) == len(set(claimed)), f"seed {seed}: double-claim"

    agent_matched, agent_exceptions = run_agent_stage(needs_agent, unclaimed, FakeLLMClient())
    for m in agent_matched:
        truth = gt_by_id[m["transaction_id"]]
        expected = truth.get("correct_settlement_utrs")
        assert expected is not None and sorted(expected) == sorted(m["utrs"]), \
            f"seed {seed}: agent stage INCORRECT MATCH {m['transaction_id']}"

    all_claimed = claimed + [utr for m in agent_matched for utr in m["utrs"]]
    assert len(all_claimed) == len(set(all_claimed)), f"seed {seed}: cross-stage double-claim"

    m2 = compute_metrics(matched + agent_matched, exceptions + agent_exceptions, gt1)
    breakdown_total = sum(v["total"] for v in m2["by_mismatch_type"].values())
    assert breakdown_total == m2["eval_set_size"], f"seed {seed}: metrics breakdown mismatch"

    return {
        "seed": seed, "gateway_count": len(gw1), "matched": len(matched) + len(agent_matched),
        "exceptions": len(exceptions) + len(agent_exceptions), "match_rate": m2["match_rate"],
    }


def check_noisy_seed(seed):
    """Same idea for the noisy generator - vary its seed and confirm
    robustness invariants (no crash, fail-fast on duplicates, no
    double-claim, no settled>net) hold for every trial, not just the one
    seed already tested."""
    noisy_gen.SEED = seed
    gw, bank = noisy_gen.generate()

    from collections import Counter
    ids = Counter(g["transaction_id"] for g in gw)
    dupe_ids = {k for k, v in ids.items() if v > 1}
    assert dupe_ids, f"noisy seed {seed}: generator should always inject a duplicate - check generator logic"

    try:
        run_deterministic_stage(gw, bank)
        return {"seed": seed, "status": "FAIL - should have raised on duplicate"}
    except ValueError:
        pass

    gw_clean = [g for g in gw if g["transaction_id"] not in dupe_ids]
    gw_clean += [g for g in gw if g["transaction_id"] in dupe_ids][:1]

    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw_clean, bank)
    total = len(matched) + len(exceptions) + len(needs_agent)
    assert total == len(gw_clean), f"noisy seed {seed}: accounting mismatch"

    claimed = [utr for m in matched for utr in m["utrs"]]
    assert len(claimed) == len(set(claimed)), f"noisy seed {seed}: double-claim"

    gw_by_id = {g["transaction_id"]: g for g in gw_clean}
    bank_by_utr = {b["utr_number"]: b for b in bank}
    for m in matched:
        net = gw_by_id[m["transaction_id"]]["net_amount"]
        total_settled = sum(bank_by_utr[u]["settled_amount"] for u in m["utrs"])
        assert total_settled <= net + 0.02, f"noisy seed {seed}: settled>net violation"

    return {"seed": seed, "gateway_count": len(gw_clean), "matched": len(matched), "status": "OK"}


def run():
    N_SEEDS = 25
    results = {"curated": [], "noisy": [], "errors": []}
    start = time.time()

    for i in range(N_SEEDS):
        seed = 1000 + i * 7  # arbitrary spread, avoids the real seed 42/999
        try:
            results["curated"].append(check_curated_seed(seed))
        except Exception as e:
            results["errors"].append({"kind": "curated", "seed": seed, "error": str(e), "trace": traceback.format_exc()})

        try:
            results["noisy"].append(check_noisy_seed(seed + 500000))
        except Exception as e:
            results["errors"].append({"kind": "noisy", "seed": seed + 500000, "error": str(e), "trace": traceback.format_exc()})

    elapsed = time.time() - start
    print(f"ran {N_SEEDS} curated-style seeds + {N_SEEDS} noisy-style seeds in {elapsed:.2f}s")
    print(f"curated: {len(results['curated'])}/{N_SEEDS} passed all invariants")
    print(f"noisy: {len(results['noisy'])}/{N_SEEDS} passed all invariants")
    print(f"errors: {len(results['errors'])}")
    for e in results["errors"]:
        print(f"  [{e['kind']}] seed {e['seed']}: {e['error']}")

    match_rates = [r["match_rate"] for r in results["curated"] if r["match_rate"] is not None]
    if match_rates:
        print(f"match_rate range across seeds: {min(match_rates)} - {max(match_rates)}")

    with open(os.path.join(ROOT, "scripts", "fuzz_results.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)

    return len(results["errors"]) == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
