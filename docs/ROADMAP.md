# Roadmap

Each stage has a concrete deliverable and a status. A stage isn't "done"
until its deliverable has been verified, not just written — see
`docs/DECISIONS.md` for how each verification was actually done.

## Stage 1 — Synthetic data ✅ DONE

**Deliverable:** `data/synthetic_generator.py` + generated
`gateway_transactions.json`, `bank_settlement.json`, `ground_truth.json`.

52 gateway transactions across 7 mismatch types, 4 standalone orphan bank
records, 65/35 dev/eval split. Verified: sample records inspected, mismatch
type distribution confirmed, reference-token linkage confirmed working
after a design fix (see error log below).

## Stage 2 — Deterministic matcher ✅ DONE

**Deliverable:** `agent/matcher.py` — resolves exact-arithmetic matches
with zero LLM calls.

Verified against ground truth: 37/52 records resolved, 0 incorrect matches.
Correctly routes only genuinely ambiguous records (FEE_DRIFT, a hard
GARBLED_REF case, ORPHAN_GATEWAY, DUPLICATE) onward.

## Stage 3 — LLM agent + verifier ✅ DONE, real-run validated on two providers

**Deliverable:** `agent/react_loop.py`, `tools.py`, `prompts.py`,
`llm_client.py`, `verifier.py`.

Structurally verified with a scripted fake client (52/52 accounted for, 0
incorrect) before any real API access existed — this part is
provider-agnostic.

**Real judgment quality confirmed independently on two different
providers/models**, matching exactly: **95% match rate, 0% false positive
rate** on the full corrected eval split, on both `moonshotai/kimi-k2-0905`
(OpenRouter) and `openai/gpt-oss-120b` (Groq, the active default).
`FEE_DRIFT` — the one mismatch category that's pure LLM reasoning, not
arithmetic — went 4/4 correct on the OpenRouter run. Two different models
landing on the identical number is good evidence this reflects genuine
reasoning quality on the task, not one provider's particular quirks.

**One open verification item**, found by an adversarial stress test, not
by real data: the verifier's `VERIFIER_SYSTEM_PROMPT` now asks the LLM to
distinguish "no reference found at all" (still plausibly acceptable) from
"a *different* order's reference is present" (a real collision, reject
even if the amount matches). The underlying production code fix is
verified in isolation; whether a *real* LLM correctly makes this specific
distinction under the updated prompt has not yet been confirmed with an
actual API call — the fake client can't replicate this kind of judgment
(a first attempt to make it try broke a different, correct case). Worth a
small targeted real run before the submission if time allows, though it
doesn't affect the confirmed 95% (real data never has amount collisions
in the first place).

## Stage 4 — Eval harness ✅ DONE

**Deliverable:** `eval/metrics.py`, `eval/run_batch.py`,
`eval/results.json`.

Scores strictly against the eval split, never dev. Fixed a real scoring gap
(ORPHAN_BANK records were silently excluded) on 2026-08-23 - see error log.
Final confirmed numbers: 20/20 eval records scored, 19/20 correct (the one
"incorrect" is the documented DUPLICATE honest-deferral case, not a real
error).

## Stage 5 — API layer ✅ DONE, real-run confirmed

**Deliverable:** `api/app.py` (FastAPI app), `api/jobs.py` (SQLite-backed
job store + background worker — originally in-memory, upgraded during
production hardening below; survives a server restart).

Async, job-based design rather than a single blocking endpoint — a
real-run reconciliation batch can take minutes and hit transient LLM
provider errors (this session hit 25 of them), so blocking a live demo on
one HTTP request would be a real risk, not a hypothetical one.

- `POST /runs` — starts a run (full batch, or a `sample_size`-limited demo
  sample), returns a `run_id` immediately without waiting for completion.
  Optionally accepts `merchant_id` (see Production Hardening below).
- `GET /runs/{id}/status` — poll current state and progress
- `GET /runs/{id}/stream` — Server-Sent Events, live progress as each
  record resolves
- `GET /runs/{id}/results` — full results once complete
- `GET /runs` — list all runs
- `GET /audit`, `POST /refunds/reconcile`, `POST /batches/reconcile`,
  `POST /fx/reconcile`, `POST`/`GET /merchants/{id}/config` — all added
  during production hardening, see below.

**Demo sample is deliberately biased, not a naive slice.** Only ~12/52
records in the shipped dataset ever reach the agent stage — a random or
first-N small sample could easily miss them and show nothing but instant,
uninteresting deterministic matches. `jobs.build_demo_sample()` runs the
(fast, no-LLM) deterministic stage on the full dataset first specifically
to guarantee the sample includes live agent reasoning, since that's the
actual point of a demo.

**One shared LLM client instance**, created lazily on first real use, not
per-request — this is what makes the sticky `max_tokens` learning (see
error log) actually pay off across the API's lifetime instead of being
thrown away. Since production hardening, this can be a `FallbackClient`
wrapping Groq and OpenRouter (see below) rather than a bare `GroqClient`.

Verified three ways: 12 tests using `FakeLLMClient` via dependency
injection (no real API key needed), plus an actual `uvicorn api.app:app`
server boot with real HTTP requests against `/docs` and `/runs` — not just
in-process `TestClient` simulation. Found and fixed 5 real bugs building
and real-testing this (see error log): two missing `sys.path` entries, a
missing `GROQ_API_KEY` surfacing as a raw stack trace instead of a clean
error, a progress counter that never incremented, and a missing schema
field that only surfaced several turns into a real run.

**Real-run confirmed, not just structurally tested:** a complete 52-record
batch run through `POST /runs` (empty body) landed at **95% match rate, 0%
false positive rate** — identical to the CLI-confirmed number, this time
reached entirely through the API's own job/thread/streaming code path.
This is the strongest evidence in the whole build that the API layer is a
genuine equivalent to the proven CLI pipeline, not a lookalike. **Since
re-confirmed a second time, after all production hardening below, against
live Groq traffic on a second machine** - same 95%/0%, see
`docs/DECISIONS.md`.

## Stage 6 — Frontend dashboard ✅ DONE, plus a real post-launch UI rework

**Deliverable:** React/Vite dashboard (`frontend/`) showing the batch run
live - match rate, exception list with reasons, mismatch-type breakdown.

**Full phased build plan, mapped to the exact real API surface (not
assumed - checked against the live `openapi()` schema): see
`docs/STAGE_6_FRONTEND_PLAN.md`.** Seven phases (scaffold → dashboard →
live-run streaming → results → audit trail → reconciliation tools panel
→ merchant config admin → polish), each with concrete screens,
components, and an explicit API mapping table. Phases 0-3 are the core
story and the minimum that makes the frontend genuinely additive; Phase
5 (the five reconciliation-tool forms) is the first thing to cut or
simplify if time runs short before Sept 5.

