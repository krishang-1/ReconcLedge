"""FastAPI layer over the reconciliation pipeline. Run with:

    uvicorn api.app:app --reload

from the project root. Requires GROQ_API_KEY in the environment for real
runs - importing/testing this module does not, since the LLM client is
created lazily (see get_llm_client()) rather than at import time.
"""

import asyncio
import json
import os
import sys
import threading

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

import jobs
import merchant_config
from auth import require_api_key, warn_if_auth_disabled
from batch_settlement import find_bounded_subset_matches, reconcile_by_batch_id
from fx_reconciliation import reconcile_fx_transaction
from chargeback_matcher import reconcile_chargeback
from marketplace_settlement import reconcile_split_transaction
from llm_client import FallbackClient, GroqClient, OpenRouterClient
from refund_matcher import reconcile_refunds

# require_api_key is a no-op unless API_KEYS is set in the environment -
# see api/auth.py. Wired here at the app level (not per-route) so it
# covers every existing route above plus any added later, in one place.
app = FastAPI(title="Razorpay Finance Controller", version="1.0", dependencies=[Depends(require_api_key)])

# Every endpoint except /health lives on this router, mounted twice at
# the bottom of this file: unprefixed (backward compatible) and under
# /v1 (canonical). /health stays unprefixed on `app` - health checks
# expect a stable path that doesn't move with version bumps.
router = APIRouter()

# Once at import time (process startup), not per-request - the
# disabled-by-default auth design is deliberate, but shouldn't be silent.
warn_if_auth_disabled()

