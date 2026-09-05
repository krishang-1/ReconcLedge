# Razorpay Buildathon — AI Finance Controller

An AI agent that reconciles payment gateway transactions against bank
settlements: matches deterministically wherever the arithmetic is exact,
reasons with an LLM where it isn't, independently verifies its own
proposed matches before committing them, and reports an honest exception
list for anything it genuinely can't resolve — rather than guessing.

**95% match rate, 0% false positive rate** — confirmed on two independent
LLM providers, then again against live Groq traffic through the complete,
fully-built system (not just the pipeline). Full detail below.

📹 **Demo video:** _[add link here]_ &nbsp;·&nbsp; 🚀 **Quick start:** see [Setup](#setup) below

---

## Status

Data layer, deterministic matcher, LLM agent stage, verifier, and eval
harness are built and **confirmed independently on two different
providers/models**: **95% match rate, 0% false positive rate** on the
full, corrected 20-record held-out eval split (drawn from a 52-record
synthetic batch total) — matched exactly on both
`moonshotai/kimi-k2-0905` (OpenRouter) and `openai/gpt-oss-120b` (Groq),
**re-confirmed a third time against live Groq traffic after all
production-hardening work below**, and **confirmed a fourth time on
2026-08-29 through the complete, fully-built system** — real frontend,
real live SSE streaming, real cost/latency reporting, against real Groq
traffic on Krishang's own machine — landing on the identical 95%/0%,
with 8 exceptions correctly including all 3 genuinely-ambiguous
duplicate-settlement cases, none force-matched. This is the first time
the entire system (not just the backend pipeline) has been exercised
against a live model rather than the deterministic `FakeLLMClient` used
throughout this project's own development and CI-style verification.
`FEE_DRIFT` — the one category requiring genuine LLM judgment rather than
arithmetic — scored 4/4. The single non-perfect result (`DUPLICATE`) is
expected, documented behavior, not a bug: two settlements of the identical
amount with no distinguishing reference are correctly flagged for human
review rather than guessed at — a confident guess here would trade a
transparent, correct exception for a coin-flip chance of a silent wrong
match, which is the wrong trade for a finance system to make.

**Production hardening (beyond the original submission scope) is done —
all 6 planned items shipped and verified.** See `docs/ROADMAP.md` for the
full tier-by-tier breakdown and `docs/DECISIONS.md` for the complete
build/hardening narrative (49+ real issues found and fixed across the
whole project, from the original build through hardening). Summary of
what hardening added:

- **Persistence** — SQLite-backed job store (was in-memory), survives a
  server restart. Proven with two independent subprocesses, not simulated.
- **Audit logging** — immutable `audit_log` table, structurally
  append-only, queryable by transaction ID across every run. `GET /audit`.
- **API authentication** — `X-API-Key` header, configurable via `API_KEYS`,
  disabled by default for zero-friction local dev.
- **LLM provider fallback** — a circuit breaker (`FallbackClient`) that
  falls back from Groq to OpenRouter after consecutive real failures, with
  automatic half-open recovery. Opt-in via `OPENROUTER_API_KEY`.
- **Value-based escalation** — high-value transactions (configurable
  threshold) flagged `requires_human_review` as pure additive metadata,
  structurally incapable of changing a match decision.
- **Confidence-based escalation gating** — a second, separate pass on
  top of value-based escalation: recovers a real signal the verifier
  already computes (deterministic corroboration vs. genuine LLM
  judgment) rather than touching the LLM's own prompt, so it carries
  zero risk to the proven match rate. Widens `requires_human_review`,
  never narrows it.
- **Refund / partial-capture modeling** — a genuinely separate
  reconciliation path (`POST /refunds/reconcile`) for classifying full,
  partial, and over-refund scenarios against the real dataset.
- **Chargeback handling** — a genuinely different two-phase mechanic
  from a refund: a chargeback is bank-initiated and debits the merchant
  provisionally before any outcome is known, only becoming final or
  reversed after a bank/network decision. `POST /chargebacks/reconcile`,
  grounded in Razorpay's real documented dispute lifecycle.

**Tier 3 is now complete** (all code-addressable items). First item: **N-way settlement batching** —
previously the largest structural gap (real bank settlement nets many
transactions into one credit line, not the 1-or-2-way splits the core
matcher handles). Two-mechanism design in `agent/batch_settlement.py`:
grouping by a stated `settlement_batch_id` (the realistic, deterministic
primary path) plus a bounded, capped subset-sum fallback for the rare
no-batch-id case — explicitly refusing to search past a small pool size
rather than pretending brute-force guessing solves the "hundreds of
transactions" scale. New `POST /batches/reconcile` endpoint. Second item:
**merchant-specific configuration** — a merchant's settlement window and
escalation threshold now genuinely parameterize the real matcher/escalation
logic (not a separate demo path), proven byte-for-byte identical to the
old behavior when a `merchant_id` is omitted or unregistered, and proven
to genuinely take effect when configured. New `POST/GET
/merchants/{merchant_id}/config` endpoints, `merchant_id` accepted on
`POST /runs`. Third item: **multi-currency/FX reconciliation** — works off
a caller-supplied rate BAND rather than a single exact rate (no live FX
feed exists, and pretending to know the exact applied rate would be false
precision), with an optional markup/spread parameter and every result
unconditionally flagged for human review. New `POST /fx/reconcile`
endpoint. Fourth and final item: **marketplace/Route-style multi-party
settlement** — grounded in Razorpay Route's actual documented mechanics
(Linked Accounts, commission, Settlement On Hold, transfer reversal), the
opposite direction from N-way batching (one payment split into many
transfers, not many transactions netted into one credit line). New `POST
/marketplace/reconcile` endpoint. See `docs/DECISIONS.md` for the full
design rationale on all four.

Every hardening item was verified against the complete pre-existing test
suite at each step (zero regressions throughout), plus a combined
concurrency stress simulation exercising the original six hardening items
together under real multi-threaded load (`scripts/deep_fuzz_hardening.py`),
and a full local verification pass on a separate machine (Windows, Python
3.14.3) — **the full suite, all stress scripts, and a live-Groq run all
came back clean with zero new bugs**, and this same full regression +
stress-script check has been repeated after every Tier 3 item added since.

## Structure

```
data/       synthetic_generator.py, refund_generator.py, batch_generator.py, fx_generator.py, generated datasets, ground_truth.json
agent/      tool-calling reconciliation loop, verifier, escalation, refund/batch/FX/merchant-config modules
eval/       batch runner + metrics (match rate, false-positive rate)
api/        FastAPI app — auth, async job pipeline, audit, refund/batch/FX/merchant-config endpoints
frontend/   React/Vite dashboard (not yet built — deprioritized)
docs/       architecture notes, decision log
tests/      pytest suite (310 tests) covering every module above
scripts/    heavier pre-submission-only stress tools, see scripts/README.md
Dockerfile  container image for deployment reproducibility, see "Running with Docker" below
```

## Setup

```
pip install -r requirements.txt
```

## Running the data generator

```
cd data
python synthetic_generator.py
```

Produces `gateway_transactions.json`, `bank_settlement.json`, and
`ground_truth.json` in `data/`. Deterministic (fixed seed) — same output
every run.

## Running the reconciliation pipeline (CLI)

Requires a Groq API key (free, no credit card required):

```
set GROQ_API_KEY=your_key_here
python eval\run_batch.py
```

Prints a summary and writes `eval/results.json` with the full match/exception
list plus metrics computed strictly from the held-out eval split of
`ground_truth.json`. Default model is `openai/gpt-oss-120b`. An
`OpenRouterClient` is also available in `agent/llm_client.py` — set
`OPENROUTER_API_KEY` as well to enable automatic fallback via
`FallbackClient` if Groq has a sustained outage (see `docs/DECISIONS.md`
for the circuit-breaker design). With just `GROQ_API_KEY` set, behavior
is unchanged from before hardening.

## Running the API

```
set GROQ_API_KEY=your_key_here
set API_KEYS=your-chosen-key
uvicorn api.app:app --reload
```

Every endpoint below is available two ways: unprefixed (`/runs`) and
under `/v1` (`/v1/runs`) — both work identically and share the same
underlying state. `/v1` is the new canonical, versioned surface;
the unprefixed paths stay as backward-compatible aliases, not
deprecated. `/health` is the one exception — deliberately unversioned,
since orchestrator health checks expect a stable path.

`API_KEYS` is optional — omit it to leave auth disabled for local dev
(matches the original zero-friction setup). If set, every request needs
a matching `X-API-Key` header. **If it's left unset, the app prints a
warning to stderr at startup** so this is never silently invisible in a
real deployment's logs. `MAX_REQUEST_BODY_BYTES` is also optional
(defaults to 2MB) — caps how large any single request body can be,
across every endpoint; a real deployment should also enforce a
body-size limit at the reverse-proxy layer as defense in depth, see
`docs/DECISIONS.md`. `STALE_JOB_TIMEOUT_SECONDS` (default 1800 / 30 min)
controls when a job stuck in `pending`/`running` — e.g. from a crashed
process — gets automatically marked `failed` the next time anything
reads job status; see `docs/DECISIONS.md` for the self-correcting
design. `CORS_ALLOWED_ORIGINS` (comma-separated, default `*`) controls
which browser origins may call this API — permissive by default
(matching this project's own "convenient by default, configurable for
production" pattern), since real access control here is the
`X-API-Key` header, which CORS doesn't replace or weaken either way. A
real deployment with a known, fixed frontend origin should set this
explicitly. `MERCHANT_CONFIG_DB_PATH` follows the exact same pattern as
`JOBS_DB_PATH` (see `agent/merchant_config.py`) — merchant settings are
now genuinely persisted to their own SQLite file, surviving a real
process restart; this was a real inconsistency found and fixed (see
`docs/DECISIONS.md`) — `api/jobs.py`'s job store and audit log had
real persistence from early on, but merchant config was still an
in-memory dict until this fix.

Then open `http://127.0.0.1:8000/docs` for interactive API docs, or:

```
curl -X POST http://127.0.0.1:8000/runs -H "Content-Type: application/json" -H "X-API-Key: your-chosen-key" -d "{\"sample_size\": 10}"
```

- `GET /health` — unauthenticated health check (real orchestrators like
  a k8s probe or an ALB don't carry an API key), checks real database
  connectivity, not just process liveness — returns `503` if the
  database is unreachable.

- `POST /runs` — starts a run. `{"sample_size": 10}` for a fast demo
  sample (deliberately biased to include live agent reasoning, not just
  instant deterministic matches — see `docs/DECISIONS.md`); omit
  `sample_size` for the full batch with real eval metrics.
- `GET /runs/{run_id}/status` — poll current state and progress
- `GET /runs/{run_id}/stream` — Server-Sent Events, live progress as each
  record resolves (note: needs a header-aware client, not a browser's
  native `EventSource` — see `docs/DECISIONS.md`)
- `GET /runs/{run_id}/results` — full results once complete, including a
  `requires_human_review` count (gated on both transaction value and
  match confidence — see below)
- `GET /runs` — list all runs
- `GET /audit?transaction_id=...&run_id=...` — the immutable audit trail:
  every decision ever made about a transaction, across every run, never
  updated or deleted once written
- `POST /refunds/reconcile` — standalone refund/partial-capture
  reconciliation; body is `{"refund_events": [{"transaction_id": ...,
  "refund_amount": ...}]}`, returns a classification (`full_refund` /
  `partial_refund` / `over_refunded`) per transaction referenced
- `POST /batches/reconcile` — standalone N-way settlement batch
  reconciliation; body is `{"gateway_records": [...], "bank_batch_records":
  [...]}`, each gateway record optionally carrying a `settlement_batch_id`.
  Returns batch-level matches for records with a stated batch_id, plus a
  bounded (capped at 12 unbatched transactions) subset-sum fallback for
  records without one — see `docs/DECISIONS.md` for why this doesn't
  attempt to solve the case at real scale (hundreds of transactions)
- `POST /merchants/{merchant_id}/config` — registers a merchant's
  settlement window (`date_window_days`) and/or escalation threshold
  (`escalation_threshold`); a full replace, not a per-field patch — an
  omitted field falls back to the global default, not the merchant's
  previous value. `GET /merchants/{merchant_id}/config` reads it back,
  including a `known_merchant` flag. Pass `merchant_id` on `POST /runs`
  to apply a registered config to that run — omitted or unregistered
  behaves identically to before this feature existed (proven in
  `tests/test_merchant_config_integration.py`). Registry is in-memory
  only — see `docs/DECISIONS.md` for why.
- `POST /fx/reconcile` — standalone multi-currency/FX reconciliation;
  body is `{"gateway_record": {"transaction_id", "amount", "currency"},
  "bank_record": {"settled_amount", "currency"}, "rate_min", "rate_max",
  "markup_bps"}`. Works off a caller-supplied rate band, never a single
  exact rate (no live FX feed exists here) — every result is
  unconditionally `requires_human_review: true`, even a clean match. See
  `docs/DECISIONS.md` for the full rationale.
- `POST /marketplace/reconcile` — standalone Route-style multi-party
  settlement reconciliation; body is `{"gateway_record": {"transaction_id",
  "net_amount"}, "transfers": [{"linked_account_id", "amount", "status"}],
  "platform_commission"}`. `status` is one of `settled`/`on_hold`/`reversed`
  — real Razorpay Route vocabulary, not invented. Returns `fully_reconciled`,
  `pending_hold` (money accounted for, just not all settled), `reversal_accounted`
  (a transfer was clawed back, ledger still balances), or `mismatch` (a real
  gap). See `docs/DECISIONS.md` for the full rationale.
- `POST /chargebacks/reconcile` — standalone chargeback/dispute
  reconciliation; body is `{"gateway_record": {"transaction_id",
  "net_amount"}, "chargeback_event": {"status", "disputed_amount",
  "chargeback_fee", "initiated_by"}}`. `status` is one of Razorpay's real
  documented dispute statuses (`open`/`under_review`/`pre_arbitration`/
  `arbitration`/`won`/`lost`). Every result is unconditionally
  `requires_human_review: true`. See `docs/DECISIONS.md` for the full
  rationale, including why this needed its own module rather than
  extending the refund endpoint.

Job data persists in `api/jobs.db` (a real file, created automatically) —
survives a server restart. Set `JOBS_DB_PATH` to use a different location;
tests use `:memory:` for isolated, disk-free runs (see
`tests/conftest.py`).

## How matching works

1. **Deterministic stage** (`agent/matcher.py`, no LLM call) — resolves any
   record where reference lookup plus exact-arithmetic checking gives a
   confident answer: clean matches, timing-lag matches, split settlements,
   and duplicate-settlement detection.
2. **Agent stage** (`agent/react_loop.py`, Groq-backed, with optional
   OpenRouter fallback) — only for records stage 1 couldn't confidently
   resolve. A tool-calling loop searches by amount/date proximity and
   proposes a match or reports an exception.
3. **Verifier** (`agent/verifier.py`) — every agent-proposed match is
   independently re-checked before being accepted; the agent never marks
   its own match final.
4. **Escalation** (`agent/escalation.py`) — pure post-processing after
   both stages finish: flags high-value transactions for human review
   without touching any match decision.

Every unresolved record gets an explicit exception code
(`agent/exceptions.py`) and reason — never silently dropped.

## Running the tests

```
pip install -r requirements.txt
python -m pytest tests\
```

310 tests covering data generation, the deterministic matcher, verifier
parsing/calibration and cross-tier threshold consistency, metrics
scoring, full pipeline mechanics, mocked HTTP-layer tests for both LLM
clients (including independent retry budgets and the `FallbackClient`
circuit breaker), the FastAPI layer (job creation, status polling, SSE
streaming, demo-sample biasing, auth, the refund, batch-settlement, and
FX endpoints), a dedicated noisy stress-test suite (deliberately messy,
~10x-volume data), a dedicated concurrency stress-test suite, date-
arithmetic edge cases, a multi-seed fuzz sample, genuine cross-process
persistence and audit-log tests (real subprocesses, not simulated), and
dedicated suites for value-based escalation, refund reconciliation,
N-way settlement batch reconciliation, merchant-specific configuration
(including a real simulated-schema-migration test), and multi-currency
FX reconciliation. Heavier
pre-submission-only checks — more seeds, harder concurrency, a
real-world settlement simulation, and a combined hardening stress test
exercising persistence+audit+auth+fallback together under real
threading — live in `scripts/`, see `scripts/README.md`. Uses a fake LLM
client for pipeline and API tests — confirms the code's logic, not real
LLM judgment quality (that needs a real provider key run, see above; now
independently re-confirmed on a second machine, see `docs/DECISIONS.md`).
On Windows, use `python -m pytest tests\` if the bare `pytest` command
isn't found on PATH.

## Running with Docker

```
docker build -t razorpay-finance-controller .
docker run -p 8000:8000 \
  -e GROQ_API_KEY=your_key_here \
  -e API_KEYS=your-chosen-key \
  -v finance-controller-jobs:/app/api \
  -v finance-controller-merchants:/app/agent \
  razorpay-finance-controller
```

Both the job database (`api/jobs.db`) and merchant config
(`agent/merchant_config.db`) live inside the container filesystem by
default — mount **both** volumes above, or either is lost on every
restart (a real gap, found and fixed: an earlier version of this
example only mounted one, from back when merchant config was still
in-memory and had nothing to persist — see `docs/DECISIONS.md`). All
the same environment variables from "Running the API" above apply
(`GROQ_API_KEY`, `API_KEYS`, `OPENROUTER_API_KEY`,
`MAX_REQUEST_BODY_BYTES`, `STALE_JOB_TIMEOUT_SECONDS`,
`CORS_ALLOWED_ORIGINS`, `MERCHANT_CONFIG_DB_PATH`) — none are baked
into the image. Includes a `HEALTHCHECK` against the real `GET /health`
endpoint. Runs as a non-root user. **Confirmed on 2026-08-29 with a real
`docker build` + `docker run` on Krishang's own machine (Docker Desktop)
— the healthcheck genuinely reported `(healthy)`, and a merchant
registered before a real `docker restart` was still there afterward,
proving the two-volume persistence setup above actually works against
a real container restart, not just simulated file I/O.** This was the
one part of the project I could never verify myself — no Docker in the
sandbox that built it — so every prior claim about it rested on the
closest approximation I could construct (a real uvicorn server, bound
to `0.0.0.0`, booted from an isolated directory containing only the
exact files the `Dockerfile`'s `COPY` directives copy — nothing from
`tests/`, `scripts/`, or `docs/`). See `docs/DECISIONS.md` for both the
original simulated verification and today's real confirmation.

## Troubleshooting

If a Groq call 404s with "model does not exist or you do not have access to
it," not every model in Groq's public docs is available to every
account/tier. Check what's actually accessible:

```
python agent\list_models.py
```

**Real, current architectural limits, named plainly rather than left
implicit** (originally found and closed as a documentation gap during a
deep verification pass — see `docs/DECISIONS.md`): both the job store
(`api/jobs.py`) and merchant config (`agent/merchant_config.py`) are
SQLite-backed and genuinely persist across a real process restart —
merchant config's own in-memory design was a real inconsistency found
during a later pass and fixed to match, not left as a permanent gap.
What's still true and worth naming: SQLite doesn't support multiple
backend replicas behind a load balancer without real coordination — a
production deployment beyond a single instance would need a real
shared datastore (Postgres, etc.) instead. A single-process deployment
(this submission's actual context) is unaffected by this.

