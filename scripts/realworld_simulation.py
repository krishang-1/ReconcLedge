"""Real-world financial scenario simulation - distinct from every prior
test in this project, which generated synthetic data with edge cases
chosen to test CODE correctness. This generates data chosen to test
DOMAIN REALISM: does the system's model of the world (settlement
windows, fee structures) actually match how Indian payment settlement
works in production, at volume where a small modeling gap becomes a
real, quantifiable financial/operational exposure?

Grounded in real Razorpay/Indian banking behavior:
- Settlement happens on BUSINESS DAYS only (no weekends, no bank
  holidays) - not a fixed calendar-day offset from the transaction date.
- Standard settlement cycle is T+2 working days for most methods; UPI
  is often faster (T+1). This is NOT what data/synthetic_generator.py
  models (a flat 1-3 CALENDAR day random offset regardless of weekday),
  and matcher.py's DATE_WINDOW_DAYS=3 is a fixed calendar-day check that
  never accounts for weekend crossing.
- Payment method mix in Indian digital payments is heavily UPI-skewed
  (roughly half of transaction volume), not the curated dataset's
  method-agnostic model.
- Transaction amounts follow a long-tail distribution in real merchant
  data (many small transactions, a few large B2B ones) - not the
  curated dataset's uniform random range.
"""

import json
import os
import random
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "eval"))

from matcher import run_deterministic_stage, DATE_WINDOW_DAYS

HOLIDAYS_2026 = {
    date(2026, 1, 26), date(2026, 3, 4), date(2026, 8, 15),
    date(2026, 10, 2), date(2026, 11, 8), date(2026, 12, 25),
}


def is_business_day(d):
    return d.weekday() < 5 and d not in HOLIDAYS_2026


def add_business_days(start_date, n):
    """The REAL settlement rule: N working days, skipping weekends and
    holidays - not a flat calendar-day offset."""
    d = start_date
    added = 0
    while added < n:
        d += timedelta(days=1)
        if is_business_day(d):
            added += 1
    return d


def random_hex(n):
    return "".join(random.choices("0123456789abcdef", k=n))


def ref_token_for(order_id):
    return order_id.split("_")[1][:8]


PAYMENT_METHODS = [
    ("UPI", 0.50, 1),
    ("Card", 0.28, 2),
    ("Netbanking", 0.15, 2),
    ("Wallet", 0.07, 1),
]


def sample_payment_method():
    r = random.random()
    cumulative = 0
    for method, share, cycle in PAYMENT_METHODS:
        cumulative += share
        if r <= cumulative:
            return method, cycle
    return PAYMENT_METHODS[-1][0], PAYMENT_METHODS[-1][2]


def sample_amount():
    if random.random() < 0.03:
        return round(random.uniform(50_000, 800_000), 2)
    return round(random.lognormvariate(6.5, 1.1), 2)


def simulate(n_transactions, start_date, days_span):
    random.seed(20260824)
    gateway_records = []
    bank_records = []
    truth = []

    for _ in range(n_transactions):
        txn_offset = random.randint(0, days_span - 1)
        txn_date = start_date + timedelta(days=txn_offset)
        method, cycle_days = sample_payment_method()

        amount = sample_amount()
        fee = round(amount * 0.02, 2)
        tax = round(fee * 0.18, 2)
        net_amount = round(amount - fee - tax, 2)

        settle_date = add_business_days(date(txn_date.year, txn_date.month, txn_date.day), cycle_days)
        calendar_gap = (settle_date - date(txn_date.year, txn_date.month, txn_date.day)).days

        txn_id = "txn_" + random_hex(14)
        order_id = "order_" + random_hex(12)

        gateway_records.append({
            "transaction_id": txn_id,
            "order_id": order_id,
            "amount": amount,
            "fee": fee,
            "tax": tax,
            "net_amount": net_amount,
            "timestamp": txn_date.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "captured",
            "customer_ref": "cust_" + random_hex(8),
        })

        # Realistic mix: most settlements pass through at exactly net_amount
        # (no extra deduction beyond the already-netted gateway fee); a
        # minority carry a genuine small additional bank charge. Applying
        # extra_charge to EVERY record (an earlier version of this script's
        # mistake) would confound the date-window question with an amount-
        # gap question on every single record, making it impossible to
        # isolate which effect is actually causing wrongful routing.
        if random.random() < 0.25:
            extra_charge = round(random.uniform(2, 25), 2)
            settled_amount = round(net_amount - extra_charge, 2)
        else:
            settled_amount = net_amount
        bank_records.append({
            "utr_number": "UTR" + "".join(random.choices("0123456789", k=12)),
            "settled_amount": settled_amount,
            "settlement_date": settle_date.strftime("%Y-%m-%d"),
            "narration": f"NEFT CR {ref_token_for(order_id)} SETTLEMENT",
        })

        truth.append({
            "transaction_id": txn_id, "payment_method": method, "calendar_gap": calendar_gap,
            "txn_weekday": txn_date.strftime("%A"), "crossed_weekend_or_holiday": calendar_gap > cycle_days,
        })

    return gateway_records, bank_records, truth


