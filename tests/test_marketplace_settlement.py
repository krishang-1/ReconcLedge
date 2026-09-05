"""Tests for agent/marketplace_settlement.py's reconcile_split_transaction()."""

import marketplace_generator
from marketplace_settlement import (
    FULLY_RECONCILED,
    MISMATCH,
    PENDING_HOLD,
    REVERSAL_ACCOUNTED,
    reconcile_split_transaction,
)


def test_clean_fully_settled_split():
    gw, transfers, commission = marketplace_generator.generate()[0]
    result = reconcile_split_transaction(gw, transfers, commission)
    assert result["status"] == FULLY_RECONCILED
    assert result["gap"] == 0.0


def test_on_hold_transfer_is_pending_not_mismatch():
    gw, transfers, commission = marketplace_generator.generate()[1]
    result = reconcile_split_transaction(gw, transfers, commission)
    assert result["status"] == PENDING_HOLD
    assert result["on_hold_transfer_total"] == 4500.00
    assert result["gap"] == 0.0  # money is fully accounted for, just not settled


def test_reversed_transfer_is_added_not_subtracted():
    """Regression guard for a real mistake caught while writing this
    module: a reversed transfer represents money that WAS part of the
    original split (at the time it was made) - it must be ADDED to the
    accounted-for total, not subtracted, or a genuinely balanced ledger
    with a reversal in it would incorrectly show a gap."""
    gw, transfers, commission = marketplace_generator.generate()[2]
    result = reconcile_split_transaction(gw, transfers, commission)
    assert result["status"] == REVERSAL_ACCOUNTED
    assert result["reversed_transfer_total"] == 1800.00
    assert result["gap"] == 0.0


def test_genuine_mismatch_reports_real_gap():
    gw, transfers, commission = marketplace_generator.generate()[3]
    result = reconcile_split_transaction(gw, transfers, commission)
    assert result["status"] == MISMATCH
    assert result["gap"] == 50.0


def test_unrecognized_transfer_status_fails_fast():
    gw = {"transaction_id": "x", "net_amount": 100.0}
    transfers = [{"linked_account_id": "v1", "amount": 100.0, "status": "typo_status"}]
    try:
        reconcile_split_transaction(gw, transfers, 0.0)
        assert False, "should have raised on an unrecognized transfer status"
    except ValueError as e:
        assert "typo_status" in str(e)


def test_on_hold_takes_priority_over_reversal_in_classification():
    """When both an on-hold and a reversed transfer exist in the same
    split, pending_hold should win - there's still live, unresolved
    money movement to track, which is more actionable than the fact a
    reversal also occurred."""
    gw = {"transaction_id": "x", "net_amount": 1000.0}
    transfers = [
        {"linked_account_id": "v1", "amount": 400.0, "status": "on_hold"},
        {"linked_account_id": "v2", "amount": 300.0, "status": "reversed"},
    ]
    result = reconcile_split_transaction(gw, transfers, platform_commission=300.0)
    assert result["status"] == PENDING_HOLD


def test_no_transfers_at_all_just_commission():
    gw = {"transaction_id": "x", "net_amount": 100.0}
    result = reconcile_split_transaction(gw, [], platform_commission=100.0)
    assert result["status"] == FULLY_RECONCILED


def test_does_not_mutate_inputs():
    gw, transfers, commission = marketplace_generator.generate()[0]
    original_transfer_count = len(transfers)
    reconcile_split_transaction(gw, transfers, commission)
    assert len(transfers) == original_transfer_count
    assert "status" in transfers[0]  # original transfer dict untouched, no result-shape keys leaked in
    assert "gap" not in transfers[0]
