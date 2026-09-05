"""Deterministic refund-event generator for tests and stress scripts.
Reads the real curated gateway_transactions.json read-only, to build
realistic full/partial/over/unknown refund scenarios against real
transaction_ids and amounts - never writes to or modifies any of the
three real dataset files (gateway_transactions.json,
bank_settlement.json, ground_truth.json), consistent with those being
the untouchable basis for the reported 37/3/12 split and match rate.
"""

import json
import os
import random

SEED = 20260824
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def generate(seed=SEED):
    """Returns a list of refund events against a mix of real transaction
    IDs from the curated dataset, covering: a clean full refund, a
    clean partial refund, an over-refund (duplicate-submission shape:
    the same amount submitted twice), a transaction split across two
    partial refund events that together equal a full refund, and one
    event referencing a transaction_id that doesn't exist at all."""
    rng = random.Random(seed)
    with open(os.path.join(DATA_DIR, "gateway_transactions.json")) as f:
        gateway = json.load(f)

    txns = rng.sample(gateway, 5)
    events = []

    # 1. Clean full refund
    events.append({"transaction_id": txns[0]["transaction_id"], "refund_amount": txns[0]["net_amount"], "refund_date": "2026-08-15"})

    # 2. Clean partial refund (40% of the net amount)
    events.append({"transaction_id": txns[1]["transaction_id"], "refund_amount": round(txns[1]["net_amount"] * 0.4, 2), "refund_date": "2026-08-16"})

    # 3. Over-refund: same amount submitted twice (the realistic shape of
    #    a duplicate refund submission, not just an arbitrary excess)
    single = round(txns[2]["net_amount"] * 0.7, 2)
    events.append({"transaction_id": txns[2]["transaction_id"], "refund_amount": single, "refund_date": "2026-08-17"})
    events.append({"transaction_id": txns[2]["transaction_id"], "refund_amount": single, "refund_date": "2026-08-17"})

    # 4. Two partial refunds that together sum to a full refund
    half = round(txns[3]["net_amount"] / 2, 2)
    events.append({"transaction_id": txns[3]["transaction_id"], "refund_amount": half, "refund_date": "2026-08-18"})
    events.append({"transaction_id": txns[3]["transaction_id"], "refund_amount": txns[3]["net_amount"] - half, "refund_date": "2026-08-19"})

    # 5. Unknown transaction_id - real-world bad/mismatched input
    events.append({"transaction_id": "txn_does_not_exist_anywhere", "refund_amount": 500.0, "refund_date": "2026-08-20"})

    return events
