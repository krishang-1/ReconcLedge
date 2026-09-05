"""Tests for agent/chargeback_matcher.py's reconcile_chargeback()."""

import chargeback_generator
from chargeback_matcher import (
    FINALIZED_DEBIT,
    IN_FLIGHT,
    INVALID_DISPUTE,
    REVERSED,
    reconcile_chargeback,
)


def test_in_flight_dispute_classified_correctly():
    gw, cb = chargeback_generator.generate()[0]
    result = reconcile_chargeback(gw, cb)
    assert result["classification"] == IN_FLIGHT
    assert result["requires_human_review"] is True


def test_won_dispute_reverses_the_debit():
    gw, cb = chargeback_generator.generate()[1]
    result = reconcile_chargeback(gw, cb)
    assert result["classification"] == REVERSED
    # fee still applies per the module's named assumption - balance is
    # net_amount minus fee, not the full net_amount
    assert result["current_expected_balance"] == gw["net_amount"] - cb["chargeback_fee"]


def test_lost_dispute_finalizes_the_debit():
    gw, cb = chargeback_generator.generate()[2]
    result = reconcile_chargeback(gw, cb)
    assert result["classification"] == FINALIZED_DEBIT
    assert result["current_expected_balance"] == gw["net_amount"] - cb["disputed_amount"] - cb["chargeback_fee"]


def test_arbitration_is_still_in_flight_not_a_separate_terminal_state():
    gw, cb = chargeback_generator.generate()[3]
    result = reconcile_chargeback(gw, cb)
    assert result["classification"] == IN_FLIGHT


def test_invalid_dispute_flagged_not_computed_as_nonsense():
    gw, cb = chargeback_generator.generate()[4]
    result = reconcile_chargeback(gw, cb)
    assert result["classification"] == INVALID_DISPUTE
    assert result["current_expected_balance"] is None


def test_full_value_dispute_with_fee_can_legitimately_go_negative():
    """Real, correct behavior in this module specifically (unlike other
    reconciliation modules where a negative number signals an anomaly):
    a full-value dispute plus a genuine chargeback fee can leave the
    merchant's expected balance on that transaction negative - they
    owe more than they originally collected. Not a bug."""
    gw = {"transaction_id": "x", "net_amount": 1000.0}
    cb = {"status": "open", "disputed_amount": 1000.0, "chargeback_fee": 100.0, "initiated_by": "customer"}
    result = reconcile_chargeback(gw, cb)
    assert result["current_expected_balance"] == -100.0
    assert result["classification"] == IN_FLIGHT  # not flagged as invalid_dispute - the amount itself is valid


def test_unrecognized_status_fails_fast():
    gw = {"transaction_id": "x", "net_amount": 100.0}
    cb = {"status": "typo_status", "disputed_amount": 50.0, "chargeback_fee": 10.0, "initiated_by": "customer"}
    try:
        reconcile_chargeback(gw, cb)
        assert False, "should have raised on an unrecognized status"
    except ValueError as e:
        assert "typo_status" in str(e)


def test_every_outcome_requires_human_review():
    for gw, cb in chargeback_generator.generate():
        result = reconcile_chargeback(gw, cb)
        assert result["requires_human_review"] is True


def test_does_not_mutate_inputs():
    gw, cb = chargeback_generator.generate()[0]
    reconcile_chargeback(gw, cb)
    assert "classification" not in gw
    assert "classification" not in cb
