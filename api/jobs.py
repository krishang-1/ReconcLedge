"""SQLite-backed job store and the background worker that actually runs
the pipeline. Swapped from an in-memory dict (see docs/DECISIONS.md,
"Persistence") specifically to survive a server restart - every prior
version lost the entire audit trail of what was reconciled and why the
moment the process stopped. The public interface below
(create_job/get_job/list_jobs/_update/_append_event) is unchanged from
the dict version on purpose: every caller (api/app.py, every existing
test) interacts only through these functions, never the storage
internals, so this swap needed zero changes anywhere else - verified by
running the full pre-existing test suite unchanged after the swap, not
assumed safe because the interface looks the same.

Also has audit_log (see docs/DECISIONS.md, "Audit logging"): a SEPARATE,
INSERT-only table from jobs/job_events. The distinction matters - jobs
and job_events are mutable operational/UI state (a job's status changes
as it progresses, that's the point), while audit_log is the durable
record of what decision was made about each transaction and why, meant
to answer "what happened to transaction X" on its own, independent of
which run it was part of or whether that job record still exists. No
function in this module ever UPDATEs or DELETEs a row in audit_log -
that's an intentional, structural guarantee (not cryptographic
tamper-evidence, which is a further, named scope decision, not
attempted here).

Still single-process (one SQLite file, one connection) - a real
multi-process deployment would need Postgres/MySQL with a real
connection pool, a further scope decision, not an oversight. This gets
you past "a restart erases everything," not to "horizontally scalable."
"""

import json
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))

import escalation
import merchant_config
from confidence import annotate_confidence
from escalation import annotate_escalation
from matcher import run_deterministic_stage
from metrics import compute_metrics
from react_loop import run_agent_stage

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Real deployments get a real file next to this module, surviving restarts.
# Tests set JOBS_DB_PATH=":memory:" (see tests/conftest.py) for isolated,
# fast, per-process runs matching the old dict version's behavior - never
# touches disk, never leaks state between test sessions.
JOBS_DB_PATH = os.environ.get("JOBS_DB_PATH", os.path.join(os.path.dirname(__file__), "jobs.db"))

_conn = sqlite3.connect(JOBS_DB_PATH, check_same_thread=False)
_jobs_lock = threading.Lock()

