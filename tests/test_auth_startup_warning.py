"""Tests for api/auth.py's warn_if_auth_disabled(), called once at
api/app.py's module import time (process startup). Uses real
subprocesses, same pattern as tests/test_persistence.py, since the
warning only fires once per process at import - a test running against
an already-imported app module (like every other test file in this
suite) can't observe it, because that import already happened before
this test file even runs."""

import os
import subprocess
import sys


def test_warning_appears_on_fresh_import_when_api_keys_unset():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = f"""
import sys, os
os.environ.pop("API_KEYS", None)
sys.path.insert(0, {root + "/api"!r})
sys.path.insert(0, {root + "/agent"!r})
sys.path.insert(0, {root + "/eval"!r})
sys.path.insert(0, {root + "/data"!r})
import app
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "JOBS_DB_PATH": ":memory:"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"import failed: {result.stderr}"
    assert "API_KEYS is not set" in result.stderr
    assert "DISABLED" in result.stderr


def test_no_warning_appears_on_fresh_import_when_api_keys_set():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = f"""
import sys
sys.path.insert(0, {root + "/api"!r})
sys.path.insert(0, {root + "/agent"!r})
sys.path.insert(0, {root + "/eval"!r})
sys.path.insert(0, {root + "/data"!r})
import app
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "JOBS_DB_PATH": ":memory:", "API_KEYS": "a-real-key"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"import failed: {result.stderr}"
    assert "API_KEYS is not set" not in result.stderr
