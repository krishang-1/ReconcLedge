"""Thin LLM chat-completions wrappers (OpenAI-compatible tool calling).
Kept as a small interface (chat()) rather than baking any one provider's
specifics into the agent loop, so react_loop.py and verifier.py work
unchanged against GroqClient, OpenRouterClient, or eval/fake_llm_client.py.
"""

import json
import os
import re
import threading
import time

import requests

MAX_RETRIES = 5
# Separate, larger budget from MAX_RETRIES: credit-shortfall retries cost
# no time and adapt to new information each attempt, unlike wait-based
# 429 retries. Sharing one budget let concurrent threads exhaust their
# retries on recoverable shortfalls before a real attempt.
MAX_CREDIT_SHORTFALL_RETRIES = 20
# Our responses are tiny (one tool call or a one-line verdict). Without a
# cap, OpenRouter reserves the model's full 65k completion budget per
# request, costing far more credit than we ever actually use.
MAX_TOKENS = 4096
# Below this, a reduced max_tokens cap isn't worth retrying with - our
# smallest real responses (a bare tool call) still need a few hundred
# tokens of headroom, so an affordable amount below this is functionally
# the same as having none.
MIN_VIABLE_TOKENS = 512


class OpenRouterClient:
    """OpenRouter-backed client. Requires OPENROUTER_API_KEY in the environment.
    Kept as an alternative - GroqClient is the default in eval/run_batch.py
    again as of 2026-08-23, since Groq is genuinely free (no card required)
    and OpenRouter's account here ran into repeated credit constraints
    (see docs/DECISIONS.md). Default model is moonshotai/kimi-k2-0905 if
    you do switch back to this client - deliberately not a gpt-oss model,
    since gpt-oss's "harmony" chat format is what caused the recurring
    commentary tool-call bug on Groq. Kimi K2 doesn't use that format."""

    URL = "https://openrouter.ai/api/v1/chat/completions"
    DEFAULT_MODEL = "moonshotai/kimi-k2-0905"

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set in environment")
        self.model = model or self.DEFAULT_MODEL
        # Real, cumulative usage/latency tracking - same reasoning and
        # same shape as GroqClient's own (see that class's __init__
        # comment for the full rationale, added to close a real design
        # flaw named in a self-critique - see docs/DECISIONS.md).
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_latency_seconds = 0.0
        self.total_calls = 0
        # Sticky across calls, not reset per chat() call - see chat()'s
        # docstring for why. Monotonically decreases as real runs discover
        # a lower affordable ceiling; never increases mid-run since a
        # balance won't replenish during a single batch run.
        self.max_tokens = MAX_TOKENS

    def chat(self, messages, tools=None, tool_choice="auto"):
        """Sends one chat-completion request, retrying on 429 and recoverable
        402s. Returns the raw message dict from choices[0].

        max_tokens is remembered on self, not reset to MAX_TOKENS at the
        start of every call. A single batch run calls chat() dozens of
        times (once per tool-calling step, across every record) - without
        this, every single call would waste a guaranteed-to-fail attempt
        at the full 4096 tokens once the account balance has already
        dropped below that, before rediscovering the same lower number all
        over again. Found by inspecting a real run where the same "can
        only afford N" value repeated a dozen times in a row - not because
        one record was stuck, but because every call was independently
        relearning something already known. See docs/DECISIONS.md.
        """
        wait_attempt = 0
        credit_attempt = 0
        start_time = time.time()
        while True:
            payload = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": self.max_tokens}
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice

            response = requests.post(
                self.URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/krishang-1/razorpay-finance-controller",
                    "X-Title": "Razorpay Buildathon Finance Controller",
                },
                data=json.dumps(payload),
                timeout=60,
            )

            if response.status_code == 402:
                affordable = _parse_affordable_tokens(response)
                if affordable is not None and affordable >= MIN_VIABLE_TOKENS and credit_attempt < MAX_CREDIT_SHORTFALL_RETRIES:
                    # A small credit shortfall (e.g. wanted 4096, can afford
                    # fixable without adding credits: the error states the
                    # affordable cap, and our responses need far less than
                    # MAX_TOKENS anyway. Retry immediately at that cap.
                    credit_attempt += 1
                    previous = self.max_tokens
                    self.max_tokens = max(affordable - 10, MIN_VIABLE_TOKENS)
                    print(f"  [402 credit-limited - retrying with max_tokens={self.max_tokens} (was {previous}), attempt {credit_attempt}/{MAX_CREDIT_SHORTFALL_RETRIES}]")
                    continue
                if "Retry-After" in response.headers and wait_attempt < MAX_RETRIES:
                    # A different 402: OpenRouter's in-flight request
                    # budget, not account balance - transient, and the API
                    # says so via Retry-After.
                    wait_attempt += 1
                    wait = float(response.headers["Retry-After"])
                    try:
                        detail = response.json().get("error", {}).get("message", response.text)[:200]
                    except (ValueError, KeyError):
                        detail = response.text[:200]
                    print(f"  [402 transient - waiting {wait:.0f}s, attempt {wait_attempt}/{MAX_RETRIES}] {detail}")
                    time.sleep(wait)
                    continue
                # Neither recoverable case applies - a real "add credits"
                # failure. No amount of retrying fixes this.
                raise RuntimeError(f"OpenRouter API error 402 (add credits at openrouter.ai/settings/credits): {response.text}")

            if response.status_code == 429:
                if wait_attempt < MAX_RETRIES:
                    wait_attempt += 1
                    wait = float(response.headers.get("Retry-After", 2 ** wait_attempt))
                    # Print the actual body, not just "rate limited" - OpenRouter's 429s
                    # can mean its own per-key limit, an upstream provider's limit passed
                    # through, or (less commonly) a misreported credit issue. Blindly
                    # retrying without seeing which one wastes time if it's not transient.
                    try:
                        detail = response.json().get("error", {}).get("message", response.text)[:200]
                    except (ValueError, KeyError):
                        detail = response.text[:200]
                    print(f"  [429 - waiting {wait:.0f}s, attempt {wait_attempt}/{MAX_RETRIES}] {detail}")
                    time.sleep(wait)
                    continue
                # Raise the specific message here - this branch was once
                # unreachable, so exhausted rate-limit retries surfaced as
                # a generic API error instead.
                raise RuntimeError(f"OpenRouter API still rate-limited after {MAX_RETRIES} retries: {response.text}")

            if response.status_code == 400:
                recovered = _recover_invalid_tool_call(response)
                if recovered:
                    return recovered

            if not response.ok:
                raise RuntimeError(f"OpenRouter API error {response.status_code}: {response.text}")

            try:
                body = response.json()
            except ValueError:
                raise RuntimeError(f"OpenRouter returned a non-JSON response body (status {response.status_code}): {response.text[:300]}")
            if "error" in body:
                raise RuntimeError(f"OpenRouter API returned an error in a 200 response: {body['error']}")
            try:
                return body["choices"][0]["message"]
            except (KeyError, IndexError):
                raise RuntimeError(f"OpenRouter response had an unexpected shape (no choices/message): {json.dumps(body)[:300]}")
            finally:
                # In a `finally` so a malformed response (which raises
                # above) still counts as a real call that took real time,
                # even without clean token counts.
                usage = body.get("usage", {})
                self.total_prompt_tokens += usage.get("prompt_tokens", 0)
                self.total_completion_tokens += usage.get("completion_tokens", 0)
                self.total_latency_seconds += time.time() - start_time
                self.total_calls += 1

        # Genuinely unreachable with while True above - every path either
        # returns or raises. Kept only as a defensive last resort in case a
        # future edit adds a path that falls through without doing either.
        raise RuntimeError("OpenRouter API chat() exited its retry loop without returning or raising - this should never happen")