with _jobs_lock:
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            sample_size INTEGER,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            progress_json TEXT NOT NULL,
            results_json TEXT,
            error TEXT,
            merchant_id TEXT
        )
    """)
    # Migration for a jobs.db created before merchant_id existed -
    # CREATE TABLE IF NOT EXISTS only covers brand-new files. SQLite has
    # no ADD COLUMN IF NOT EXISTS, hence catching the duplicate-column
    # error on already-migrated databases.
    try:
        _conn.execute("ALTER TABLE jobs ADD COLUMN merchant_id TEXT")
        _conn.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event_json TEXT NOT NULL
        )
    """)
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            transaction_id TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            method TEXT,
            detail_json TEXT NOT NULL,
            actor TEXT,
            recorded_at TEXT NOT NULL
        )
    """)
    # transaction_id is the primary real-world audit query - "show me every
    # decision ever made about this transaction, across every run." Indexed
    # since that's the query this table exists to answer quickly.
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_transaction_id ON audit_log (transaction_id)")
    _conn.commit()


def _load_data():
    """Loads the shipped datasets. Separate from eval/run_batch.py's
    loader since the API imports from a different relative path, but
    reads the same files - single source of truth for the data itself."""
    with open(os.path.join(DATA_DIR, "gateway_transactions.json")) as f:
        gateway = json.load(f)
    with open(os.path.join(DATA_DIR, "bank_settlement.json")) as f:
        bank = json.load(f)
    with open(os.path.join(DATA_DIR, "ground_truth.json")) as f:
        ground_truth = json.load(f)
    return gateway, bank, ground_truth


def build_demo_sample(gateway_records, bank_records, sample_size, date_window_days=None):
    """Builds a sample biased toward including agent-routed records, not a
    naive first-N or random slice. The whole point of a demo run is
    showing live LLM reasoning happen - only ~12/52 records in the shipped
    dataset ever reach the agent stage, so an unbiased sample of a small
    size could easily miss them entirely and just show boring instant
    deterministic matches. Runs the (fast, no-LLM) deterministic stage on
    the FULL dataset first to know which records would need the agent,
    then samples from both pools deliberately.

    date_window_days: passed through to run_deterministic_stage() - see
    that function's docstring. None (the default) means "use
    matcher.py's own module default", matching every pre-merchant-config
    caller exactly."""
    kwargs = {} if date_window_days is None else {"date_window_days": date_window_days}
    det_matched, det_exceptions, needs_agent, unclaimed = run_deterministic_stage(gateway_records, bank_records, **kwargs)

    # min(..., sample_size) prevents the max(..., 2) floor from returning
    # MORE records than requested for a small sample_size.
    agent_want = min(max(sample_size // 3, 2), len(needs_agent), sample_size)
    sampled_agent = needs_agent[:agent_want]

    det_want = max(sample_size - agent_want, 0)
    sampled_det_matched = det_matched[:det_want]

    return sampled_det_matched, sampled_agent, unclaimed


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_job(sample_size=None, merchant_id=None):
    """Creates a new job record and returns its id. The actual run is
    started separately via start_job() - split so the API can return the
    run_id immediately without waiting for the background thread to spin up.

    merchant_id: optional, see agent/merchant_config.py. Stored on the
    job row (not just passed through in-memory to _run_pipeline) so the
    persisted/audited record of a run shows which merchant's config it
    actually ran under - omitted, this defaults to NULL and the run uses
    plain global defaults, identical to before merchant config existed."""
    run_id = uuid.uuid4().hex[:12]
    initial_progress = json.dumps({"stage": "pending", "current": 0, "total": 0})
    with _jobs_lock:
        _conn.execute(
            "INSERT INTO jobs (run_id, status, sample_size, created_at, started_at, completed_at, progress_json, results_json, error, merchant_id) "
            "VALUES (?, 'pending', ?, ?, NULL, NULL, ?, NULL, NULL, ?)",
            (run_id, sample_size, _now(), initial_progress, merchant_id),
        )
        _conn.commit()
    return run_id


def _row_to_job_dict(row, events):
    run_id, status, sample_size, created_at, started_at, completed_at, progress_json, results_json, error, merchant_id = row
    return {
        "run_id": run_id,
        "status": status,
        "sample_size": sample_size,
        "created_at": created_at,
        "started_at": started_at,
        "completed_at": completed_at,
        "progress": json.loads(progress_json),
        "events": events,
        "results": json.loads(results_json) if results_json is not None else None,
        "error": error,
        "merchant_id": merchant_id,
    }


STALE_JOB_TIMEOUT_SECONDS = int(os.environ.get("STALE_JOB_TIMEOUT_SECONDS", 1800))  # 30 minutes


def _reap_stale_jobs():
    """Lazily marks jobs stuck in "pending"/"running" for longer than
    STALE_JOB_TIMEOUT_SECONDS as "failed" - called at the top of every
    read path (get_job(), list_jobs()), not from a separate background
    scheduler thread or FastAPI lifespan hook.

    Deliberately lazy rather than a genuine periodic thread: this
    project already prefers simple, minimal-footprint mechanisms over
    scheduler/lifecycle machinery where a simpler approach covers the
    real need (see api/auth.py's warn_if_auth_disabled() choosing a
    plain import-time check over a FastAPI lifespan hook, for the same
    reason) - and the exact failure this protects against ("the process
    died mid-run, leaving a job stuck in 'running' forever" - see the
    real cascading-failure bug this same session already found and
    fixed in _run_pipeline's own exception handler, docs/DECISIONS.md)
    is only ever discovered by something asking about the job's status
    afterward anyway. Sweeping opportunistically on read covers the
    real scenario without an extra thread to manage or shut down
    cleanly.

    Self-correcting in the one case this design can't perfectly
    distinguish: a job still LEGITIMATELY running in the SAME,
    still-alive process (just slow - real Groq rate-limiting genuinely
    took several minutes on Krishang's real verification run) could in
    principle be reaped if it exceeds the timeout before finishing. If
    that happens, the real background thread's own eventual
    `_update(status="completed")` call simply overwrites the reaper's
    "failed" status when the real work actually finishes - a narrow,
    temporary mis-report during that window, not a permanent stuck
    state either way. `STALE_JOB_TIMEOUT_SECONDS` defaults generously
    (30 minutes) specifically to make this an edge case, not a routine
    occurrence - configurable via env var for a real deployment to
    tune against its own realistic worst-case run time.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_TIMEOUT_SECONDS)).isoformat()
    with _jobs_lock:
        rows = _conn.execute(
            "SELECT run_id FROM jobs WHERE status IN ('pending', 'running') "
            "AND COALESCE(started_at, created_at) < ?",
            (cutoff,),
        ).fetchall()
    for (run_id,) in rows:
        _update(
            run_id, status="failed", completed_at=_now(),
            error=f"job exceeded the {STALE_JOB_TIMEOUT_SECONDS}s stale-job timeout without completing - "
                  f"likely orphaned by a process restart or crash",
        )


