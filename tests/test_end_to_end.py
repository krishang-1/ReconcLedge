"""End-to-end tests using the fake LLM client (eval/fake_llm_client.py).
These can't confirm real LLM judgment quality - that requires a real
provider key and is checked manually, see docs/DECISIONS.md - but they do
confirm the full pipeline's mechanics never regress: every record gets
accounted for, no record is ever double-matched, and the recovery paths
built for real failures this session actually work.
"""

import json

import synthetic_generator as gen
from matcher import run_deterministic_stage
from react_loop import run_agent_stage, run_agent_on_record
from fake_llm_client import FakeLLMClient
from metrics import compute_metrics
from llm_client import _recover_invalid_tool_call


def _full_pipeline():
    gw, bank, gt = gen.generate()
    det_matched, det_exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    agent_matched, agent_exceptions = run_agent_stage(needs_agent, unclaimed, FakeLLMClient())
    return gw, gt, det_matched + agent_matched, det_exceptions + agent_exceptions


def test_every_record_accounted_for_end_to_end():
    gw, gt, matched, exceptions = _full_pipeline()
    assert len(matched) + len(exceptions) == len(gw)


def test_no_incorrect_matches_end_to_end():
    """Same non-negotiable property as the deterministic-only test, but
    across the full pipeline including the fake-client agent stage."""
    gw, gt, matched, exceptions = _full_pipeline()
    gt_by_id = {r["transaction_id"]: r for r in gt if r.get("transaction_id")}
    for m in matched:
        truth = gt_by_id[m["transaction_id"]]
        assert sorted(truth["correct_settlement_utrs"] or []) == sorted(m["utrs"])


def test_orphan_gateway_never_force_matched():
    """ORPHAN_GATEWAY transactions have no correct settlement by design -
    if the agent ever proposes a match for one, that's a false positive
    with real financial consequences in a production system."""
    gw, gt, matched, exceptions = _full_pipeline()
    gt_by_id = {r["transaction_id"]: r for r in gt if r.get("transaction_id")}
    matched_ids = {m["transaction_id"] for m in matched}
    orphan_ids = [txn_id for txn_id, truth in gt_by_id.items() if truth["mismatch_type"] == "ORPHAN_GATEWAY"]
    assert orphan_ids, "no ORPHAN_GATEWAY records generated - can't test this"
    for txn_id in orphan_ids:
        assert txn_id not in matched_ids


def test_metrics_pipeline_stays_internally_consistent():
    """Regression guard combining the matcher + metrics fixes together -
    by_mismatch_type totals must always sum to eval_set_size, end to end."""
    gw, gt, matched, exceptions = _full_pipeline()
    m = compute_metrics(matched, exceptions, gt)
    total_from_breakdown = sum(v["total"] for v in m["by_mismatch_type"].values())
    assert total_from_breakdown == m["eval_set_size"]


def test_recovers_from_commentary_tool_call_leak():
    """Regression guard for docs/DECISIONS.md errors #4 and #5: a model
    calling an unrecognized tool name (observed as gpt-oss's harmony-format
    'commentary' leak) must never crash the batch run - the loop should
    absorb it and reach a real resolution within its step budget."""

    class LeakThenRecoverClient:
        """First call simulates the exact leaked tool call Groq passed
        through; second call behaves normally."""

        def __init__(self):
            self.call_count = 0

        def chat(self, messages, tools=None, tool_choice="auto"):
            self.call_count += 1
            if self.call_count == 1:
                return {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": "leaked_call",
                        "function": {
                            "name": "commentary",
                            "arguments": json.dumps({"exception_type": "NO_CANDIDATE_FOUND", "reasoning": "no candidates"}),
                        },
                    }],
                }
            return {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "call_2",
                    "function": {"name": "report_exception", "arguments": json.dumps({
                        "exception_type": "NO_CANDIDATE_FOUND", "reasoning": "confirmed after nudge",
                    })},
                }],
            }

    gw, bank, gt = gen.generate()
    client = LeakThenRecoverClient()
    result = run_agent_on_record(gw[0], "test routing reason", [], client)
    assert result["status"] == "exception"
    assert client.call_count == 2, "loop should nudge and retry within budget, not crash on the first bad call"


def test_recover_invalid_tool_call_parses_real_groq_error_shape():
    """Regression guard for docs/DECISIONS.md error #5 specifically - the
    400-level recovery, tested against the literal error shape Groq
    returned in the real traceback that surfaced this bug."""

    class FakeResponse:
        status_code = 400

        def json(self):
            return {
                "error": {
                    "code": "tool_use_failed",
                    "failed_generation": json.dumps({
                        "name": "commentary",
                        "arguments": {"exception_type": "NO_CANDIDATE_FOUND", "reasoning": "test"},
                    }),
                }
            }

    recovered = _recover_invalid_tool_call(FakeResponse())
    assert recovered is not None
    assert recovered["tool_calls"][0]["function"]["name"] == "commentary"


def test_recovered_tool_call_includes_required_type_field():
    """Regression guard for a real error hit mid-session: the reconstructed
    tool_calls object was missing 'type': 'function', which Groq's schema
    requires on every later call that replays the message history back -
    it doesn't fail immediately when the message is first created, only
    several turns later when a fuller history gets re-validated, which is
    exactly why this slipped past 73 passing tests: the fake client had
    the identical gap, so nothing ever exercised the real schema
    requirement. See docs/DECISIONS.md."""

    class FakeResponse:
        status_code = 400

        def json(self):
            return {
                "error": {
                    "code": "tool_use_failed",
                    "failed_generation": json.dumps({"name": "commentary", "arguments": {}}),
                }
            }

    recovered = _recover_invalid_tool_call(FakeResponse())
    assert recovered["tool_calls"][0]["type"] == "function"


