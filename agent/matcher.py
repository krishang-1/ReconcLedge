"""Deterministic matcher: resolves any gateway transaction where reference
lookup plus exact-arithmetic checking gives a confident answer, with zero
LLM calls. Only records this stage cannot confidently resolve are handed to
the LLM agent loop. This split is a deliberate design choice, not a
shortcut: arithmetic identity (does settled amount equal net amount to the
cent) doesn't need a language model's judgment, and calling one anyway would
add cost, latency, and hallucination risk for no benefit. The LLM is
reserved for genuinely ambiguous cases - a small unexplained amount gap
(FEE_DRIFT), a reference that fails exact lookup (GARBLED_REF), or multiple
same-amount candidates with no other discriminator.
"""

import re
from datetime import date, datetime

from exceptions import AMBIGUOUS_MULTIPLE_CANDIDATES

AMOUNT_EPSILON = 0.02
# Calendar days, sized for BUSINESS-day settlement: a T+2 cycle starting
# Thursday settles 4 calendar days later, and a bank holiday collision
# pushes that to 5. A 2000-transaction simulation against a real Indian
# holiday calendar (scripts/realworld_simulation.py) showed a 3-day
# window misrouting 217 transactions to escalation purely from this gap;
# 7 eliminates that while staying far tighter than a genuinely wrong
# settlement date (weeks, not days).
DATE_WINDOW_DAYS = 7


def normalize(text):
    """Strips all non-alphanumeric characters and uppercases, for tolerant text comparison."""
    return re.sub(r"[^A-Za-z0-9]", "", text).upper()


def ref_token(order_id):
    """Returns the reference token a bank narration would echo for this order_id."""
    return order_id.split("_")[1][:8]


def txn_date(gateway_record):
    """Returns the transaction date (date-only) from a gateway record's timestamp."""
    return datetime.fromisoformat(gateway_record["timestamp"]).date()


def settle_date(bank_record):
    """Returns the settlement date (date-only) from a bank record."""
    return date.fromisoformat(bank_record["settlement_date"])


def within_date_window(gateway_record, bank_record, date_window_days=DATE_WINDOW_DAYS):
    """Returns True if the settlement date falls within the allowed window
    after the transaction date.

    date_window_days defaults to the module constant - existing callers
    (eval/run_batch.py, the full test suite, the curated-dataset
    pipeline) are unaffected either way, since Python resolves the
    default at call time from the same DATE_WINDOW_DAYS every existing
    caller already relies on. The parameter exists so merchant-specific
    configuration (see agent/merchant_config.py) can override it for a
    specific run without duplicating this function or forking the
    matching logic - see docs/DECISIONS.md for the regression proof that
    an omitted override is byte-for-byte identical to before this
    parameter existed."""
    delta = (settle_date(bank_record) - txn_date(gateway_record)).days
    return 0 <= delta <= date_window_days


def reference_candidates(gateway_record, bank_records):
    """Returns bank records whose narration contains this transaction's reference token."""
    token = normalize(ref_token(gateway_record["order_id"]))
    return [b for b in bank_records if token in normalize(b["narration"])]