class GroqClient:
    """Real Groq-backed client. Requires GROQ_API_KEY in the environment.
    The default in eval/run_batch.py again as of 2026-08-23 - genuinely
    free (no card required), unlike the OpenRouter account this session
    hit repeated credit constraints on.

    Default model is openai/gpt-oss-120b - reverted back from a brief
    attempt at llama-3.3-70b-versatile, which turned out to be listed as
    accessible in Krishang's Groq console but not actually callable via
    the API on his key ("no access" on a real attempt). gpt-oss-120b is
    the confirmed-working choice: a full real run on it independently
    landed at the same 95% match rate OpenRouter's kimi-k2-0905 gave,
    which is good evidence the number reflects genuine reasoning quality
    rather than one provider's quirks. It can occasionally leak an
    internal reasoning-channel artifact as a bogus "commentary" tool call
    (see docs/DECISIONS.md) - handled by _recover_invalid_tool_call()
    below plus the defensive tool-name handling in react_loop.py, both
    tested against this exact failure shape and confirmed not to have
    caused any problem in the successful real run."""

    URL = "https://api.groq.com/openai/v1/chat/completions"
    DEFAULT_MODEL = "openai/gpt-oss-120b"

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set in environment")
        self.model = model or self.DEFAULT_MODEL
        # Cumulative, not per-call, deliberately: this client is a
        # singleton shared across runs, so it can't own a per-run
        # counter. Callers take a before/after delta (see api/jobs.py).
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_latency_seconds = 0.0
        self.total_calls = 0

    def chat(self, messages, tools=None, tool_choice="auto"):
        """Sends one chat-completion request, retrying on 429. Returns the raw message dict from choices[0]."""
        start_time = time.time()
        payload = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": MAX_TOKENS}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        for attempt in range(MAX_RETRIES + 1):
            response = requests.post(
                self.URL,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=60,
            )
            if response.status_code == 402 and "Retry-After" in response.headers and attempt < MAX_RETRIES:
                wait = float(response.headers["Retry-After"])
                print(f"  [402 transient - waiting {wait:.0f}s, attempt {attempt + 1}/{MAX_RETRIES}]")
                time.sleep(wait)
                continue

            if response.status_code == 429:
                if attempt < MAX_RETRIES:
                    wait = float(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                    print(f"  [rate limited - waiting {wait:.0f}s, attempt {attempt + 1}/{MAX_RETRIES}]")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"Groq API still rate-limited after {MAX_RETRIES} retries: {response.text}")

            if response.status_code == 400:
                recovered = _recover_invalid_tool_call(response)
                if recovered:
                    # No clean usage data available from a malformed-
                    # response recovery path - still honestly count the
                    # call and its real latency (a real network round
                    # trip did happen), just not token counts we don't
                    # actually have.
                    self.total_latency_seconds += time.time() - start_time
                    self.total_calls += 1
                    return recovered

            if not response.ok:
                raise RuntimeError(f"Groq API error {response.status_code}: {response.text}")
            try:
                body = response.json()
            except ValueError:
                raise RuntimeError(f"Groq returned a non-JSON response body (status {response.status_code}): {response.text[:300]}")
            try:
                message = body["choices"][0]["message"]
            except (KeyError, IndexError):
                raise RuntimeError(f"Groq response had an unexpected shape (no choices/message): {json.dumps(body)[:300]}")
            usage = body.get("usage", {})
            self.total_prompt_tokens += usage.get("prompt_tokens", 0)
            self.total_completion_tokens += usage.get("completion_tokens", 0)
            self.total_latency_seconds += time.time() - start_time
            self.total_calls += 1
            return message

        raise RuntimeError(f"Groq API exited its retry loop unexpectedly after {MAX_RETRIES} retries")  # defensive fallback, should be unreachable