def test_survives_malformed_tool_call_json():
    """A model emitting invalid JSON in a tool call's arguments must not
    crash the batch run - this was found unguarded and fixed (see
    docs/DECISIONS.md, error #17): the raw json.loads() call had no
    try/except at all before this fix."""

    class MalformedThenRecoverClient:
        def __init__(self):
            self.call_count = 0

        def chat(self, messages, tools=None, tool_choice="auto"):
            self.call_count += 1
            if self.call_count == 1:
                return {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "bad_call", "function": {
                        "name": "report_exception",
                        "arguments": '{"exception_type": "NO_CANDIDATE_FOUND", "reasoning": ',  # truncated/invalid JSON
                    }}],
                }
            return {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_2", "function": {"name": "report_exception", "arguments": json.dumps({
                    "exception_type": "NO_CANDIDATE_FOUND", "reasoning": "confirmed after retry",
                })}}],
            }

    gw, bank, gt = gen.generate()
    result = run_agent_on_record(gw[0], "test", [], MalformedThenRecoverClient())
    assert result["status"] == "exception"
    assert result["reason"] == "confirmed after retry"


def test_survives_missing_required_tool_argument():
    """report_exception without its required exception_type field must be
    nudged back, not crash with a KeyError."""

    class MissingFieldThenRecoverClient:
        def __init__(self):
            self.call_count = 0

        def chat(self, messages, tools=None, tool_choice="auto"):
            self.call_count += 1
            if self.call_count == 1:
                return {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "bad_call", "function": {
                        "name": "report_exception",
                        "arguments": json.dumps({"reasoning": "missing the required field"}),
                    }}],
                }
            return {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_2", "function": {"name": "report_exception", "arguments": json.dumps({
                    "exception_type": "NO_CANDIDATE_FOUND", "reasoning": "confirmed after retry",
                })}}],
            }

    gw, bank, gt = gen.generate()
    result = run_agent_on_record(gw[0], "test", [], MissingFieldThenRecoverClient())
    assert result["status"] == "exception"
    assert result["reason"] == "confirmed after retry"


def test_step_budget_exhausted_terminates_gracefully():
    """A model that never calls propose_match or report_exception must
    still terminate cleanly at MAX_STEPS, not loop forever or crash."""

    class NeverTerminatesClient:
        def chat(self, messages, tools=None, tool_choice="auto"):
            return {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "x", "function": {
                    "name": "search_by_amount_date", "arguments": json.dumps({"tolerance_pct": 0.1}),
                }}],
            }

    gw, bank, gt = gen.generate()
    result = run_agent_on_record(gw[0], "test", bank, NeverTerminatesClient())
    assert result["status"] == "exception"
    assert "step budget" in result["reason"]


def test_no_utr_claimed_across_both_stages():
    """Regression guard combining matcher.py and react_loop.py - a UTR
    claimed by the deterministic stage must never also be claimed by the
    agent stage, and vice versa, across a full real pipeline run."""
    gw, bank, gt = gen.generate()
    det_matched, det_exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    agent_matched, agent_exceptions = run_agent_stage(needs_agent, unclaimed, FakeLLMClient())

    all_claimed = [utr for m in (det_matched + agent_matched) for utr in m["utrs"]]
    assert len(all_claimed) == len(set(all_claimed)), "a UTR was claimed by more than one match across the two stages"


def test_agent_matched_records_carry_verifier_method_without_changing_outcomes():
    """Regression guard for the confidence-gating groundwork: preserving
    verifier.verify()'s own "deterministic"/"llm" distinction on each
    agent-matched record (agent/confidence.py's raw material) must not
    change which records match, how many, or the 37/3/12 + 7/5
    structural baseline - only add metadata that was previously
    computed and discarded. See docs/DECISIONS.md."""
    gw, bank, gt = gen.generate()
    det_matched, det_exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    assert (len(det_matched), len(det_exceptions), len(needs_agent)) == (37, 3, 12)

    agent_matched, agent_exceptions = run_agent_stage(needs_agent, unclaimed, FakeLLMClient())
    assert (len(agent_matched), len(agent_exceptions)) == (7, 5)

    for m in agent_matched:
        assert "verifier_method" in m
        assert m["verifier_method"] in ("deterministic", "llm")


def test_agent_progress_events_carry_reasoning_text_without_changing_outcomes():
    """Regression guard for a real gap found while building the
    frontend's live-run view (Stage 6 Phase 2, docs/DECISIONS.md): the
    SSE-facing on_progress event previously carried only status/reason,
    never the actual agent_reasoning text - meaning a live viewer could
    see THAT a record resolved but never WHY. Purely additive: must not
    change which records match, how many, or the 37/3/12 + 7/5
    structural baseline."""
    gw, bank, gt = gen.generate()
    det_matched, det_exceptions, needs_agent, unclaimed = run_deterministic_stage(gw, bank)
    assert (len(det_matched), len(det_exceptions), len(needs_agent)) == (37, 3, 12)

    events = []
    agent_matched, agent_exceptions = run_agent_stage(
        needs_agent, unclaimed, FakeLLMClient(), on_progress=lambda i, t, e: events.append(e)
    )
    assert (len(agent_matched), len(agent_exceptions)) == (7, 5)

    matched_events = [e for e in events if e["status"] == "matched"]
    assert len(matched_events) == len(agent_matched)
    for event in matched_events:
        assert "agent_reasoning" in event
        assert event["agent_reasoning"] != ""  # the fake client always provides real reasoning text

    exception_events = [e for e in events if e["status"] == "exception"]
    assert len(exception_events) == len(agent_exceptions)
    for event in exception_events:
        assert event["agent_reasoning"] == ""  # exceptions don't have agent_reasoning to report
