"""Date-arithmetic edge cases the curated dataset (a fixed ~20-day window
in Aug 2026) never exercises: leap years, year boundaries, month
rollovers, and the exact inclusive boundary of the match window.
DATE_WINDOW_DAYS was widened from 3 to 7 after a real-world settlement
simulation found the narrower window wrongly flagged 16.3% of realistic
transaction volume - see docs/DECISIONS.md and
scripts/realworld_simulation.py. Fast and cheap (no LLM, no data
generation), folded into the permanent suite rather than kept as a
one-off exploration.
"""

import pytest
from matcher import within_date_window, DATE_WINDOW_DAYS

CASES = [
    ("2024-02-28T00:00:00", "2024-02-29", True, "leap year Feb 29 (2024 is a leap year)"),
    ("2024-02-29T00:00:00", "2024-03-01", True, "settlement crosses Feb->Mar in a leap year"),
    ("2025-02-28T00:00:00", "2025-03-01", True, "2025 is NOT a leap year - Feb 28 to Mar 1 is a 1-day gap"),
    ("2026-12-31T00:00:00", "2027-01-01", True, "settlement crosses a year boundary, 1 day"),
    ("2026-12-25T00:00:00", "2027-01-01", True, "settlement crosses a year boundary, 7 days (edge of window)"),
    ("2026-12-24T00:00:00", "2027-01-01", False, "settlement crosses a year boundary, 8 days (just outside window)"),
    ("2026-01-31T00:00:00", "2026-02-01", True, "Jan->Feb month rollover, 1 day"),
    ("2026-04-30T00:00:00", "2026-05-01", True, "Apr->May month rollover (30-day month), 1 day"),
    ("2026-08-10T00:00:00", "2026-08-10", True, "same-day settlement, 0 days"),
    ("2026-08-10T00:00:00", "2026-08-09", False, "settlement BEFORE transaction - always invalid regardless of gap size"),
    ("2026-08-10T00:00:00", "2026-08-17", True, "exactly 7 days - the window's inclusive upper boundary"),
    ("2026-08-10T00:00:00", "2026-08-18", False, "exactly 8 days - just past the window"),
    # A Thursday transaction with T+2 business-day settlement crossing a
    # weekend - the exact real-world scenario the widened window fixes.
    # 2026-08-13 is a Thursday; +2 business days (skip Sat/Sun) = Monday
    # 2026-08-17, a 4-calendar-day gap that the old DATE_WINDOW_DAYS=3
    # would have wrongly rejected.
    ("2026-08-13T00:00:00", "2026-08-17", True, "Thursday txn, T+2 settlement crossing a weekend (4 calendar days) - was wrongly rejected before the fix"),
]


def test_window_is_seven_days_not_three():
    """Regression guard: confirms the constant itself, not just its
    behavior, in case a future edit accidentally reverts it."""
    assert DATE_WINDOW_DAYS == 7


@pytest.mark.parametrize("gw_ts,bank_date,expected,desc", CASES)
def test_date_window_edge_case(gw_ts, bank_date, expected, desc):
    gw = {"timestamp": gw_ts}
    bank = {"settlement_date": bank_date}
    assert within_date_window(gw, bank) == expected, desc
