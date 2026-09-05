"""Tests for agent/llm_client.py's HTTP-level logic: retries, backoff, and
error handling. Everything here has, until now, only ever been validated
by real calls against live Groq/OpenRouter traffic - expensive, slow, and
non-repeatable. Mocks requests.post so this logic can be verified on every
run without needing a real API key or network access.
"""

import json

import llm_client


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code, json_body=None, text=None, headers=None, raise_on_json=False):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text if text is not None else json.dumps(json_body or {})
        self.headers = headers or {}
        self._raise_on_json = raise_on_json

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._raise_on_json:
            raise ValueError("not valid JSON")
        return self._json_body


def _success_body(content="hello", tool_calls=None, usage=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    body = {"choices": [{"message": message}]}
    if usage is not None:
        body["usage"] = usage
    return body


def test_missing_api_key_raises_immediately(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    try:
        llm_client.OpenRouterClient()
        assert False, "should have raised without an API key"
    except RuntimeError as e:
        assert "OPENROUTER_API_KEY" in str(e)


def test_succeeds_immediately_on_200(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse(200, _success_body("plain answer"))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "plain answer"


def test_retries_on_429_then_succeeds(monkeypatch):
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "0"})
        return FakeResponse(200, _success_body("recovered"))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)  # don't actually wait in tests

    client = llm_client.OpenRouterClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "recovered"
    assert calls["count"] == 2


def test_raises_after_max_retries_exhausted(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse(429, {"error": {"message": "still limited"}}, headers={"Retry-After": "0"})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)

    client = llm_client.OpenRouterClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised after exhausting retries"
    except RuntimeError as e:
        assert "rate-limited" in str(e)


def test_400_recovers_via_full_chat_call(monkeypatch):
    """Same recovery mechanism as test_recover_invalid_tool_call_parses_real_groq_error_shape
    in test_end_to_end.py, but exercised through the actual .chat() call path
    end to end, not just the extraction helper in isolation."""

    def fake_post(*args, **kwargs):
        return FakeResponse(400, {
            "error": {
                "code": "tool_use_failed",
                "failed_generation": json.dumps({"name": "commentary", "arguments": {"x": 1}}),
            }
        })

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x"}}])
    assert result["tool_calls"][0]["function"]["name"] == "commentary"


def test_400_unrecoverable_raises_clear_error(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse(400, {"error": {"code": "invalid_request_error", "message": "something else broke"}})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised - this 400 shape isn't recoverable"
    except RuntimeError as e:
        assert "400" in str(e)


def test_non_json_response_body_raises_clear_error(monkeypatch):
    """Regression guard for the proactive hardening fix: a non-JSON 200
    response (e.g. a proxy error page) must raise a clear, diagnosable
    error - not an uncaught ValueError from response.json()."""

    def fake_post(*args, **kwargs):
        return FakeResponse(200, text="<html>502 Bad Gateway</html>", raise_on_json=True)

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised on a non-JSON body"
    except RuntimeError as e:
        assert "non-JSON" in str(e)


def test_missing_choices_in_response_raises_clear_error(monkeypatch):
    """Regression guard: a 200 response with an unexpected shape (e.g. no
    choices, from upstream content filtering) must raise clearly instead
    of an uncaught KeyError/IndexError."""

    def fake_post(*args, **kwargs):
        return FakeResponse(200, {"id": "resp_123"})  # no "choices" key at all

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised on a missing choices key"
    except RuntimeError as e:
        assert "unexpected shape" in str(e)


def test_groq_missing_api_key_raises_immediately(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    try:
        llm_client.GroqClient()
        assert False, "should have raised without an API key"
    except RuntimeError as e:
        assert "GROQ_API_KEY" in str(e)


def test_groq_succeeds_immediately_on_200(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse(200, _success_body("plain answer"))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.GroqClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "plain answer"


def test_groq_retries_on_429_then_succeeds(monkeypatch):
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "0"})
        return FakeResponse(200, _success_body("recovered"))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)

    client = llm_client.GroqClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "recovered"
    assert calls["count"] == 2


def test_groq_raises_after_max_retries_exhausted(monkeypatch):
    """Same dead-code bug found for OpenRouter (error #20) applied
    identically to GroqClient - confirm the fix landed there too, not
    just on the default client."""

    def fake_post(*args, **kwargs):
        return FakeResponse(429, {"error": {"message": "still limited"}}, headers={"Retry-After": "0"})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)

    client = llm_client.GroqClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised after exhausting retries"
    except RuntimeError as e:
        assert "rate-limited" in str(e)


def test_groq_400_recovers_via_full_chat_call(monkeypatch):
    """The commentary tool-call leak (errors #4/#5) was found and fixed on
    Groq specifically - confirm the recovery path still works end to end
    through GroqClient.chat(), not just OpenRouterClient's."""

    def fake_post(*args, **kwargs):
        return FakeResponse(400, {
            "error": {
                "code": "tool_use_failed",
                "failed_generation": json.dumps({"name": "commentary", "arguments": {"x": 1}}),
            }
        })

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.GroqClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x"}}])
    assert result["tool_calls"][0]["function"]["name"] == "commentary"


