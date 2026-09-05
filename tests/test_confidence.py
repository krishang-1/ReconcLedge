"""Tests for agent/confidence.py's assign_confidence() and
annotate_confidence()."""

from confidence import HIGH, LOW, MEDIUM, annotate_confidence, assign_confidence


def test_deterministic_match_is_high_confidence():
    record = {"transaction_id": "t1", "method": "deterministic"}
    assert assign_confidence(record, is_exception=False) == HIGH


def test_agent_match_with_deterministic_verifier_method_is_high_confidence():
    record = {"transaction_id": "t1", "method": "agent_verified", "verifier_method": "deterministic"}
    assert assign_confidence(record, is_exception=False) == HIGH


def test_agent_match_with_llm_verifier_method_is_medium_confidence():
    record = {"transaction_id": "t1", "method": "agent_verified", "verifier_method": "llm"}
    assert assign_confidence(record, is_exception=False) == MEDIUM


def test_exception_is_always_low_confidence():
    record = {"transaction_id": "t1", "type": "NO_CANDIDATE_FOUND"}
    assert assign_confidence(record, is_exception=True) == LOW


def test_unrecognized_method_defaults_to_medium_not_high():
    record = {"transaction_id": "t1", "method": "some_future_method"}
    assert assign_confidence(record, is_exception=False) == MEDIUM


def test_annotate_confidence_widens_but_never_narrows_existing_escalation():
    matched = [
        {"transaction_id": "t1", "method": "deterministic", "requires_human_review": True},  # already flagged by value
        {"transaction_id": "t2", "method": "agent_verified", "verifier_method": "llm", "requires_human_review": False},
    ]
    new_matched, _ = annotate_confidence(matched, [])
    t1 = next(m for m in new_matched if m["transaction_id"] == "t1")
    t2 = next(m for m in new_matched if m["transaction_id"] == "t2")
    assert t1["requires_human_review"] is True  # stays True (value-flagged), confidence is HIGH so wouldn't add anything anyway
    assert t1["confidence"] == HIGH
    assert t2["requires_human_review"] is True  # newly widened - MEDIUM confidence triggers it
    assert t2["confidence"] == MEDIUM


def test_annotate_confidence_leaves_high_confidence_unflagged_records_alone():
    matched = [{"transaction_id": "t1", "method": "deterministic", "requires_human_review": False}]
    new_matched, _ = annotate_confidence(matched, [])
    assert new_matched[0]["requires_human_review"] is False
    assert new_matched[0]["confidence"] == HIGH


def test_annotate_confidence_tolerates_missing_requires_human_review_key():
    """Not the intended call order (escalation should run first), but
    shouldn't crash if it's called on raw records with no existing flag."""
    matched = [{"transaction_id": "t1", "method": "deterministic"}]
    new_matched, _ = annotate_confidence(matched, [])
    assert new_matched[0]["requires_human_review"] is False


def test_exceptions_always_get_flagged_for_review():
    exceptions = [{"transaction_id": "e1", "type": "NO_CANDIDATE_FOUND", "requires_human_review": False}]
    _, new_exceptions = annotate_confidence([], exceptions)
    assert new_exceptions[0]["requires_human_review"] is True
    assert new_exceptions[0]["confidence"] == LOW


def test_does_not_mutate_inputs():
    matched = [{"transaction_id": "t1", "method": "deterministic", "requires_human_review": False}]
    annotate_confidence(matched, [])
    assert "confidence" not in matched[0]