- ✅ **Phase 0 done**: React/Vite/TS scaffold, Tailwind, the full
  "ledger on a dark desk" design system, a typed API client
  (`frontend/src/api/`) covering every real endpoint - types captured
  from live schema/pipeline output, not guessed. Verified with a clean
  `tsc -b`, a clean production build, and a real backend + real dev
  server booted together with the `/health` proxy confirmed working
  end to end. See `docs/DECISIONS.md`.
- ✅ **Phase 1 done**: real dashboard (`GET /v1/runs`), auto-refreshing,
  with genuine loading/empty/error states. Pulled forward the auth
  context/Settings screen from the plan's cross-cutting section since
  the dashboard needs to work against an authenticated backend from
  the start. Verified with real seeded runs (a demo run and a full
  run - the two genuinely different shapes the formatting layer
  handles) flowing through the real dev server proxy, and the real
  `401`/`200` auth scenario confirmed against a real `API_KEYS`-enabled
  backend. See `docs/DECISIONS.md`.
- ✅ **Phase 2 done**: New Run form (freeform merchant ID - corrected
  the plan's own inaccurate "dropdown from /merchants" claim before
  building, since no such list endpoint exists) and the live-run view -
  the plan's centerpiece. Found and fixed a real backend gap while
  building it: the SSE stream never carried the agent's actual
  reasoning text, only status/reason - a small, purely additive fix to
  `agent/react_loop.py`'s progress event, core 37/3/12 + 7/5 baseline
  re-confirmed unchanged. Real end-to-end proof: a real `uvicorn`
  server + real Vite dev server, a real run created and streamed
  through the exact proxy path the frontend uses, confirming genuine
  reasoning text arrives live. See `docs/DECISIONS.md`.
- ✅ **Phase 3 done**: full filterable matched-records table,
  click-to-expand exceptions table with type-level descriptions plus
  the real backend reason text verbatim, and a confidence-tier
  breakdown - all reusing the existing design system rather than new
  UI craft (deliberate calibration: build quality matters, but the
  real differentiator is implementation logic). Grounded the
  honest-deferral story in real data - checked the actual `DUPLICATE`
  classification before writing any UI copy about it, rather than
  assuming. Found and fixed a real, serious leftover bug while
  re-reading this file: an earlier audit fix had left the OLD,
  superseded error-handling code path in place above the new one,
  making that fix dead code the whole time - caught by careful
  re-reading, not any automated check. See `docs/DECISIONS.md`.
- ✅ **Phase 4 done**: audit trail search by transaction ID and/or run
  ID, both independent optional filters matching the real backend.
  Found a real, latent-since-Phase-0 bug via the real deployment
  browser test: single-parameter searches (the most common real case)
  silently returned zero results, because `URLSearchParams` stringifies
  an `undefined` object value as the literal text `"undefined"` rather
  than omitting the key - the backend correctly filtered on that
  nonsense value and correctly found nothing. Fixed, verified via a
  real screenshot showing correctly-scoped real results. See
  `docs/DECISIONS.md`.
- ✅ **Intensive-but-efficient verification sweep** (targeted, not
  exhaustive - only the genuinely untested paths): a full run's real
  95% match rate visually confirmed in a browser for the first time,
  the Settings/API-key flow driven by a real browser for the first
  time, a real (not hypothetical) error state actually triggered and
  observed. No new application bugs found - a real, meaningful
  investigation instead surfaced and fixed genuine bugs in the test
  script's own process cleanup (an orphaned backend process from an
  earlier failed run caused a confusing failure that looked exactly
  like a CORS regression but wasn't). See `docs/DECISIONS.md`.
- ✅ **Phase 5 done**: five standalone reconciliation tool forms
  (refunds, batches, FX, marketplace, chargebacks), each pre-filled
  with real backend-verified example data, submitting to the real
  endpoints. Found and fixed a real navigation gap while wiring this
  in: `/audit` (Phase 4) and `/tools` were both fully built but had no
  visible link anywhere in the UI - added both to the header. Caught
  by the real browser test's own click-through path, which a
  goto-by-URL check could never have detected. See `docs/DECISIONS.md`.
- ✅ **Phase 6 done**: merchant config admin, designed around a real
  backend risk confirmed before writing any code - `POST
  /merchants/{id}/config` is a full replace, not a patch, so the form
  always loads current values first and always submits both fields
  together, never a partial request. Nav link and real browser-test
  coverage both added from the first commit this time, not discovered
  missing afterward. `ComingSoon.tsx` removed - every planned route is
  now real. See `docs/DECISIONS.md`.
- ✅ **Real deployment-level browser testing added, retroactive to
  Phases 0-2** - a real headless Chromium instance (Playwright) driving
  the frontend's actual production build served on a genuinely
  different origin than the backend, not a curl simulation. Found a
  real bug invisible to every prior check: a demo run's results summary
  rendered the literal string `"undefined"` for one field, caused by a
  real shape difference between demo/full run results that the
  TypeScript layer didn't account for. Fixed, then re-verified visually
  via a fresh screenshot. Also visually confirmed the bug-4
  state-leak fix (from the comprehensive audit) with a real two-run
  navigation test. New permanent `scripts/deployment_browser_test.py`.
  See `docs/DECISIONS.md`.

## Production Hardening (beyond the original submission scope)

