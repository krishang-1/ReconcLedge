"""Generates synthetic gateway_transactions.json, bank_settlement.json, and
ground_truth.json for the reconciliation agent. Deterministic via SEED so
the dataset is reproducible across runs and across dev/eval splits.

Mismatch types modeled (each gateway transaction is tagged with exactly one):
  CLEAN            - straightforward match, net amount = amount - fee - tax
  FEE_DRIFT         - settled net amount differs from the simple fee formula
                      (e.g. an extra gateway charge), so a naive subtraction
                      won't match it; the agent has to reason about the gap
  TIMING_LAG        - settlement date is 1-3 days after the transaction date
  GARBLED_REF       - bank narration has a truncated/reformatted reference
                      instead of the clean UTR, so exact-match lookup fails
  DUPLICATE         - the same transaction produced two bank settlement rows
  SPLIT             - one transaction settled across two separate bank rows
  ORPHAN_GATEWAY    - transaction exists with no corresponding settlement
                      at all (e.g. settlement still pending)
  ORPHAN_BANK       - a bank settlement row exists with no matching gateway
                      transaction (e.g. a manual adjustment or refund entry)

ground_truth.json maps every gateway transaction_id to its correct
settlement UTR(s) (or null for ORPHAN_GATEWAY), the mismatch_type label,
and a dev/eval split flag. The eval split (~1/3 of records) is the set the
final reported match rate must be computed against - not tuned against.
"""

import json
import random
from datetime import datetime, timedelta

SEED = 42
OUTPUT_DIR = "."
BASE_DATE = datetime(2026, 8, 1)

COUNTS = {
    "CLEAN": 26,
    "FEE_DRIFT": 6,
    "TIMING_LAG": 5,
    "GARBLED_REF": 4,
    "DUPLICATE": 3,
    "SPLIT": 3,
    "ORPHAN_GATEWAY": 5,
}
ORPHAN_BANK_COUNT = 4


def random_hex(n):
    """Returns an n-character random hex string, seeded by random.seed() -
    unlike uuid.uuid4(), which draws from the OS's CSPRNG and is never
    controlled by random.seed(), so using it here would silently break the
    determinism this generator otherwise guarantees. See docs/DECISIONS.md."""
    return "".join(random.choices("0123456789abcdef", k=n))


def make_txn_id():
    """Returns a Razorpay-style transaction id string."""
    return "txn_" + random_hex(14)


def make_utr():
    """Returns a bank-style UTR reference string."""
    return "UTR" + "".join(random.choices("0123456789", k=12))


def garble_token(token):
    """Returns a realistically mangled version of a reference token for narration fields."""
    style = random.choice(["truncate", "case_and_dash", "spaced"])
    if style == "truncate":
        return token[:5]
    if style == "case_and_dash":
        return "-".join([token[:4], token[4:]]).upper()
    return " ".join([token[i:i + 2] for i in range(0, len(token), 2)])


def ref_token(order_id):
    """Returns the order reference token that a bank narration would echo back."""
    return order_id.split("_")[1][:8]


def random_amount():
    """Returns a plausible transaction amount in rupees (as a float, 2dp)."""
    return round(random.uniform(150.0, 45000.0), 2)


def compute_fee_tax(amount):
    """Returns (fee, tax) using Razorpay's standard ~2% + 18% GST on fee model."""
    fee = round(amount * 0.02, 2)
    tax = round(fee * 0.18, 2)
    return fee, tax


def build_gateway_record(mismatch_type, txn_date):
    """Builds a single gateway_transactions record with fields common to all types."""
    amount = random_amount()
    fee, tax = compute_fee_tax(amount)
    net_amount = round(amount - fee - tax, 2)
    return {
        "transaction_id": make_txn_id(),
        "order_id": "order_" + random_hex(12),
        "amount": amount,
        "fee": fee,
        "tax": tax,
        "net_amount": net_amount,
        "timestamp": txn_date.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "captured",
        "customer_ref": "cust_" + random_hex(8),
        "_mismatch_type": mismatch_type,
    }


def build_bank_record(utr, settled_amount, settle_date, narration_ref):
    """Builds a single bank_settlement record. narration_ref is the (possibly
    garbled) order reference token echoed in the narration - the primary
    field a matcher should try before falling back to amount/date proximity."""
    return {
        "utr_number": utr,
        "settled_amount": settled_amount,
        "settlement_date": settle_date.strftime("%Y-%m-%d"),
        "narration": f"NEFT CR {narration_ref} RAZORPAY SETTLEMENT",
    }


