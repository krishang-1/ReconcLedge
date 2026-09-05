"""N-way settlement batch reconciliation - a separate path from
matcher.py (1 txn -> 0/1/2 bank records). Real settlement often nets
many gateway transactions into ONE credit line per cycle, so this
answers "does this batch credit account for these transactions".

Two mechanisms, in order of confidence:

1. reconcile_by_batch_id() - the primary path. Real settlement systems
   provide a batch-level breakdown, so grouping by batch_id and checking
   the group sums to the credited amount is deterministic arithmetic,
   not a guess.

2. find_bounded_subset_matches() - a bounded fallback for records with
   no batch_id. Explicitly NOT a solution to "hundreds of transactions
   in one opaque lump sum": that search is both infeasible (choosing 5
   from 200 is ~2.5bn combinations) and untrustworthy at scale, since
   multiple different subsets summing to the same total becomes
   near-certain. Refuses to search past MAX_POOL_SIZE and says so,
   rather than guessing or silently returning nothing.

Fallback-path matches are always requires_human_review=True - inferring
group membership is inherently lower-confidence than a stated batch_id.
"""

from itertools import combinations

from matcher import AMOUNT_EPSILON

MAX_GROUP_SIZE = 5
MAX_POOL_SIZE = 12  # C(12, 5) = 792 combinations - bounded and fast; see
# module docstring for why this is a hard refusal point, not a soft cap.


def reconcile_by_batch_id(gateway_records, bank_batch_records):
    """Groups gateway_records by their settlement_batch_id (records
    without one are skipped entirely - they're the no-batch-id fallback's
    job, not this function's) and checks whether each group's summed
    net_amount matches the corresponding bank_batch_records entry's
    credited_amount within AMOUNT_EPSILON - the same rounding-slack
    constant matcher.py uses, reused rather than redefined so the two
    modules never silently drift apart on what counts as "equal".

    Returns a list of per-batch reports:
        {
            "batch_id": str,
            "gateway_transaction_ids": [str, ...],
            "expected_sum": float,
            "credited_amount": float | None,   # None if no bank record for this batch_id
            "matched": bool,
            "reason": str,
        }

    A batch_id present in gateway_records with no corresponding entry in
    bank_batch_records is still reported (credited_amount=None,
    matched=False) rather than silently dropped - a batch that was
    expected to settle and didn't is exactly the kind of gap a real
    reconciliation report needs to surface, not hide.

    A bank_batch_records entry whose batch_id matches no gateway records
    at all is not included here (nothing to reconcile it against) - a
    real deployment would want that surfaced as its own kind of orphan,
    matching this project's existing ORPHAN_BANK handling in
    matcher.py/synthetic_generator.py, but that's real further work, not
    attempted here (see docs/DECISIONS.md).

    Raises ValueError if bank_batch_records contains the same non-empty
    batch_id more than once - a real data conflict (e.g. a duplicate
    remittance file submission), fail-fast rather than silently letting
    one entry overwrite the other, same discipline as matcher.py's
    validate_input() guarding duplicate transaction_id/UTR. Found via a
    deeper post-shipping audit, not by any prior test - see
    docs/DECISIONS.md.
    """
    groups = {}
    for gw in gateway_records:
        batch_id = gw.get("settlement_batch_id")
        if not batch_id:
            continue
        groups.setdefault(batch_id, []).append(gw)

    seen_batch_ids = set()
    for b in bank_batch_records:
        batch_id = b.get("batch_id")
        if not batch_id:
            continue  # unbatched credit lines legitimately have no batch_id and can repeat
        if batch_id in seen_batch_ids:
            raise ValueError(
                f"duplicate batch_id '{batch_id}' in bank_batch_records - each batch_id must "
                f"appear at most once, or one credit line would silently overwrite the other"
            )
        seen_batch_ids.add(batch_id)

    credited_by_batch = {b["batch_id"]: b["credited_amount"] for b in bank_batch_records}

    reports = []
    for batch_id, records in groups.items():
        expected_sum = round(sum(r["net_amount"] for r in records), 2)
        credited = credited_by_batch.get(batch_id)

        if credited is None:
            reports.append({
                "batch_id": batch_id,
                "gateway_transaction_ids": [r["transaction_id"] for r in records],
                "expected_sum": expected_sum,
                "credited_amount": None,
                "matched": False,
                "reason": "no bank credit line found for this batch_id - batch may not have settled yet",
            })
            continue

        matched = abs(expected_sum - credited) <= AMOUNT_EPSILON
        reports.append({
            "batch_id": batch_id,
            "gateway_transaction_ids": [r["transaction_id"] for r in records],
            "expected_sum": expected_sum,
            "credited_amount": credited,
            "matched": matched,
            "reason": "batch sum matches credited amount" if matched
                      else f"batch sum ({expected_sum}) does not match credited amount ({credited}) - gap of {round(credited - expected_sum, 2)}",
        })

    return reports


