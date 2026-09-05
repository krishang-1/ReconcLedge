"""Tests for agent/refund_matcher.py's reconcile_refunds()."""

from refund_matcher import FULL_REFUND, OVER_REFUNDED, PARTIAL_REFUND, reconcile_refunds


def _gw(txn_id, amount):
    return {"transaction_id": txn_id, "net_amount": amount}


def test_full_refund_classified_correctly():
    gateway = [_gw("t1", 1000.0)]
    events = [{"transaction_id": "t1", "refund_amount": 1000.0}]
    result = reconcile_refunds(gateway, events)
    assert len(result) == 1
    r = result[0]
    assert r["classification"] == FULL_REFUND
    assert r["known_transaction"] is True
    assert r["net_expected_settlement"] == 0.0


def test_partial_refund_classified_correctly():
    gateway = [_gw("t1", 1000.0)]
    events = [{"transaction_id": "t1", "refund_amount": 400.0}]
    result = reconcile_refunds(gateway, events)
    r = result[0]
    assert r["classification"] == PARTIAL_REFUND
    assert r["net_expected_settlement"] == 600.0


def test_over_refund_flagged_not_computed_as_negative():
    gateway = [_gw("t1", 1000.0)]
    events = [
        {"transaction_id": "t1", "refund_amount": 700.0},
        {"transaction_id": "t1", "refund_amount": 700.0},
    ]
    result = reconcile_refunds(gateway, events)
    r = result[0]
    assert r["classification"] == OVER_REFUNDED
    assert r["total_refunded"] == 1400.0
    assert r["refund_count"] == 2


def test_multiple_partial_refunds_summed_to_full():
    gateway = [_gw("t1", 1000.0)]
    events = [
        {"transaction_id": "t1", "refund_amount": 500.0},
        {"transaction_id": "t1", "refund_amount": 500.0},
    ]
    result = reconcile_refunds(gateway, events)
    r = result[0]
    assert r["classification"] == FULL_REFUND
    assert r["total_refunded"] == 1000.0
    assert r["refund_count"] == 2


def test_unknown_transaction_id_flagged_not_dropped_not_crashed():
    gateway = [_gw("t1", 1000.0)]
    events = [{"transaction_id": "does_not_exist", "refund_amount": 250.0}]
    result = reconcile_refunds(gateway, events)
    assert len(result) == 1
    r = result[0]
    assert r["known_transaction"] is False
    assert r["original_amount"] is None
    assert r["net_expected_settlement"] is None
    assert r["classification"] is None
    assert r["total_refunded"] == 250.0  # still reported even though unattributable


def test_transaction_with_no_refund_events_not_included():
    gateway = [_gw("t1", 1000.0), _gw("t2", 2000.0)]
    events = [{"transaction_id": "t1", "refund_amount": 100.0}]
    result = reconcile_refunds(gateway, events)
    ids = {r["transaction_id"] for r in result}
    assert ids == {"t1"}  # t2 never mentioned, correctly absent


def test_amount_epsilon_rounding_slack_does_not_misclassify():
    # 0.01 under the true amount should still count as a full refund -
    # matches AMOUNT_EPSILON (0.02) reused from matcher.py
    gateway = [_gw("t1", 1000.00)]
    events = [{"transaction_id": "t1", "refund_amount": 999.99}]
    result = reconcile_refunds(gateway, events)
    assert result[0]["classification"] == FULL_REFUND


def test_does_not_mutate_inputs():
    gateway = [_gw("t1", 1000.0)]
    events = [{"transaction_id": "t1", "refund_amount": 400.0}]
    reconcile_refunds(gateway, events)
    assert "classification" not in gateway[0]
    assert "classification" not in events[0]
