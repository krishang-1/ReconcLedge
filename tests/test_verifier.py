"""Tests for agent/verifier.py. Covers the two real bugs found and fixed
in this component this session: a brittle JSON parser (docs/DECISIONS.md
error #13) and a miscalibrated vague-language threshold (error #14).
"""

import verifier


def test_extract_json_bare():
    assert verifier._extract_json('{"verdict": "accept", "reason": "ok"}') == {"verdict": "accept", "reason": "ok"}


def test_extract_json_markdown_fenced():
    text = '```json\n{"verdict": "accept", "reason": "ok"}\n```'
    assert verifier._extract_json(text) == {"verdict": "accept", "reason": "ok"}


def test_extract_json_fenced_no_language_tag():
    text = '```\n{"verdict": "reject", "reason": "too large"}\n```'
    assert verifier._extract_json(text) == {"verdict": "reject", "reason": "too large"}


def test_extract_json_with_preamble():
    text = 'Looking at this, the gap is small.\n\n{"verdict": "accept", "reason": "small gap"}'
    assert verifier._extract_json(text) == {"verdict": "accept", "reason": "small gap"}


def test_extract_json_preamble_and_fence_combined():
    text = 'Let me analyze.\n\n```json\n{"verdict": "reject", "reason": "too large"}\n```\n\nDone.'
    assert verifier._extract_json(text) == {"verdict": "reject", "reason": "too large"}


def test_extract_json_returns_none_for_unparseable():
    assert verifier._extract_json("I cannot determine this without more info.") is None


def test_extract_json_returns_none_for_empty_or_none():
    assert verifier._extract_json("") is None
    assert verifier._extract_json(None) is None


def _gw(net_amount, timestamp="2026-08-10T00:00:00", order_id="order_deadbeef0000"):
    return {"transaction_id": "txn_test", "net_amount": net_amount, "timestamp": timestamp, "order_id": order_id}


def _bank(settled_amount, settlement_date="2026-08-10", narration="NEFT CR deadbeef SETTLEMENT"):
    """Default narration matches _gw's default order_id's reference token, so
    existing exact-match tests keep passing without needing to know about the
    reference-corroboration check added after the collision-risk finding."""
    return {"utr_number": "UTR_test", "settled_amount": settled_amount, "settlement_date": settlement_date, "narration": narration}


def test_precheck_accepts_exact_match():
    verdict, reason = verifier.deterministic_precheck(_gw(1000.0), [_bank(1000.0)])
    assert verdict == "accept"


def test_precheck_does_not_blindly_accept_coincidental_amount_collision():
    """Regression guard for a real finding from a deliberate adversarial
    stress test: an exact amount+date match from a COMPLETELY UNRELATED
    order (different reference token in the narration) was previously
    auto-accepted purely on the arithmetic coincidence, with zero check
    that the settlement actually corresponds to this transaction. Two
    unrelated transactions sharing an identical settled amount is
    entirely plausible with round-number payments in real life."""
    gw = _gw(300.0)
    gw["order_id"] = "order_deadbeef0001"
    unrelated = {
        "utr_number": "UTR_unrelated", "settled_amount": 300.0, "settlement_date": "2026-08-10",
        "narration": "NEFT CR ffffffff SETTLEMENT",  # a different order's reference token entirely
    }
    verdict = verifier.deterministic_precheck(gw, [unrelated])
    assert verdict is None, "an exact-amount match with no reference corroboration must not be blindly auto-accepted"


def test_precheck_still_accepts_exact_match_with_reference_confirmed():
    """The fix must not become overly conservative - a genuinely correct
    match (reference token present) should still auto-accept without an
    LLM call."""
    gw = _gw(300.0)
    gw["order_id"] = "order_deadbeef0001"
    correct = {
        "utr_number": "UTR_correct", "settled_amount": 300.0, "settlement_date": "2026-08-10",
        "narration": "NEFT CR deadbeef SETTLEMENT",  # matches gw's reference token
    }
    verdict, reason = verifier.deterministic_precheck(gw, [correct])
    assert verdict == "accept"


def test_precheck_rejects_settled_above_net():
    """Settlement can never legitimately exceed the gateway net amount -
    that direction isn't explainable as a bank fee."""
    verdict, reason = verifier.deterministic_precheck(_gw(1000.0), [_bank(1050.0)])
    assert verdict == "reject"


def test_precheck_rejects_grossly_large_gap():
    verdict, reason = verifier.deterministic_precheck(_gw(1000.0), [_bank(700.0)])
    assert verdict == "reject"


def test_precheck_rejects_invalid_date_before_transaction():
    verdict, reason = verifier.deterministic_precheck(
        _gw(1000.0, timestamp="2026-08-10T00:00:00"),
        [_bank(1000.0, settlement_date="2026-08-08")],
    )
    assert verdict == "reject"


def test_precheck_returns_none_for_ambiguous_small_gap():
    """A small gap (a few percent, well within the FEE_DRIFT range) should
    NOT be resolved deterministically - it needs the LLM judgment call.
    This is the exact zone where the vague-language calibration bug lived."""
    verdict = verifier.deterministic_precheck(_gw(1000.0), [_bank(980.0)])
    assert verdict is None


def test_precheck_does_not_reject_small_transaction_large_percentage_small_absolute_gap():
    """Regression guard for a real inconsistency found on review: a small
    transaction can have a gap that exceeds 15% while staying well under
    the LLM verifier's own Rs 50 acceptance ceiling - e.g. Rs 35 on a
    Rs 150 transaction is ~23%, over the old percentage-only threshold,
    but comfortably under Rs 50. This must NOT be hard-rejected here -
    it needs to reach the LLM's own rule, which would accept it."""
    verdict = verifier.deterministic_precheck(_gw(150.0), [_bank(115.0)])  # gap=35, 23.3%, well under Rs50
    assert verdict is None, "a gap under the LLM's own Rs 50 ceiling must never be pre-rejected by percentage alone"


def test_precheck_still_rejects_when_both_percentage_and_absolute_are_large():
    """The fix must not become a loophole - a gap that's large by BOTH
    measures should still reject deterministically without wasting an
    LLM call."""
    verdict, reason = verifier.deterministic_precheck(_gw(1000.0), [_bank(700.0)])  # gap=300, 30%, well over Rs50
    assert verdict == "reject"


def test_verify_rejects_hallucinated_utrs_with_clear_reason():
    """Regression guard for a real clarity gap found on review: an empty
    proposed_bank_records list (all proposed UTRs hallucinated, none
    exist in the unclaimed pool) was previously rejected only by
    arithmetic coincidence (sum([])=0 produces a 100% gap that happens
    to exceed both rejection thresholds for this dataset's transaction
    sizes), with a confusing 'gap too large' message. Must now be
    explicit about what actually happened."""
    result = verifier.verify(_gw(1000.0), [], llm_client=None)
    assert result["accepted"] is False
    assert "hallucinated" in result["reason"] or "don't actually exist" in result["reason"]