def _parse_affordable_tokens(response):
    """Parses OpenRouter's credit-shortfall 402 message ("You requested up
    to X tokens, but can only afford Y") for the affordable amount Y.
    Returns None if the response isn't this specific, parseable shape -
    caller should treat that as a non-recoverable 402."""
    try:
        message = response.json()["error"]["message"]
    except (KeyError, ValueError, TypeError):
        return None
    match = re.search(r"can only afford (\d+)", message)
    return int(match.group(1)) if match else None


def _recover_invalid_tool_call(response):
    """Parses a tool_use_failed-style 400 error and reconstructs the attempted
    call as a normal tool_calls message, so it flows through the same
    unrecognized-tool handling in react_loop.py a valid-but-unknown call
    would. Returns None (caller should raise normally) if the error body
    isn't this specific, recoverable shape - provider-agnostic best effort,
    since OpenRouter may not use exactly Groq's error shape."""
    try:
        body = response.json()["error"]
        failed_generation = body.get("failed_generation") or body.get("metadata", {}).get("raw")
        if not failed_generation:
            return None
        attempted = json.loads(failed_generation)
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "recovered_call",
                "type": "function",  # required by Groq/OpenAI's schema on every later call that
                                     # replays this message back - its absence here doesn't fail
                                     # immediately, only several turns later when the full history
                                     # gets re-validated. Found on a real run, not by any test - see
                                     # docs/DECISIONS.md.
                "function": {"name": attempted["name"], "arguments": json.dumps(attempted.get("arguments", {}))},
            }],
        }
    except (KeyError, ValueError, TypeError):
        return None