def find_bounded_subset_matches(unbatched_gateway_records, unexplained_credit_amount, max_group_size=MAX_GROUP_SIZE):
    """Bounded fallback for a bank credit line with no batch_id to group
    against - searches small combinations of unbatched_gateway_records
    (2 up to max_group_size at a time) for a subset summing to
    unexplained_credit_amount within AMOUNT_EPSILON.

    Returns exactly one of:
        {"status": "pool_too_large", "pool_size": int,
         "reason": "..."}                                    # refused to search - see module docstring
        {"status": "no_match_found", "reason": "..."}         # searched, found nothing
        {"status": "ambiguous", "candidate_count_found_before_stopping": int,
         "example_candidates": [[txn_id, ...], ...],          # capped preview, not the full list
         "reason": "..."}
        {"status": "candidate_match", "transaction_ids": [...],
         "requires_human_review": True, "reason": "..."}      # exactly one group found - still not auto-accepted

    Deliberately stops enumerating once a second valid group is found
    (doesn't need to find every possible ambiguous grouping to know the
    result is unusable) - this bounds the ambiguous case's own cost too,
    not just the no-match case.
    """
    if len(unbatched_gateway_records) > MAX_POOL_SIZE:
        return {
            "status": "pool_too_large",
            "pool_size": len(unbatched_gateway_records),
            "reason": f"candidate pool ({len(unbatched_gateway_records)}) exceeds the bounded-search "
                      f"limit ({MAX_POOL_SIZE}) - at this scale a combinatorial search is both computationally "
                      f"infeasible and untrustworthy even if it finished (too many subsets would coincidentally "
                      f"sum to the same total). This needs a real batch_id/remittance breakdown, not a guess.",
        }

    found = []
    for size in range(2, max_group_size + 1):
        for combo in combinations(unbatched_gateway_records, size):
            total = round(sum(r["net_amount"] for r in combo), 2)
            if abs(total - unexplained_credit_amount) <= AMOUNT_EPSILON:
                found.append([r["transaction_id"] for r in combo])
                if len(found) >= 2:
                    break
        if len(found) >= 2:
            break

    if not found:
        return {
            "status": "no_match_found",
            "reason": f"no combination of up to {max_group_size} unbatched transactions "
                      f"sums to {unexplained_credit_amount} within the pool searched",
        }

    if len(found) >= 2:
        return {
            "status": "ambiguous",
            "candidate_count_found_before_stopping": len(found),
            "example_candidates": found,
            "reason": "more than one distinct group of transactions sums to this credit amount - "
                      "cannot deterministically pick one, same discipline as matcher.py's pairwise-split ambiguity check",
        }

    return {
        "status": "candidate_match",
        "transaction_ids": found[0],
        "requires_human_review": True,
        "reason": "exactly one group found within the bounded search, but inferred without an explicit "
                  "batch_id - lower confidence than a stated batch match, always flagged for review, never auto-accepted",
    }
