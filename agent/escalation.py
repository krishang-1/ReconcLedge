"""Value-based escalation: flags high-value transactions for human review.

Deliberately NOT part of matcher.py or react_loop.py - this is pure
post-processing over their already-final output (matched/exceptions),
looked up against the original gateway records for the amount. Zero risk
of shifting a single match decision, because it runs strictly after both
stages have already decided everything; the 37/3/12 deterministic split
and whatever match rate the agent stage produces are physically unable
to change based on anything in this file. Additive-only: every existing
key on every matched/exception dict is preserved untouched, this only
adds two new ones (requires_human_review, amount).

HIGH_VALUE_THRESHOLD is an illustrative default (roughly the 80th
percentile of the curated dataset's amounts), not a figure derived from
any real RBI/PMLA reporting threshold - unlike the DATE_WINDOW_DAYS fix
(genuinely grounded in real Indian settlement practice, see
docs/DECISIONS.md), this project has no real merchant risk data to
calibrate against. A real deployment would set this per-merchant based
on their actual transaction volume distribution, not a fixed constant -
named here as a real scope limitation, not silently implied to be
compliance-derived.
"""

HIGH_VALUE_THRESHOLD = 35000.0


def annotate_escalation(matched, exceptions, gateway_records, threshold=HIGH_VALUE_THRESHOLD):
    """Returns (new_matched, new_exceptions) - copies of the input lists
    with requires_human_review and amount added to every dict. Does not
    mutate the inputs, so callers that still hold the original lists
    (e.g. for streamed progress events already sent before this runs)
    aren't affected by a later annotation pass.

    amount is looked up from gateway_records by transaction_id rather
    than trusting a value already on the matched/exception dict, because
    neither matcher.py's nor react_loop.py's output dicts carry the
    original amount at all (see their functions' return-shape comments)
    - the only place net_amount still exists post-stage is the original
    gateway record.

    A transaction_id with no corresponding gateway record (shouldn't
    happen given every matched/exception dict is derived from iterating
    gateway_records in the first place - see run_deterministic_stage and
    run_agent_stage) is annotated as requires_human_review=False rather
    than raising, since a missing amount is a reason to not-escalate on
    value grounds specifically, not a reason to crash the whole run over
    a metadata annotation pass.
    """
    amount_by_id = {g["transaction_id"]: g.get("net_amount") for g in gateway_records}
    # A duplicate transaction_id would silently let one amount win here.
    # Safe in practice: gateway_records is always internal curated data,
    # never a request body, and matcher.py's validate_input() rejects
    # duplicates earlier in the same run. batch_settlement.py's
    # equivalent IS request-fed and needs a real fail-fast guard.

    def _annotate(record):
        amount = amount_by_id.get(record["transaction_id"])
        high_value = amount is not None and amount >= threshold
        return {**record, "amount": amount, "requires_human_review": high_value}

    new_matched = [_annotate(m) for m in matched]
    new_exceptions = [_annotate(e) for e in exceptions]
    return new_matched, new_exceptions