def try_resolve(gateway_record, unclaimed_bank_records, date_window_days=DATE_WINDOW_DAYS):
    """Attempts a confident deterministic resolution for one gateway record.

    date_window_days: see within_date_window()'s docstring - defaults to
    identical existing behavior, threaded through for merchant-specific
    overrides.

    Returns a dict with one of:
      {"status": "matched", "utrs": [...], "method": "deterministic"}
      {"status": "needs_agent", "reason": str}
      {"status": "exception", "type": str, "reason": str}
    """
    candidates = reference_candidates(gateway_record, unclaimed_bank_records)
    net = gateway_record["net_amount"]

    if not candidates:
        return {"status": "needs_agent", "reason": "no reference match - needs amount/date fallback search"}

    dated = [c for c in candidates if within_date_window(gateway_record, c, date_window_days)]
    if not dated:
        return {"status": "needs_agent", "reason": "reference matched but all candidates fall outside the date window"}

    single_exact = [c for c in dated if abs(c["settled_amount"] - net) <= AMOUNT_EPSILON]
    if len(single_exact) == 1:
        return {"status": "matched", "utrs": [single_exact[0]["utr_number"]], "method": "deterministic"}

    if len(single_exact) > 1:
        return {
            "status": "exception",
            "type": AMBIGUOUS_MULTIPLE_CANDIDATES,
            "reason": f"{len(single_exact)} reference-matched candidates all settle the exact net amount - "
                      f"cannot deterministically pick one (likely duplicate settlement)",
        }

    if len(dated) >= 2:
        # Collect ALL valid pairs, not just the first - returning early
        # would silently pick an arbitrary answer when two pairs both sum
        # to net.
        valid_pairs = []
        for i in range(len(dated)):
            for j in range(i + 1, len(dated)):
                pair_sum = dated[i]["settled_amount"] + dated[j]["settled_amount"]
                if abs(pair_sum - net) <= AMOUNT_EPSILON:
                    valid_pairs.append((dated[i]["utr_number"], dated[j]["utr_number"]))

        if len(valid_pairs) == 1:
            return {"status": "matched", "utrs": list(valid_pairs[0]), "method": "deterministic"}
        if len(valid_pairs) > 1:
            return {
                "status": "exception",
                "type": AMBIGUOUS_MULTIPLE_CANDIDATES,
                "reason": f"{len(valid_pairs)} different pairs of reference-matched candidates all sum to the net amount - cannot deterministically pick one",
            }

    return {"status": "needs_agent", "reason": "reference matched but amount does not reconcile exactly - needs judgment on whether the gap is explainable"}


def validate_input(gateway_records, bank_records):
    """Fails fast on structurally invalid input, before any matching or
    agent-stage LLM calls happen. Found via a deliberately noisy stress
    test (data/noisy_stress_generator.py, not the curated submission
    dataset): a duplicate transaction_id previously flowed silently all
    the way through matching and the agent stage, only getting caught by
    eval/metrics.py's duplicate guard at metrics-computation time - by
    which point real API budget could already have been spent processing
    input that was invalid from the start. Raises ValueError with a clear
    message instead. See docs/DECISIONS.md.
    """
    txn_ids = [g["transaction_id"] for g in gateway_records]
    seen, dupe_txns = set(), set()
    for tid in txn_ids:
        (dupe_txns if tid in seen else seen).add(tid)
    if dupe_txns:
        raise ValueError(f"duplicate transaction_id(s) in gateway_records: {dupe_txns}")

    utrs = [b["utr_number"] for b in bank_records]
    seen, dupe_utrs = set(), set()
    for utr in utrs:
        (dupe_utrs if utr in seen else seen).add(utr)
    if dupe_utrs:
        raise ValueError(f"duplicate utr_number(s) in bank_records: {dupe_utrs}")


def run_deterministic_stage(gateway_records, bank_records, on_progress=None, date_window_days=DATE_WINDOW_DAYS):
    """Runs the deterministic matcher over every gateway record.

    Returns (matched, exceptions, needs_agent) - three lists. Claimed bank
    records are removed from the pool as they're matched so later records
    can't double-claim the same settlement row.

    on_progress, if given, is called after each record as
    on_progress(index, total, event_dict) - optional and backward
    compatible, added for the API layer's live progress reporting. Every
    existing caller (eval/run_batch.py, the test suite) omits it and is
    unaffected.

    date_window_days: see within_date_window()'s docstring. Defaults to
    the module constant, so every existing caller is unaffected; exists
    so a merchant-specific settlement window (see
    agent/merchant_config.py) can be applied for a specific run without
    touching this function's core logic or forking it."""
    validate_input(gateway_records, bank_records)
    unclaimed = list(bank_records)
    matched, exceptions, needs_agent = [], [], []

    for i, gw in enumerate(gateway_records):
        result = try_resolve(gw, unclaimed, date_window_days)
        if result["status"] == "matched":
            matched.append({
                "transaction_id": gw["transaction_id"],
                "utrs": result["utrs"],
                "method": result["method"],
            })
            unclaimed = [b for b in unclaimed if b["utr_number"] not in result["utrs"]]
        elif result["status"] == "exception":
            exceptions.append({
                "transaction_id": gw["transaction_id"],
                "type": result["type"],
                "reason": result["reason"],
            })
        else:
            needs_agent.append({"gateway_record": gw, "reason": result["reason"]})

        if on_progress:
            on_progress(i + 1, len(gateway_records), {"stage": "deterministic", "transaction_id": gw["transaction_id"], "status": result["status"]})

    return matched, exceptions, needs_agent, unclaimed
