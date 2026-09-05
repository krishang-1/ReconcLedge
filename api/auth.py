"""Simple API-key authentication, additive and interface-preserving.

Enforced only when API_KEYS is set in the environment (comma-separated
list of valid keys). This deliberately mirrors the existing
GROQ_API_KEY / OPENROUTER_API_KEY pattern in agent/llm_client.py and
get_llm_client() in app.py: importing and testing this module never
requires configuration, so the pre-existing 135-test suite keeps
passing unchanged with zero test file edits - auth is exercised by a
new, dedicated tests/test_auth.py instead (same discipline as the
audit-log item: new capability, new tests, existing tests untouched).

This is a real, named scope trade-off, not an oversight: "disabled
until configured" is fine for local dev and for this hackathon's demo
deployment, but a real production rollout would need to either
(a) always set API_KEYS in its environment - the mechanism already
supports this, nothing else would need to change - or (b) fail closed
instead of open when unset, which this does not do. Documented in
docs/DECISIONS.md rather than silently assumed to be handled.

Also a named limitation: this protects every route via a header
(X-API-Key), which works for POST/GET requests and for the SSE stream
endpoint when hit with a header-aware client (requests, httpx, the
TestClient, a fetch()-based EventSource polyfill) - but NOT for a
browser's native EventSource, which cannot set custom request headers.
A real frontend consuming /runs/{run_id}/stream would need either a
fetch-based SSE client or a short-lived signed query-param token
instead. Not attempted here - Stage 6 (frontend) is explicitly
deprioritized, so there's no real consumer of the stream endpoint yet
to build that against.
"""

import os
import sys

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Paths an orchestrator (k8s probes, an ALB health check) must reach
# without credentials - otherwise a misconfigured auth setup makes the
# whole service look unhealthy. Exact-match only: a prefix match could
# accidentally exempt routes added later.
UNAUTHENTICATED_PATHS = {"/health"}


def _configured_keys() -> set[str]:
    """Reads API_KEYS live on every call (not cached at import) so tests
    can monkeypatch the environment per-test without reimporting the
    app module - matches how get_llm_client() reads GROQ_API_KEY lazily
    rather than at import time."""
    raw = os.environ.get("API_KEYS", "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def require_api_key(request: Request, api_key: str | None = Security(_api_key_header)) -> None:
    """FastAPI dependency, wired at the app level in app.py so it covers
    every current and future route in one place rather than being
    repeated per-endpoint.

    No-op immediately for UNAUTHENTICATED_PATHS (see module-level
    comment) - checked first, before anything else, since a health
    check must never be gated on credentials.

    No-op (auth disabled) if API_KEYS isn't set in the environment - see
    module docstring. When it is set: missing header -> 401 (not
    authenticated at all), header present but not a configured key ->
    403 (authenticated attempt, wrong credential) - the standard
    distinction between the two statuses, not interchangeable here.
    """
    if request.url.path in UNAUTHENTICATED_PATHS:
        return
    keys = _configured_keys()
    if not keys:
        return  # auth disabled - not configured for this deployment
    if api_key is None:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    if api_key not in keys:
        raise HTTPException(status_code=403, detail="invalid API key")


def warn_if_auth_disabled():
    """Prints a loud, one-time warning to stderr if API_KEYS is unset at
    the moment this is called. Meant to be called exactly once, at
    process startup (see api/app.py's module-level call to this) - not
    from require_api_key(), which runs on every single request and
    would spam the log instead of announcing it once at boot.

    "Disabled until configured" is a deliberate, documented tradeoff
    (see this module's own docstring above) - but a real deployment
    silently running with auth off and nobody noticing until an
    incident is exactly the kind of gap a boot-time log line exists to
    prevent. The design decision itself isn't changed by this function;
    it's just no longer invisible."""
    if not _configured_keys():
        print(
            "WARNING: API_KEYS is not set - authentication is DISABLED. Every "
            "request to this API will be processed with no credential check at "
            "all. This is fine for local development, but a real deployment "
            "should set API_KEYS - see README.md for setup instructions.",
            file=sys.stderr,
        )
