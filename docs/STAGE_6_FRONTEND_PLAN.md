# Stage 6 — Frontend Dashboard: Detailed Plan

Every screen and API call in this document maps to something that
genuinely exists in the backend today (confirmed against the real
`openapi()` schema at time of writing, not assumed) — nothing here
requires a new backend endpoint. This is deliberate: Stage 6 was
deprioritized specifically so backend correctness could be proven
first; this plan exists so building the frontend now is a mapping
exercise, not a design-while-discovering-gaps exercise.

## Goals, tied to judging criteria

- **Build quality**: a real, working dashboard — not a mockup — driven
  entirely by the existing API, no new backend work required.
- **AI judgment, made visible**: the agent's live tool-calling
  reasoning (SSE stream) and the verifier's accept/reject decisions are
  currently only visible in logs and test output. This is the single
  highest-value thing the frontend adds — it turns something already
  built into something a judge experiences directly.
- **Failure recovery, made visible**: the honest `DUPLICATE`
  deferral, the exception reasons, the confidence tiers, the
  audit trail — all real, all currently text-only. Surfacing them
  visually is the second-highest-value addition.
- **Problem taste**: the reconciliation-tools panel (refunds, batches,
  FX, marketplace, chargebacks) demonstrates breadth without needing a
  full run each time.

## Tech stack

React + Vite + TypeScript (matches the existing empty `frontend/`
directory and the stack already named in `README.md`). Plain fetch
(no heavyweight data-fetching library needed — the API surface is
small and mostly polling/SSE-driven, not complex cache invalidation).
Tailwind for styling (fast to build with, easy to make look
intentional rather than default — see the `frontend-design` skill
when this is actually built, for avoiding a templated look).

## Information architecture (site map)

```
/                          Dashboard (runs list)
/runs/new                  New Run (modal or dedicated screen)
/runs/:id                  Run detail (live while running, results once done)
/audit                     Audit trail search
/tools                     Reconciliation tools (tabbed)
  /tools/refunds
  /tools/batches
  /tools/fx
  /tools/marketplace
  /tools/chargebacks
/merchants                 Merchant config admin
/settings                  API key entry (client-side only, see below)
```

## Auth handling (client-side)

