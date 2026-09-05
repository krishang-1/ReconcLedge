"""Deterministic synthetic data for FX/multi-currency reconciliation
scenarios. Self-contained, like batch_generator.py - the curated
gateway_transactions.json has no currency field at all (implicitly INR
throughout), so there's no real data to build FX scenarios against.

Covers: a clean match within a realistic USD->INR rate band, a
transaction whose settled amount is genuinely implausible for the given
band (a real conversion error or wrong-currency mixup), a same-currency
pair fed in by mistake (to exercise the "not an FX case" guard), and a
boundary case sitting exactly at the edge of the rate band.
"""


def generate():
    """Returns a list of (gateway_record, bank_record, rate_min, rate_max, markup_bps) tuples."""
    scenarios = []

    # 1. Clean match - USD 100 settling in INR within a realistic day-range band
    scenarios.append((
        {"transaction_id": "fx_txn_001", "amount": 100.00, "currency": "USD"},
        {"settled_amount": 8300.00, "currency": "INR"},
        82.50, 83.50, 0,
    ))

    # 2. Implausible - settled amount far outside any realistic USD->INR band
    scenarios.append((
        {"transaction_id": "fx_txn_002", "amount": 100.00, "currency": "USD"},
        {"settled_amount": 5000.00, "currency": "INR"},
        82.50, 83.50, 0,
    ))

    # 3. Same currency fed in by mistake - not an FX case at all
    scenarios.append((
        {"transaction_id": "fx_txn_003", "amount": 5000.00, "currency": "INR"},
        {"settled_amount": 5000.00, "currency": "INR"},
        82.50, 83.50, 0,
    ))

    # 4. Boundary - settled amount sits exactly at the low edge of the band
    scenarios.append((
        {"transaction_id": "fx_txn_004", "amount": 100.00, "currency": "USD"},
        {"settled_amount": 8250.00, "currency": "INR"},  # exactly 100 * 82.50
        82.50, 83.50, 0,
    ))

    # 5. With a real markup applied - a 50 bps (0.5%) conversion fee
    scenarios.append((
        {"transaction_id": "fx_txn_005", "amount": 200.00, "currency": "USD"},
        {"settled_amount": round(200.00 * 83.00 * 0.995, 2), "currency": "INR"},
        82.50, 83.50, 50,
    ))

    return scenarios
