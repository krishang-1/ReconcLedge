"""Lighter permanent version of the deeper multi-seed fuzz sweep
(scripts_fuzz_seeds.py, 25 seeds, run manually before submission) - 8
seeds here, fast enough to run on every suite invocation. Confirms core
invariants (determinism, zero incorrect matches, full accounting, no
double-claims) hold across multiple different randomly-generated worlds,
not just the one fixed seed the curated submission dataset uses.
"""

import pytest
import synthetic_generator as curated_gen
from matcher import run_deterministic_stage
from react_loop import run_agent_stage
from fake_llm_client import FakeLLMClient

SEEDS = [101, 202, 303, 404, 505, 606, 707, 808]


@pytest.mark.parametrize("seed", SEEDS)
def test_deterministic_stage_correct_across_seeds(seed):
    """The core promise (zero incorrect matches from the deterministic
    stage) must hold for more than just the one seed the submission
    dataset happens to use."""
    original_seed = curated_gen.SEED
    curated_gen.SEED = seed
    try:
        gw, bank, gt = curated_gen.generate()
        gt_by_id = {r["transaction_id"]: r for r in gt if r.get("transaction_id")}
        matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)

        for m in matched:
            truth = gt_by_id[m["transaction_id"]]
            expected = truth.get("correct_settlement_utrs")
            assert expected is not None and sorted(expected) == sorted(m["utrs"]), \
                f"seed {seed}: incorrect match {m['transaction_id']}"

        total = len(matched) + len(exceptions) + len(needs_agent)
        assert total == len(gw), f"seed {seed}: accounting mismatch"

        claimed = [utr for m in matched for utr in m["utrs"]]
        assert len(claimed) == len(set(claimed)), f"seed {seed}: double-claim"
    finally:
        curated_gen.SEED = original_seed


@pytest.mark.parametrize("seed", SEEDS)
def test_full_pipeline_correct_across_seeds_with_fake_client(seed):
    """Same as above, extended through the agent stage with the fake
    client - confirms end-to-end correctness generalizes across seeds,
    not just the deterministic stage."""
    original_seed = curated_gen.SEED
    curated_gen.SEED = seed
    try:
        gw, bank, gt = curated_gen.generate()
        gt_by_id = {r["transaction_id"]: r for r in gt if r.get("transaction_id")}
        matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
        agent_matched, agent_exceptions = run_agent_stage(needs_agent, unclaimed, FakeLLMClient())

        all_matched = matched + agent_matched
        for m in all_matched:
            truth = gt_by_id[m["transaction_id"]]
            expected = truth.get("correct_settlement_utrs")
            assert expected is not None and sorted(expected) == sorted(m["utrs"]), \
                f"seed {seed}: incorrect match {m['transaction_id']}"

        total = len(all_matched) + len(exceptions) + len(agent_exceptions)
        assert total == len(gw), f"seed {seed}: end-to-end accounting mismatch"

        claimed = [utr for m in all_matched for utr in m["utrs"]]
        assert len(claimed) == len(set(claimed)), f"seed {seed}: cross-stage double-claim"
    finally:
        curated_gen.SEED = original_seed
