"""Tools available to the LLM agent stage. Every tool here operates only on
the pool of bank records the deterministic stage (matcher.py) left
unclaimed - the agent never sees records that were already resolved.
"""

from matcher import normalize, settle_date, txn_date

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_by_amount_date",
            "description": "Search unclaimed bank settlement records by approximate amount and date proximity to the transaction. Use when reference lookup found nothing or an ambiguous set.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tolerance_pct": {"type": "number", "description": "Fractional tolerance on amount, e.g. 0.15 for +/-15%. Default 0.15."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bank_record",
            "description": "Fetch full details of one bank record by UTR number.",
            "parameters": {
                "type": "object",
                "properties": {"utr_number": {"type": "string"}},
                "required": ["utr_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_match",
            "description": "Commit to a proposed match. Ends the turn - the proposal is sent to an independent verifier, not accepted automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "utrs": {"type": "array", "items": {"type": "string"}, "description": "One UTR, or two for a split settlement."},
                    "reasoning": {"type": "string"},
                },
                "required": ["utrs", "reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_exception",
            "description": "Give up on this record with an explicit taxonomy code. Ends the turn. Use when no candidate reasonably fits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "exception_type": {
                        "type": "string",
                        "enum": ["NO_CANDIDATE_FOUND", "AMBIGUOUS_MULTIPLE_CANDIDATES", "AMOUNT_MISMATCH_UNEXPLAINED"],
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["exception_type", "reasoning"],
            },
        },
    },
]


def search_by_amount_date(gateway_record, unclaimed_bank_records, tolerance_pct=0.15, window_days=7):
    """Returns unclaimed bank records within an amount tolerance and forward-looking date window of the transaction."""
    net = gateway_record["net_amount"]
    # Capped at net_amount, not net*(1+tolerance): fees only ever reduce a
    # settlement, so candidates above net could never be accepted anyway - a
    # symmetric band just wastes agent reasoning on impossible matches.
    low, high = net * (1 - tolerance_pct), net + 0.02
    results = []
    for b in unclaimed_bank_records:
        delta_days = (settle_date(b) - txn_date(gateway_record)).days
        if low <= b["settled_amount"] <= high and 0 <= delta_days <= window_days:
            results.append(b)
    return results


def get_bank_record(utr_number, unclaimed_bank_records):
    """Returns the full bank record matching a UTR, or None if not found in the unclaimed pool."""
    for b in unclaimed_bank_records:
        if b["utr_number"] == utr_number:
            return b
    return None


def dispatch_tool_call(name, arguments, gateway_record, unclaimed_bank_records):
    """Routes a parsed tool call to its implementation. Returns a JSON-serializable result."""
    if name == "search_by_amount_date":
        tolerance = arguments.get("tolerance_pct", 0.15)
        return search_by_amount_date(gateway_record, unclaimed_bank_records, tolerance_pct=tolerance)
    if name == "get_bank_record":
        record = get_bank_record(arguments["utr_number"], unclaimed_bank_records)
        return record if record else {"error": "utr_number not found in unclaimed pool"}
    raise ValueError(f"dispatch_tool_call does not handle terminal tool '{name}' - handle it in the caller")