# Bounds the request PARSE itself - batch_settlement.py's
# pool_too_large check only protects the search after parsing, so an
# oversized body could tie up a worker before it ever runs. 2MB is
# generous for a real batch but bounded against abuse.
MAX_REQUEST_BODY_BYTES = int(os.environ.get("MAX_REQUEST_BODY_BYTES", 2_000_000))


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    """Rejects a request with 413 before its body is read at all, based
    on the client-declared Content-Length header - cheap and effective
    for the overwhelmingly common case (a normal HTTP client always
    sends this header).

    Named scope limit, not silently glossed over: this checks the
    DECLARED Content-Length, not the actual bytes received. A client
    that omits Content-Length (chunked transfer-encoding) or lies about
    it isn't caught by this middleware alone - a real production
    deployment should also enforce a body-size limit at the reverse-
    proxy/load-balancer layer (nginx's client_max_body_size, an ALB
    request-size limit, etc.) as defense in depth, which is standard
    practice and not something the application layer alone should be
    relied on to fully own. This middleware is the cheap, immediate
    layer; the proxy layer is the real backstop."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"request body ({content_length} bytes) exceeds the "
                                        f"{MAX_REQUEST_BODY_BYTES}-byte limit"},
                )
        except ValueError:
            pass  # malformed Content-Length header - let normal request handling surface the real error
    return await call_next(request)


# Registered AFTER limit_request_body_size deliberately: Starlette
# makes the LAST-registered middleware the outermost layer, so
# registering CORS any earlier meant error responses (e.g. a 413 from
# the body-size check) came back with no CORS headers, surfacing in the
# browser as a confusing CORS error instead of the real status.
#
# CORS_ALLOWED_ORIGINS (comma-separated, default "*") is permissive by
# default - safe here because this API never uses cookie/session
# credentials (allow_credentials=False), so X-API-Key remains the real
# access boundary. Set it explicitly for a known frontend origin.
_cors_origins_raw = os.environ.get("CORS_ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_origins_raw == "*" else [o.strip() for o in _cors_origins_raw.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@app.exception_handler(RuntimeError)
async def missing_api_key_handler(request: Request, exc: RuntimeError):
    """Without this, a missing GROQ_API_KEY surfaces as a raw stack trace
    and a generic 500 - a near-certain first mistake for anyone setting
    this up, and confusing to debug from a bare traceback. Converts it to
    a clear, actionable error instead."""
    if "API_KEY not set" in str(exc):
        return JSONResponse(status_code=503, content={"detail": f"{exc} - see README for setup instructions"})
    raise exc


@app.get("/health")
def health():
    """Unauthenticated health check for a real orchestrator (a k8s
    liveness/readiness probe, an ALB health check) - see
    api/auth.py's UNAUTHENTICATED_PATHS for why this bypasses auth
    entirely rather than requiring a credential the orchestrator
    wouldn't have configured.

    Actually checks database connectivity (a cheap `SELECT 1`), not
    just that the process can respond to HTTP - a real orchestrator
    wants to know if the app can actually do its job, not merely that
    it's running. Returns 503 if the database is unreachable, so a real
    deployment stops routing traffic to an instance that can't serve
    real requests, rather than reporting healthy while every actual
    request would fail."""
    try:
        jobs._conn.execute("SELECT 1")
        db_status = "ok"
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "degraded", "database": f"unreachable: {e}"})
    return {"status": "ok", "database": db_status}

_shared_client = None
_client_lock = threading.Lock()


def get_llm_client():
    """Returns one shared client instance, created lazily on first real
    use rather than at import time - this is what lets the app (and its
    tests) import cleanly without GROQ_API_KEY set, and it's also what
    makes the sticky max_tokens learning (see docs/DECISIONS.md) actually
    pay off: one instance reused across every run remembers what it
    learned about the account's credit ceiling instead of rediscovering it
    per request. Overridden in tests via app.dependency_overrides.

    If OPENROUTER_API_KEY is also set, wraps Groq (primary) and
    OpenRouter (secondary) in a FallbackClient - a circuit breaker that
    tries Groq first and only falls back after repeated consecutive
    failures (see llm_client.FallbackClient's docstring for why not
    per-call). GROQ_API_KEY is still required either way: Groq stays the
    primary, matching the rest of this project's "Groq is the default,
    real-account-tested choice" stance (see GroqClient's docstring) -
    OpenRouter is opt-in extra resilience, not a replacement default.
    If OPENROUTER_API_KEY isn't set, this returns a bare GroqClient with
    no fallback at all - the exact prior behavior, unchanged - rather
    than silently requiring a second API key nobody asked to configure.

    Locked because two concurrent first requests could otherwise both see
    _shared_client as None and each construct their own client - not
    harmful (neither client's constructor has side effects beyond reading
    an env var), just a wasted duplicate allocation, but the lock closes
    it for free. Found on review, not a real-run failure.
    """
    global _shared_client
    with _client_lock:
        if _shared_client is None:
            primary = GroqClient()
            if os.environ.get("OPENROUTER_API_KEY"):
                _shared_client = FallbackClient(primary, OpenRouterClient())
            else:
                _shared_client = primary
        return _shared_client


class RunRequest(BaseModel):
    sample_size: int | None = Field(default=None, ge=1)
    merchant_id: str | None = None


class RefundEvent(BaseModel):
    transaction_id: str
    refund_amount: float = Field(gt=0)
    refund_date: str | None = None


class RefundReconcileRequest(BaseModel):
    refund_events: list[RefundEvent]


class BatchGatewayRecord(BaseModel):
    transaction_id: str
    net_amount: float
    settlement_batch_id: str | None = None


class BankBatchRecord(BaseModel):
    batch_id: str | None = None
    credited_amount: float


class BatchReconcileRequest(BaseModel):
    gateway_records: list[BatchGatewayRecord]
    bank_batch_records: list[BankBatchRecord]


@router.post("/batches/reconcile")
def reconcile_batches_endpoint(request: BatchReconcileRequest):
    """A genuinely separate reconciliation path, same category as
    /refunds/reconcile - see batch_settlement.py's module docstring for
    the two-mechanism design (batch_id grouping as the primary,
    realistic path; a bounded no-batch-id fallback for small ad hoc
    groups only). Unlike /refunds/reconcile, this does NOT read the
    curated dataset at all - settlement_batch_id isn't a field that
    dataset models, so the caller supplies the full scenario in the
    request body.

    Each bank_batch_records entry with batch_id=None is checked against
    the FULL unbatched gateway pool independently via the bounded
    fallback - a named scope limitation, not glossed over: if multiple
    such unexplained credit lines are submitted together, this does not
    attempt a global assignment that avoids one transaction being
    proposed as a candidate for more than one credit line. That's a
    real further step (a genuinely harder combinatorial assignment
    problem) not attempted here - every candidate_match result is
    requires_human_review=True specifically because of cases exactly
    like this, not just the single-batch ambiguity case."""
    gateway_records = [r.model_dump() for r in request.gateway_records]
    bank_batch_records = [r.model_dump() for r in request.bank_batch_records]

    batched_bank_records = [b for b in bank_batch_records if b.get("batch_id")]
    unbatched_bank_records = [b for b in bank_batch_records if not b.get("batch_id")]
    unbatched_gateway_records = [g for g in gateway_records if not g.get("settlement_batch_id")]

    # A duplicate batch_id is bad request input, not a server failure -
    # translate the ValueError into a clean 422 rather than a raw 500.
    try:
        batch_id_reports = reconcile_by_batch_id(gateway_records, batched_bank_records)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    bounded_fallback_reports = []
    for bank_record in unbatched_bank_records:
        result = find_bounded_subset_matches(unbatched_gateway_records, bank_record["credited_amount"])
        bounded_fallback_reports.append({"credited_amount": bank_record["credited_amount"], **result})

    return {"batch_id_reconciliation": batch_id_reports, "bounded_fallback_reconciliation": bounded_fallback_reports}


class FxGatewayRecord(BaseModel):
    transaction_id: str
    amount: float = Field(gt=0)
    currency: str


class FxBankRecord(BaseModel):
    settled_amount: float = Field(gt=0)
    currency: str


class FxReconcileRequest(BaseModel):
    gateway_record: FxGatewayRecord
    bank_record: FxBankRecord
    rate_min: float = Field(gt=0)
    rate_max: float = Field(gt=0)
    markup_bps: float = Field(default=0, ge=0)


@router.post("/fx/reconcile")
def reconcile_fx_endpoint(request: FxReconcileRequest):
    """A genuinely separate reconciliation path, same category as
    /refunds/reconcile and /batches/reconcile - see
    fx_reconciliation.py's module docstring for why this works off a
    caller-supplied rate BAND rather than a single exact rate (no live
    FX feed exists here, and pretending to know the exact applied rate
    would be a false precision this system has no way to verify).

    Every result is requires_human_review=True regardless of outcome -
    an FX match is inherently lower-confidence than a same-currency
    exact match, since it depends on the caller's rate assumption, not
    a value this system independently verified."""
    return reconcile_fx_transaction(
        request.gateway_record.model_dump(),
        request.bank_record.model_dump(),
        request.rate_min,
        request.rate_max,
        request.markup_bps,
    )


class MarketplaceGatewayRecord(BaseModel):
    transaction_id: str
    net_amount: float = Field(gt=0)


class MarketplaceTransfer(BaseModel):
    linked_account_id: str
    amount: float = Field(ge=0)
    status: str


class MarketplaceReconcileRequest(BaseModel):
    gateway_record: MarketplaceGatewayRecord
    transfers: list[MarketplaceTransfer]
    platform_commission: float = Field(ge=0)


@router.post("/marketplace/reconcile")
def reconcile_marketplace_endpoint(request: MarketplaceReconcileRequest):
    """A genuinely separate reconciliation path, same category as the
    other standalone reconciliation endpoints - see
    marketplace_settlement.py's module docstring for the real
    Razorpay-Route-grounded mechanics (Linked Accounts, commission,
    Settlement On Hold, transfer reversal) this models.

    reconcile_split_transaction() raises ValueError on an unrecognized
    transfer status - a problem with THIS request's input, not a server
    failure, so it's converted to a clean 422 here rather than bubbling
    up as a raw 500 - the same fix already applied to
    /batches/reconcile's duplicate-batch_id case (see
    docs/DECISIONS.md), applied proactively here from the start instead
    of needing a second audit to catch it."""
    try:
        return reconcile_split_transaction(
            request.gateway_record.model_dump(),
            [t.model_dump() for t in request.transfers],
            request.platform_commission,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class ChargebackGatewayRecord(BaseModel):
    transaction_id: str
    net_amount: float = Field(gt=0)


class ChargebackEvent(BaseModel):
    status: str
    disputed_amount: float = Field(gt=0)
    chargeback_fee: float = Field(ge=0)
    initiated_by: str


class ChargebackReconcileRequest(BaseModel):
    gateway_record: ChargebackGatewayRecord
    chargeback_event: ChargebackEvent


@router.post("/chargebacks/reconcile")
def reconcile_chargeback_endpoint(request: ChargebackReconcileRequest):
    """A genuinely separate reconciliation path, same category as the
    other standalone reconciliation endpoints - see
    chargeback_matcher.py's module docstring for the real Razorpay-
    dispute-lifecycle-grounded mechanics (provisional debit, Open/Under
    Review/Won/Lost/Pre-Arbitration/Arbitration) this models, and how
    it's genuinely different from a refund (bank-initiated, two-phase,
    not a single merchant-initiated event).

    reconcile_chargeback() raises ValueError on an unrecognized status -
    converted to a clean 422 here from the start, same lesson already
    applied to /marketplace/reconcile."""
    try:
        return reconcile_chargeback(
            request.gateway_record.model_dump(),
            request.chargeback_event.model_dump(),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/refunds/reconcile")
def reconcile_refunds_endpoint(request: RefundReconcileRequest):
    """A genuinely separate reconciliation path from the core /runs
    pipeline - see refund_matcher.py's module docstring for why it's
    intentionally not wired into _run_pipeline. Reads the real gateway
    dataset read-only (via jobs._load_data(), the same loader the main
    pipeline uses, so there's exactly one source of truth for the
    dataset) to look up each refunded transaction's original amount,
    but never touches jobs/audit_log state - this is a stateless
    lookup, not a run."""
    gateway, _, _ = jobs._load_data()
    events = [e.model_dump() for e in request.refund_events]
    return {"reconciliation": reconcile_refunds(gateway, events)}


@router.post("/runs")
def create_run(request: RunRequest, llm_client=Depends(get_llm_client)):
    """Starts a new reconciliation run. sample_size omitted or >= the full
    dataset runs the complete batch (the real reported metrics); a smaller
    sample_size runs a fast, agent-biased demo sample instead - see
    jobs.build_demo_sample() for why it's biased rather than a plain slice.

    merchant_id, if given, applies that merchant's registered settlement
    window and escalation threshold (see agent/merchant_config.py and
    POST /merchants/{merchant_id}/config) to this run. Omitted, or a
    merchant_id with no registered config, behaves identically to before
    merchant configuration existed - plain global defaults."""
    run_id = jobs.create_job(sample_size=request.sample_size, merchant_id=request.merchant_id)
    jobs.start_job(run_id, request.sample_size, llm_client, merchant_id=request.merchant_id)
    return {"run_id": run_id, "status": "pending"}


class MerchantConfigRequest(BaseModel):
    date_window_days: int | None = Field(default=None, ge=0)
    escalation_threshold: float | None = Field(default=None, ge=0)


@router.post("/merchants/{merchant_id}/config")
def set_merchant_config(merchant_id: str, request: MerchantConfigRequest):
    """Registers (or overwrites) a merchant's reconciliation config -
    see agent/merchant_config.py's module docstring for why this
    actually parameterizes the real matcher/escalation logic rather
    than being a separate demo path. Any field omitted from the request
    falls back to the plain global default, not to whatever this
    merchant's previous config was - each call sets a complete config,
    it doesn't patch one field in isolation. Registry is in-memory only
    (named limitation - see the module docstring), so this needs to be
    called again after a server restart."""
    kwargs = {"merchant_id": merchant_id}
    if request.date_window_days is not None:
        kwargs["date_window_days"] = request.date_window_days
    if request.escalation_threshold is not None:
        kwargs["escalation_threshold"] = request.escalation_threshold
    config = merchant_config.MerchantConfig(**kwargs)
    merchant_config.register_merchant_config(config)
    return {"merchant_id": config.merchant_id, "date_window_days": config.date_window_days, "escalation_threshold": config.escalation_threshold}


@router.get("/merchants/{merchant_id}/config")
def get_merchant_config_endpoint(merchant_id: str):
    """Returns the merchant's registered config, or the plain global
    defaults (with known_merchant: False) if none was ever registered -
    mirrors the "never leaves the caller guessing whether a fallback
    happened silently" pattern used by refund_matcher.py's
    known_transaction field."""
    is_known = merchant_config.is_merchant_known(merchant_id)
    config = merchant_config.get_merchant_config(merchant_id)
    return {
        "merchant_id": config.merchant_id,
        "date_window_days": config.date_window_days,
        "escalation_threshold": config.escalation_threshold,
        "known_merchant": is_known,
    }


@router.get("/runs")
def get_runs():
    return jobs.list_jobs()


@router.get("/audit")
def get_audit(transaction_id: str | None = None, run_id: str | None = None):
    """Returns the immutable audit trail - every decision ever made about
    a given transaction (across every run it was part of) and/or every
    decision made within a given run. This is the real financial-audit
    question ('what happened to transaction X and why'), distinct from
    /runs/{run_id}/results, which only shows one run's results and stops
    existing in any useful form once you no longer know which run_id to
    ask about."""
    return jobs.get_audit_log(transaction_id=transaction_id, run_id=run_id)


@router.get("/runs/{run_id}/status")
def get_status(run_id: str):
    job = jobs.get_job(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    return {
        "run_id": job["run_id"],
        "status": job["status"],
        "progress": job["progress"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "completed_at": job["completed_at"],
        "error": job["error"],
    }


@router.get("/runs/{run_id}/results")
def get_results(run_id: str):
    job = jobs.get_job(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    if job["status"] == "failed":
        raise HTTPException(status_code=500, detail=f"run failed: {job['error']}")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"run is still {job['status']} - poll /runs/{run_id}/status or stream /runs/{run_id}/stream")
    return job["results"]


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str):
    """Server-Sent Events stream of live progress as records resolve.
    Polls the in-memory job's event list every 300ms and yields any events
    that arrived since the last poll - simple, no extra infra (no
    websockets, no message broker), sufficient for a single-process demo
    deployment."""
    job = jobs.get_job(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail="run_id not found")

    async def event_generator():
        # Read without jobs._jobs_lock, deliberately: events is
        # append-only, and append/slice are atomic under the GIL, so a
        # concurrent append can't tear this read - worst case it misses
        # the newest event until the next 300ms poll.
        sent = 0
        while True:
            current = jobs.get_job(run_id)
            if current is None:
                break
            events = current["events"]
            for event in events[sent:]:
                yield f"data: {json.dumps(event)}\n\n"
            sent = len(events)

            if current["status"] in ("completed", "failed"):
                done_event = {"stage": "done", "status": current["status"]}
                if current["status"] == "failed":
                    done_event["error"] = current["error"]
                yield f"data: {json.dumps(done_event)}\n\n"
                break

            await asyncio.sleep(0.3)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# Mounted twice - both /runs and /v1/runs stay live and identical.
# See the comment above `router = APIRouter()`.
app.include_router(router)
app.include_router(router, prefix="/v1")
