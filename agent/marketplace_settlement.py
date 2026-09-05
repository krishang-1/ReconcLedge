"""Marketplace / Route-style multi-party settlement reconciliation.

Grounded in Razorpay Route's documented mechanics: ONE customer payment
splits into transfers to multiple Linked Accounts (vendors), after the
platform's commission is deducted. The opposite direction from
batch_settlement.py's N-way batching, and a different shape from the
base matcher's one-to-one model.

Real Route vocabulary throughout: a Linked Account is a vendor's
settlement destination, "Settlement On Hold" is Route's own feature for
deferring a transfer (e.g. withholding a payout until a cancellation
window passes), and a reversal claws back a previously-made payout.

A standalone path, not wired into matcher.py or _run_pipeline.
"""

from matcher import AMOUNT_EPSILON

FULLY_RECONCILED = "fully_reconciled"
PENDING_HOLD = "pending_hold"
REVERSAL_ACCOUNTED = "reversal_accounted"
MISMATCH = "mismatch"

TRANSFER_SETTLED = "settled"
TRANSFER_ON_HOLD = "on_hold"
TRANSFER_REVERSED = "reversed"
VALID_STATUSES = {TRANSFER_SETTLED, TRANSFER_ON_HOLD, TRANSFER_REVERSED}


def reconcile_split_transaction(gateway_record, transfers, platform_commission):
    """gateway_record: {"transaction_id", "net_amount"} - net_amount here
    is the amount actually available to split (gross minus the gateway's
    own processing fee), matching this project's existing net_amount
    convention from synthetic_generator.py. Route splits the NET
    collected amount, not the gross - the gateway's own MDR is already
    deducted before any commission/vendor split happens (confirmed via
    real-world Route+Magento reconciliation documentation).

    transfers: list of {"linked_account_id", "amount", "status"} where
    status is one of TRANSFER_SETTLED / TRANSFER_ON_HOLD /
    TRANSFER_REVERSED.

    Returns:
        {
            "transaction_id": str,
            "net_amount": float,
            "platform_commission": float,
            "settled_transfer_total": float,
            "on_hold_transfer_total": float,
            "reversed_transfer_total": float,
            "status": one of the module constants,
            "gap": float,   # unexplained difference, 0.0 if reconciled
            "reason": str,
        }

    A reversed transfer is ADDED to the accounted-for total, not
    subtracted - a real design point worth being explicit about, since
    the opposite (subtracting it) is the intuitive-looking mistake. A
    reversal is a fact about where the ORIGINAL net_amount was allocated
    (this transfer WAS part of the original split, at the moment it was
    made) - it just means that allocation has since been clawed back.
    The clawed-back money going somewhere else (typically back to the
    customer as part of a refund) is a genuinely separate reconciliation
    question this function does not attempt to answer - see the
    "not attempted here" note below.

    Classification, checking whether settled + on_hold + commission +
    reversed accounts for the full net_amount (within AMOUNT_EPSILON,
    the same rounding-slack constant matcher.py uses):
    - Balances, no on_hold, no reversed -> fully_reconciled.
    - Balances, at least one transfer on_hold (regardless of any
      reversal) -> pending_hold. The money is fully accounted for, it's
      just not all settled yet - a real, expected state (Route's own
      documented on-hold feature exists precisely for this), not an
      error to flag the same way a genuine gap is. Takes priority over
      reversal_accounted below since there's still live, unresolved
      money movement to track.
    - Balances, at least one reversal, no on_hold -> reversal_accounted.
      Distinct from a clean fully_reconciled case because a reversal
      happened and is worth a human noticing, even though the
      arithmetic is fine.
    - Doesn't balance either way -> mismatch, with the actual gap
      reported (positive = money unaccounted for, negative = more was
      distributed than was collected) so a human reviewer sees the real
      number, not just a boolean.

    Not attempted here, a real named scope limit: whether a reversed
    transfer actually corresponds to a real customer-side refund (cross-
    checking against refund_matcher.py's reconciliation) is a genuinely
    separate, harder question - this function only confirms the
    split-transaction ledger is internally consistent, not that a
    reversal was itself triggered by a legitimate refund event.

    Raises ValueError on an unrecognized transfer status - same
    fail-fast discipline as matcher.py's validate_input() and
    batch_settlement.py's duplicate-batch_id guard, rather than silently
    treating a typo'd or new status as "settled" and getting the
    arithmetic wrong.
    """
    net_amount = gateway_record["net_amount"]

    for t in transfers:
        if t["status"] not in VALID_STATUSES:
            raise ValueError(
                f"unrecognized transfer status '{t['status']}' for linked_account_id "
                f"'{t.get('linked_account_id')}' - expected one of {sorted(VALID_STATUSES)}"
            )

    settled_total = round(sum(t["amount"] for t in transfers if t["status"] == TRANSFER_SETTLED), 2)
    on_hold_total = round(sum(t["amount"] for t in transfers if t["status"] == TRANSFER_ON_HOLD), 2)
    reversed_total = round(sum(t["amount"] for t in transfers if t["status"] == TRANSFER_REVERSED), 2)

    accounted_for = round(settled_total + on_hold_total + platform_commission + reversed_total, 2)
    gap = round(net_amount - accounted_for, 2)
    balances = abs(gap) <= AMOUNT_EPSILON

    if not balances:
        status = MISMATCH
        reason = (
            f"settled ({settled_total}) + on_hold ({on_hold_total}) + commission "
            f"({platform_commission}) + reversed ({reversed_total}) = {accounted_for}, "
            f"which does not match net_amount ({net_amount}) - unexplained gap of {gap}"
        )
    elif on_hold_total > 0:
        status = PENDING_HOLD
        reason = (
            f"fully accounted for ({accounted_for} matches net_amount {net_amount}), but "
            f"{on_hold_total} is still on hold across one or more linked accounts - not yet "
            f"fully settled, not an error"
        )
        gap = 0.0
    elif reversed_total > 0:
        status = REVERSAL_ACCOUNTED
        reason = (
            f"fully accounted for ({accounted_for} matches net_amount {net_amount}); "
            f"{reversed_total} of the original split has since been reversed - worth a "
            f"human noting, though the ledger itself is internally consistent"
        )
        gap = 0.0
    else:
        status = FULLY_RECONCILED
        reason = f"settled transfers + commission exactly account for net_amount ({net_amount})"
        gap = 0.0

    return {
        "transaction_id": gateway_record["transaction_id"],
        "net_amount": net_amount,
        "platform_commission": platform_commission,
        "settled_transfer_total": settled_total,
        "on_hold_transfer_total": on_hold_total,
        "reversed_transfer_total": reversed_total,
        "status": status,
        "gap": gap,
        "reason": reason,
    }
