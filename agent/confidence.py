"""Confidence-based escalation gating - the confidence axis to
escalation.py's value axis. A SEPARATE, composable pass over
escalation.py's output rather than a modification of it: callers run
annotate_escalation() first, then annotate_confidence() on its result.

Confidence is derived from signals the pipeline already computes, NOT a
new LLM self-report - asking the LLM to self-assess would change what it
sees, risking the proven 95% match rate for no real gain:

  - Deterministic-stage match: never touched an LLM - HIGH.
  - Agent match the verifier confirmed via reference-token + arithmetic
    alone (verifier_method == "deterministic") - HIGH: an LLM proposed
    it, but the ACCEPTANCE needed no LLM judgment.
  - Agent match the verifier could only confirm via LLM judgment
    (verifier_method == "llm") - MEDIUM.
  - Any exception - LOW by construction.
"""

HIGH = "high"
MEDIUM = "medium"
LOW = "low"


def assign_confidence(record, is_exception):
    """Pure classification, no side effects. is_exception is passed
    explicitly rather than inferred from record shape, since matched and
    exception dicts don't share a reliable single field to branch on."""
    if is_exception:
        return LOW
    method = record.get("method")
    if method == "deterministic":
        return HIGH
    if method == "agent_verified":
        return HIGH if record.get("verifier_method") == "deterministic" else MEDIUM
    return MEDIUM  # unrecognized method - conservative default, never HIGH on an unknown shape


def annotate_confidence(matched, exceptions):
    """Returns (new_matched, new_exceptions) - adds a "confidence" key to
    every record, and WIDENS "requires_human_review" to also be True
    whenever confidence isn't HIGH - never narrows what
    escalation.annotate_escalation()'s own value-based pass already
    flagged. Structurally depends on running after that pass: this reads
    an existing requires_human_review value to widen from, not to decide
    from scratch, so a record with no requires_human_review key at all
    is treated as not-yet-flagged (False) rather than raising - tolerant
    of being called on raw pipeline output too, though the intended
    order is escalation first, then this.

    Does not mutate its inputs, same convention as escalation.py."""

    def _annotate(record, is_exception):
        confidence = assign_confidence(record, is_exception)
        existing_review = record.get("requires_human_review", False)
        return {
            **record,
            "confidence": confidence,
            "requires_human_review": existing_review or confidence != HIGH,
        }

    new_matched = [_annotate(m, is_exception=False) for m in matched]
    new_exceptions = [_annotate(e, is_exception=True) for e in exceptions]
    return new_matched, new_exceptions
