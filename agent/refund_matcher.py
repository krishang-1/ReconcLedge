"""Refund / partial-capture reconciliation - a standalone path.

A merchant can refund fully or partially after capture, so a bank
settlement stops matching the original net_amount for reasons that have
nothing to do with a matching bug. This answers a different question
than matcher.py: given a transaction and refund events against it, does
the refund activity account for an amount gap?

Deliberately NOT wired into _run_pipeline (unlike escalation.py) - it
has its own endpoint (POST /refunds/reconcile). Refund events are a
separate kind of input, and staying outside the pipeline makes it
physically incapable of affecting the 37/3/12 split or the reported
match rate.

Named scope limit: this doesn't write to audit_log or tie to a run_id -
a real deployment would want refund events linked to the run whose
settlement they explain.
"""

from matcher import AMOUNT_EPSILON

FULL_REFUND = "full_refund"
PARTIAL_REFUND = "partial_refund"
OVER_REFUNDED = "over_refunded"


def reconcile_refunds(gateway_records, refund_events):
    """Returns one reconciliation record per transaction_id that appears
    in refund_events - transactions with no refund activity at all are
    not included (this function answers "what do these refund events
    mean", not "here's the status of every transaction").

    Each record:
        {
            "transaction_id": str,
            "known_transaction": bool,        # False if transaction_id isn't in gateway_records
            "original_amount": float | None,  # None if unknown transaction
            "total_refunded": float,
            "refund_count": int,
            "net_expected_settlement": float | None,
            "classification": "full_refund" | "partial_refund" | "over_refunded",
        }

    A transaction_id not found in gateway_records is still reported
    (known_transaction=False) rather than silently dropped or raising -
    a refund event referencing an unknown transaction is exactly the
    kind of real-world bad input this needs to surface, not hide.

    over_refunded (total refunded exceeds the original captured amount,
    beyond AMOUNT_EPSILON rounding slack) is flagged rather than
    computing a negative net_expected_settlement - in real settlement
    this is invalid on its face (you cannot refund more than was
    captured), most often caused by a duplicate refund submission, and
    should be surfaced for investigation, not silently accepted as a
    number.
    """
    amount_by_id = {g["transaction_id"]: g.get("net_amount") for g in gateway_records}
    # Same non-issue as escalation.py's identical pattern (see its note):
    # a duplicate transaction_id in gateway_records would silently let
    # one amount win, but this function's only real caller
    # (POST /refunds/reconcile) always sources gateway_records from
    # jobs._load_data()'s static curated dataset, never a request body -
    # unlike batch_settlement.py's analogous dict, which WAS built
    # directly from caller input and needed an actual fail-fast fix.

    by_txn = {}
    for event in refund_events:
        txn_id = event["transaction_id"]
        by_txn.setdefault(txn_id, []).append(event)

    results = []
    for txn_id, events in by_txn.items():
        original_amount = amount_by_id.get(txn_id)
        known = txn_id in amount_by_id
        total_refunded = round(sum(e["refund_amount"] for e in events), 2)

        if not known:
            results.append({
                "transaction_id": txn_id,
                "known_transaction": False,
                "original_amount": None,
                "total_refunded": total_refunded,
                "refund_count": len(events),
                "net_expected_settlement": None,
                "classification": None,
            })
            continue

        net_expected = round(original_amount - total_refunded, 2)
        if total_refunded > original_amount + AMOUNT_EPSILON:
            classification = OVER_REFUNDED
        elif total_refunded >= original_amount - AMOUNT_EPSILON:
            classification = FULL_REFUND
        else:
            classification = PARTIAL_REFUND

        results.append({
            "transaction_id": txn_id,
            "known_transaction": True,
            "original_amount": original_amount,
            "total_refunded": total_refunded,
            "refund_count": len(events),
            "net_expected_settlement": net_expected,
            "classification": classification,
        })

    return results
