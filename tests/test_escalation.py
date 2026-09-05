"""Tests for agent/escalation.py's annotate_escalation() - purely
additive metadata over already-final matched/exceptions output, so
these test the annotation logic in isolation with hand-built fixtures
rather than running the real pipeline (that's covered separately by the
37/3/12-split regression check)."""

from escalation import HIGH_VALUE_THRESHOLD, annotate_escalation


def _gw(txn_id, amount):
    return {"transaction_id": txn_id, "net_amount": amount}


def test_below_threshold_not_escalated():
    matched = [{"transaction_id": "t1", "utrs": ["u1"], "method": "reference"}]
    gateway = [_gw("t1", HIGH_VALUE_THRESHOLD - 1)]
    new_matched, new_exceptions = annotate_escalation(matched, [], gateway)
    assert new_matched[0]["requires_human_review"] is False
    assert new_matched[0]["amount"] == HIGH_VALUE_THRESHOLD - 1


def test_at_or_above_threshold_escalated():
    matched = [{"transaction_id": "t1", "utrs": ["u1"], "method": "reference"}]
    gateway = [_gw("t1", HIGH_VALUE_THRESHOLD)]
    new_matched, _ = annotate_escalation(matched, [], gateway)
    assert new_matched[0]["requires_human_review"] is True


def test_exceptions_are_annotated_too():
    exceptions = [{"transaction_id": "t2", "type": "NO_CANDIDATE_FOUND", "reason": "x"}]
    gateway = [_gw("t2", HIGH_VALUE_THRESHOLD + 5000)]
    _, new_exceptions = annotate_escalation([], exceptions, gateway)
    assert new_exceptions[0]["requires_human_review"] is True
    assert new_exceptions[0]["type"] == "NO_CANDIDATE_FOUND"  # original keys preserved


def test_missing_gateway_record_defaults_to_not_escalated_not_a_crash():
    matched = [{"transaction_id": "unknown_txn", "utrs": ["u1"], "method": "reference"}]
    new_matched, _ = annotate_escalation(matched, [], gateway_records=[])
    assert new_matched[0]["requires_human_review"] is False
    assert new_matched[0]["amount"] is None


def test_does_not_mutate_input_lists():
    matched = [{"transaction_id": "t1", "utrs": ["u1"], "method": "reference"}]
    gateway = [_gw("t1", 99999)]
    annotate_escalation(matched, [], gateway)
    assert "requires_human_review" not in matched[0]  # original dict untouched


def test_custom_threshold_respected():
    matched = [{"transaction_id": "t1", "utrs": ["u1"], "method": "reference"}]
    gateway = [_gw("t1", 500)]
    new_matched, _ = annotate_escalation(matched, [], gateway, threshold=100)
    assert new_matched[0]["requires_human_review"] is True


def test_mixed_batch_only_flags_the_high_value_ones():
    matched = [
        {"transaction_id": "low", "utrs": ["u1"], "method": "reference"},
        {"transaction_id": "high", "utrs": ["u2"], "method": "reference"},
    ]
    gateway = [_gw("low", 1000), _gw("high", 99999)]
    new_matched, _ = annotate_escalation(matched, [], gateway)
    flagged = {m["transaction_id"] for m in new_matched if m["requires_human_review"]}
    assert flagged == {"high"}