After Stages 1-6, an honest "would Razorpay actually deploy this"
assessment surfaced real gaps in three tiers: hard blockers (no
persistence, no auth, no real audit trail), financial/operational risk
at scale (LLM provider dependency, no confidence-based escalation, no
refund/chargeback modeling, no N-way settlement batching), and
business-completeness gaps (multi-currency, marketplace/Route-style
multi-party settlement, merchant-specific configuration, real fee-
schedule calibration). Some of these aren't code-fixable at all
(CERT-In audits, PCI-DSS certification, RBI PA licensing, data
localization, and Razorpay's actual internal fee schedule all require
Razorpay's own organizational process or proprietary data, not code) -
named honestly rather than pretended away. Working through the
code-addressable ones one at a time, each verified against the complete
pre-existing test suite **unchanged** before moving to the next, so
hardening the system can't silently break what six rounds of prior
verification already confirmed.

**Tier 1 (hard blockers) — all code-addressable items done:**

- ✅ **Persistence** — SQLite-backed job store behind the exact same
  public interface the in-memory dict had. 129/129 pre-existing tests
  passed with zero test file changes; genuine cross-process persistence
  proven with two independent subprocesses, not just an in-memory
  simulation of it. See `docs/DECISIONS.md`, "Production-hardening
  phase begins: persistence."
- ✅ **Audit logging** — new immutable `audit_log` table (structurally
  append-only, no `UPDATE`/`DELETE` path exists for it), a new `GET
  /audit` endpoint queryable by `transaction_id` and/or `run_id`.
  130/130 pre-existing tests passed unchanged; 5 new tests prove
  completeness, detail correctness, cross-run queryability, full-run
  coverage, and the structural immutability guarantee; genuine
  cross-process durability proven the same way as persistence. See
  `docs/DECISIONS.md`, "Production hardening, item 2: audit logging."
- ✅ **Basic API authentication** — `X-API-Key` header, configurable via
  `API_KEYS`, disabled by default for zero-friction local dev, wired at
  the app level so it covers every route. 135/135 pre-existing tests
  unchanged; 7 new tests. See `docs/DECISIONS.md`, item 3.
- 📝 **Data localization** — not code-fixable; a hosting/infrastructure
  decision, not something a codebase change solves.

**Tier 2 (financial/operational risk at scale) — done:**

- ✅ **LLM provider fallback / circuit breaker** — `FallbackClient` wraps
  Groq (primary) and OpenRouter (secondary) behind the same `chat()`
  interface, tripping after consecutive real failures with automatic
  half-open recovery. Directly answers the repeated real rate-limit and
  credit-exhaustion failures hit during this project's own build.
  135/135 pre-existing unchanged; 11 new tests. See `docs/DECISIONS.md`,
  item 4.
- ✅ **Value-based escalation** — high-value transactions (configurable
  threshold) flagged `requires_human_review` as pure additive metadata,
  structurally incapable of changing a match decision since it runs
  strictly after both matching stages are final. 153/153 pre-existing
  unchanged; 7 new tests. See `docs/DECISIONS.md`, item 5.
- ✅ **Refund / partial-capture modeling** — genuinely separate
  reconciliation path (`POST /refunds/reconcile`) classifying full,
  partial, and over-refund scenarios. 160/160 pre-existing unchanged; 13
  new tests. See `docs/DECISIONS.md`, item 6.
- ✅ **N-way settlement batching** — previously the largest structural
  gap. Two-mechanism design (`agent/batch_settlement.py`): grouping by a
  stated `settlement_batch_id` (deterministic, realistic primary path)
  plus a bounded, capped subset-sum fallback for the no-batch-id case -
  explicitly refuses to search past a small pool size rather than
  pretending brute-force guessing solves the real "hundreds of
  transactions" scale. 173/173 pre-existing unchanged; 15 new tests. See
  `docs/DECISIONS.md`, Tier 3 item 1.
- ✅ **Confidence-based escalation gating** — a second, separate
  composable pass (`agent/confidence.py`) over value-based escalation's
  output. Recovers a real signal `verifier.py` already computed and
  `react_loop.py` was discarding (deterministic vs. LLM-judgment
  verifier acceptance) rather than touching the LLM's own prompt/schema
  — zero risk to the proven 95% number. Widens `requires_human_review`
  from 10 to 24 on the structural baseline (10 value-flagged → 24 once
  every judgment-dependent decision is also surfaced) — a real,
  intentional capability upgrade. 235/235 pre-existing unchanged; 11 new
  tests. See `docs/DECISIONS.md`.
- ✅ **Chargeback handling** — genuinely different two-phase mechanic
  from refund modeling: a chargeback (cardholder-bank-initiated dispute)
  debits the merchant provisionally the moment it's raised, before any
  outcome is known, only becoming final or reversed after a bank/network
  decision. `POST /chargebacks/reconcile`, grounded in Razorpay's real
  documented dispute lifecycle (Open/Under Review/Won/Lost/
  Pre-Arbitration/Arbitration). 246/246 pre-existing unchanged; 17 new
  tests. See `docs/DECISIONS.md` — **this closes out every named Tier 2
  gap.**

**Tier 3 (business-completeness) — fully complete on the code-addressable side:**

- ✅ **Merchant-specific configuration** — a merchant's settlement
  window and escalation threshold genuinely parameterize the real
  `matcher.py`/`escalation.py` logic (not a separate demo path), proven
  byte-for-byte identical to the old behavior when a `merchant_id` is
  omitted or unregistered, and proven to genuinely take effect when
  configured. Included a real SQLite schema migration, verified safe
  against a simulated old-schema database. 190/190 pre-existing
  unchanged; 12 new tests. See `docs/DECISIONS.md`, Tier 3 item 2.
- ✅ **Multi-currency / FX reconciliation** — works off a caller-supplied
  rate band, never a single exact rate (no live FX feed exists here, and
  pretending to know the precise applied rate would be false precision).
  Every result unconditionally flagged for human review, even a clean
  match. 202/202 pre-existing unchanged; 14 new tests. See
  `docs/DECISIONS.md`, Tier 3 item 3.
- ✅ **Marketplace/Route-style multi-party settlement** — grounded in
  Razorpay Route's actual documented mechanics (Linked Accounts,
  commission, Settlement On Hold, transfer reversal), the opposite
  direction from N-way batching (one payment splits into many transfers,
  not many transactions netted into one credit line). 220/220
  pre-existing unchanged; 15 new tests. See `docs/DECISIONS.md`, Tier 3
  item 4 — **this closes out every code-addressable Tier 3 item.**
- 📝 Real fee-schedule calibration against Razorpay's actual numbers —
  not code-fixable; the current `HIGH_VALUE_THRESHOLD`, FX markup
  default, and other illustrative constants are researched
  approximations, explicitly named as such throughout, not Razorpay's
  real internal data.

## Backend hardening pitfalls (beyond the three-tier assessment)

A further pass identifying gaps a real ops team or a sharp reviewer
would hit that weren't part of the original tiered assessment - request
handling, deployment reproducibility, observability. Same discipline:
each verified against the complete pre-existing suite, real end-to-end
checks, no exception to the "prove it" standard just because these are
smaller items.

