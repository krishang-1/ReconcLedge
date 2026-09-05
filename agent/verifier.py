"""Verifies an agent-proposed match. Never accepts on the agent's own say-so.

Same two-tier principle as the matcher: a grossly invalid proposal (wrong
date order, huge amount gap, settled amount higher than net amount) is
rejected deterministically - no LLM call needed, no risk of a model getting
talked into accepting something arithmetic already rules out. Only a
genuinely plausible-looking small gap gets escalated to an LLM call, and
that call is independently framed - it never sees the agent's stated
reasoning, only the raw records - so a fluent but wrong justification can't
just be rubber-stamped.
"""

import json
import re

from exceptions import VERIFIER_REJECTED
from matcher import normalize, ref_token, settle_date, txn_date
from prompts import VERIFIER_SYSTEM_PROMPT

GROSS_MISMATCH_PCT = 0.15
# The LLM verifier's own rule (prompts.py) is a flat Rs 50 cap, not a
# percentage - on a small transaction a gap can exceed 15% while staying
# under Rs 50. Gross-mismatch rejection requires exceeding BOTH bases, so
# nothing the LLM would accept gets pre-rejected here.
GROSS_MISMATCH_ABSOLUTE_FLOOR = 50.0


def _extract_json(text):
    """Best-effort JSON extraction from an LLM response. Real responses often
    don't come back as bare JSON despite an instruction to - wrapped in
    markdown code fences, or with a sentence of preamble before the object.
    Tries progressively looser extraction rather than requiring the exact
    literal shape. Returns None if nothing parseable is found."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except ValueError:
            pass

    braces = re.search(r"\{.*\}", text, re.DOTALL)
    if braces:
        try:
            return json.loads(braces.group(0))
        except ValueError:
            pass

    return None


def deterministic_precheck(gateway_record, proposed_bank_records):
    """Returns (verdict, reason) if confidently decidable without an LLM call, else None."""
    net = gateway_record["net_amount"]
    total = sum(b["settled_amount"] for b in proposed_bank_records)

    for b in proposed_bank_records:
        delta_days = (settle_date(b) - txn_date(gateway_record)).days
        if delta_days < 0 or delta_days > 3:
            return "reject", f"settlement date {b['settlement_date']} falls outside the valid window"

    if total > net + 0.02:
        return "reject", f"settled total {total} exceeds gateway net amount {net} - not explainable as a fee deduction"

    if abs(total - net) <= 0.02:
        # An exact amount match alone can be coincidence - two unrelated
        # transactions sharing a round-number amount is common in real
        # payments. Require reference-token corroboration before
        # auto-accepting; without it, fall through to the LLM tier rather
        # than trusting arithmetic alone.
        token = normalize(ref_token(gateway_record["order_id"]))
        has_reference = any(token in normalize(b["narration"]) for b in proposed_bank_records)
        if has_reference:
            return "accept", "settled total matches net amount exactly, with reference token confirmed"
        # no reference corroboration - the gap-based logic below will compute
        # gap=0 here, which never exceeds either rejection threshold, so this
        # correctly falls through to "needs an LLM judgment call" rather than
        # being auto-rejected or auto-accepted on amount alone.

    gap = net - total
    gap_pct = gap / net
    if gap_pct > GROSS_MISMATCH_PCT and gap > GROSS_MISMATCH_ABSOLUTE_FLOOR:
        return "reject", f"settled total {total} is {gap_pct:.1%} (Rs {gap:.2f}) below net amount {net} - too large to be a plausible fee gap"

    return None  # small, ambiguous gap - needs an LLM judgment call


def verify(gateway_record, proposed_bank_records, llm_client):
    """Runs the two-tier verification. Returns {"accepted": bool, "reason": str, "method": str}."""
    if not proposed_bank_records:
        # Hallucinated UTRs. This was previously caught only by arithmetic
        # coincidence (sum([]) = 0 exceeding the gap thresholds) with a
        # misleading "gap too large" message - explicit is better.
        return {"accepted": False, "reason": "none of the proposed UTR(s) exist in the unclaimed pool - likely hallucinated", "method": "deterministic"}

    precheck = deterministic_precheck(gateway_record, proposed_bank_records)
    if precheck is not None:
        verdict, reason = precheck
        return {"accepted": verdict == "accept", "reason": reason, "method": "deterministic"}

    net = gateway_record["net_amount"]
    total = sum(b["settled_amount"] for b in proposed_bank_records)
    payload = {
        "gateway_transaction": gateway_record,
        "proposed_settlements": proposed_bank_records,
        "precomputed_shortfall": round(net - total, 2),
        "note": "precomputed_shortfall = net_amount - settled_total. Positive means settled is below net.",
    }
    messages = [
        {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload)},
    ]
    response = llm_client.chat(messages)
    content = response.get("content") or response.get("reasoning") or ""
    parsed = _extract_json(content)

    if parsed is None:
        return {
            "accepted": False,
            "reason": f"verifier response had no parseable JSON - treated as reject. Raw: {content[:200]!r}",
            "method": "llm_parse_error",
        }
    if "verdict" not in parsed:
        return {
            "accepted": False,
            "reason": f"verifier JSON missing 'verdict' field - treated as reject. Raw: {content[:200]!r}",
            "method": "llm_parse_error",
        }
    return {"accepted": parsed["verdict"] == "accept", "reason": parsed.get("reason", ""), "method": "llm"}
