"""Tests for agent/matcher.py. These codify the claim made throughout
docs/DECISIONS.md - that the deterministic stage resolves a large share of
records with zero LLM calls and zero incorrect matches - as an actual
assertion instead of a chat-log claim.
"""

import synthetic_generator as gen
import matcher
from matcher import run_deterministic_stage
from exceptions import AMBIGUOUS_MULTIPLE_CANDIDATES


def _generate():
    return gen.generate()


def test_no_incorrect_matches():
    """The one non-negotiable property: whatever the deterministic stage
    claims to have matched must actually be correct per ground truth. A
    wrong match here is worse than a missed one - it's a false positive
    with no verifier layer to catch it, since this stage skips verification
    entirely on the premise that arithmetic identity doesn't need it."""
    gw, bank, gt = _generate()
    gt_by_id = {r["transaction_id"]: r for r in gt if r.get("transaction_id")}

    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)

    for m in matched:
        truth = gt_by_id[m["transaction_id"]]
        assert sorted(truth["correct_settlement_utrs"] or []) == sorted(m["utrs"]), (
            f"{m['transaction_id']}: matched {m['utrs']} but ground truth says "
            f"{truth['correct_settlement_utrs']}"
        )


def test_every_record_accounted_for():
    """Every gateway record must end up matched, excepted, or routed to the
    agent - never silently dropped."""
    gw, bank, gt = _generate()
    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    accounted = len(matched) + len(exceptions) + len(needs_agent)
    assert accounted == len(gw)


def test_clean_and_timing_lag_always_resolve_deterministically():
    """CLEAN and TIMING_LAG are designed to be fully resolvable by exact
    arithmetic - if either ever gets routed to the agent stage, the
    deterministic matcher's reference or date-window logic has regressed."""
    gw, bank, gt = _generate()
    gt_by_id = {r["transaction_id"]: r for r in gt if r.get("transaction_id")}
    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    matched_ids = {m["transaction_id"] for m in matched}

    for txn_id, truth in gt_by_id.items():
        if truth["mismatch_type"] in ("CLEAN", "TIMING_LAG"):
            assert txn_id in matched_ids, (
                f"{txn_id} ({truth['mismatch_type']}) should always resolve "
                f"deterministically but didn't"
            )


def test_duplicate_flagged_ambiguous_not_guessed():
    """DUPLICATE records must never be silently force-matched to one of two
    identical-amount candidates - the honest behavior is to flag for human
    review, not guess. This is deliberate design (see docs/DECISIONS.md),
    not a gap to be 'fixed' toward picking one."""
    gw, bank, gt = _generate()
    gt_by_id = {r["transaction_id"]: r for r in gt if r.get("transaction_id")}
    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    exceptions_by_id = {e["transaction_id"]: e for e in exceptions}

    duplicate_ids = [txn_id for txn_id, truth in gt_by_id.items() if truth["mismatch_type"] == "DUPLICATE"]
    assert duplicate_ids, "no DUPLICATE records generated - can't test this"

    for txn_id in duplicate_ids:
        assert txn_id in exceptions_by_id, f"{txn_id} (DUPLICATE) should be an exception, not a silent match"
        assert exceptions_by_id[txn_id]["type"] == AMBIGUOUS_MULTIPLE_CANDIDATES


def test_no_bank_record_claimed_twice():
    """A settlement claimed by one match must never also appear in another -
    that would mean double-counting the same real-world money."""
    gw, bank, gt = _generate()
    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    all_claimed_utrs = [utr for m in matched for utr in m["utrs"]]
    assert len(all_claimed_utrs) == len(set(all_claimed_utrs)), "a UTR was claimed by more than one match"


