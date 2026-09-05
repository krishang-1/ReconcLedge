"""Tests for llm_client.FallbackClient - the circuit breaker wrapping a
primary and secondary LLM client behind the shared chat() interface.
Uses tiny fake clients (not requests mocks - the wrapper only depends on
chat() existing, same interface contract as the real clients and
eval/fake_llm_client.py, so testing at that level is both simpler and a
more accurate reflection of what FallbackClient actually depends on."""

import llm_client


class ScriptedClient:
    """A minimal chat() stand-in whose behavior for each call is
    pre-scripted: either a return value, or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls += 1
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _msg(content):
    return {"role": "assistant", "content": content}


def test_uses_primary_when_healthy():
    primary = ScriptedClient([_msg("from primary")])
    secondary = ScriptedClient([])
    client = llm_client.FallbackClient(primary, secondary)
    assert client.chat([]) == _msg("from primary")
    assert primary.calls == 1
    assert secondary.calls == 0
    assert client.tripped is False


def test_single_primary_failure_below_threshold_still_served_by_secondary():
    primary = ScriptedClient([RuntimeError("primary down")])
    secondary = ScriptedClient([_msg("from secondary")])
    client = llm_client.FallbackClient(primary, secondary)
    result = client.chat([])
    assert result == _msg("from secondary")
    # one failure is below CIRCUIT_FAILURE_THRESHOLD (3) - shouldn't trip yet
    assert client.tripped is False


def test_circuit_trips_after_consecutive_failures_and_stops_trying_primary():
    primary = ScriptedClient([
        RuntimeError("fail 1"), RuntimeError("fail 2"), RuntimeError("fail 3"),
    ])
    secondary = ScriptedClient([_msg("s1"), _msg("s2"), _msg("s3"), _msg("s4")])
    client = llm_client.FallbackClient(primary, secondary)

    for _ in range(3):
        client.chat([])
    assert client.tripped is True
    assert primary.calls == 3

    # circuit is open now - next call should skip primary entirely
    result = client.chat([])
    assert result == _msg("s4")
    assert primary.calls == 3  # unchanged - primary not attempted this time


def test_success_resets_consecutive_failure_count():
    primary = ScriptedClient([
        RuntimeError("fail 1"), RuntimeError("fail 2"), _msg("recovered"), RuntimeError("fail 3"),
    ])
    secondary = ScriptedClient([_msg("s1"), _msg("s2"), _msg("s3")])
    client = llm_client.FallbackClient(primary, secondary)

    client.chat([])  # fail 1 -> secondary s1, consecutive=1
    client.chat([])  # fail 2 -> secondary s2, consecutive=2
    client.chat([])  # recovered -> primary succeeds, resets to 0
    assert client.tripped is False

    result = client.chat([])  # fail 3 -> consecutive=1 again, not tripped
    assert result == _msg("s3")
    assert client.tripped is False  # a single failure after a reset shouldn't trip


def test_half_open_probe_recovers_primary_after_trip():
    # 3 failures to trip, then a probe should occur after CIRCUIT_HALF_OPEN_AFTER (5) fallback calls
    primary_script = [RuntimeError("f1"), RuntimeError("f2"), RuntimeError("f3")] + [_msg("primary recovered")]
    primary = ScriptedClient(primary_script)
    secondary = ScriptedClient([_msg(f"s{i}") for i in range(1, 8)])
    client = llm_client.FallbackClient(primary, secondary)

    for _ in range(3):
        client.chat([])
    assert client.tripped is True

    # next 4 calls served by secondary only, no primary probe yet
    for _ in range(4):
        client.chat([])
    assert primary.calls == 3  # still unchanged

    # the 5th call since trip should probe primary, which now succeeds
    result = client.chat([])
    assert result == _msg("primary recovered")
    assert client.tripped is False
    assert primary.calls == 4


def test_both_providers_failing_raises_combined_error():
    primary = ScriptedClient([RuntimeError("primary down")])
    secondary = ScriptedClient([RuntimeError("secondary also down")])
    client = llm_client.FallbackClient(primary, secondary)
    try:
        client.chat([])
        assert False, "should have raised"
    except RuntimeError as e:
        assert "primary down" in str(e)
        assert "secondary also down" in str(e)


def test_secondary_failure_alone_after_open_circuit_raises_secondary_error_unwrapped():
    # circuit already tripped, primary skipped entirely for this call -
    # if secondary fails here there's no "both failed" story, just report it
    primary = ScriptedClient([RuntimeError("f1"), RuntimeError("f2"), RuntimeError("f3")])
    secondary = ScriptedClient([_msg("s1"), _msg("s2"), _msg("s3"), RuntimeError("secondary down")])
    client = llm_client.FallbackClient(primary, secondary)
    for _ in range(3):
        client.chat([])
    assert client.tripped is True

    try:
        client.chat([])
        assert False, "should have raised"
    except RuntimeError as e:
        assert "secondary down" in str(e)
        assert "primary" not in str(e).lower()