The backend's `X-API-Key` auth is optional and off by default (see
`api/auth.py`). The frontend should:
- Store the key in memory/sessionStorage only (never localStorage —
  matches this project's own artifact-storage conventions elsewhere).
- A `/settings` screen to paste in a key, tested live against
  `GET /health` (unauthenticated) plus one authenticated call
  (`GET /runs`) to confirm it works before saving.
- If the backend has no `API_KEYS` configured at all, every call
  succeeds with no key — the UI should detect this (a `GET /runs` with
  no header succeeding) and simply not show the key-entry prompt,
  rather than force one.

---

## Phase-by-phase build plan

### Phase 0 — Scaffold & API client (foundation, no visible UI yet)

**Deliverable:** a working Vite/React/TS project, a typed API client
module wrapping every real endpoint, and a running `npm run dev`
against a local `uvicorn api.app:app` instance.

**Build:**
- `src/api/client.ts` — one function per endpoint, typed request/response
  shapes matching the real Pydantic models (see API mapping table
  below). Reads the base URL and API key from a small config/context.
- `src/api/types.ts` — TypeScript interfaces for every response shape
  (`RunStatus`, `RunResults`, `MatchedRecord`, `ExceptionRecord`,
  `AuditRow`, etc.) derived directly from the real Pydantic models in
  `api/app.py` and the real dict shapes in `agent/*.py` — not guessed.

**Acceptance criteria:** a bare page that calls `GET /health` and
renders the JSON response proves the client works end to end.

### Phase 1 — Dashboard (runs list) — MVP core

**Deliverable:** `/` shows every past run.

**Maps to:** `GET /runs`

**Screens/components:**
- `RunsList` — table: run ID (truncated, copyable), status badge
  (pending/running/completed/failed, color-coded), sample size or
  "full run", created/completed timestamps, match rate if completed.
- `NewRunButton` → opens the New Run flow (Phase 2).
- Empty state for a fresh install (no runs yet).

**Acceptance criteria:** creating a run elsewhere (e.g. via curl) and
refreshing shows it in the list with the correct status.

### Phase 2 — New Run + live streaming — the single highest-value screen

**Deliverable:** starting a run and watching it resolve live.

**Maps to:** `POST /runs`, `GET /runs/{run_id}/stream`,
`GET /runs/{run_id}/status`

**Screens/components:**
- `NewRunModal` — sample size (empty = full run, with a note that
  this is what reports the real eval metrics; a number = fast biased
  demo sample), optional merchant ID field (**freeform text input, not
  a dropdown** - the real backend has `GET /merchants/{id}/config` for
  a single lookup, but no list endpoint to enumerate registered
  merchants at all. Found and corrected in this plan before building
  Phase 2, not discovered mid-build: an earlier draft of this plan
  incorrectly assumed a `/merchants` list existed. A dropdown "backed"
  by client-side memory of recently-typed IDs is a reasonable
  Phase 6 stretch; a true backend-populated dropdown isn't possible
  without a new endpoint, which is out of scope - the plan only maps
  to endpoints that already exist), passed as `merchant_id`.
- `LiveRunView` (`/runs/:id` while status is `pending`/`running`):
  - Progress bar/counter driven by SSE events from the stream
    endpoint — deterministic-stage progress first (fast), then
    agent-stage progress (slower, one record at a time).
  - **The centerpiece**: a live feed of agent reasoning as SSE events
    arrive — each ambiguous record's tool calls, the candidate it's
    considering, and the verifier's accept/reject with the actual
    reason string. This is the "AI judgment" story happening in real
    time, not summarized after the fact.
  - Falls back cleanly to polling `GET /runs/{run_id}/status` if SSE
    isn't available (some browser/proxy setups block it) — the stream
    endpoint's own docstring already notes this limitation.
  - On completion, auto-navigates to the Results view (Phase 3).

**Acceptance criteria:** a real run against the fake client (or real
Groq) shows deterministic matches appearing near-instantly, then
agent-stage records resolving one at a time with visible reasoning,
ending on the results view.

### Phase 3 — Results view — where the numbers and the honesty live

**Deliverable:** `/runs/:id` once completed.

**Maps to:** `GET /runs/{run_id}/results`

**Screens/components:**
- `ResultsSummary` — match rate (large, prominent), false positive
  rate if present, `requires_human_review` count, confidence-tier
  breakdown (high/medium/low as a simple bar or donut).
- `MatchedRecordsTable` — transaction ID, UTR(s), method
  (deterministic/agent_verified), confidence badge, escalation flag.
- `ExceptionsTable` — transaction ID, exception type badge
  (`NO_CANDIDATE_FOUND`/`AMBIGUOUS_MULTIPLE_CANDIDATES`/
  `AMOUNT_MISMATCH_UNEXPLAINED`/`VERIFIER_REJECTED`), the real reason
  string, confidence (always `low` for exceptions, by design).
- **The honest-deferral callout**: when an exception's reason mentions
  a duplicate-amount ambiguity, surface a small explanatory note next
  to it — *"flagged for human review rather than guessed, since
  nothing in the data can distinguish which settlement is which."*
  This is worth being deliberate about, not just another table row —
  it's the project's clearest differentiator.
- Filter/sort by confidence tier, escalation status, exception type.

**Acceptance criteria:** every field in the real `results` JSON shape
(matched, exceptions, metrics, requires_human_review) has a
corresponding visible element — nothing silently dropped.

### Phase 4 — Audit trail

**Deliverable:** `/audit` — search any transaction's full history.

**Maps to:** `GET /audit?transaction_id=...&run_id=...`

**Screens/components:**
- `AuditSearch` — search by transaction ID or filter by run ID.
- `AuditTimeline` — every decision ever recorded for that transaction,
  across every run, in order, each row showing the decision detail
  (including `confidence`, `requires_human_review` — these ship in
  `detail_json`) and timestamp. Framed as "this is genuinely immutable
  — no update/delete path exists for this table" (a real, true claim
  about the backend, worth stating on the screen itself).

**Acceptance criteria:** a transaction that appears in two different
runs shows both entries, correctly attributed.

### Phase 5 — Reconciliation tools panel

**Deliverable:** `/tools/*` — five standalone forms, no full run
required, demonstrating breadth quickly.

**Maps to:** `POST /refunds/reconcile`, `POST /batches/reconcile`,
`POST /fx/reconcile`, `POST /marketplace/reconcile`,
`POST /chargebacks/reconcile`

**Screens/components (one per tab, same general shape):**
- `RefundTool` — a small repeatable form for refund events
  (transaction_id, refund_amount), submits, shows classification
  (`full_refund`/`partial_refund`/`over_refunded`) with the real
  reason text.
- `BatchTool` — gateway records + bank batch records as two editable
  lists (or a JSON paste box for speed), shows `batch_id_reconciliation`
  and `bounded_fallback_reconciliation` results, including the honest
  `pool_too_large` refusal state if triggered.
- `FxTool` — gateway/bank record + rate band + markup inputs, shows
  the classification and the **implied rate** so a user can see why a
  result was `rate_implausible`.
- `MarketplaceTool` — gateway record + transfers (with status
  dropdown: settled/on_hold/reversed) + commission, shows one of the
  four classifications with the gap if mismatched.
