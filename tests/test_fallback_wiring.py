"""Tests for api/app.py's get_llm_client() fallback-wiring decision:
does it build a plain GroqClient (old, unchanged behavior) or wrap it in
a FallbackClient with OpenRouter as secondary, based on whether
OPENROUTER_API_KEY is configured.

Resets app._shared_client (the process-wide cached singleton) before and
after each test - without this, whichever client got constructed first
in the test session would silently stick around for every later test and
every other test FILE, since get_llm_client() only constructs once.
"""

import app as api_app
import llm_client


def _reset_shared_client():
    api_app._shared_client = None


def test_plain_groq_client_when_openrouter_key_absent(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _reset_shared_client()
    try:
        client = api_app.get_llm_client()
        assert isinstance(client, llm_client.GroqClient)
        assert not isinstance(client, llm_client.FallbackClient)
    finally:
        _reset_shared_client()


def test_fallback_client_when_both_keys_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-openrouter-key")
    _reset_shared_client()
    try:
        client = api_app.get_llm_client()
        assert isinstance(client, llm_client.FallbackClient)
        assert isinstance(client.primary, llm_client.GroqClient)
        assert isinstance(client.secondary, llm_client.OpenRouterClient)
    finally:
        _reset_shared_client()


def test_groq_stays_primary_not_secondary(monkeypatch):
    """Groq is still the default, real-account-tested provider (see
    GroqClient's docstring) - OpenRouter is the fallback, never the
    other way around, regardless of key presence order."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-openrouter-key")
    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key")
    _reset_shared_client()
    try:
        client = api_app.get_llm_client()
        assert isinstance(client.primary, llm_client.GroqClient)
    finally:
        _reset_shared_client()


def test_shared_client_still_reused_across_calls(monkeypatch):
    """Regression guard for the existing sticky-instance behavior (see
    get_llm_client's docstring on why reuse matters for the sticky
    max_tokens learning) - wiring in the fallback wrapper must not
    accidentally start constructing a fresh client on every call."""
    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _reset_shared_client()
    try:
        first = api_app.get_llm_client()
        second = api_app.get_llm_client()
        assert first is second
    finally:
        _reset_shared_client()