- ✅ **Request body size limits** — none of the reconciliation endpoints
  had any protection against an oversized request body; a caller could
  tie up a worker parsing a multi-million-record payload before any
  in-code size check (like `batch_settlement.py`'s `pool_too_large`)
  ever ran. New app-level middleware, `MAX_REQUEST_BODY_BYTES`
  (default 2MB, configurable). Named scope limit: checks the declared
  `Content-Length`, not actual bytes received - a real deployment
  should also enforce this at the reverse-proxy layer as defense in
  depth. 263/263 pre-existing unchanged; 5 new tests. See
  `docs/DECISIONS.md`.
- ✅ **`/health` endpoint** — unauthenticated (required a small, exact-
  match allowlist in `api/auth.py`'s shared dependency, since FastAPI's
  app-level `dependencies=[...]` has no built-in per-route bypass);
  genuinely checks database connectivity, not just process liveness.
  Re-ran the full route-by-route auth audit since this touched the
  shared dependency every other route relies on — all 14 real routes
  confirmed correct, turned into a permanent test. Also found and fixed
  a real, pre-existing bug in `scripts/deep_fuzz_hardening.py`'s own
  recovery-window loop while re-running the stress sweep (unrelated to
  this item's changes) — verified fixed across 5 clean runs. 268/268
  pre-existing unchanged; 4 new tests plus 1 new comprehensive auth
  audit test. See `docs/DECISIONS.md`.
- ✅ **Startup warning when auth is silently disabled** — the
  disabled-by-default design (see `api/auth.py`) is a deliberate
  tradeoff for local dev, but shouldn't be invisible in a real
  deployment's logs. Prints to stderr once at process startup if
  `API_KEYS` is unset; silent if it's set. Genuinely subprocess-tested
  (the warning only fires once per process at import time). 276/276
  pre-existing unchanged; 2 new tests. See `docs/DECISIONS.md` — which
  also has a direct, computed "grand scheme" gradient-stability trace
  proving `match_rate` is invariant across every combination of
  optional annotation layers added this session, not just claimed.
- ✅ **Orphaned-job cleanup** — two parts: a cascading-failure crash
  (found via stress testing, see `docs/DECISIONS.md`) was fixed first;
  this item adds the active detection that fix didn't provide. New
  `_reap_stale_jobs()`, a lazy sweep on every `get_job()`/`list_jobs()`
  call (not a scheduler thread) — any job stuck `pending`/`running`
  past `STALE_JOB_TIMEOUT_SECONDS` (default 30 min) gets marked
  `failed` with a clear orphan message. Self-correcting if a
  legitimately-slow same-process job gets prematurely reaped: its real
  completion simply overwrites the reaper's status. 278/278
  pre-existing unchanged; 7 new tests. See `docs/DECISIONS.md`.
- ✅ **API versioning** — every real endpoint except `/health` mounted
  on a single `APIRouter`, included twice: unprefixed (backward
  compatible with all existing tests/scripts) and under `/v1` (the new
  canonical surface). Both work identically forever. 285/285
  pre-existing unchanged (one test's route-enumeration mechanism fixed
  to use FastAPI's stable `openapi()` schema instead of Starlette
  routing internals that don't flatten included-router routes — same
  property checked, just via a stable API); 9 new tests. See
  `docs/DECISIONS.md`.
- ✅ **Dockerfile for deployment reproducibility** — multi-layer build
  (deps cached separately from code), non-root user, real `HEALTHCHECK`
  against the new `GET /health` endpoint, `--host 0.0.0.0` (a common
  real Docker gotcha, gotten right the first time). No secrets baked in
  — every env var read at runtime. Named the `jobs.db`-lost-on-restart
  volume-mount consideration explicitly rather than as a silent
  surprise. Verified without Docker actually being available in the
  build sandbox: a real uvicorn server, bound to `0.0.0.0` exactly as
  the container would, booted from an isolated directory containing
  only the exact files the `Dockerfile`'s `COPY` directives copy —
  correctly served `/health`, enforced auth, and served both API
  surfaces. See `docs/DECISIONS.md`.

This closes out every backend hardening pitfall identified this
session — all six items done.

## Comprehensive audit finding: CORS (found after the six pitfalls, not part of the original list)

- ✅ **CORS configuration** — a real, severe gap found via a
  comprehensive line-by-line audit (explicitly requested, covering
  environment/integration constraints, requirements-to-deliverable
  alignment, and frontend+backend logic together for the first time),
  not part of the original six-item pitfalls list above. Zero CORS
  configuration existed at all - a real cross-origin preflight request
  came back as a bare `405` with no CORS headers, meaning every
  cross-origin request (exactly the scenario the frontend's
  `VITE_API_BASE_URL` config and the Docker "separate services" story
  already anticipate) would be silently blocked by any real browser.
  Only ever worked during Stage 6's build because the Vite dev proxy
  made every request same-origin, completely masking it. Fixed with
  `CORSMiddleware` + `CORS_ALLOWED_ORIGINS` (default `*`, configurable).
  A second bug found while fixing the first: initial middleware
  placement meant CORS headers were missing specifically on *rejected*
  responses (413, 401) - fixed once Starlette's real (LAST-registered-
  is-outermost) ordering was confirmed empirically, not assumed. A
  third, this time in the fix's own test file: a `del` on shared
  session-wide test state broke 5 unrelated tests, caught by re-running
  the full suite, not trusting the new file in isolation. 295/295
  pre-existing unchanged; 6 new tests. Same audit also found and fixed
  3 real frontend bugs (a state-leak-across-navigation bug, an error
  state that discarded partial live-run progress, and a storage
  exception that could mask a successful run creation as a failure) and
  correctly ruled out one suspected bug as a false alarm after careful
  tracing. See `docs/DECISIONS.md` for the complete narrative.

## Post-Stage-6 design-flaw fixes (self-critique, not a bug report)

After all 7 planned Stage 6 phases and multiple rounds of hardening,
Krishang asked for a genuinely critical "selection committee"
self-evaluation - not a bug hunt, an honest assessment of real product
and architecture weaknesses a sharp reviewer could raise. Fixing the
actionable ones directly, one at a time, before Phase 7.

- ✅ **Merchant config persistence** — a real engineering-rigor
  inconsistency, not a bug: `api/jobs.py`'s job store and audit log
  both got real SQLite persistence during Tier 1 hardening, but
  `agent/merchant_config.py` stayed an in-memory dict the whole time,
  silently losing every registered merchant on restart while
  everything else in the system survived one. Rewritten to mirror
  `api/jobs.py`'s own pattern exactly (`MERCHANT_CONFIG_DB_PATH`,
  `:memory:` for tests, same locking approach) - public API unchanged.
  Found and fixed a small leaky-abstraction bug along the way
  (`api/app.py` was reaching into another module's private `_registry`
  dict directly - added a proper `is_merchant_known()` function).
  Found and fixed a real gap in three stress scripts that didn't know
  about the new env var and were leaving a stray database file in the
  actual source directory. Found and fixed a real gap in the Docker
  volume-mount guidance, which only covered the job store's directory.
  Proven with two genuinely separate processes - one registers a
  merchant via the real API, a second independent process confirms it
  survived. 302/302 pre-existing unchanged, 304/304 full suite (twice,
  stable). See `docs/DECISIONS.md`.
