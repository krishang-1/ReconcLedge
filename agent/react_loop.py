"""Runs the LLM agent stage over records matcher.py routed to it. For each
one: a tool-calling loop (search / inspect / propose / report_exception),
capped at MAX_STEPS, then any propose_match result is sent to verifier.py
before being accepted. A verifier rejection becomes a VERIFIER_REJECTED
exception carrying both the agent's and the verifier's stated reasoning -
never silently dropped.
"""

import json

import tools
import verifier
from exceptions import NO_CANDIDATE_FOUND, VERIFIER_REJECTED
from prompts import AGENT_SYSTEM_PROMPT

MAX_STEPS = 6
KNOWN_SEARCH_TOOLS = {"search_by_amount_date", "get_bank_record"}


def _initial_user_message(gateway_record, routing_reason):
    """Builds the first user-turn message describing the transaction and why it reached the agent."""
    return (
        f"Deterministic matcher could not resolve this transaction: {routing_reason}\n\n"
        f"Gateway transaction:\n{json.dumps(gateway_record, indent=2)}\n\n"
        "Use the available tools to search for a matching settlement, then "
        "call propose_match or report_exception."
    )


def run_agent_on_record(gateway_record, routing_reason, unclaimed_bank_records, llm_client):
    """Runs the tool-calling loop for one gateway record.

    Returns one of:
      {"status": "matched", "utrs": [...], "method": "agent_verified", "agent_reasoning": str}
      {"status": "exception", "type": str, "reason": str}
    """
    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": _initial_user_message(gateway_record, routing_reason)},
    ]

    for step in range(MAX_STEPS):
        response = llm_client.chat(messages, tools=tools.TOOL_SCHEMAS)
        tool_calls = response.get("tool_calls") or []

        if not tool_calls:
            messages.append({"role": "user", "content": "You must call a tool - either search, propose_match, or report_exception."})
            continue

        messages.append({"role": "assistant", "content": response.get("content"), "tool_calls": tool_calls})

        for call in tool_calls:
            name = call["function"]["name"]

            try:
                arguments = json.loads(call["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                # Malformed JSON in a tool call's arguments is a documented,
                # common LLM failure mode - without this guard it crashes the
                # entire batch run, not just one record. Nudge and continue,
                # same principle as the unrecognized-tool-name handling below.
                messages.append({
                    "role": "tool", "tool_call_id": call["id"],
                    "content": json.dumps({"error": "arguments were not valid JSON - retry this tool call with valid JSON arguments"}),
                })
                continue

            if name == "propose_match":
                if "utrs" not in arguments:
                    messages.append({
                        "role": "tool", "tool_call_id": call["id"],
                        "content": json.dumps({"error": "propose_match requires a 'utrs' field"}),
                    })
                    continue
                result = verifier.verify(gateway_record, [
                    b for b in unclaimed_bank_records if b["utr_number"] in arguments["utrs"]
                ], llm_client)
                if result["accepted"]:
                    return {
                        "status": "matched",
                        "utrs": arguments["utrs"],
                        "method": "agent_verified",
                        "agent_reasoning": arguments.get("reasoning", ""),
                        # Whether the verifier accepted via exact arithmetic
                        # ("deterministic") or genuine LLM judgment ("llm") -
                        # feeds confidence tiering (see agent/confidence.py).
                        "verifier_method": result["method"],
                    }
                return {
                    "status": "exception",
                    "type": VERIFIER_REJECTED,
                    "reason": f"agent proposed {arguments['utrs']} ({arguments.get('reasoning', '')}); "
                              f"verifier rejected: {result['reason']} [{result['method']}]",
                }

            if name == "report_exception":
                if "exception_type" not in arguments:
                    messages.append({
                        "role": "tool", "tool_call_id": call["id"],
                        "content": json.dumps({"error": "report_exception requires an 'exception_type' field"}),
                    })
                    continue
                return {"status": "exception", "type": arguments["exception_type"], "reason": arguments.get("reasoning", "")}

            if name in KNOWN_SEARCH_TOOLS:
                tool_result = tools.dispatch_tool_call(name, arguments, gateway_record, unclaimed_bank_records)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(tool_result)})
                continue

            # Unrecognized tool name - observed for real with gpt-oss's
            # "commentary" channel leaking through as a tool call. Nudge the
            # model back on track rather than crashing the whole batch.
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps({"error": f"'{name}' is not a valid tool. Valid tools: "
                                                 f"search_by_amount_date, get_bank_record, propose_match, report_exception."}),
            })

    return {"status": "exception", "type": NO_CANDIDATE_FOUND, "reason": f"agent exceeded {MAX_STEPS}-step budget without resolving"}


def run_agent_stage(needs_agent, unclaimed_bank_records, llm_client, on_progress=None):
    """Runs the agent loop over every record the deterministic stage routed here.

    Returns (matched, exceptions) - unclaimed_bank_records is updated in
    place as records get claimed, so later records in the batch can't
    double-claim a settlement an earlier one already matched.

    on_progress, if given, is called after each record as
    on_progress(index, total, event_dict) - optional and backward
    compatible, added for the API layer's live progress reporting.
    """
    matched, exceptions = [], []
    pool = list(unclaimed_bank_records)

    for i, item in enumerate(needs_agent):
        gw = item["gateway_record"]
        result = run_agent_on_record(gw, item["reason"], pool, llm_client)
        if result["status"] == "matched":
            matched.append({
                "transaction_id": gw["transaction_id"],
                "utrs": result["utrs"],
                "method": result["method"],
                "agent_reasoning": result["agent_reasoning"],
                "verifier_method": result.get("verifier_method"),
            })
            pool = [b for b in pool if b["utr_number"] not in result["utrs"]]
        else:
            exceptions.append({"transaction_id": gw["transaction_id"], "type": result["type"], "reason": result["reason"]})

        if on_progress:
            # agent_reasoning included so a live viewer sees WHY a record
            # resolved, not just that it did - the reasoning text is already
            # computed above, this only widens what on_progress receives.
            on_progress(i + 1, len(needs_agent), {
                "stage": "agent",
                "transaction_id": gw["transaction_id"],
                "status": result["status"],
                "reason": result.get("reason", ""),
                "agent_reasoning": result.get("agent_reasoning", ""),
            })

    return matched, exceptions
