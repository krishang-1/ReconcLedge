"""Makes agent/, eval/, and data/ importable from tests/ without a package
structure - the project's modules use flat sys.path-style imports
throughout (see e.g. eval/run_batch.py), so tests follow the same
convention rather than introducing a different one just for tests.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for subdir in ("agent", "eval", "data", "api"):
    sys.path.insert(0, os.path.join(ROOT, subdir))

# Tests get an isolated in-memory job store, not the real persistent file -
# matches the old in-memory-dict version's behavior exactly (fresh, fully
# isolated per test process, never touches disk). Must be set before any
# test module imports api/jobs.py, which reads this env var at import time
# to open its SQLite connection - pytest guarantees conftest.py runs first.
os.environ.setdefault("JOBS_DB_PATH", ":memory:")

# Same reasoning and same requirement, for agent/merchant_config.py's own
# SQLite connection (see that module's docstring for why it moved off the
# old in-memory dict) - without this, repeated test runs would accumulate
# real merchant registrations in a real file on disk across separate
# pytest invocations, instead of getting a fresh, isolated store every run.
os.environ.setdefault("MERCHANT_CONFIG_DB_PATH", ":memory:")
