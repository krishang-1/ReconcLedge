"""Tests for agent/fx_reconciliation.py's reconcile_fx_transaction()."""

import fx_generator
from fx_reconciliation import (
    INVALID_RATE_BAND,
    MATCHED_WITHIN_BAND,
    NOT_A_CURRENCY_MISMATCH,
    RATE_IMPLAUSIBLE,
    reconcile_fx_transaction,
)


def test_clean_match_within_band():
    gw, bank, rmin, rmax, markup = fx_generator.generate()[0]
    result = reconcile_fx_transaction(gw, bank, rmin, rmax, markup)
    assert result["status"] == MATCHED_WITHIN_BAND
    assert result["requires_human_review"] is True  # always, even a clean match


def test_implausible_settlement_flagged_not_forced():
    gw, bank, rmin, rmax, markup = fx_generator.generate()[1]
    result = reconcile_fx_transaction(gw, bank, rmin, rmax, markup)
    assert result["status"] == RATE_IMPLAUSIBLE
    assert result["implied_rate"] is not None
    assert "implied rate" in result["reason"].lower()


def test_same_currency_pair_is_not_an_fx_case():
    gw, bank, rmin, rmax, markup = fx_generator.generate()[2]
    result = reconcile_fx_transaction(gw, bank, rmin, rmax, markup)
    assert result["status"] == NOT_A_CURRENCY_MISMATCH
    assert result["expected_range"] is None


def test_boundary_at_exact_low_edge_of_band_matches():
    gw, bank, rmin, rmax, markup = fx_generator.generate()[3]
    result = reconcile_fx_transaction(gw, bank, rmin, rmax, markup)
    assert result["status"] == MATCHED_WITHIN_BAND


def test_markup_reduces_expected_settlement_range():
    gw, bank, rmin, rmax, markup = fx_generator.generate()[4]
    result = reconcile_fx_transaction(gw, bank, rmin, rmax, markup)
    assert result["status"] == MATCHED_WITHIN_BAND
    # with a real markup, the expected range's max must be strictly below
    # the naive (no-markup) conversion ceiling
    naive_max = round(gw["amount"] * rmax, 2)
    assert result["expected_range"][1] < naive_max


def test_invalid_rate_band_rejected_not_silently_swapped():
    gw = {"transaction_id": "x", "amount": 100.0, "currency": "USD"}
    bank = {"settled_amount": 8000.0, "currency": "INR"}
    result = reconcile_fx_transaction(gw, bank, rate_min=90.0, rate_max=80.0)
    assert result["status"] == INVALID_RATE_BAND


def test_every_result_requires_human_review_regardless_of_outcome():
    """The core design commitment: FX reconciliation never auto-accepts,
    no matter how clean the match looks."""
    for gw, bank, rmin, rmax, markup in fx_generator.generate():
        result = reconcile_fx_transaction(gw, bank, rmin, rmax, markup)
        assert result["requires_human_review"] is True