def generate():
    """Generates the three output datasets and returns them as a tuple."""
    random.seed(SEED)
    gateway_records = []
    bank_records = []
    ground_truth = []

    for mismatch_type, count in COUNTS.items():
        for _ in range(count):
            txn_date = BASE_DATE + timedelta(days=random.randint(0, 20))
            gw = build_gateway_record(mismatch_type, txn_date)
            gateway_records.append(gw)

            if mismatch_type == "ORPHAN_GATEWAY":
                ground_truth.append({
                    "transaction_id": gw["transaction_id"],
                    "correct_settlement_utrs": None,
                    "mismatch_type": mismatch_type,
                })
                continue

            utr = make_utr()
            settle_date = txn_date + timedelta(
                days=random.randint(1, 3) if mismatch_type == "TIMING_LAG" else 0
            )
            clean_ref = ref_token(gw["order_id"])

            if mismatch_type == "FEE_DRIFT":
                extra_charge = round(random.uniform(5, 40), 2)
                settled_amount = round(gw["net_amount"] - extra_charge, 2)
                bank_records.append(build_bank_record(utr, settled_amount, settle_date, clean_ref))
                ground_truth.append({
                    "transaction_id": gw["transaction_id"],
                    "correct_settlement_utrs": [utr],
                    "mismatch_type": mismatch_type,
                })

            elif mismatch_type == "GARBLED_REF":
                bank_records.append(
                    build_bank_record(utr, gw["net_amount"], settle_date, garble_token(clean_ref))
                )
                ground_truth.append({
                    "transaction_id": gw["transaction_id"],
                    "correct_settlement_utrs": [utr],
                    "mismatch_type": mismatch_type,
                })

            elif mismatch_type == "DUPLICATE":
                bank_records.append(build_bank_record(utr, gw["net_amount"], settle_date, clean_ref))
                dup_utr = make_utr()
                bank_records.append(build_bank_record(dup_utr, gw["net_amount"], settle_date, clean_ref))
                ground_truth.append({
                    "transaction_id": gw["transaction_id"],
                    "correct_settlement_utrs": [utr],
                    "mismatch_type": mismatch_type,
                    "note": f"duplicate settlement row also exists: {dup_utr}",
                })

            elif mismatch_type == "SPLIT":
                part_a = round(gw["net_amount"] / 2, 2)
                part_b = round(gw["net_amount"] - part_a, 2)
                utr_b = make_utr()
                bank_records.append(build_bank_record(utr, part_a, settle_date, clean_ref))
                bank_records.append(build_bank_record(utr_b, part_b, settle_date, clean_ref))
                ground_truth.append({
                    "transaction_id": gw["transaction_id"],
                    "correct_settlement_utrs": [utr, utr_b],
                    "mismatch_type": mismatch_type,
                })

            else:  # CLEAN, TIMING_LAG
                bank_records.append(build_bank_record(utr, gw["net_amount"], settle_date, clean_ref))
                ground_truth.append({
                    "transaction_id": gw["transaction_id"],
                    "correct_settlement_utrs": [utr],
                    "mismatch_type": mismatch_type,
                })

    for _ in range(ORPHAN_BANK_COUNT):
        settle_date = BASE_DATE + timedelta(days=random.randint(0, 23))
        utr = make_utr()
        junk_ref = random_hex(8)
        bank_records.append(build_bank_record(utr, random_amount(), settle_date, junk_ref))
        ground_truth.append({
            "transaction_id": None,
            "orphan_bank_utr": utr,
            "correct_settlement_utrs": None,
            "mismatch_type": "ORPHAN_BANK",
        })

    random.shuffle(ground_truth)
    split_point = int(len(ground_truth) * 0.65)
    for i, record in enumerate(ground_truth):
        record["split"] = "dev" if i < split_point else "eval"

    for gw in gateway_records:
        del gw["_mismatch_type"]

    random.shuffle(gateway_records)
    random.shuffle(bank_records)
    return gateway_records, bank_records, ground_truth


def write_outputs(gateway_records, bank_records, ground_truth):
    """Writes the three generated datasets to OUTPUT_DIR as JSON files."""
    with open(f"{OUTPUT_DIR}/gateway_transactions.json", "w") as f:
        json.dump(gateway_records, f, indent=2)
    with open(f"{OUTPUT_DIR}/bank_settlement.json", "w") as f:
        json.dump(bank_records, f, indent=2)
    with open(f"{OUTPUT_DIR}/ground_truth.json", "w") as f:
        json.dump(ground_truth, f, indent=2)


if __name__ == "__main__":
    gw, bank, gt = generate()
    write_outputs(gw, bank, gt)
    dev_count = sum(1 for r in gt if r["split"] == "dev")
    eval_count = sum(1 for r in gt if r["split"] == "eval")
    print(f"gateway_transactions: {len(gw)} records")
    print(f"bank_settlement: {len(bank)} records")
    print(f"ground_truth: {len(gt)} records ({dev_count} dev / {eval_count} eval)")
