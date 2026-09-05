"""Chargeback / dispute reconciliation.

Grounded in Razorpay's documented dispute lifecycle (real statuses:
Open, Under Review, Pre-Arbitration, Arbitration, Won, Lost). The key
difference from a refund: a chargeback is initiated by the cardholder's
issuing bank and debits the merchant PROVISIONALLY at dispute creation,
before any outcome is known - only becoming final (lost) or reversed
(won) after evidence review, which can take 30-90 days.

A standalone path, not wired into matcher.py or _run_pipeline.

Named assumption, not confirmed Razorpay policy: the chargeback fee is
modeled as applying regardless of outcome, matching how most card-
network dispute fees work. Flagged rather than assumed with false
confidence. "Closed" is deliberately not modeled - its real meaning
isn't determinable from status alone.
"""

from matcher import AMOUNT_EPSILON

IN_FLIGHT = "in_flight"
REVERSED = "reversed"
FINALIZED_DEBIT = "finalized_debit"
INVALID_DISPUTE = "invalid_dispute"

IN_FLIGHT_STATUSES = {"open", "under_review", "pre_arbitration", "arbitration"}
RESOLVED_WON = "won"
RESOLVED_LOST = "lost"
VALID_STATUSES = IN_FLIGHT_STATUSES | {RESOLVED_WON, RESOLVED_LOST}


def reconcile_chargeback(gateway_record, chargeback_event):
    """gateway_record: {"transaction_id", "net_amount"} - the ORIGINAL,
    undisputed settled amount.

    chargeback_event: {"status", "disputed_amount", "chargeback_fee",
    "initiated_by"} - initiated_by is "issuing_bank" or "customer" (real
    Razorpay-documented distinction), informational only, doesn't affect
    classification.

    Returns:
        {
            "transaction_id": str,
            "original_net_amount": float,
            "disputed_amount": float,
            "chargeback_fee": float,
            "status": str,               # the input status, echoed back
            "classification": one of the module constants,
            "current_expected_balance": float | None,
            "requires_human_review": True,   # always - see below
            "reason": str,
        }

    Classification:
    - status in IN_FLIGHT_STATUSES -> "in_flight": the provisional debit
      is ALREADY applied (current_expected_balance = net_amount -
      disputed_amount - chargeback_fee), outcome not yet known.
    - status == "won" -> "reversed": the provisional debit has been
      reversed; current_expected_balance = net_amount - chargeback_fee
      (see the module's named fee assumption above).
    - status == "lost" -> "finalized_debit": the provisional debit is
      now permanent; current_expected_balance = net_amount -
      disputed_amount - chargeback_fee, same number as in_flight but
      now a closed, final state rather than a pending one.
    - disputed_amount exceeds net_amount (an anomaly - a chargeback
      can't legitimately exceed what was actually captured) ->
      "invalid_dispute", flagged rather than silently computing a
      negative/nonsensical balance.

    requires_human_review is unconditionally True for every outcome,
    same design commitment as fx_reconciliation.py: a chargeback is, by
    its nature, a dispute a bank or cardholder raised about this
    transaction - genuinely worth a human's awareness regardless of
    which way it resolved, not just the in-flight/invalid cases.

    Raises ValueError on an unrecognized status - same fail-fast
    discipline as every other reconciliation module in this project.
    """
    status = chargeback_event["status"]
    if status not in VALID_STATUSES:
        raise ValueError(
            f"unrecognized chargeback status '{status}' - expected one of {sorted(VALID_STATUSES)}"
        )

    net_amount = gateway_record["net_amount"]
    disputed_amount = chargeback_event["disputed_amount"]
    fee = chargeback_event["chargeback_fee"]

    base_result = {
        "transaction_id": gateway_record["transaction_id"],
        "original_net_amount": net_amount,
        "disputed_amount": disputed_amount,
        "chargeback_fee": fee,
        "status": status,
        "requires_human_review": True,
    }

    if disputed_amount > net_amount + AMOUNT_EPSILON:
        return {
            **base_result,
            "classification": INVALID_DISPUTE,
            "current_expected_balance": None,
            "reason": f"disputed_amount ({disputed_amount}) exceeds the original net_amount "
                      f"({net_amount}) - a chargeback cannot legitimately exceed what was captured",
        }

    if status in IN_FLIGHT_STATUSES:
        balance = round(net_amount - disputed_amount - fee, 2)
        classification = IN_FLIGHT
        reason = (f"dispute status '{status}' - provisional debit already applied, outcome pending; "
                  f"current expected balance {balance}")
    elif status == RESOLVED_WON:
        balance = round(net_amount - fee, 2)
        classification = REVERSED
        reason = (f"dispute won - provisional debit reversed; current expected balance {balance} "
                  f"(chargeback fee still applies - see module docstring's named assumption)")
    else:  # lost
        balance = round(net_amount - disputed_amount - fee, 2)
        classification = FINALIZED_DEBIT
        reason = f"dispute lost - debit is now final; current expected balance {balance}"

    return {**base_result, "classification": classification, "current_expected_balance": balance, "reason": reason}