# Consecutive primary failures (each already having exhausted its own
# retry budget) before the circuit trips. Not 1 - a single failure may be
# a blip, and the secondary covers that call anyway. Not large either -
# each failure stacks its own full retry wait before anything changes.
CIRCUIT_FAILURE_THRESHOLD = 3
# Fallback-served calls before probing primary again. A production
# breaker would use a timer, but this project's calls run back-to-back
# in one synchronous pipeline, making a call count near-equivalent here
# and deterministically testable without faking a clock.
CIRCUIT_HALF_OPEN_AFTER = 5


class FallbackClient:
    """Wraps a primary and secondary LLM client behind the same chat()
    interface every caller already uses (react_loop.py, verifier.py) -
    neither needs to know a fallback exists, matching this module's
    stated goal of keeping chat() a small, swappable interface.

    Circuit breaker over consecutive failures, not per-call fallback and
    not "wait for a single call to exhaust every retry it has." Primary's
    own chat() already retries transient errors (429, 402-with-Retry-
    After) internally before ever raising, so a raised exception here is
    already a real signal, not noise - tripping on the very first one
    would abandon a provider mid one-off hiccup. This tracks CONSECUTIVE
    failures across calls and trips after CIRCUIT_FAILURE_THRESHOLD,
    tolerating isolated blips while still reacting within a few calls to
    a genuine sustained outage - not immediately, not never.

    Every individual call is covered by the secondary on ANY primary
    failure, tripped or not - a call below the threshold still gets
    served, it just also nudges the counter. This means the circuit
    state controls which provider is tried FIRST on the next call, not
    whether a given call's result reaches the caller.

    Half-open recovery: once tripped, stays on the secondary but retries
    the primary every CIRCUIT_HALF_OPEN_AFTER calls; a real success there
    closes the circuit back to primary immediately. Mirrors this
    project's existing sticky-but-adaptive pattern (see the sticky
    max_tokens ceiling above) rather than either "never look back" or
    "always re-check every call."

    Thread-safe: react_loop.py's agent stage and the API's background
    job worker can both drive concurrent chat() calls through one shared
    client instance (see get_llm_client() in api/app.py) - same reason
    api/app.py locks around shared-client construction.
    """

    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        self._consecutive_failures = 0
        self._tripped = False
        self._calls_since_probe = 0
        self._lock = threading.Lock()

    @property
    def total_prompt_tokens(self):
        """Real usage tracking, aggregated rather than independently
        tracked here (see docs/DECISIONS.md) - deliberately a read-only
        sum of whichever underlying client actually served each call,
        not a separately-maintained counter of its own. Avoids the two
        counters ever being able to drift out of sync with each other,
        since there's only ever one real source of truth per call."""
        return self.primary.total_prompt_tokens + self.secondary.total_prompt_tokens

    @property
    def total_completion_tokens(self):
        return self.primary.total_completion_tokens + self.secondary.total_completion_tokens

    @property
    def total_latency_seconds(self):
        return self.primary.total_latency_seconds + self.secondary.total_latency_seconds

    @property
    def total_calls(self):
        return self.primary.total_calls + self.secondary.total_calls

    @property
    def tripped(self):
        """Read-only, for tests and observability - not used to make any
        routing decision from outside chat() itself."""
        with self._lock:
            return self._tripped

    def chat(self, messages, tools=None, tool_choice="auto"):
        with self._lock:
            if self._tripped:
                self._calls_since_probe += 1
                probing = self._calls_since_probe >= CIRCUIT_HALF_OPEN_AFTER
            else:
                probing = False
            try_primary_first = (not self._tripped) or probing

        if try_primary_first:
            try:
                result = self.primary.chat(messages, tools=tools, tool_choice=tool_choice)
                with self._lock:
                    self._consecutive_failures = 0
                    self._tripped = False
                    self._calls_since_probe = 0
                return result
            except Exception as exc:
                # Python deletes the `as exc` binding at the end of this
                # except block, so it must be copied to a plain variable
                # here to survive into the secondary-call block below.
                primary_error = exc
                with self._lock:
                    self._consecutive_failures += 1
                    self._calls_since_probe = 0
                    if self._consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
                        self._tripped = True
                # Falls through to the secondary below rather than raising
                # here - a call that trips (or is below) the threshold
                # should still get served if the secondary can serve it.
        else:
            primary_error = None

        try:
            return self.secondary.chat(messages, tools=tools, tool_choice=tool_choice)
        except Exception as secondary_error:
            if primary_error is not None:
                raise RuntimeError(
                    f"Both LLM providers failed - primary: {primary_error}; secondary: {secondary_error}"
                ) from secondary_error
            raise