def test_groq_400_unrecoverable_raises_clear_error(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse(400, {"error": {"code": "invalid_request_error", "message": "something else broke"}})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.GroqClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised - this 400 shape isn't recoverable"
    except RuntimeError as e:
        assert "400" in str(e)


def test_groq_non_json_response_body_raises_clear_error(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse(200, text="<html>502 Bad Gateway</html>", raise_on_json=True)

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.GroqClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised on a non-JSON body"
    except RuntimeError as e:
        assert "non-JSON" in str(e)


def test_groq_missing_choices_in_response_raises_clear_error(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse(200, {"id": "resp_123"})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.GroqClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised on a missing choices key"
    except RuntimeError as e:
        assert "unexpected shape" in str(e)


def test_groq_retries_on_transient_402_then_succeeds(monkeypatch):
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(402, {"error": {"message": "in-flight budget exhausted"}}, headers={"Retry-After": "0"})
        return FakeResponse(200, _success_body("recovered from 402"))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)

    client = llm_client.GroqClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "recovered from 402"
    assert calls["count"] == 2


def test_retries_on_transient_402_then_succeeds(monkeypatch):
    """Regression guard for the real error hit mid-session: OpenRouter's
    'in_flight_budget_exhausted' 402 includes a Retry-After header, meaning
    it's explicitly meant to be retried - unlike a hard credit-exhaustion
    402. Previously ANY 402 failed immediately with zero retries."""
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(
                402,
                {"error": {"message": "in-flight budget exhausted", "code": 402}},
                headers={"Retry-After": "0"},
            )
        return FakeResponse(200, _success_body("recovered from 402"))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)

    client = llm_client.OpenRouterClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "recovered from 402"
    assert calls["count"] == 2


def test_credit_shortfall_402_retries_with_reduced_max_tokens(monkeypatch):
    """The other kind of 402 - not a transient budget issue, but a small,
    precisely-specified credit shortfall (e.g. wanted 4096, can afford
    3613). Fixable without adding credits, since the error message tells
    us exactly how much we can spend and our real responses fit
    comfortably in far less than the default cap anyway."""
    seen_max_tokens = []

    def fake_post(*args, **kwargs):
        body = json.loads(kwargs["data"])
        seen_max_tokens.append(body["max_tokens"])
        if len(seen_max_tokens) == 1:
            return FakeResponse(402, {
                "error": {"message": "This request requires more credits, or fewer max_tokens. "
                                      "You requested up to 4096 tokens, but can only afford 3613.", "code": 402}
            })  # no Retry-After - this is the credit-shortfall shape, not the transient one
        return FakeResponse(200, _success_body("recovered from credit shortfall"))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}])

    assert result["content"] == "recovered from credit shortfall"
    assert seen_max_tokens[0] == llm_client.MAX_TOKENS
    assert seen_max_tokens[1] < seen_max_tokens[0]
    assert seen_max_tokens[1] >= llm_client.MIN_VIABLE_TOKENS


def test_credit_shortfall_too_small_fails_without_wasting_a_retry(monkeypatch):
    """If the affordable amount is below MIN_VIABLE_TOKENS, retrying with
    it wouldn't produce a usable response anyway - fail immediately with
    a clear message instead of burning a retry on something hopeless."""
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        return FakeResponse(402, {
            "error": {"message": "You requested up to 4096 tokens, but can only afford 50.", "code": 402}
        })

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised"
    except RuntimeError as e:
        assert "add credits" in str(e)
    assert calls["count"] == 1


def test_credit_shortfall_retries_survive_more_than_max_retries_in_a_row(monkeypatch):
    """Regression guard for a real bug found via a concurrency stress
    test (reproduced in ~7% of runs, not a one-off fluke): credit-
    shortfall retries previously shared MAX_RETRIES (5) with wait-based
    retries (429, transient 402), even though they're fundamentally
    different - free (no sleep) and making genuine progress each time,
    not hoping the same request succeeds differently. Under real
    concurrent load, more than 5 legitimate shrinking-credit responses
    in a row could exhaust the shared budget before ever getting a real
    attempt. Confirms 8 consecutive credit-shortfall 402s (more than the
    old shared MAX_RETRIES=5, well under the new independent
    MAX_CREDIT_SHORTFALL_RETRIES=20) still succeeds."""
    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        call_count["n"] += 1
        body = json.loads(kwargs["data"])
        if call_count["n"] <= 8:
            shortfall = body["max_tokens"] - 20
            return FakeResponse(402, {
                "error": {"message": f"You requested up to {body['max_tokens']} tokens, but can only afford {shortfall}.", "code": 402}
            })
        return FakeResponse(200, _success_body("succeeded after 8 shrinking retries"))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "succeeded after 8 shrinking retries"
    assert call_count["n"] == 9