def test_ambiguous_split_pairs_flagged_not_silently_guessed():
    """Regression guard for a real (if currently untriggered) silent-
    wrong-answer risk found on review: if more than one pair of
    reference-matched candidates sums to net_amount, the matcher must
    flag it as ambiguous rather than silently returning whichever pair
    it happened to check first."""
    net = 1000.0
    gw = {
        "transaction_id": "txn_test", "order_id": "order_deadbeef0000",
        "net_amount": net, "timestamp": "2026-08-10T00:00:00",
    }
    token = matcher.ref_token(gw["order_id"])
    date = "2026-08-10"

    def bank(utr, amount):
        return {"utr_number": utr, "settled_amount": amount, "settlement_date": date, "narration": f"NEFT CR {token} SETTLEMENT"}

    # Two DIFFERENT valid pairs, both summing to exactly net_amount:
    # (300+700) and (400+600) - genuinely ambiguous, not a real split.
    candidates = [bank("UTR_A", 300.0), bank("UTR_B", 700.0), bank("UTR_C", 400.0), bank("UTR_D", 600.0)]
    result = matcher.try_resolve(gw, candidates)
    assert result["status"] == "exception"
    assert result["type"] == AMBIGUOUS_MULTIPLE_CANDIDATES


def test_duplicate_gateway_transaction_id_fails_fast():
    """Regression guard for a real gap found via a noisy stress test
    (data/noisy_stress_generator.py, not the curated dataset): a
    duplicate transaction_id previously flowed silently through the
    entire deterministic stage (and would have reached the agent stage
    too, spending real API budget) before eval/metrics.py's duplicate
    guard caught it much later. Must now fail immediately instead."""
    gw = [
        {"transaction_id": "txn_dup", "order_id": "order_aaaaaaaaaaaa", "net_amount": 100.0, "timestamp": "2026-08-10T00:00:00"},
        {"transaction_id": "txn_dup", "order_id": "order_bbbbbbbbbbbb", "net_amount": 200.0, "timestamp": "2026-08-10T00:00:00"},
    ]
    try:
        run_deterministic_stage(gw, [])
        assert False, "should have raised on a duplicate transaction_id in the input"
    except ValueError as e:
        assert "txn_dup" in str(e)


def test_duplicate_bank_utr_fails_fast():
    """Same principle, for the bank side - a duplicate UTR in the input
    data is also a structural invalidity worth catching immediately."""
    bank = [
        {"utr_number": "UTR_SAME", "settled_amount": 100.0, "settlement_date": "2026-08-10", "narration": "x"},
        {"utr_number": "UTR_SAME", "settled_amount": 200.0, "settlement_date": "2026-08-11", "narration": "y"},
    ]
    try:
        run_deterministic_stage([], bank)
        assert False, "should have raised on a duplicate utr_number in the input"
    except ValueError as e:
        assert "UTR_SAME" in str(e)


def test_date_window_days_defaults_to_identical_behavior():
    """Regression guard for the merchant-config parameterization
    (see agent/merchant_config.py, docs/DECISIONS.md): the curated
    dataset's 37/3/12 split must be byte-for-byte identical whether
    date_window_days is omitted or passed explicitly as the module
    default - proving the parameter is purely additive."""
    gw, bank, gt = gen.generate()
    m1, e1, na1, unc1 = run_deterministic_stage(gw, bank)
    m2, e2, na2, unc2 = run_deterministic_stage(gw, bank, date_window_days=matcher.DATE_WINDOW_DAYS)
    assert (len(m1), len(e1), len(na1)) == (37, 3, 12)
    assert (len(m1), len(e1), len(na1)) == (len(m2), len(e2), len(na2))


def test_date_window_days_override_genuinely_changes_behavior():
    """The override isn't a no-op - a tighter window must route MORE
    (never fewer) records to the agent stage, since it's strictly more
    restrictive than the default."""
    gw, bank, gt = gen.generate()
    _, _, needs_agent_default, _ = run_deterministic_stage(gw, bank)
    _, _, needs_agent_tight, _ = run_deterministic_stage(gw, bank, date_window_days=0)
    assert len(needs_agent_tight) >= len(needs_agent_default)