- ✅ **Surfacing the honest-deferral moment** — the project's clearest
  real differentiator (the agent correctly declining to guess rather
  than force a match) was buried one click deep in a collapsed
  exceptions table. New `HonestyCallout` component, placed at the very
  top of the results view, framed broadly (every exception type is an
  honest refusal, not just the duplicate-settlement case) then naming
  the duplicate case specifically when present. Caught and fixed an
  overclaiming risk in the first draft before it shipped - a "wrong
  roughly half the time" statistic that only holds for exactly two
  candidates, not something the exception type actually guarantees.
  304/304 unaffected (frontend-only). Real screenshot confirms it
  renders first, with real numbers matching the real exception table
  below it. See `docs/DECISIONS.md`.
- ✅ **Cost/latency reporting** — no run previously reported how much
  LLM work it did or how long it took. The raw API response already
  includes real token usage - it was being silently discarded, same
  pattern as two earlier real finds this session. Deliberately no
  dollar-cost estimate (the real default provider is genuinely free
  for this project's usage, so a `$` figure would be uninformative,
  not dishonest, but still not the real signal). Solved a genuine
  design complication properly: the LLM client is a long-lived
  singleton shared across every run, so a per-run figure needed a
  before/after delta, not a naive running total. Found and fixed one
  real bug (an unconditional attribute read broke an unrelated test's
  deliberately-minimal client stub) and two self-inflicted instances of
  an already-known bug class (two new tests left a shared test
  override in place, breaking a different test file) - all caught by
  full-suite reruns, not trusted from isolated test passes. 304→310
  tests, three stable runs. Core matching numbers re-confirmed
  unchanged. Real screenshot confirms genuine data rendering. See
  `docs/DECISIONS.md`.
- ✅ **Cross-referencing standalone tools against exceptions (safe
  version of the breadth-vs-depth critique)** — connects the five
  disconnected standalone tools to the core reconciliation loop at the
  presentation layer only, deliberately never touching the core
  pipeline's matching algorithm or decision counts. Two of four
  exception types get a "Check against..." link, chosen for a real,
  defensible connection (`AMOUNT_MISMATCH_UNEXPLAINED` → Refunds,
  `NO_CANDIDATE_FOUND` → Batches) - the other two get no link rather
  than a contrived one. Caught and fixed a real structural bug before
  it shipped (nesting a `<Link>` inside the exceptions table's existing
  clickable `<button>` row - invalid HTML, would have conflicted with
  the row's own collapse toggle). A destination tool opened via
  cross-check starts with an honest empty amount field, not a
  misleading unrelated example value, with a clear banner stating this
  is a human's own hypothesis to test, never an automatic
  reclassification. Real browser test clicks all the way through, not
  just checks a link exists - confirmed via screenshot. 310/310
  backend completely untouched (frontend-only by design). **This
  closes out all four design flaws from the self-critique.** See
  `docs/DECISIONS.md`.
- ✅ **Phase 7 done: a real, systemic design polish pass** — grounded
  in a genuine screenshot audit before any code changed, not
  guesswork. Found and fixed the single biggest issue (every page's
  content confined to the top-left with a huge empty void below - a
  subtle background texture + vignette + footer now gives that space
  real intentionality), a missing active-nav indicator, flat
  typography, unrefined buttons (with one real inconsistency found and
  fixed along the way), and a completely off-brand leftover favicon.
  Every fix extracted into a single reusable CSS class applied
  everywhere at once, not scattered per-page tweaks - the actual
  distinction between "considered" and "vibe-coded" execution. 310/310
  backend untouched; the full real browser test's all 12 interaction
  checks re-confirmed passing twice after the visual changes. See
  `docs/DECISIONS.md`.
- ✅ **Live Groq + real Docker confirmed (2026-08-29)** — Krishang
  deployed the complete, fully-built system on his own machine. Real
  full run against real Groq traffic through the actual UI landed on
  the identical 95%/0% match rate, with real rate-limiting handled
  correctly by the existing retry/backoff logic. Real `docker build` +
  `docker run` on Docker Desktop: healthcheck genuinely reported
  `(healthy)`, and a merchant registered before a real `docker restart`
  was still there afterward - proving the two-volume persistence fix
  actually works against a real container restart, not just simulated
  file I/O. Both the last two items that could only ever be
  approximated from within the sandbox that built this project are now
  confirmed for real. See `docs/DECISIONS.md`.
- ✅ **A real UI rework, at Krishang's explicit request (2026-08-30)**
  — footer removed, IBM Plex Serif for headings (a direct answer to an
  open design question, not a guess), a global font-size increase done
  systemically (one root change, not file-by-file), real progress bars
  using data the backend had always streamed but the frontend never
  displayed, a hand-rolled SVG donut chart (deliberately no new
  charting-library dependency this close to the deadline), a
  persistent sidebar showing recent/active runs from any page, and a
  global notification system for run completions. The notification
  system's correctness required a real design fix (an empty-baseline
  simplification found while making the detection rule independently
  testable) and a new deterministic test
  (`scripts/verify_run_transitions.mjs`) proving that rule correct with
  zero timing dependency, specifically because a real timed E2E test
  can't reliably observe it against the near-instant fake client used
  throughout this project's own testing. 310/310 backend untouched;
  the real browser test's now-13-step suite re-run three times, all
  clean. See `docs/DECISIONS.md`.

**Deliverable:** Finalized `README.md`, an architecture diagram, and a
clean run-through of `docs/DECISIONS.md` for submission. Numbers should
reflect a final real run, not an intermediate one.

---

## Error log

Quick-reference table of every real error hit and how it was actually
resolved. Full narrative and reasoning for each is in `docs/DECISIONS.md` -
this table is for scanning, not for the full story.

