"""Tests for data/synthetic_generator.py."""

import synthetic_generator as gen


def test_deterministic_across_runs():
    """Same seed must produce byte-identical output every run - eval numbers
    are only comparable across agent iterations if the dataset never
    silently changes underneath them."""
    gw1, bank1, gt1 = gen.generate()
    gw2, bank2, gt2 = gen.generate()
    assert gw1 == gw2
    assert bank1 == bank2
    assert gt1 == gt2


def test_at_least_fifty_records():
    """Buildathon track requires a 50+ record batch."""
    gw, bank, gt = gen.generate()
    assert len(gw) >= 50


def test_all_seven_mismatch_types_present():
    """Every designed mismatch type must actually appear at least once,
    or the corresponding matcher/agent code path is untested by this data."""
    gw, bank, gt = gen.generate()
    types_present = {r["mismatch_type"] for r in gt}
    expected = {
        "CLEAN", "FEE_DRIFT", "TIMING_LAG", "GARBLED_REF",
        "DUPLICATE", "SPLIT", "ORPHAN_GATEWAY", "ORPHAN_BANK",
    }
    assert expected <= types_present


def test_dev_eval_split_both_nonempty():
    """A held-out eval split that ends up empty would make every eval metric
    meaningless - guard against a future seed/ratio change silently breaking this."""
    gw, bank, gt = gen.generate()
    dev = [r for r in gt if r["split"] == "dev"]
    eval_ = [r for r in gt if r["split"] == "eval"]
    assert len(dev) > 0
    assert len(eval_) > 0


def test_reference_token_present_and_findable_for_clean():
    """CLEAN records must have their reference token exactly present in the
    matched bank record's narration - this is the primary match key the
    deterministic matcher relies on."""
    gw, bank, gt = gen.generate()
    gw_by_id = {g["transaction_id"]: g for g in gw}
    bank_by_utr = {b["utr_number"]: b for b in bank}

    clean_cases = [r for r in gt if r["mismatch_type"] == "CLEAN"]
    assert clean_cases, "no CLEAN records generated - can't test this"

    for r in clean_cases[:5]:
        g = gw_by_id[r["transaction_id"]]
        b = bank_by_utr[r["correct_settlement_utrs"][0]]
        token = gen.ref_token(g["order_id"])
        assert token in b["narration"]


def test_garbled_ref_token_not_directly_findable():
    """GARBLED_REF exists specifically to defeat naive exact-substring
    reference lookup - if the clean token were still findable verbatim in
    the narration, this mismatch type would be silently identical to CLEAN
    (the exact bug this test suite exists to catch a repeat of - see
    docs/DECISIONS.md, error #1)."""
    gw, bank, gt = gen.generate()
    gw_by_id = {g["transaction_id"]: g for g in gw}
    bank_by_utr = {b["utr_number"]: b for b in bank}

    garbled_cases = [r for r in gt if r["mismatch_type"] == "GARBLED_REF"]
    assert garbled_cases, "no GARBLED_REF records generated - can't test this"

    for r in garbled_cases:
        g = gw_by_id[r["transaction_id"]]
        b = bank_by_utr[r["correct_settlement_utrs"][0]]
        token = gen.ref_token(g["order_id"])
        assert token not in b["narration"], (
            f"GARBLED_REF record {r['transaction_id']} still has its clean "
            f"token verbatim in the narration - garbling isn't doing anything"
        )


def test_split_amounts_sum_to_net():
    """SPLIT records' two settlement amounts must sum to the gateway net
    amount within rounding tolerance, or the matcher's sum-based split
    detection has nothing correct to find."""
    gw, bank, gt = gen.generate()
    gw_by_id = {g["transaction_id"]: g for g in gw}
    bank_by_utr = {b["utr_number"]: b for b in bank}

    split_cases = [r for r in gt if r["mismatch_type"] == "SPLIT"]
    assert split_cases, "no SPLIT records generated - can't test this"

    for r in split_cases:
        g = gw_by_id[r["transaction_id"]]
        parts = [bank_by_utr[utr]["settled_amount"] for utr in r["correct_settlement_utrs"]]
        assert abs(sum(parts) - g["net_amount"]) <= 0.02
