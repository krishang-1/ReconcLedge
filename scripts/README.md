# Deep fuzz scripts

Heavier, slower stress tests kept separate from `tests/` (which stays
fast for everyday iteration). Run these manually before a final
submission, not on every `pytest` invocation.

**Run `deployment_browser_test.py` especially, not just "if there's
time."** Found via a deep verification pass (see `docs/DECISIONS.md`):
every other script here checks the backend, or the frontend's
compiled/curl-level correctness - genuinely valuable, but neither
catches what only shows up when something actually clicks through the
real, rendered app. This script has found a real, previously invisible
bug in 4 of the last 5 phases it was extended into (a field rendering
the literal text `"undefined"`, a search that silently returned zero
results, two fully-built pages with no way to reach them) - none of
which any other check in this project would have caught. It is not
optional scaffolding; it is the highest-signal script in this
directory for anything touching the frontend.

## `deep_fuzz_seeds.py`

25 different random seeds for both the curated-style and noisy-style data
generators (versus the 8 seeds folded into `tests/test_multiseed_fuzz.py`
for speed). Confirms determinism, zero incorrect matches, full accounting,
and no double-claims hold across many different randomly-generated
datasets, not just the one seed the actual submission dataset uses.

```
python scripts/deep_fuzz_seeds.py
```

## `deep_fuzz_concurrency.py`

20 threads × 30 calls each against the shared LLM client with a 50%
injected credit-shortfall rate (versus 8×20 threads at 30% in
`tests/test_concurrency.py`) - pushes harder on the retry-budget fix
found via concurrency stress testing (see `docs/DECISIONS.md`). Also runs
50 sequential API requests checking for job-store growth issues or
response-time degradation.

```
python scripts/deep_fuzz_concurrency.py
```

## `realworld_simulation.py`

A different kind of test from everything else in this project - not
synthetic edge cases chosen to test code correctness, but a simulation
grounded in how Indian payment settlement actually works in production:
a realistic UPI-heavy payment-method mix, long-tail transaction amounts,
and settlement computed on real business days (skipping weekends and a
representative Indian holiday calendar), not a flat random calendar-day
offset. Found and led to fixing a real domain-realism gap - see
`docs/DECISIONS.md`, "Real-world settlement simulation" - where a fixed
calendar-day match window wrongly escalated ~11% of realistic
transaction volume to costly agent-level review, purely because it
couldn't recognize normal weekend-crossing settlement.

```
python scripts/realworld_simulation.py
```

Prints a before/after-style report and writes
`scripts/realworld_simulation_results.json`. Worth rerunning after any
change to `matcher.py`'s date-window logic or `data/synthetic_generator.py`'s
settlement-timing assumptions, to confirm the fix still holds.

## `deep_fuzz_hardening.py`

A different target from the three scripts above: not the core matching
pipeline, but everything added during production hardening (persistence,
audit logging, API auth, LLM provider fallback) - and specifically what
only shows up when they're all live *together* under real threading,
which none of their individual test suites exercise on their own (same
category of gap concurrency stress testing already found once, in the
retry-budget race - see `docs/DECISIONS.md`).

Three sections: (A) `FallbackClient` under real multi-threaded contention
with randomized primary/secondary failure injection - its own test suite
is entirely sequential/scripted; (B) the full API stack (auth + real
file-backed persistence + audit + fallback wiring) under 20 concurrent
requests with a deliberate mix of valid and invalid API keys, checking
auth holds correctly under the same kind of load that already broke
something once, and that the audit trail exactly matches results with
zero duplication or cross-run leakage; (C) noisy high-volume stress data
run through the real pipeline with a primary LLM client that fails
outright for a stretch of calls then recovers, simulating an actual
mid-run provider outage rather than a static failure rate.

```
python scripts/deep_fuzz_hardening.py
```

Cleans up its own scratch database (`scripts/_deep_fuzz_hardening_jobs.db`)
after every run, win or lose - also covered defensively in `.gitignore`
either way.

## `deep_fuzz_reconciliation_endpoints.py`

Targets a different gap from every script above: the five standalone
reconciliation endpoints (refunds, batches, FX, marketplace,
chargebacks) added during Tier 2/3 hardening, each of which had only
ever been load-tested individually before this. Three sections: (A)
mixed concurrent load across all five endpoints plus `/runs` and
`/health` at once, from 20 threads; (B) malformed/noisy input fuzzing
directly against the six raw reconciliation functions - NaN,
±infinity, extreme magnitudes, unicode, SQL-injection-shaped strings,
null bytes - checking for unexpected crashes, not "correct"
classification (garbage input has no correct answer); (C) a specific
check that NaN inputs never get silently misclassified as a clean
match anywhere, given Python's unusual NaN comparison semantics.

Found a real bug the first time it ran (see `docs/DECISIONS.md`): a
background-thread failure-handling gap in `api/jobs.py` where a
secondary database failure while recording an original failure could
crash the thread uncaught, leaving a job orphaned in `"running"`
forever. Fixed, and this script's own premature-cleanup bug (racing
with still-in-flight background threads) fixed alongside it.

```
python scripts/deep_fuzz_reconciliation_endpoints.py
```

## `deployment_browser_test.py`

Different in kind from every script above: a real deployment-level
test using an actual headless Chromium instance (via Playwright), not
a curl simulation. Boots its own backend and its own static file
server for the frontend's real production build (`vite build`), served
on a genuinely different origin than the backend - exactly the
deployment scenario the CORS fix (see `docs/DECISIONS.md`) exists for,
and the only way to truly validate CORS at all, since `curl` never
enforces it. Drives the browser through real user interactions
(clicking "New run", filling the form, submitting, watching the live
feed populate, navigating between two different runs) and asserts on
the actual rendered page content - this is what caught a real bug
invisible to every prior compile-time and curl-level check: a demo
run's results summary rendered the literal string `"undefined"` for
one field, because the type layer didn't account for a real shape
difference between demo and full run results. See `docs/DECISIONS.md`
for the full narrative.

Requires Playwright and a downloaded Chromium build - deliberately
NOT added to the core `requirements.txt`, since forcing every
contributor to download a ~300MB browser just to run the app or the
pytest suite would be a heavy, unjustified default for what is
genuinely an optional, occasional verification tool - same reasoning
this file already applies to every other script here.

```
pip install playwright --break-system-packages
playwright install chromium
python scripts/deployment_browser_test.py
```

## `verify_run_transitions.mjs`

Deterministic, timing-independent verification of the frontend's
run-completion notification logic (`frontend/src/lib/runTransitions.ts`).
Exists specifically because `deployment_browser_test.py`'s own step 13
can't reliably PROVE the notification fires through a real, timed E2E
test - a run can resolve well under one 5-second poll interval against
the near-instant fake client used everywhere else in this project's
own testing, the same inherent limitation documented there for the
`LiveFeed` "watching for the next record" indicator. This checks the
actual rule directly against hand-built before/after run lists, with
no timing involved at all - a run genuinely transitioning is reported,
an already-finished run seen for the first time is not, a run that
was already finished on the last poll doesn't re-fire every poll
after. Requires no dependencies beyond Node itself:

```
node --experimental-strip-types scripts/verify_run_transitions.mjs
```

All seven scripts exit non-zero on any failure and print a clear
PASS/FAIL summary either way.