def run():
    start = date(2026, 8, 1)
    gw, bank, truth = simulate(n_transactions=2000, start_date=start, days_span=45)

    matched, exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)

    matched_ids = {m["transaction_id"] for m in matched}
    needs_agent_ids = {n["gateway_record"]["transaction_id"] for n in needs_agent}

    gw_by_id = {g["transaction_id"]: g for g in gw}

    weekend_crossers = [t for t in truth if t["crossed_weekend_or_holiday"]]
    weekend_crossers_matched = [t for t in weekend_crossers if t["transaction_id"] in matched_ids]

    # Precise isolation: only count a record as "wrongly flagged by the date
    # window specifically" if that's the ACTUAL reason matcher.py gave, not
    # just because it happens to also be a weekend-crosser. An earlier
    # version of this analysis conflated two independent effects - a
    # genuine amount gap (this simulation deliberately injects one on 25%
    # of records, unrelated to date) can coincidentally co-occur with a
    # weekend-crossing date, and get correctly routed to the agent for the
    # amount reason, not the date reason. Checking the actual routing
    # reason text instead of inferring it from co-occurrence is what
    # caught this - see docs/DECISIONS.md.
    needs_agent_by_id = {n["gateway_record"]["transaction_id"]: n for n in needs_agent}
    weekend_crossers_needing_agent = [
        t for t in weekend_crossers
        if t["transaction_id"] in needs_agent_by_id
        and "date window" in needs_agent_by_id[t["transaction_id"]]["reason"]
    ]

    total_value_at_risk = sum(gw_by_id[t["transaction_id"]]["amount"] for t in weekend_crossers_needing_agent)

    print(f"Simulated {len(gw)} realistic transactions over 45 days ({start} onward)")
    print(f"Deterministic stage: {len(matched)} matched, {len(exceptions)} exceptions, {len(needs_agent)} routed to agent")
    print()
    print("Real settlement gap analysis (ground truth, not matcher.py's view):")
    print(f"  Transactions whose REAL settlement legitimately crosses a weekend/holiday: {len(weekend_crossers)} / {len(gw)} ({len(weekend_crossers)/len(gw)*100:.1f}%)")
    print(f"  Of those, still correctly matched by matcher.py anyway: {len(weekend_crossers_matched)}")
    print(f"  Of those, WRONGLY routed to the agent stage purely because DATE_WINDOW_DAYS={DATE_WINDOW_DAYS} is too narrow for a legitimate weekend-crossing settlement: {len(weekend_crossers_needing_agent)}")
    print(f"  Aggregate transaction VALUE wrongly requiring agent escalation for this reason alone: Rs {total_value_at_risk:,.2f}")
    print()

    from collections import Counter
    weekday_breakdown = Counter(t["txn_weekday"] for t in weekend_crossers_needing_agent)
    print("Day-of-week breakdown of wrongly-flagged transactions:")
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        print(f"  {day}: {weekday_breakdown.get(day, 0)}")

    return {
        "total_transactions": len(gw),
        "weekend_crossers": len(weekend_crossers),
        "wrongly_flagged": len(weekend_crossers_needing_agent),
        "value_at_risk": total_value_at_risk,
    }


if __name__ == "__main__":
    results = run()
    with open(os.path.join(ROOT, "scripts", "realworld_simulation_results.json"), "w") as f:
        json.dump(results, f, indent=2)
