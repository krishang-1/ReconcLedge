"""Deterministic synthetic data for marketplace/Route-style split
settlement scenarios. Self-contained, like batch_generator.py and
fx_generator.py - the curated dataset has no Linked Account/transfer
concept at all, so there's no real data to build scenarios against.

Covers: a clean fully-settled split (the Mplace.com phone+case example
from Razorpay's own Route documentation - a phone sold by one seller,
a case by another, commission taken by the platform), a split with one
linked account's transfer genuinely on hold (the ExploreLifeTraveling
case study's real use case - a host's payout withheld until a
cancellation window passes), a split with a reversed transfer (a refund
clawing back a previously-made vendor payout), and a genuine mismatch
(commission miscalculated upstream).
"""


def generate():
    """Returns a list of (gateway_record, transfers, platform_commission) tuples."""
    scenarios = []

    # 1. Clean fully-settled split - grounded in Razorpay's own published
    # Route example: a Rs 10,000 phone (Seller A) + Rs 500 case (Seller B),
    # net_amount here standing in for the post-MDR amount actually split.
    scenarios.append((
        {"transaction_id": "route_txn_001", "net_amount": 10350.00},
        [
            {"linked_account_id": "seller_a", "amount": 9500.00, "status": "settled"},
            {"linked_account_id": "seller_b", "amount": 475.00, "status": "settled"},
        ],
        375.00,  # platform commission
    ))

    # 2. One linked account's transfer on hold - grounded in the real
    # ExploreLifeTraveling case study's documented use of Route's
    # Settlement On Hold feature (host payout withheld pending a
    # cancellation window).
    scenarios.append((
        {"transaction_id": "route_txn_002", "net_amount": 5000.00},
        [
            {"linked_account_id": "host_x", "amount": 4500.00, "status": "on_hold"},
        ],
        500.00,
    ))

    # 3. Reversed transfer - a refund clawing back a previously-made
    # vendor payout, grounded in Route's documented "Reverse transferred
    # funds and manage customer refunds with automated reversals." The
    # reversed transfer is still counted toward the original net_amount
    # (it WAS part of the original split at the time it was made) - see
    # reconcile_split_transaction()'s docstring for why reversed amounts
    # are added, not subtracted, when checking the ledger balances.
    scenarios.append((
        {"transaction_id": "route_txn_003", "net_amount": 2000.00},
        [
            {"linked_account_id": "seller_c", "amount": 1800.00, "status": "reversed"},
        ],
        200.00,  # commission was kept even though the vendor's payout was reversed
    ))

    # 4. Genuine mismatch - commission miscalculated upstream
    scenarios.append((
        {"transaction_id": "route_txn_004", "net_amount": 1000.00},
        [
            {"linked_account_id": "seller_d", "amount": 850.00, "status": "settled"},
        ],
        100.00,  # 850 + 100 = 950, not 1000 - a real Rs 50 shortfall
    ))

    return scenarios
