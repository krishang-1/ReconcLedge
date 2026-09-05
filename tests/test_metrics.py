"""Tests for eval/metrics.py. Covers the real scoring gap found and fixed
this session (docs/DECISIONS.md error #11): ORPHAN_BANK records were
silently excluded from eval scoring entirely.
"""

from metrics import compute_metrics


def _ground_truth():
    return [
        {"transaction_id": "txn_a", "correct_settlement_utrs": ["UTR_a"], "mismatch_type": "CLEAN", "split": "eval"},
        {"transaction_id": "txn_b", "correct_settlement_utrs": None, "mismatch_type": "ORPHAN_GATEWAY", "split": "eval"},
        {"transaction_id": None, "orphan_bank_utr": "UTR_orphan", "correct_settlement_utrs": None, "mismatch_type": "ORPHAN_BANK", "split": "eval"},
        # dev-split record must never affect eval numbers
        {"transaction_id": "txn_dev", "correct_settlement_utrs": ["UTR_dev"], "mismatch_type": "CLEAN", "split": "dev"},
    ]


def test_orphan_bank_correctly_unclaimed_scores_as_correct():
    matched = [{"transaction_id": "txn_a", "utrs": ["UTR_a"], "method": "deterministic"}]
    exceptions = [{"transaction_id": "txn_b", "type": "NO_CANDIDATE_FOUND", "reason": "no candidate"}]
    m = compute_metrics(matched, exceptions, _ground_truth())
    assert m["orphan_bank_correctly_unclaimed"] == 1
    assert m["orphan_bank_wrongly_claimed"] == 0
    assert m["by_mismatch_type"]["ORPHAN_BANK"]["correct"] == 1


def test_orphan_bank_wrongly_claimed_scores_as_incorrect():
    """If some match erroneously consumes an orphan bank record's UTR, that
    must count against the score - it's a false positive even though it
    doesn't show up as a wrong transaction match."""
    matched = [
        {"transaction_id": "txn_a", "utrs": ["UTR_a"], "method": "deterministic"},
        {"transaction_id": "txn_wrong", "utrs": ["UTR_orphan"], "method": "agent_verified"},
    ]
    exceptions = [{"transaction_id": "txn_b", "type": "NO_CANDIDATE_FOUND", "reason": "no candidate"}]
    m = compute_metrics(matched, exceptions, _ground_truth())
    assert m["orphan_bank_wrongly_claimed"] == 1
    assert m["by_mismatch_type"]["ORPHAN_BANK"]["incorrect"] == 1


def test_eval_set_size_includes_orphan_bank():
    """Regression guard for the exact bug found this session: eval_set_size
    must count ORPHAN_BANK records, not silently drop them for lacking a
    transaction_id."""
    matched = [{"transaction_id": "txn_a", "utrs": ["UTR_a"], "method": "deterministic"}]
    exceptions = [{"transaction_id": "txn_b", "type": "NO_CANDIDATE_FOUND", "reason": "no candidate"}]
    m = compute_metrics(matched, exceptions, _ground_truth())
    assert m["eval_set_size"] == 3  # txn_a, txn_b, and the orphan bank record - NOT txn_dev


def test_dev_split_never_affects_eval_metrics():
    """Isolated fixture: a single dev-split record and nothing else. If dev
    ever leaked into eval scoring, eval_set_size would be nonzero here."""
    gt = [{"transaction_id": "txn_dev", "correct_settlement_utrs": ["UTR_dev"], "mismatch_type": "CLEAN", "split": "dev"}]
    matched = [{"transaction_id": "txn_dev", "utrs": ["WRONG_UTR"], "method": "deterministic"}]
    m = compute_metrics(matched, [], gt)
    assert m["eval_set_size"] == 0
    assert m["by_mismatch_type"] == {}


def test_by_mismatch_type_totals_sum_to_eval_set_size():
    """Regression guard: this exact check (do the per-type totals sum to the
    whole) is what originally surfaced the ORPHAN_BANK gap in a real run."""
    matched = [{"transaction_id": "txn_a", "utrs": ["UTR_a"], "method": "deterministic"}]
    exceptions = [{"transaction_id": "txn_b", "type": "NO_CANDIDATE_FOUND", "reason": "no candidate"}]
    m = compute_metrics(matched, exceptions, _ground_truth())
    total_from_breakdown = sum(v["total"] for v in m["by_mismatch_type"].values())
    assert total_from_breakdown == m["eval_set_size"]


def test_duplicate_transaction_id_raises_instead_of_silently_wrong_metrics():
    """Regression guard for a real risk found on review: if the same
    transaction_id ever appeared twice across matched/exceptions (a bug
    elsewhere), the dict-comprehension pattern in compute_metrics would
    silently keep only the last occurrence and undercount every metric
    with no error at all. Must fail loudly instead."""
    matched = [
        {"transaction_id": "txn_a", "utrs": ["UTR_1"], "method": "deterministic"},
        {"transaction_id": "txn_a", "utrs": ["UTR_2"], "method": "agent_verified"},  # same txn_id, real bug scenario
    ]
    try:
        compute_metrics(matched, [], _ground_truth())
        assert False, "should have raised on a duplicate transaction_id"
    except ValueError as e:
        assert "txn_a" in str(e)
