"""Deterministic synthetic data for N-way batch settlement scenarios.

Self-contained, unlike refund_generator.py which reads the real curated
dataset - the curated gateway_transactions.json has no
settlement_batch_id field at all (batch settlement isn't a concept the
core 52-record submission dataset models), so there's no real data to
build scenarios against here. This generates its own small,
purpose-built gateway/bank record set instead, covering:

  1. A clean batch: 4 gateway transactions sharing a batch_id, one bank
     credit line whose amount exactly equals their sum.
  2. A batch whose credited amount doesn't match its expected sum (a
     real settlement discrepancy - e.g. a fee miscalculation upstream).
  3. A batch_id with gateway transactions but no corresponding bank
     credit line at all (hasn't settled yet).
  4. A small group (3 transactions) with NO batch_id, whose sum exactly
     matches an otherwise-unexplained bank credit line - exercises the
     bounded subset-sum fallback's clean case.
  5. A deliberately ambiguous case: two different small groups from a
     shared unbatched pool that both sum to the same unexplained credit
     amount - exercises the fallback's ambiguity detection.
"""

SEED_NOTE = "hand-constructed exact amounts, not randomly seeded - the ambiguity and gap scenarios need precise engineered collisions that a random generator would need extra machinery to guarantee anyway."


def generate():
    """Returns (gateway_records, bank_batch_records) - see module
    docstring for the five scenarios covered."""
    gateway_records = []
    bank_batch_records = []

    # 1. Clean batch
    clean_batch = [
        {"transaction_id": "batch_txn_001", "net_amount": 1000.00, "settlement_batch_id": "BATCH_CLEAN_01"},
        {"transaction_id": "batch_txn_002", "net_amount": 2500.50, "settlement_batch_id": "BATCH_CLEAN_01"},
        {"transaction_id": "batch_txn_003", "net_amount": 750.25, "settlement_batch_id": "BATCH_CLEAN_01"},
        {"transaction_id": "batch_txn_004", "net_amount": 3200.00, "settlement_batch_id": "BATCH_CLEAN_01"},
    ]
    gateway_records += clean_batch
    bank_batch_records.append({
        "batch_id": "BATCH_CLEAN_01",
        "credited_amount": round(sum(r["net_amount"] for r in clean_batch), 2),
    })

    # 2. Batch with a genuine discrepancy
    gap_batch = [
        {"transaction_id": "batch_txn_101", "net_amount": 5000.00, "settlement_batch_id": "BATCH_GAP_01"},
        {"transaction_id": "batch_txn_102", "net_amount": 1200.00, "settlement_batch_id": "BATCH_GAP_01"},
    ]
    gateway_records += gap_batch
    bank_batch_records.append({
        "batch_id": "BATCH_GAP_01",
        "credited_amount": 6100.00,  # expected 6200.00 - a real Rs 100 shortfall
    })

    # 3. Batch that hasn't settled yet (no bank credit line at all)
    unsettled_batch = [
        {"transaction_id": "batch_txn_201", "net_amount": 800.00, "settlement_batch_id": "BATCH_PENDING_01"},
        {"transaction_id": "batch_txn_202", "net_amount": 1500.00, "settlement_batch_id": "BATCH_PENDING_01"},
    ]
    gateway_records += unsettled_batch
    # deliberately no corresponding bank_batch_records entry

    # 4. Unbatched pool - clean bounded-subset case (3-of-N summing exactly)
    unbatched_clean_pool = [
        {"transaction_id": "unbatched_301", "net_amount": 400.00},
        {"transaction_id": "unbatched_302", "net_amount": 600.00},
        {"transaction_id": "unbatched_303", "net_amount": 250.00},
        {"transaction_id": "unbatched_304", "net_amount": 9999.99},  # noise, doesn't belong to any group
    ]
    gateway_records += unbatched_clean_pool
    # 400 + 600 + 250 = 1250.00, the only combination in this pool that sums to it
    bank_batch_records.append({"batch_id": None, "credited_amount": 1250.00, "label": "unbatched_clean_credit"})

    # 5. Unbatched pool - deliberately ambiguous (two different groups sum to the same total).
    # Amounts deliberately far from the clean pool's range (100s-1000s) so this
    # scenario's target doesn't accidentally collide with combinations drawn
    # from the clean pool when both are searched together in one combined
    # request (found for real via test_batch_endpoint.py testing the full
    # combined pool through the actual endpoint, not just this scenario in
    # isolation - see docs/DECISIONS.md).
    unbatched_ambiguous_pool = [
        {"transaction_id": "unbatched_401", "net_amount": 1100.00},
        {"transaction_id": "unbatched_402", "net_amount": 1200.00},
        {"transaction_id": "unbatched_403", "net_amount": 1150.00},
        {"transaction_id": "unbatched_404", "net_amount": 1150.00},
    ]
    gateway_records += unbatched_ambiguous_pool
    # {401,402} = 2300.00 AND {403,404} = 2300.00 - genuinely ambiguous by design
    bank_batch_records.append({"batch_id": None, "credited_amount": 2300.00, "label": "unbatched_ambiguous_credit"})

    return gateway_records, bank_batch_records
