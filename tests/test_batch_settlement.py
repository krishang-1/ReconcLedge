"""Tests for agent/batch_settlement.py - both the primary batch_id
mechanism and the bounded no-batch-id fallback, exercised against
data/batch_generator.py's five scenarios."""

import batch_generator
from batch_settlement import (
    MAX_POOL_SIZE,
    find_bounded_subset_matches,
    reconcile_by_batch_id,
)


def test_clean_batch_matches():
    gw, bank = batch_generator.generate()
    reports = reconcile_by_batch_id(gw, bank)
    clean = next(r for r in reports if r["batch_id"] == "BATCH_CLEAN_01")
    assert clean["matched"] is True
    assert clean["credited_amount"] == clean["expected_sum"]
    assert set(clean["gateway_transaction_ids"]) == {"batch_txn_001", "batch_txn_002", "batch_txn_003", "batch_txn_004"}


def test_batch_with_genuine_discrepancy_flagged_not_matched():
    gw, bank = batch_generator.generate()
    reports = reconcile_by_batch_id(gw, bank)
    gap = next(r for r in reports if r["batch_id"] == "BATCH_GAP_01")
    assert gap["matched"] is False
    assert gap["expected_sum"] == 6200.00
    assert gap["credited_amount"] == 6100.00
    assert "gap" in gap["reason"].lower() or "does not match" in gap["reason"]


def test_batch_with_no_bank_credit_line_reported_not_dropped():
    gw, bank = batch_generator.generate()
    reports = reconcile_by_batch_id(gw, bank)
    pending = next(r for r in reports if r["batch_id"] == "BATCH_PENDING_01")
    assert pending["credited_amount"] is None
    assert pending["matched"] is False
    assert len(pending["gateway_transaction_ids"]) == 2


def test_unbatched_records_skipped_by_batch_id_mechanism():
    gw, bank = batch_generator.generate()
    reports = reconcile_by_batch_id(gw, bank)
    batch_ids = {r["batch_id"] for r in reports}
    assert None not in batch_ids  # unbatched records never produce a batch_id=None report here


def test_bounded_fallback_finds_the_clean_group():
    gw, bank = batch_generator.generate()
    unbatched = [g for g in gw if "settlement_batch_id" not in g]
    clean_pool = [g for g in unbatched if g["transaction_id"].startswith("unbatched_3")]
    result = find_bounded_subset_matches(clean_pool, 1250.00)
    assert result["status"] == "candidate_match"
    assert set(result["transaction_ids"]) == {"unbatched_301", "unbatched_302", "unbatched_303"}
    assert result["requires_human_review"] is True  # never auto-accepted


def test_bounded_fallback_detects_genuine_ambiguity():
    gw, bank = batch_generator.generate()
    unbatched = [g for g in gw if "settlement_batch_id" not in g]
    ambiguous_pool = [g for g in unbatched if g["transaction_id"].startswith("unbatched_4")]
    result = find_bounded_subset_matches(ambiguous_pool, 2300.00)
    assert result["status"] == "ambiguous"
    assert result["candidate_count_found_before_stopping"] >= 2


def test_bounded_fallback_no_match_found_is_explicit():
    result = find_bounded_subset_matches([{"transaction_id": "x", "net_amount": 50.0}], 99999.0)
    assert result["status"] == "no_match_found"


def test_bounded_fallback_refuses_oversized_pool_rather_than_searching():
    oversized_pool = [{"transaction_id": f"t{i}", "net_amount": float(i)} for i in range(MAX_POOL_SIZE + 5)]
    result = find_bounded_subset_matches(oversized_pool, 12345.0)
    assert result["status"] == "pool_too_large"
    assert result["pool_size"] == MAX_POOL_SIZE + 5


def test_bounded_fallback_at_exactly_the_pool_limit_still_searches():
    pool = [{"transaction_id": f"t{i}", "net_amount": 1.0} for i in range(MAX_POOL_SIZE)]
    result = find_bounded_subset_matches(pool, 999999.0)
    assert result["status"] != "pool_too_large"  # exactly at the limit, not over it - should still attempt


def test_does_not_mutate_inputs():
    gw, bank = batch_generator.generate()
    original_len = len(gw)
    reconcile_by_batch_id(gw, bank)
    assert len(gw) == original_len
    assert "matched" not in gw[0]


def test_duplicate_batch_id_in_bank_records_fails_fast_not_silently_overwritten():
    """Found via a deeper post-shipping audit: the dict-comprehension
    lookup used to silently let a second bank_batch_records entry with
    the same batch_id overwrite the first, with no warning at all - a
    real data-conflict case (e.g. a duplicate remittance submission)
    that should fail fast, matching matcher.py's validate_input()
    discipline for duplicate transaction_id/UTR."""
    gw = [{"transaction_id": "t1", "net_amount": 100.0, "settlement_batch_id": "B1"}]
    bank = [
        {"batch_id": "B1", "credited_amount": 100.0},
        {"batch_id": "B1", "credited_amount": 999.0},
    ]
    try:
        reconcile_by_batch_id(gw, bank)
        assert False, "should have raised on a duplicate batch_id in bank_batch_records"
    except ValueError as e:
        assert "B1" in str(e)


def test_multiple_unbatched_credit_lines_with_no_batch_id_are_not_treated_as_duplicates():
    """Unbatched credit lines legitimately have batch_id=None/absent and
    can appear more than once - the duplicate-batch_id guard must only
    apply to real, non-empty batch_ids."""
    gw = [{"transaction_id": "t1", "net_amount": 100.0}]  # no settlement_batch_id
    bank = [
        {"batch_id": None, "credited_amount": 50.0},
        {"batch_id": None, "credited_amount": 75.0},
    ]
    reports = reconcile_by_batch_id(gw, bank)  # should not raise
    assert reports == []  # neither gateway record nor bank record has a real batch_id to group on
