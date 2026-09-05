"""Computes reported metrics from the held-out eval split only (~35% of
ground_truth.json, per the dev/eval split fixed at data-generation time).
Never scored against the dev split - that would let the numbers reflect
tuning rather than genuine performance.

Scores two distinct kinds of ground-truth record:
  - gateway transactions (keyed by transaction_id) - correct if matched to
    the right settlement UTR(s), or correctly given an exception when no
    match should exist (e.g. ORPHAN_GATEWAY).
  - ORPHAN_BANK records (keyed by orphan_bank_utr, no transaction_id) -
    bank-side settlements with no real gateway counterpart. Correct if the
    system never claims that UTR as part of any match. These were
    previously silently excluded from scoring entirely (compute_metrics
    only iterated records with a transaction_id) - fixed 2026-08-23, see
    docs/DECISIONS.md.
"""

from collections import defaultdict


def compute_metrics(matched, exceptions, ground_truth):
    """Returns a metrics dict scored only against ground_truth records with split == 'eval'."""
    # Defensive check before anything else: matched_by_id/exceptions_by_id below
    # are built as dicts keyed by transaction_id. If a bug elsewhere ever caused
    # the same transaction_id to appear twice (e.g. claimed by both stages), the
    # dict comprehension would silently keep only the last occurrence and every
    # metric downstream would be silently wrong - undercounting with no error at
    # all. This is protecting the single most important number in the project,
    # so it fails loudly instead of computing a wrong answer quietly. Found on
    # review, not triggered by any real bug so far - see docs/DECISIONS.md.
    all_ids = [m["transaction_id"] for m in matched] + [e["transaction_id"] for e in exceptions]
    seen, duplicates = set(), set()
    for tid in all_ids:
        (duplicates if tid in seen else seen).add(tid)
    if duplicates:
        raise ValueError(f"duplicate transaction_id(s) across matched/exceptions - metrics would be silently wrong: {duplicates}")

    eval_txns = {r["transaction_id"]: r for r in ground_truth if r.get("split") == "eval" and r.get("transaction_id")}
    eval_orphan_banks = [r for r in ground_truth if r.get("split") == "eval" and r.get("mismatch_type") == "ORPHAN_BANK"]
    matched_by_id = {m["transaction_id"]: m for m in matched}
    exceptions_by_id = {e["transaction_id"]: e for e in exceptions}
    claimed_utrs = {utr for m in matched for utr in m["utrs"]}

    correct, incorrect, correctly_excepted, wrongly_excepted, missing = 0, 0, 0, 0, 0
    by_type = defaultdict(lambda: {"correct": 0, "incorrect": 0, "total": 0})

    for txn_id, truth in eval_txns.items():
        by_type[truth["mismatch_type"]]["total"] += 1
        expected = truth.get("correct_settlement_utrs")

        if txn_id in matched_by_id:
            actual = sorted(matched_by_id[txn_id]["utrs"])
            if expected is not None and sorted(expected) == actual:
                correct += 1
                by_type[truth["mismatch_type"]]["correct"] += 1
            else:
                incorrect += 1
                by_type[truth["mismatch_type"]]["incorrect"] += 1
        elif txn_id in exceptions_by_id:
            if expected is None:
                correctly_excepted += 1
                by_type[truth["mismatch_type"]]["correct"] += 1
            else:
                wrongly_excepted += 1
                by_type[truth["mismatch_type"]]["incorrect"] += 1
        else:
            missing += 1
            by_type[truth["mismatch_type"]]["incorrect"] += 1

    orphan_bank_correct, orphan_bank_incorrect = 0, 0
    for r in eval_orphan_banks:
        by_type["ORPHAN_BANK"]["total"] += 1
        if r["orphan_bank_utr"] in claimed_utrs:
            orphan_bank_incorrect += 1
            by_type["ORPHAN_BANK"]["incorrect"] += 1
        else:
            orphan_bank_correct += 1
            by_type["ORPHAN_BANK"]["correct"] += 1

    total = len(eval_txns) + len(eval_orphan_banks)
    total_correct = correct + correctly_excepted + orphan_bank_correct
    total_wrong_actions = incorrect + orphan_bank_incorrect  # active wrong matches, not exception mishandling
    return {
        "eval_set_size": total,
        "correct_matches": correct,
        "incorrect_matches": incorrect,
        "correctly_flagged_exceptions": correctly_excepted,
        "wrongly_flagged_exceptions": wrongly_excepted,
        "unaccounted_for": missing,
        "orphan_bank_correctly_unclaimed": orphan_bank_correct,
        "orphan_bank_wrongly_claimed": orphan_bank_incorrect,
        "match_rate": round(total_correct / total, 4) if total else None,
        "false_positive_rate": round(total_wrong_actions / total, 4) if total else None,
        "by_mismatch_type": dict(by_type),
    }
