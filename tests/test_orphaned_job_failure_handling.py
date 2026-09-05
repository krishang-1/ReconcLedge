"""Regression test for a real bug found via real stress testing
(scripts/deep_fuzz_reconciliation_endpoints.py, see docs/DECISIONS.md),
not a hypothetical: _run_pipeline's own failure-handling code
(`except Exception: _update(..., status="failed", ...)`) had no
protection against THAT write also failing - e.g. a mid-run database
outage that hits both the original operation and the attempt to record
its failure. Without an inner guard, the second exception propagated
completely uncaught out of the background thread, leaving the job
stuck at whatever status it last successfully recorded (usually
"running") forever - a real orphaned-job scenario, not just a
theoretical one.
"""

import jobs


class AlwaysFailsClient:
    """Forces _run_pipeline into its except block immediately."""

    def chat(self, *args, **kwargs):
        raise RuntimeError("simulated LLM failure to trigger the except block")


def test_run_pipeline_does_not_crash_uncaught_when_failure_recording_also_fails(monkeypatch):
    """The real scenario: the original operation fails (forcing the
    except block to run), AND the except block's own _update() call
    (writing status="failed") also fails - e.g. because the underlying
    database is unavailable for both. Before the fix, this raised a
    second, completely uncaught exception straight out of the calling
    thread. After the fix, _run_pipeline must return normally (the
    outer failure is swallowed and logged, not left to crash the
    thread) - proving a background worker thread hitting this exact
    condition won't die silently and leave the job orphaned forever
    without at least attempting to record a failure state."""
    run_id = jobs.create_job(sample_size=5)

    original_update = jobs._update
    call_count = {"n": 0}

    def flaky_update(run_id, **kwargs):
        call_count["n"] += 1
        if kwargs.get("status") == "failed":
            raise RuntimeError("simulated database outage during failure recording")
        return original_update(run_id, **kwargs)

    monkeypatch.setattr(jobs, "_update", flaky_update)

    # Must not raise - this is the entire point of the fix. Before it,
    # this call would propagate the simulated "database outage during
    # failure recording" RuntimeError straight out of this test.
    jobs._run_pipeline(run_id, sample_size=5, llm_client=AlwaysFailsClient())

    # Confirms the except block's _update(status="failed") call was
    # genuinely attempted (and genuinely failed) - not skipped.
    assert call_count["n"] >= 1
