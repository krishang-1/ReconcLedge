"""Tests for api/jobs.py's _reap_stale_jobs() - lazy sweep for jobs
stuck in "pending"/"running" longer than STALE_JOB_TIMEOUT_SECONDS,
called at the top of get_job()/list_jobs(). See docs/DECISIONS.md for
why this is lazy-on-read rather than a genuine periodic background
thread, and for the self-correcting property tested below.
"""

from datetime import datetime, timedelta, timezone

import jobs


def _backdated_running_job(hours_ago):
    run_id = jobs.create_job(sample_size=5)
    jobs._update(
        run_id, status="running",
        started_at=(datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
    )
    return run_id


def test_stale_running_job_is_reaped_on_get_job():
    run_id = _backdated_running_job(hours_ago=2)  # well past the 1800s (30min) default
    job = jobs.get_job(run_id)
    assert job["status"] == "failed"
    assert "stale-job timeout" in job["error"]


def test_fresh_running_job_is_not_reaped():
    run_id = jobs.create_job(sample_size=5)
    jobs._update(run_id, status="running", started_at=jobs._now())
    job = jobs.get_job(run_id)
    assert job["status"] == "running"


def test_stale_pending_job_with_no_started_at_is_also_reaped():
    """A job that never even got started (started_at is NULL) should
    fall back to created_at for staleness, not be permanently immune
    just because it never reached "running"."""
    run_id = jobs.create_job(sample_size=5)
    jobs._update(
        run_id,
        created_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )
    job = jobs.get_job(run_id)
    assert job["status"] == "failed"


def test_completed_job_is_never_reaped_regardless_of_age():
    run_id = _backdated_running_job(hours_ago=2)
    jobs._update(run_id, status="completed", completed_at=jobs._now(), results={"summary": {"total": 5}})
    job = jobs.get_job(run_id)
    assert job["status"] == "completed"


def test_reap_via_list_jobs_too_not_just_get_job():
    run_id = _backdated_running_job(hours_ago=2)
    jobs.list_jobs()  # triggers the reaper as a side effect
    job = jobs.get_job(run_id)
    assert job["status"] == "failed"


def test_a_legitimate_late_completion_overwrites_a_premature_reap():
    """The self-correcting property: if a job gets reaped while its
    real background thread is still (unusually slowly) legitimately
    working, that thread's own eventual completion still wins - not a
    permanently stuck-wrong state."""
    run_id = _backdated_running_job(hours_ago=2)
    reaped = jobs.get_job(run_id)
    assert reaped["status"] == "failed"

    jobs._update(run_id, status="completed", completed_at=jobs._now(), results={"summary": {"total": 5}})
    final = jobs.get_job(run_id)
    assert final["status"] == "completed"


def test_stale_job_timeout_is_configurable(monkeypatch):
    monkeypatch.setattr(jobs, "STALE_JOB_TIMEOUT_SECONDS", 60)
    run_id = jobs.create_job(sample_size=5)
    jobs._update(
        run_id, status="running",
        started_at=(datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),
    )
    job = jobs.get_job(run_id)
    assert job["status"] == "failed"