| # | Error | Root cause | Fix | Verified how |
|---|-------|-----------|-----|---------------|
| 1 | `GARBLED_REF` behaved identically to `CLEAN` | No shared reference field existed between gateway and bank records at all | Added a shared reference token (from `order_id`, echoed in bank narration) as the primary match key | Regenerated data, inspected CLEAN vs GARBLED_REF narration directly |
| 2 | Groq `404` on first real run | `llama-3.3-70b-versatile` deprecated by Groq since original design | Switched default model to `openai/gpt-oss-120b` | Confirmed via Groq's live docs |
| 3 | Groq `429` on agent stage | Free-tier rate limits, expected given per-record tool-calling volume | Added retry-with-backoff to the LLM client | Real run cleared the limit and continued |
| 4 | Groq `400 tool_use_failed` ("commentary" tool) | `gpt-oss`'s harmony chat format leaks its internal reasoning channel as a bogus tool call - documented issue across multiple inference stacks | Added defensive handling for unrecognized tool names in `react_loop.py` | Unit test + fake-client end-to-end test |
| 5 | Same `commentary` error persisted after fix #4 | Groq rejects the call server-side with an HTTP 400 *before* our code ever sees a normal response - fix #4 was one layer too late | Added `_recover_invalid_tool_call()` to parse the attempted call out of the 400 error body and feed it through the same existing handling | Unit test against the exact real error body + end-to-end scripted-client test |
| 6 | `kimi-k2-instruct-0905` 404 on Groq | Not every model in Groq's docs/third-party listings is accessible on every account/tier | Reverted to `gpt-oss-120b` (confirmed accessible) rather than guessing at another model name; added `agent/list_models.py` to check real account access directly | Ran the diagnostic reasoning, not guesswork |
| 7 | DNS resolution failure (`getaddrinfo failed`) | Local network issue, unrelated to any provider | Identified as network-layer, not API-layer; no code change needed | Explained the distinction, no fix required |
| 8 | Stale `kimi-k2` error reappeared after the revert | Old extracted zip folder was still being run - Windows auto-suffixes repeat downloads (`(1)`, `(2)`) | Had Krishang delete all old extracted folders and re-extract fresh | Confirmed by having him check the actual `MODEL` line on disk |
| 9 | Persistent `429`s after migrating to OpenRouter | Genuinely ambiguous from the error alone whether this was OpenRouter's own limit, an upstream provider limit, or a credits issue | Changed the retry handler to print the actual error body instead of a generic message | Real run confirmed it was ordinary transient rate limiting - completed successfully after ~26 retries |
| 10 | `OpenRouterClient.chat()` silently truncated mid-edit | A `str_replace` edit cut off the success path and final raise | Caught before shipping by asserting specific code paths existed in source, not just checking the file imported | `inspect.getsource()` assertions + full regression re-run |
| 11 | `ORPHAN_BANK` eval records silently unscored | `compute_metrics` only iterated ground-truth records with a `transaction_id`; orphan-bank records don't have one | Rewrote `metrics.py` to score orphan-bank records by checking whether their UTR was ever wrongly claimed by a match | Confirmed `eval_set_size` went from 18→20 and `by_mismatch_type` totals now sum to the full eval split |
| 12 | OpenRouter `402` — "requires more credits" | Neither client set `max_tokens`, so both defaulted to the model's max completion length (65,536) - reserving far more than any real response (a tool call, a short JSON verdict) ever needs | Added an explicit `MAX_TOKENS = 2048` cap to both clients | Source inspection confirmed both payloads updated and neither success path broke; full regression re-run |
| 13 | Match rate dropped 94%→75% after fix #12 | Looked like an LLM judgment regression, wasn't: `verifier.py` used bare `json.loads()`, silently rejecting every correctly-reasoned proposal that came back wrapped in markdown fences or preamble text as a parse error | Added a proper `_extract_json()` with progressive fallback (bare → fenced → regex); parse failures now include the raw content in the reason instead of being opaque | Unit-tested the extractor against 8 realistic response shapes; full regression re-run |
| 14 | Match rate improved but still below baseline (85% vs 94-95%) | Not a parsing issue this time — verifier was genuinely rejecting, but self-contradicting its own stated rule: rejected Rs 19-38 shortfalls as exceeding "a few tens of rupees," when they plainly are. Vague natural-language threshold, inconsistently applied | Replaced with a concrete Rs 50 threshold grounded in real NEFT/RTGS fee slabs (not reverse-engineered from the synthetic data); pre-computed the shortfall and passed it directly in the verifier payload | Confirmed the prompt no longer contains the vague phrase and does contain the concrete rule; full regression re-run. Real-run confirmation pending |
| 15 | Data generator claimed "deterministic" but wasn't fully | `transaction_id`, `order_id`, `customer_ref`, and the orphan-bank junk ref all used `uuid.uuid4()`, which `random.seed()` never controls, unlike every other field | Replaced with a `random.choices()`-based hex generator, seeded like everything else | New `tests/test_data_generator.py::test_deterministic_across_runs` — found on its first run |
| 16 | Fake-client test suite got a false positive on an ORPHAN_GATEWAY case | The fake verifier stub unconditionally returned "accept," a stale assumption from before the real verifier's threshold logic was tightened | Fake client now applies the same Rs 50 threshold the real verifier prompt uses, instead of a blind stub | Full 35-test suite passes consistently across repeated runs |
| 17 | *(not yet triggered — found proactively)* Malformed tool-call JSON would crash the whole batch | `json.loads()` on tool-call arguments had no error handling at all — bigger blast radius than error #4/#5 since it kills the entire run, not one record | Wrapped in try/except; malformed JSON now nudges the model and retries within budget | New test: malformed JSON recovers and resolves within 2 calls |
| 18 | *(not yet triggered)* Missing required tool-call fields (`utrs`, `exception_type`) would `KeyError` | Fields accessed directly despite being schema-required — LLMs occasionally violate their own declared schema | Added explicit presence checks before access, same nudge-and-retry pattern | New test: missing field recovers and resolves within 2 calls |
| 19 | *(not yet triggered)* Non-JSON or malformed API responses would crash with an opaque parser exception | Neither client guarded `response.json()` on a 200 status — a proxy error page or empty `choices` array had no clear failure mode | Both clients now raise a clear `RuntimeError` with the raw body included, instead of an uncaught exception | Code review + full regression; genuinely hard to trigger without mocking the HTTP layer, so verified by inspection rather than a live test |
| 20 | `"still rate-limited after N retries"` message was dead code | On the final retry attempt, `attempt < MAX_RETRIES` is false and the loop fell through to a generic `not response.ok` raise a few lines earlier — the specific, clearer message could never actually fire | Restructured the 429 branch to raise the specific message directly on the final attempt, in both clients | New `tests/test_llm_client.py` with a mocked `requests.post` — found on the suite's very first run |
| 21 | A second real `402` — `in_flight_budget_exhausted`, with a `Retry-After: 120` header — failed with zero retries | Retry logic only ever handled `429`; a transient, explicitly-retryable `402` (distinct from the hard credit-exhaustion `402` in error #12) had no retry path at all | Retry a `402` specifically when it includes a `Retry-After` header (the API's own signal it's transient); still fail immediately without one | Two new mocked-HTTP tests: one confirms the retryable case recovers, one confirms the non-retryable case still fails on the first attempt |
| 22 | A third real `402` — a small, precisely-specified credit shortfall (wanted 4096 tokens, could afford 3613) — correctly not retried, but shouldn't have failed at all | The error message states exactly how much is affordable; a 483-token gap needs a smaller request, not more account credit | Parse "can only afford N" from the error and retry with `max_tokens` reduced to N (with a `MIN_VIABLE_TOKENS = 512` floor below which it's not worth retrying) | New test captures the actual `max_tokens` sent per attempt and confirms it shrinks; a second test confirms a too-small affordable amount still fails immediately |
| 23 | Same "can only afford N" value repeated a dozen times in a row on a real run — looked like pure balance depletion, wasn't fully that | `max_tokens` was a local variable reset to the full default on every `chat()` call; a batch run calls it dozens of times, so every call wastefully retried the full amount first before rediscovering the same lower ceiling | Made `max_tokens` sticky on the client instance instead of a per-call local — learned once, reused on every later call | New test calling `chat()` twice on one instance, confirming the second call starts at the learned value instead of resetting |
| 24 | OpenRouter account ran out of usable credit (no funds available to top up) | Three `402` variants in one session made OpenRouter untenable without spending money | Migrated the default client back to `GroqClient` — genuinely free, already fully built with every fix from this session | Wrote 9 new tests giving `GroqClient` the same coverage `OpenRouterClient` had before switching; confirmed `run_batch.py` wires to it correctly as a real script run |
| 25 | `llama-3.3-70b-versatile` — console showed it as accessible, real API call said "no access" | Console visibility and actual API access on a given key aren't the same thing | Reverted to `openai/gpt-oss-120b` | A real run on it independently confirmed the same 95% match rate OpenRouter's kimi-k2-0905 gave — the revert didn't just avoid a dead end, it produced the cross-provider confirmation the submission needed |
| 26 | *(caught before shipping)* `api/app.py` and `api/jobs.py` both failed to import on first run (`ModuleNotFoundError: jobs`, then `metrics`) | New modules, each needing a different subset of the project's flat-import path setup — `api/`'s own directory, `agent/`, and `eval/` — none of which were added yet | Added the missing `sys.path` entries in both files, and added `api` to `tests/conftest.py`'s shared path setup rather than a fragile per-test `sys.path` hack | Actually ran `TestClient` construction, not just checked for syntax errors — failed twice before it imported cleanly |
| 27 | *(caught before shipping)* A missing `GROQ_API_KEY` surfaced as a raw, multi-frame stack trace and a generic 500 | No handling for the `RuntimeError` `GroqClient.__init__` raises — this is a near-certain first mistake for anyone setting the API up | Added a FastAPI exception handler converting it to a clean `503` with an actionable message | Reproduced the raw traceback first to confirm the problem was real, then a regression test confirming the clean `503` |
| 28 | Real curl output showed every deterministic-stage SSE event reporting `"current": 7, "total": 7"` instead of counting up | `for m in det_matched: ... "current": len(det_matched)` never used `enumerate()` — constant, not a running index. 71 passing unit tests never caught it, since none checked the *shape* of the progress sequence | Fixed with `enumerate()` | New test asserts the sequence increments cleanly (1,2,3...); deliberately reverted the fix first to confirm the test actually fails against the old bug before trusting it passes against the new code |
| 29 | Real full-batch API run failed 4 records into the agent stage: `400`, `'tool_calls.0.type' : property 'type' is missing` | The `commentary`-leak recovery mechanism's reconstructed `tool_calls` object was missing Groq's required `"type": "function"` field — invisible until a *later* call replayed the fuller history and re-validated it | Added the missing field to both the real recovery code and the fake client, which shared the identical gap | Deliberately reverted the fix to confirm the new test fails against the old code first; also fixed the SSE stream's terminal event to include the error message directly instead of requiring a second `/status` call to learn why a run failed |
| 30 | *(found by re-reading the code, not a real-run failure)* Deterministic precheck rejected purely on a 15% threshold, while the LLM verifier's own rule is a flat Rs 50 — inconsistent bases could hard-reject a small-transaction gap the LLM's own rule would accept | A gap over 15% but under Rs 50 (e.g. Rs 35 on a Rs 150 transaction) never reached the nuanced rule that would have accepted it | Reject now requires the gap to exceed BOTH the percentage AND the absolute threshold | Deliberately reverted the fix to confirm the new test fails against the old code, exactly as predicted, before restoring it |
| 31 | *(found by re-reading the code)* `build_demo_sample()`'s "at least 2 agent-routed records" floor could return more records than requested for `sample_size` 1 or 0 | `max(sample_size // 3, 2)` had no upper cap tied to `sample_size` itself — never triggered since every test/run used `sample_size: 10` | Capped `agent_want` by `sample_size` too; added `ge=1` API validation so 0/negative requests get a clean `422` | Deliberately reverted the fix to confirm the new test fails against the old code (`sample_size=0` returned 2 records) before restoring it |
| 32 | *(full adversarial review)* `tools.py`'s search returned candidates above `net_amount`, which are never acceptable anywhere else in the system | Symmetric `net*(1±tolerance)` band instead of a one-sided cap | Capped the upper bound at `net_amount` itself | Code review — a settled amount above net always fails the deterministic precheck and the verifier prompt's own rule |
| 33 | *(full adversarial review)* `matcher.py` silently picked the first valid split-pair if more than one existed | Nested loop returned on the first match instead of checking for ambiguity | Collect all valid pairs first; more than one → `AMBIGUOUS_MULTIPLE_CANDIDATES` | Reverted to confirm the new test fails against the old silent-pick behavior first |
| 34 | *(full adversarial review)* `metrics.py` had no protection against a duplicate `transaction_id` silently corrupting the reported match rate | Dict-comprehension pattern would silently keep only the last occurrence with no error | Added an explicit duplicate check that raises instead of computing a wrong number quietly | Reverted to confirm the new test fails (proceeds silently) against the old code first |
| 35 | *(full adversarial review)* A full run's deterministic-stage `DUPLICATE` exceptions never appeared in the live SSE stream, only matches did | The streaming loop only ever iterated `det_matched` | Stream `det_exceptions` too; fixed the progress `total` to reflect the true deterministic-stage record count | Reverted to confirm the new test finds zero exception events against the old code first |
| 36 | *(full adversarial review)* A hallucinated (non-existent) proposed UTR was rejected correctly, but only by an arithmetic coincidence with a confusing message | `sum([])=0` happens to produce a 100% gap that clears both rejection thresholds for this dataset's transaction sizes — not guaranteed in general | Added an explicit, honest check for an empty proposed-records list | New test confirms the clear message and that no LLM call is wasted on it |
| 37 | *(full adversarial review)* Benign race in lazy `GroqClient` creation; an unlocked SSE event read | Two concurrent first requests could each construct a client; the SSE stream reads an append-only list without the writer's lock | Added a lock for the former; documented why the latter is safe under CPython's GIL for this exact access pattern, rather than leaving it unexplained | Full suite + real `uvicorn` boot re-confirmed clean after both changes |
| 38 | *(adversarial stress test)* Verifier auto-accepted an exact amount+date match from a completely unrelated order — zero check the narration actually referenced this transaction | Two unrelated transactions sharing an identical settled amount by coincidence (plausible with round-number payments) would sail through with full confidence | `deterministic_precheck` now requires reference-token corroboration before auto-accepting; the LLM prompt updated too, since escalating to an LLM that was never told to check for this would accomplish nothing | Isolated unit test calling `deterministic_precheck` directly (not through the fake client), verified via revert-test-restore; real-dataset regression re-confirmed unchanged (95%, 0 incorrect) |
| 39 | *(same investigation)* Fake client's attempt to mirror the fix above broke a real, correct case | A `GARBLED_REF` record's reference is *intentionally* unrecoverable — "no reference found" is its correct, expected state, not a collision signal the stub could distinguish from a real one | Reverted the fake client to shortfall-only; the real protection lives in the isolated, independently-verified production code, not the stub | Caught by rerunning the real-dataset regression immediately after the change — match rate dropped 95%→90%, not assumed safe |
| 40 | *(noisy stress test)* A duplicate `transaction_id` in the input flowed silently through the entire deterministic stage — both copies matched independently — before `metrics.py`'s guard caught it, and only after the agent stage (real API budget) would already have run | No input validation existed before matching began; the metrics guard fires too late to save any wasted cost | Added `matcher.validate_input()`, called at the start of `run_deterministic_stage()` — fails fast, before any LLM calls | Verified directly against real noisy data, both before (duplicate silently reached `matched`) and after (clean `ValueError`) the fix; confirmed the curated dataset's 37/3/12 split is unaffected |
| 41 | *(concurrency stress test)* `test_shared_client_survives_concurrent_calls_no_crash` failed intermittently (~7% of runs) with a hard `402 (add credits)` error | Credit-shortfall retries (free, always-recoverable, genuine progress each time) shared `MAX_RETRIES=5` with wait-based retries (429s, transient 402s) — under real concurrent load, several legitimate shrinking-credit responses in a row could exhaust the shared budget before a real attempt | Gave credit-shortfall retries their own independent, larger budget (`MAX_CREDIT_SHORTFALL_RETRIES=20`); restructured the retry loop with two separate counters | Reverted to the old shared-counter logic to confirm the new test genuinely fails against it (reproduced the exact same `402 add credits` error); restored and confirmed 50/50 repeated runs of the original flaky test now pass, versus ~7% failing before |
| 42 | *(production-readiness sweep)* My own date-arithmetic test case had a wrong expected value (Feb 28→Mar 1 in a non-leap year is a 1-day gap; I'd written `expected=False`) | A construction mistake in the test, not the code — `matcher.py`'s actual behavior was already correct | Fixed the test's expected value | Confirmed by the test's own comment already correctly saying "1-day gap" — the code and the reasoning agreed, only the assertion was wrong |
| 43 | *(real-world settlement simulation)* `DATE_WINDOW_DAYS=3` — a fixed calendar-day check — wrongly routed 217 real-pattern transactions (10.85% of volume, ₹29.6 lakh) to costly agent escalation | Real Indian bank settlement runs on business days, not calendar days — a Thursday/Friday transaction with normal T+2 settlement crosses a weekend, landing 4 calendar days out, which the fixed window rejected even though nothing was wrong | Widened `DATE_WINDOW_DAYS` (and `tools.py`'s matching default) from 3 to 7 — grounded in real settlement math (T+2 + weekend + one holiday can reach 5 calendar days), strictly permissive so it can't reject anything the old window accepted | Precise before/after simulation (217 → 0 wrongly-flagged, measured against the actual routing reason, not co-occurrence); curated dataset's 37/3/12 split confirmed completely unchanged; full suite re-run clean |

## Production-readiness sweep summary

Four separate sweeps beyond the standard suite, each targeting variance
the fixed-example tests couldn't reveal: **25 different random seeds**
for both generator styles (25/25 clean, zero errors — 8 folded into the
permanent suite); **12 date-arithmetic edge cases** (leap years, year
boundaries, month rollovers — 12/12 clean after fixing my own test
mistake); **heavier concurrency** (20 threads × 30 calls, 50% injected
failure rate — 1,236 calls, zero errors) plus **50 sequential API
requests** with no measurable degradation; and a **30x repeated full-
suite regression sweep** (30/30 clean, no flakiness anywhere). One real
bug found and fixed (error #41 above); everything else came back
consistently clean — which is itself the evidence this sweep was for.
See `scripts/README.md` for the heavier pre-submission scripts.

## Real-world settlement simulation

A genuinely different class of finding from everything above — not a
code-logic bug (the code always did exactly what it was written to do),
but a **domain-realism gap**: the fixed 3-calendar-day match window
didn't reflect how real Indian bank settlement actually works (business
days, not calendar days). Simulated 2000 realistic transactions across
45 days with a real UPI-heavy payment mix, long-tail amounts, and
business-day-aware settlement against a real Indian holiday calendar.
Found 217 transactions (10.85%, ₹29.6 lakh) wrongly escalated for no
reason other than this gap — fixed by widening the window to 7,
confirmed via precise before/after measurement (217 → 0) and full
regression. See `scripts/realworld_simulation.py`.

## Running the tests

```
pip install -r requirements.txt
python -m pytest tests/
```

310 tests across data generation, the deterministic matcher, verifier
parsing, threshold calibration and cross-tier consistency, metrics
scoring, full pipeline mechanics (including the commentary-leak recovery
path, its required schema fields, and three proactively-hardened failure
modes), mocked HTTP-layer tests for **both** LLM clients with equivalent
coverage (including independent retry budgets for credit-shortfall vs
wait-based failures, and the `FallbackClient` circuit breaker), the
FastAPI layer (job creation, status polling, SSE streaming with
progress-shape and error-visibility regression guards, demo-sample
biasing with a never-exceeds-requested-size guard, auth, and the refund,
batch-settlement, and FX endpoints), a dedicated noisy stress-test suite,
a dedicated concurrency stress-test suite, date-arithmetic edge cases,
and a multi-seed fuzz sample covering real multi-threaded access to the
shared LLM client and the API's job store (`tests/test_noisy_stress.py`,
using `data/noisy_stress_generator.py` - deliberately messy, ~10x-volume
data distinct from the curated submission dataset) covering fail-fast
input validation, cross-stage double-claim safety, reference-collision
safety, and the settled-above-net invariant at scale, genuine
cross-process persistence and audit-log tests (real subprocesses, not
simulated, including a real SQLite schema-migration test), and dedicated
suites for value-based escalation, refund reconciliation, N-way
settlement batch reconciliation, merchant-specific configuration, and
multi-currency FX reconciliation. Uses the fake LLM client for
pipeline and API tests — confirms the code's logic never regresses, not
real LLM judgment quality, which still needs a real provider key to check
(see Stage 3 above - **independently re-confirmed via a real Groq run on
a second machine**, see `docs/DECISIONS.md`). On Windows, use
`python -m pytest tests\` if the bare `pytest` command isn't found (a
PATH issue, not a project issue).