def test_max_tokens_persists_across_separate_chat_calls(monkeypatch):
    """Regression guard for the real bug found mid-session: max_tokens was
    a local variable reset to the full default on every chat() call, so a
    single batch run (which calls chat() dozens of times across many
    records) wastefully rediscovered the same lower affordable ceiling
    from scratch every time - visible in a real run as the same 'can only
    afford N' value repeating a dozen times in a row. It must now be
    learned once and reused on every subsequent call from the same client
    instance."""
    seen_max_tokens = []

    def fake_post(*args, **kwargs):
        body = json.loads(kwargs["data"])
        seen_max_tokens.append(body["max_tokens"])
        if len(seen_max_tokens) == 1:
            return FakeResponse(402, {
                "error": {"message": "You requested up to 4096 tokens, but can only afford 3603.", "code": 402}
            })
        return FakeResponse(200, _success_body("ok"))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")

    client.chat([{"role": "user", "content": "first call"}])
    client.chat([{"role": "user", "content": "second call, separate chat() invocation"}])

    # first call: 4096 (default), then 3593 after learning the ceiling
    # second call: must start at 3593 directly, NOT reset back to 4096
    assert seen_max_tokens[0] == llm_client.MAX_TOKENS
    assert seen_max_tokens[1] == client.max_tokens
    assert seen_max_tokens[2] == client.max_tokens, (
        "second chat() call reset max_tokens back to the default instead of "
        "reusing what the first call already learned - this is the exact bug "
        "seen in the real run"
    )


def test_non_retryable_402_fails_immediately_without_retrying(monkeypatch):
    """A hard credit-exhaustion 402 (no Retry-After header - see the
    original max_tokens bug, docs/DECISIONS.md error #12) must NOT be
    retried - no amount of waiting adds funds to the account, so retrying
    would just waste time before failing anyway."""
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        return FakeResponse(402, {"error": {"message": "insufficient credits", "code": 402}})  # no Retry-After

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    try:
        client.chat([{"role": "user", "content": "hi"}])
        assert False, "should have raised"
    except RuntimeError as e:
        assert "402" in str(e)
    assert calls["count"] == 1, "a non-retryable 402 should fail on the first attempt, not retry"


def test_groq_client_tracks_real_usage_from_the_response(monkeypatch):
    """Real design-flaw fix (see docs/DECISIONS.md): the raw API
    response already includes token usage - it was being discarded at
    chat()'s return statement. Confirms it's now genuinely captured,
    not just that chat() still works."""
    def fake_post(*args, **kwargs):
        return FakeResponse(200, _success_body(usage={"prompt_tokens": 120, "completion_tokens": 15}))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.GroqClient(api_key="test_key")
    assert client.total_calls == 0
    client.chat([{"role": "user", "content": "hi"}])
    assert client.total_prompt_tokens == 120
    assert client.total_completion_tokens == 15
    assert client.total_calls == 1
    assert client.total_latency_seconds >= 0

    # Cumulative, not overwritten - a second call adds, doesn't replace.
    client.chat([{"role": "user", "content": "hi again"}])
    assert client.total_prompt_tokens == 240
    assert client.total_completion_tokens == 30
    assert client.total_calls == 2


def test_openrouter_client_tracks_real_usage_from_the_response(monkeypatch):
    def fake_post(*args, **kwargs):
        return FakeResponse(200, _success_body(usage={"prompt_tokens": 80, "completion_tokens": 10}))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.OpenRouterClient(api_key="test_key")
    client.chat([{"role": "user", "content": "hi"}])
    assert client.total_prompt_tokens == 80
    assert client.total_completion_tokens == 10
    assert client.total_calls == 1


def test_missing_usage_field_does_not_crash_tracking(monkeypatch):
    """A response with no "usage" key at all (some providers/edge cases
    might omit it) must not crash - falls back to 0 for that call's
    token counts, but still honestly counts the call and its latency."""
    def fake_post(*args, **kwargs):
        return FakeResponse(200, _success_body())  # no usage= passed

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = llm_client.GroqClient(api_key="test_key")
    client.chat([{"role": "user", "content": "hi"}])
    assert client.total_prompt_tokens == 0
    assert client.total_completion_tokens == 0
    assert client.total_calls == 1


def test_fallback_client_aggregates_usage_from_both_underlying_clients(monkeypatch):
    """Real behavior, not just a formula read from the source - a call
    that succeeds on the primary contributes to the primary's own
    counters, and FallbackClient's aggregating properties must reflect
    that sum, not an independently-tracked total of its own that could
    drift out of sync."""
    def fake_post(*args, **kwargs):
        return FakeResponse(200, _success_body(usage={"prompt_tokens": 50, "completion_tokens": 5}))

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    primary = llm_client.GroqClient(api_key="test_key")
    secondary = llm_client.OpenRouterClient(api_key="test_key_2")
    fallback = llm_client.FallbackClient(primary, secondary)

    fallback.chat([{"role": "user", "content": "hi"}])
    assert fallback.total_prompt_tokens == 50
    assert fallback.total_calls == 1
    assert primary.total_calls == 1
    assert secondary.total_calls == 0  # never touched - primary succeeded
