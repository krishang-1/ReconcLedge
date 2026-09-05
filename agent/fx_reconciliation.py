"""Multi-currency / FX settlement reconciliation - a standalone path.

There is no live FX rate feed here, and trusting "today's rate" for a
transaction that settled on a different day would be its own guess. So
this works off a RATE BAND the caller supplies (rate_min, rate_max),
consistent with every other tolerance in this codebase - a documented
slack band rather than false precision. A real deployment would source
the band from an RBI reference rate plus the real conversion spread.

markup_bps models the FX conversion spread as a reduction to the
expected range (the merchant receives less than a pure market-rate
conversion). The 0-bps default is illustrative, not Razorpay's real fee
schedule - the same honesty limitation named for HIGH_VALUE_THRESHOLD.

Every result is requires_human_review=True: an FX match depends on a
caller-supplied rate assumption this module can't independently verify.
"""

from matcher import AMOUNT_EPSILON

MATCHED_WITHIN_BAND = "matched_within_rate_band"
RATE_IMPLAUSIBLE = "rate_implausible"
NOT_A_CURRENCY_MISMATCH = "not_a_currency_mismatch"
INVALID_RATE_BAND = "invalid_rate_band"


def reconcile_fx_transaction(gateway_record, bank_record, rate_min, rate_max, markup_bps=0):
    """gateway_record: {"transaction_id", "amount", "currency"}
    bank_record: {"settled_amount", "currency"}

    Returns:
        {
            "transaction_id": str,
            "status": one of the module constants above,
            "gateway_amount": float, "gateway_currency": str,
            "settled_amount": float, "settled_currency": str,
            "expected_range": [float, float] | None,
            "implied_rate": float | None,   # settled_amount / gateway_amount, for a human reviewer's context
            "requires_human_review": True,  # always, see module docstring
            "reason": str,
        }

    Same-currency gateway/bank pairs are explicitly NOT this module's
    job (that's an ordinary deterministic match, matcher.py's job) -
    returned as not_a_currency_mismatch rather than silently attempting
    a currency conversion that shouldn't happen. rate_min > rate_max is
    a caller input error, returned as invalid_rate_band rather than
    silently swapping them or proceeding with a nonsensical band.
    """
    gw_amount = gateway_record["amount"]
    gw_currency = gateway_record["currency"]
    settled_amount = bank_record["settled_amount"]
    settled_currency = bank_record["currency"]
    txn_id = gateway_record["transaction_id"]

    base_result = {
        "transaction_id": txn_id,
        "gateway_amount": gw_amount,
        "gateway_currency": gw_currency,
        "settled_amount": settled_amount,
        "settled_currency": settled_currency,
        "requires_human_review": True,
    }

    if gw_currency == settled_currency:
        return {
            **base_result,
            "status": NOT_A_CURRENCY_MISMATCH,
            "expected_range": None,
            "implied_rate": None,
            "reason": "gateway and settlement currencies match - this is an ordinary same-currency "
                      "reconciliation, not an FX case; use the regular matcher/reconciliation flow instead",
        }

    if rate_min > rate_max:
        return {
            **base_result,
            "status": INVALID_RATE_BAND,
            "expected_range": None,
            "implied_rate": None,
            "reason": f"rate_min ({rate_min}) is greater than rate_max ({rate_max}) - invalid input, not evaluated",
        }

    markup_factor = 1 - (markup_bps / 10000)
    expected_min = round(gw_amount * rate_min * markup_factor, 2)
    expected_max = round(gw_amount * rate_max * markup_factor, 2)
    implied_rate = round(settled_amount / gw_amount, 6) if gw_amount else None

    within_band = (expected_min - AMOUNT_EPSILON) <= settled_amount <= (expected_max + AMOUNT_EPSILON)

    return {
        **base_result,
        "status": MATCHED_WITHIN_BAND if within_band else RATE_IMPLAUSIBLE,
        "expected_range": [expected_min, expected_max],
        "implied_rate": implied_rate,
        "reason": (
            f"settled amount falls within the expected {gw_currency}->{settled_currency} "
            f"range for the given rate band"
            if within_band else
            f"settled amount {settled_amount} {settled_currency} falls outside the expected "
            f"range [{expected_min}, {expected_max}] implied by the given rate band - "
            f"implied rate was {implied_rate}, requested band was [{rate_min}, {rate_max}]"
        ),
    }
