"""Deterministic synthetic data for chargeback/dispute reconciliation
scenarios. Self-contained, like the other Tier 2/3 generators - the
curated dataset has no dispute concept at all.

Covers: a fresh in-flight dispute (issuing-bank-initiated, provisional
debit just applied), a dispute the merchant won (reversed), a dispute
the merchant lost (finalized debit), an escalated arbitration case
(still in-flight, but at the costlier real escalation stage), and an
invalid dispute where the disputed amount exceeds what was actually
captured.
"""


def generate():
    """Returns a list of (gateway_record, chargeback_event) tuples."""
    scenarios = []

    # 1. Fresh in-flight dispute, issuing-bank-initiated (fraud suspicion)
    scenarios.append((
        {"transaction_id": "cb_txn_001", "net_amount": 5000.00},
        {"status": "open", "disputed_amount": 5000.00, "chargeback_fee": 250.00, "initiated_by": "issuing_bank"},
    ))

    # 2. Won - merchant's evidence was accepted, debit reversed
    scenarios.append((
        {"transaction_id": "cb_txn_002", "net_amount": 3000.00},
        {"status": "won", "disputed_amount": 3000.00, "chargeback_fee": 250.00, "initiated_by": "customer"},
    ))

    # 3. Lost - evidence rejected, debit is now final
    scenarios.append((
        {"transaction_id": "cb_txn_003", "net_amount": 2000.00},
        {"status": "lost", "disputed_amount": 2000.00, "chargeback_fee": 250.00, "initiated_by": "customer"},
    ))

    # 4. Escalated to arbitration - still in-flight, but at the costliest stage
    scenarios.append((
        {"transaction_id": "cb_txn_004", "net_amount": 10000.00},
        {"status": "arbitration", "disputed_amount": 10000.00, "chargeback_fee": 500.00, "initiated_by": "customer"},
    ))

    # 5. Invalid - disputed amount exceeds what was actually captured
    scenarios.append((
        {"transaction_id": "cb_txn_005", "net_amount": 1000.00},
        {"status": "open", "disputed_amount": 1500.00, "chargeback_fee": 250.00, "initiated_by": "issuing_bank"},
    ))

    return scenarios
