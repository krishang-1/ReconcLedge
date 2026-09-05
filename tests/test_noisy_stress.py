"""Stress tests using data/noisy_stress_generator.py - deliberately messy,
high-volume, edge-case-heavy data distinct from the curated submission
dataset. These answer a different question than the rest of the suite:
not "is the reported 95% accurate" but "does the pipeline behave sanely,
or at least fail clearly, under conditions the curated dataset never
exercises." Found and fixed a real bug on first use (duplicate
transaction_id validation, docs/DECISIONS.md) - kept here permanently so
that class of gap can't silently regress.
"""

import noisy_stress_generator as noisy
from matcher import run_deterministic_stage, ref_token
from react_loop import run_agent_stage
from fake_llm_client import FakeLLMClient


def _generate_valid():
    """Generates a noisy batch and removes the intentional duplicate
    transaction_id, for tests that need to get past validate_input to
    exercise the rest of the pipeline. The duplicate-ID case itself is
    tested separately, deliberately, below."""
    gw, bank = noisy.generate()
    from collections import Counter
    ids = Counter(g["transaction_id"] for g in gw)
    dupe_ids = {k for k, v in ids.items() if v > 1}
    gw_clean = [g for g in gw if g["transaction_id"] not in dupe_ids]
    gw_clean += [g for g in gw if g["transaction_id"] in dupe_ids][:1]
    return gw_clean, bank


def test_noisy_batch_has_duplicate_transaction_id_by_design():
    """Confirms the generator's intentional duplicate-ID stressor is
    actually present - a sanity check on the stress test itself, not the
    pipeline, so a future change to the generator can't silently stop
    testing what this file claims to test."""
    gw, bank = noisy.generate()
    from collections import Counter
    ids = Counter(g["transaction_id"] for g in gw)
    assert any(v > 1 for v in ids.values()), "generator should inject at least one duplicate transaction_id"


def test_duplicate_transaction_id_fails_fast_on_real_noisy_data():
    """Regression guard for the real bug found via this exact stress test:
    a duplicate transaction_id previously flowed silently through the
    entire deterministic stage before eval/metrics.py's guard caught it
    much later, at metrics-computation time - after real API budget could
    already have been spent on the agent stage. Must fail immediately."""
    gw, bank = noisy.generate()
    try:
        run_deterministic_stage(gw, bank)
        assert False, "should have raised on the generator's intentional duplicate transaction_id"
    except ValueError as e:
        assert "transaction_id" in str(e)


def test_deterministic_stage_survives_high_volume_no_crash():
    gw, bank = _generate_valid()
    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    assert len(matched) + len(exceptions) + len(needs_agent) == len(gw)


def test_deterministic_stage_never_double_claims_under_noise():
    gw, bank = _generate_valid()
    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    claimed = [utr for m in matched for utr in m["utrs"]]
    assert len(claimed) == len(set(claimed))


def test_no_reference_token_collision_at_volume():
    """Stress-tests the 'negligible collision probability' assumption
    behind matcher.py's reference lookup - the noisy generator
    deliberately injects near-collision junk into orphan bank narrations
    to try to trigger a false-positive match. Confirms no claimed match's
    bank narration ambiguously contains more than one real transaction's
    reference token."""
    import re
    gw, bank = _generate_valid()
    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)

    real_tokens = {ref_token(g["order_id"]) for g in gw}
    bank_by_utr = {b["utr_number"]: b for b in bank}
    claimed_utrs = {utr for m in matched for utr in m["utrs"]}

    for utr in claimed_utrs:
        narration = bank_by_utr[utr]["narration"]
        normalized = re.sub(r"[^A-Za-z0-9]", "", narration).upper()
        matching_tokens = [t for t in real_tokens if t.upper() in normalized]
        assert len(matching_tokens) <= 1, f"{utr} narration ambiguously matches {len(matching_tokens)} tokens: {narration}"


def test_full_pipeline_no_crash_at_volume_with_fake_client():
    """Runs both stages end to end on a noisy, high-volume batch (~500
    records, ~200 routed to the agent stage - over 15x the curated
    dataset's agent-stage volume) and confirms it completes without
    crashing, with exact accounting and no double-claims across stages."""
    gw, bank = _generate_valid()
    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    agent_matched, agent_exceptions = run_agent_stage(needs_agent, unclaimed, FakeLLMClient())

    total = len(matched) + len(exceptions) + len(agent_matched) + len(agent_exceptions)
    assert total == len(gw)

    all_claimed = [utr for m in (matched + agent_matched) for utr in m["utrs"]]
    assert len(all_claimed) == len(set(all_claimed)), "a UTR was claimed by more than one match across stages"


def test_no_settled_amount_exceeds_net_amount_at_volume():
    """Regression guard for the tools.py fix - confirms no match, across
    either stage, under noisy high-volume conditions, ever has a settled
    total exceeding the gateway net amount (which is never legitimately
    explainable as a fee deduction)."""
    gw, bank = _generate_valid()
    gw_by_id = {g["transaction_id"]: g for g in gw}
    bank_by_utr = {b["utr_number"]: b for b in bank}

    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    agent_matched, agent_exceptions = run_agent_stage(needs_agent, unclaimed, FakeLLMClient())

    for m in matched + agent_matched:
        net = gw_by_id[m["transaction_id"]]["net_amount"]
        total = sum(bank_by_utr[utr]["settled_amount"] for utr in m["utrs"])
        assert total <= net + 0.02, f"{m['transaction_id']}: settled {total} exceeds net {net}"