def get_job(run_id):
    _reap_stale_jobs()
    with _jobs_lock:
        row = _conn.execute(
            "SELECT run_id, status, sample_size, created_at, started_at, completed_at, progress_json, results_json, error, merchant_id "
            "FROM jobs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        event_rows = _conn.execute(
            "SELECT event_json FROM job_events WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        events = [json.loads(r[0]) for r in event_rows]
        return _row_to_job_dict(row, events)


def list_jobs():
    _reap_stale_jobs()
    with _jobs_lock:
        rows = _conn.execute("SELECT run_id, status, sample_size, created_at FROM jobs").fetchall()
        return [{"run_id": r[0], "status": r[1], "sample_size": r[2], "created_at": r[3]} for r in rows]


def _update(run_id, **fields):
    if not fields:
        return
    columns, values = [], []
    for key, value in fields.items():
        if key == "results":
            columns.append("results_json = ?")
            values.append(json.dumps(value) if value is not None else None)
        else:
            columns.append(f"{key} = ?")
            values.append(value)
    values.append(run_id)
    with _jobs_lock:
        _conn.execute(f"UPDATE jobs SET {', '.join(columns)} WHERE run_id = ?", values)
        _conn.commit()


def _append_event(run_id, event):
    with _jobs_lock:
        _conn.execute(
            "INSERT INTO job_events (run_id, event_json) VALUES (?, ?)",
            (run_id, json.dumps(event)),
        )
        if "progress" in event:
            _conn.execute(
                "UPDATE jobs SET progress_json = ? WHERE run_id = ?",
                (json.dumps(event["progress"]), run_id),
            )
        _conn.commit()


def _record_audit_entries(run_id, matched, exceptions, actor=None):
    """Writes one immutable audit_log row per transaction decision, from
    the FINAL, fully-resolved matched/exceptions lists - not the live
    progress events, which (for agent-stage matches specifically) don't
    carry the full utrs/agent_reasoning detail, only transaction_id and
    status. This is the one place in the pipeline where every decision's
    complete detail is available at once, so it's the correct source for
    the audit record. actor is None for now (system-attributed) - will
    be populated once authentication exists (the next hardening item);
    the schema is already shaped for that, so this table won't need to
    change when it does.
    """
    recorded_at = _now()
    with _jobs_lock:
        for m in matched:
            _conn.execute(
                "INSERT INTO audit_log (run_id, transaction_id, decision_type, method, detail_json, actor, recorded_at) "
                "VALUES (?, ?, 'matched', ?, ?, ?, ?)",
                (run_id, m["transaction_id"], m.get("method"), json.dumps(m), actor, recorded_at),
            )
        for e in exceptions:
            _conn.execute(
                "INSERT INTO audit_log (run_id, transaction_id, decision_type, method, detail_json, actor, recorded_at) "
                "VALUES (?, ?, 'exception', NULL, ?, ?, ?)",
                (run_id, e["transaction_id"], json.dumps(e), actor, recorded_at),
            )
        _conn.commit()


def get_audit_log(transaction_id=None, run_id=None):
    """Returns immutable audit entries, optionally filtered by
    transaction_id and/or run_id - the actual audit-review question this
    exists to answer: 'show me every decision ever made about
    transaction X,' across every run it was ever part of, not just
    whatever the current job state happens to say."""
    query = "SELECT run_id, transaction_id, decision_type, method, detail_json, actor, recorded_at FROM audit_log WHERE 1=1"
    params = []
    if transaction_id is not None:
        query += " AND transaction_id = ?"
        params.append(transaction_id)
    if run_id is not None:
        query += " AND run_id = ?"
        params.append(run_id)
    query += " ORDER BY id"
    with _jobs_lock:
        rows = _conn.execute(query, params).fetchall()
    return [
        {
            "run_id": r[0], "transaction_id": r[1], "decision_type": r[2],
            "method": r[3], "detail": json.loads(r[4]), "actor": r[5], "recorded_at": r[6],
        }
        for r in rows
    ]


def _run_pipeline(run_id, sample_size, llm_client, merchant_id=None):
    """The actual background work. Runs synchronously in a worker thread -
    see api/app.py for how it's dispatched off the request-handling thread.

    merchant_id: optional, see agent/merchant_config.py. None (the
    default) means every existing caller's exact prior behavior - the
    deterministic stage's date window and the escalation threshold both
    stay at matcher.py's/escalation.py's own module defaults, looked up
    via merchant_config.get_merchant_config() only when a merchant_id is
    actually given."""
    try:
        _update(run_id, status="running", started_at=_now())
        gateway, bank, ground_truth = _load_data()

        merchant_cfg = merchant_config.get_merchant_config(merchant_id) if merchant_id else None
        date_window_days = merchant_cfg.date_window_days if merchant_cfg else None
        escalation_threshold = merchant_cfg.escalation_threshold if merchant_cfg else escalation.HIGH_VALUE_THRESHOLD

        is_demo = sample_size is not None and sample_size < len(gateway)

        if is_demo:
            det_matched, sampled_agent, unclaimed = build_demo_sample(gateway, bank, sample_size, date_window_days=date_window_days)
            det_exceptions = []
        else:
            kwargs = {} if date_window_days is None else {"date_window_days": date_window_days}
            det_matched, det_exceptions, sampled_agent, unclaimed = run_deterministic_stage(gateway, bank, **kwargs)

        # Stream deterministic exceptions too, not just matches - the
        # honest-deferral cases were invisible in the live feed otherwise,
        # and "total" undercounted records actually processed.
        det_total = len(det_matched) + len(det_exceptions)
        for i, m in enumerate(det_matched):
            _append_event(run_id, {
                "stage": "deterministic", "transaction_id": m["transaction_id"], "status": "matched",
                "progress": {"stage": "deterministic", "current": i + 1, "total": det_total},
            })
        for i, e in enumerate(det_exceptions):
            _append_event(run_id, {
                "stage": "deterministic", "transaction_id": e["transaction_id"], "status": "exception", "reason": e["reason"],
                "progress": {"stage": "deterministic", "current": len(det_matched) + i + 1, "total": det_total},
            })

        def on_agent_progress(i, total, event):
            _append_event(run_id, {**event, "progress": {"stage": "agent", "current": i, "total": total}})

        # Per-run usage is a before/after DELTA, not the client's raw
        # totals - the client is a singleton shared across every run
        # (api/app.py's get_llm_client()), so its totals include other
        # runs. getattr defaults keep this additive: a client that
        # doesn't expose these attributes must never break a run.
        def _usage_snapshot(client):
            return (
                getattr(client, "total_prompt_tokens", 0), getattr(client, "total_completion_tokens", 0),
                getattr(client, "total_latency_seconds", 0.0), getattr(client, "total_calls", 0),
            )

        usage_before = _usage_snapshot(llm_client)
        agent_matched, agent_exceptions = run_agent_stage(sampled_agent, unclaimed, llm_client, on_progress=on_agent_progress)
        usage_after = _usage_snapshot(llm_client)
        llm_usage = {
            "prompt_tokens": usage_after[0] - usage_before[0],
            "completion_tokens": usage_after[1] - usage_before[1],
            "latency_seconds": round(usage_after[2] - usage_before[2], 3),
            "calls": usage_after[3] - usage_before[3],
        }

        all_matched = det_matched + agent_matched
        all_exceptions = det_exceptions + agent_exceptions

        # Additive-only, strictly after both stages have decided - can't
        # affect the match rate (see escalation.py). Runs before the
        # audit write so the immutable record captures review status.
        all_matched, all_exceptions = annotate_escalation(all_matched, all_exceptions, gateway, threshold=escalation_threshold)
        # Separate composable pass (see agent/confidence.py): only ever
        # WIDENS requires_human_review, never narrows what escalation
        # already flagged.
        all_matched, all_exceptions = annotate_confidence(all_matched, all_exceptions)
        escalated_count = sum(1 for r in all_matched + all_exceptions if r["requires_human_review"])

        # Immutable audit record - every decision this run made about
        # every transaction, independent of whether the job record above
        # is ever read again. Written for both demo and full runs alike:
        # a demo run still makes real decisions about real transaction
        # IDs, and the audit trail shouldn't have a gap for that case.
        _record_audit_entries(run_id, all_matched, all_exceptions)

        if is_demo:
            results = {
                "mode": "demo_sample",
                "sample_size": sample_size,
                "matched": all_matched,
                "exceptions": all_exceptions,
                "summary": {
                    "total": len(all_matched) + len(all_exceptions),
                    "matched": len(all_matched),
                    "exceptions": len(all_exceptions),
                    "requires_human_review": escalated_count,
                },
                "llm_usage": llm_usage,
                "note": "Demo sample results are for live demonstration only, not the reported eval metric - see /runs?sample_size= omitted for the full run.",
            }
        else:
            metrics = compute_metrics(all_matched, all_exceptions, ground_truth)
            results = {
                "mode": "full_run", "matched": all_matched, "exceptions": all_exceptions,
                "metrics": metrics, "requires_human_review": escalated_count, "llm_usage": llm_usage,
            }

        _update(run_id, status="completed", completed_at=_now(), results=results)

    except Exception as e:
        try:
            _update(run_id, status="failed", completed_at=_now(), error=str(e))
        except Exception as update_error:
            # The write that RECORDS a failure can itself fail (a DB
            # outage hits both). Without this guard the second exception
            # escapes this background thread uncaught, stranding the job
            # at "running" forever with no recorded reason.
            print(
                f"CRITICAL: run {run_id} failed ({e!r}), and recording that failure "
                f"also failed ({update_error!r}) - this job may be permanently stuck.",
                file=sys.stderr,
            )


def start_job(run_id, sample_size, llm_client, merchant_id=None):
    """Starts _run_pipeline in a background thread, not asyncio - the LLM
    clients use the synchronous requests library, so a real thread (not a
    coroutine) is what actually lets other API requests (status polling,
    SSE) keep being served concurrently while a run executes."""
    thread = threading.Thread(target=_run_pipeline, args=(run_id, sample_size, llm_client, merchant_id), daemon=True)
    thread.start()
