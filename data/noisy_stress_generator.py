"""Generates a deliberately noisy, high-volume, edge-case-heavy dataset for
stress-testing the pipeline's robustness - distinct from
data/synthetic_generator.py, which produces the clean, curated dataset
used for the actual submission and reported metrics. This is NOT that
dataset. It exists to answer a different question: not "is the reported
95% accurate" but "does the pipeline behave sanely, or at least fail
clearly, under conditions the curated dataset never exercises."

Deliberately injected stressors:
- High volume (500 gateway transactions, ~650 bank records - far more
  than the 52-record submission dataset)
- Extreme amounts: near-zero (Rs 0.50), very large (Rs 5,000,000)
- Small transactions specifically sized to stress the threshold-
  consistency fix (net_amount just above/below the Rs 50 absolute floor)
- Intentional duplicate transaction_ids (a genuine input-validation edge
  case the curated generator never produces, since it always generates
  unique IDs by construction)
- Near-collision reference tokens: bank narrations that coincidentally
  contain a substring resembling an unrelated transaction's token, to
  stress-test the "negligible collision probability" assumption in
  matcher.py's reference lookup at higher volume
- A large pool of true orphans on both sides (records with no
  counterpart at all)
- Wide date range spanning years, not the curated dataset's ~20-day window
"""

import json
import random

SEED = 999  # different seed from the submission dataset - deliberately not reusing it
COUNT_GATEWAY = 500
COUNT_ORPHAN_BANK = 100


def random_hex(n):
    return "".join(random.choices("0123456789abcdef", k=n))


def ref_token(order_id):
    return order_id.split("_")[1][:8]


def make_gateway_record(force_duplicate_id=None, force_small_amount=False, force_extreme_amount=False):
    if force_extreme_amount:
        amount = random.choice([0.5, 1.0, 5_000_000.0, 4_999_999.99])
    elif force_small_amount:
        amount = round(random.uniform(50.0, 400.0), 2)  # near the Rs50 threshold boundary
    else:
        amount = round(random.uniform(10.0, 100_000.0), 2)

    fee = round(amount * 0.02, 2)
    tax = round(fee * 0.18, 2)
    net_amount = round(amount - fee - tax, 2)

    txn_id = force_duplicate_id or ("txn_" + random_hex(14))
    order_id = "order_" + random_hex(12)
    year = random.choice([2024, 2025, 2026])
    month = random.randint(1, 12)
    day = random.randint(1, 28)

    return {
        "transaction_id": txn_id,
        "order_id": order_id,
        "amount": amount,
        "fee": fee,
        "tax": tax,
        "net_amount": net_amount,
        "timestamp": f"{year:04d}-{month:02d}-{day:02d}T00:00:00",
        "status": "captured",
        "customer_ref": "cust_" + random_hex(8),
    }


def make_bank_record(narration_ref, settled_amount, year=2026, month=8, day=10, near_collision_junk=""):
    return {
        "utr_number": "UTR" + "".join(random.choices("0123456789", k=12)),
        "settled_amount": settled_amount,
        "settlement_date": f"{year:04d}-{month:02d}-{day:02d}",
        "narration": f"NEFT CR {narration_ref}{near_collision_junk} SETTLEMENT",
    }


def generate():
    random.seed(SEED)
    gateway_records = []
    bank_records = []

    # A batch of genuinely matchable records (so the pipeline has *something*
    # real to resolve, not pure noise) - about 60% of volume
    for _ in range(int(COUNT_GATEWAY * 0.55)):
        gw = make_gateway_record()
        gateway_records.append(gw)
        token = ref_token(gw["order_id"])
        year, month, day = map(int, gw["timestamp"][:10].split("-"))
        bank_records.append(make_bank_record(token, gw["net_amount"], year, month, day))

    # Small-amount records specifically stressing the threshold-consistency fix
    for _ in range(30):
        gw = make_gateway_record(force_small_amount=True)
        gateway_records.append(gw)
        token = ref_token(gw["order_id"])
        year, month, day = map(int, gw["timestamp"][:10].split("-"))
        extra_charge = round(random.uniform(5, 45), 2)
        settled = round(gw["net_amount"] - extra_charge, 2)
        bank_records.append(make_bank_record(token, settled, year, month, day))

    # Extreme-amount records (near-zero, very large)
    for _ in range(20):
        gw = make_gateway_record(force_extreme_amount=True)
        gateway_records.append(gw)
        token = ref_token(gw["order_id"])
        year, month, day = map(int, gw["timestamp"][:10].split("-"))
        bank_records.append(make_bank_record(token, gw["net_amount"], year, month, day))

    # Intentional duplicate transaction_ids - a genuine input-validation
    # stressor the curated dataset never produces
    dup_id = "txn_" + random_hex(14)
    for _ in range(3):
        gw = make_gateway_record(force_duplicate_id=dup_id)
        gateway_records.append(gw)
        token = ref_token(gw["order_id"])
        year, month, day = map(int, gw["timestamp"][:10].split("-"))
        bank_records.append(make_bank_record(token, gw["net_amount"], year, month, day))

    # True orphan gateway transactions (no counterpart at all)
    while len(gateway_records) < COUNT_GATEWAY:
        gateway_records.append(make_gateway_record())

    # True orphan bank records, including some with near-collision junk
    # appended to their narration - stress-testing whether an unrelated
    # transaction's reference token could ever coincidentally match
    existing_tokens = [ref_token(g["order_id"]) for g in gateway_records[:50]]
    for _ in range(COUNT_ORPHAN_BANK):
        junk = random.choice(["", " " + random.choice(existing_tokens)[:3], " REF" + random_hex(4)])
        bank_records.append(make_bank_record(random_hex(8), round(random.uniform(10, 50000), 2), near_collision_junk=junk))

    random.shuffle(gateway_records)
    random.shuffle(bank_records)
    return gateway_records, bank_records


if __name__ == "__main__":
    gw, bank = generate()
    with open("noisy_gateway_transactions.json", "w") as f:
        json.dump(gw, f, indent=2)
    with open("noisy_bank_settlement.json", "w") as f:
        json.dump(bank, f, indent=2)
    print(f"gateway: {len(gw)} records, bank: {len(bank)} records")
