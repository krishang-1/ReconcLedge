"""Tests the actual point of the SQLite swap (see docs/DECISIONS.md,
"Persistence"): does job data survive a real process boundary, not just
pass through the same in-memory dict the whole test session. Every other
test uses JOBS_DB_PATH=":memory:" (see conftest.py) for fast, isolated
runs - that proves the interface swap didn't break anything, but an
in-memory database dies with the process just like the old dict did, so
it can't prove the actual persistence guarantee. This test uses a real
temp file and two genuinely separate subprocesses to prove it for real.
"""

import json
import os
import subprocess
import sys
import tempfile


def test_job_data_survives_a_process_restart():
    """Simulates a real server restart: one subprocess writes a job, exits
    completely (no shared memory, no shared process), a second, fully
    independent subprocess reads it back from the same file. If this
    passes, a real deploy restarting the API process would not lose data -
    the actual claim this whole change exists to make true."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "persistence_test.db")
        env = {**os.environ, "JOBS_DB_PATH": db_path}

        writer_script = f"""
import sys
sys.path.insert(0, {root + "/agent"!r})
sys.path.insert(0, {root + "/api"!r})
import jobs
run_id = jobs.create_job(sample_size=10)
jobs._update(run_id, status="completed", results={{"summary": {{"total": 10, "matched": 8}}}})
jobs._append_event(run_id, {{"stage": "deterministic", "transaction_id": "txn_persist_test", "status": "matched"}})
print(run_id)
"""
        writer = subprocess.run([sys.executable, "-c", writer_script], env=env, capture_output=True, text=True, timeout=30)
        assert writer.returncode == 0, f"writer subprocess failed: {writer.stderr}"
        run_id = writer.stdout.strip()

        reader_script = f"""
import sys, json
sys.path.insert(0, {root + "/agent"!r})
sys.path.insert(0, {root + "/api"!r})
import jobs
job = jobs.get_job({run_id!r})
print(json.dumps(job))
"""
        reader = subprocess.run([sys.executable, "-c", reader_script], env=env, capture_output=True, text=True, timeout=30)
        assert reader.returncode == 0, f"reader subprocess failed: {reader.stderr}"

        job = json.loads(reader.stdout.strip())
        assert job is not None, "second process could not find the job the first process wrote"
        assert job["run_id"] == run_id
        assert job["status"] == "completed"
        assert job["results"]["summary"]["matched"] == 8
        assert len(job["events"]) == 1
        assert job["events"][0]["transaction_id"] == "txn_persist_test"
