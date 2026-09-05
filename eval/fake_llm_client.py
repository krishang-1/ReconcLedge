"""Fake LLM client for smoke-testing agent/react_loop.py and agent/verifier.py
without a real Groq call (this sandbox has no network route to Groq's API).
Implements the same chat(messages, tools=None) interface as GroqClient, so
the production loop code runs completely unmodified against it. This is a
TEST-ONLY tool for validating loop mechanics - it does not replace running
the real agent against Groq, and makes no claim to match real model
judgment quality.
"""

import json
import re
import time


class FakeLLMClient:
    """Scripted client: proposes the closest amount candidate found via
    search, or reports NO_CANDIDATE_FOUND if search returns nothing. For
    verifier calls (no tools passed), applies the same concrete Rs 50
    shortfall threshold the real VERIFIER_SYSTEM_PROMPT uses (see
    agent/prompts.py).

    Deliberately does NOT attempt the prompt's reference-vs-collision
    distinction (a genuinely garbled-but-correct reference vs. a different
    order's reference actually present) - telling those apart requires
    real judgment a scripted stub can't replicate; a first attempt at
    this here incorrectly rejected a legitimate GARBLED_REF case (whose
    reference is *intentionally* unrecoverable) as if it were a collision,
    dropping the confirmed real-dataset match rate from 95% to 90%. That
    protection now lives only in the real deterministic_precheck (verified
    by an isolated unit test that calls it directly, independent of this
    stub) and the real LLM prompt - see docs/DECISIONS.md."""

    def __init__(self):
        self.call_count = 0
        # Real design-flaw fix (see docs/DECISIONS.md): api/jobs.py now
        # reads total_prompt_tokens/total_completion_tokens/
        # total_latency_seconds/total_calls off whatever client is in
        # use, uniformly, regardless of whether it's a real client or
        # this fake one - added here so that interface works during
        # this project's own testing (this sandbox has no network route
        # to a real LLM API at all). Deliberately honest about what
        # these numbers are: token counts are a real, simple estimate
        # from actual message length (roughly 4 characters per token,
        # a commonly used rough heuristic - not a claim to match a real
        # tokenizer exactly), and latency is the REAL wall-clock time
        # this fake call actually took (near-instant, since it's a
        # synchronous in-memory scripted response) - not a contrived
        # number pretending to simulate real network latency, which
        # would be dishonest in the opposite direction.
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_latency_seconds = 0.0
        self.total_calls = 0

    def _track_call(self, start_time, messages, response_content):
        self.total_prompt_tokens += len(json.dumps(messages)) // 4
        self.total_completion_tokens += len(json.dumps(response_content)) // 4
        self.total_latency_seconds += time.time() - start_time
        self.total_calls += 1

    def chat(self, messages, tools=None, tool_choice="auto"):
        start_time = time.time()
        self.call_count += 1

        if tools is None:
            payload = json.loads(messages[1]["content"])
            shortfall = payload.get("precomputed_shortfall", 0)
            accept = 0 <= shortfall <= 50
            response = {"role": "assistant", "content": json.dumps({
                "verdict": "accept" if accept else "reject",
                "reason": f"fake client: shortfall {shortfall} vs Rs 50 threshold",
            })}
            self._track_call(start_time, messages, response)
            return response

        last_tool_msgs = [m for m in messages if m.get("role") == "tool"]
        user_msg = messages[1]["content"]
        net_match = re.search(r'"net_amount":\s*([\d.]+)', user_msg)
        net_amount = float(net_match.group(1)) if net_match else None

        if not last_tool_msgs:
            return self._tool_call("search_by_amount_date", {"tolerance_pct": 0.15}, start_time, messages)

        candidates = json.loads(last_tool_msgs[-1]["content"])
        if not candidates:
            return self._tool_call("report_exception", {
                "exception_type": "NO_CANDIDATE_FOUND",
                "reasoning": "fake client: amount/date search returned no candidates",
            }, start_time, messages)

        best = min(candidates, key=lambda c: abs(c["settled_amount"] - (net_amount or 0)))
        return self._tool_call("propose_match", {
            "utrs": [best["utr_number"]],
            "reasoning": f"fake client: closest amount candidate ({best['settled_amount']} vs net {net_amount})",
        }, start_time, messages)

    def _tool_call(self, name, arguments, start_time, messages):
        response = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{self.call_count}",
                "type": "function",  # matches the real Groq/OpenAI schema - see llm_client.py's
                                     # _recover_invalid_tool_call() for why this field matters
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }
        self._track_call(start_time, messages, response)
        return response