- `ChargebackTool` — gateway record + dispute status dropdown (all six
  real Razorpay statuses) + amounts, shows the classification and
  current expected balance — including the legitimate-negative-balance
  case, explained rather than looking like an error.
- Every tool: pre-filled "load example" buttons using the real
  `data/*_generator.py` scenarios, so a judge can see a realistic
  result in one click without hand-typing test data.

**Acceptance criteria:** each tool's "load example" button reproduces
the exact classifications already proven in the backend's own test
suite for that scenario.

### Phase 6 — Merchant config admin

**Deliverable:** `/merchants` — register and view merchant-specific
settlement windows and escalation thresholds.

**Maps to:** `POST /merchants/{merchant_id}/config`,
`GET /merchants/{merchant_id}/config`

**Screens/components:**
- `MerchantConfigForm` — merchant ID, date window days, escalation
  threshold. Submitting is a full replace (matches the real backend
  semantics) — the UI should say so, not imply a partial patch.
- `MerchantConfigLookup` — enter an ID, see its config and the
  `known_merchant` flag (distinguishing "registered with these values"
  from "never registered, seeing plain defaults").
- Feeds a client-side "recently used merchant IDs" suggestion list in the New Run flow (Phase 2) - not a true backend-populated dropdown, since no list endpoint exists.

**Acceptance criteria:** registering a tight date window here, then
running a full batch with that merchant selected, visibly shifts more
records into the agent-stage reasoning feed from Phase 2 — the two
phases should visibly connect for a judge trying it live.

### Phase 7 — Polish pass

- Loading states, error states (backend unreachable, `401`/`403`
  surfaced clearly, `413` for an oversized tool submission).
- The `/health` badge somewhere persistent (a small dot in the header)
  — genuinely useful and a nice, free demonstration that the frontend
  is aware of the backend's own health-check work.
- Responsive layout — not mobile-first, but shouldn't break in a
  judge's browser window at a reasonable laptop width.
- A short "How this works" panel on the dashboard summarizing the
  deterministic-then-agent-then-verifier pipeline in a few lines, for
  a judge who lands on the dashboard without context.

---

## Full API-to-UI mapping table

| Endpoint | Method | Used by |
|---|---|---|
| `/health` | GET | Header status badge (Phase 7), settings screen key test |
| `/runs` | GET | Dashboard runs list (Phase 1) |
| `/runs` | POST | New Run modal (Phase 2) |
| `/runs/{id}/status` | GET | Live run polling fallback (Phase 2) |
| `/runs/{id}/stream` | GET | Live run SSE feed (Phase 2) |
| `/runs/{id}/results` | GET | Results view (Phase 3) |
| `/audit` | GET | Audit trail search (Phase 4) |
| `/refunds/reconcile` | POST | Refund tool (Phase 5) |
| `/batches/reconcile` | POST | Batch tool (Phase 5) |
| `/fx/reconcile` | POST | FX tool (Phase 5) |
| `/marketplace/reconcile` | POST | Marketplace tool (Phase 5) |
| `/chargebacks/reconcile` | POST | Chargeback tool (Phase 5) |
| `/merchants/{id}/config` | POST | Merchant config form (Phase 6) |
| `/merchants/{id}/config` | GET | Merchant config lookup (Phase 6), New Run merchant ID field autofill check |

Every route above also has a `/v1/`-prefixed twin (see the API
versioning work in `docs/DECISIONS.md`) — the frontend should call the
`/v1/` versions as the canonical surface, since that's the one meant
to be stable going forward.

---

## Scope cuts (explicit, not silently dropped)

- **No auth UI beyond a plain key-entry field** — no login flow, no
  user accounts. The backend has one shared API key concept, not
  per-user identity; building more than that would be inventing scope
  the backend doesn't have.
- **No editing of past runs or audit rows** — matches the backend's
  own real invariants (audit log has no update/delete path at all).
- **No real-time multi-user collaboration features** — out of scope
  for a hackathon dashboard.
- **No charting library for historical trends across many runs** — a
  reasonable stretch goal if time allows, not core to the plan.

## Suggested build order given the Sept 5 deadline

Phases 0 → 1 → 2 → 3 are the core story (scaffold, see past runs,
start one, watch it happen, see results) and are the minimum that
makes the frontend genuinely additive rather than decorative. Phase 4
(audit) and Phase 6 (merchant config) are medium-value, quick to build
given how thin their API surface is. Phase 5 (tools panel) is the
most build-time-expensive for its marginal value — five near-identical
forms — and is the first thing to cut or simplify (e.g., a single
generic "paste JSON, hit an endpoint, see the response" tool instead
of five bespoke forms) if time runs short. Phase 7 (polish) scales to
whatever time remains.
