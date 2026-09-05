"""Merchant-specific reconciliation configuration.

Unlike the five standalone reconciliation modules, this genuinely
parameterizes the core pipeline: a merchant's settlement window reaches
matcher.py itself, changing what counts as a valid match. Both
overrides default to the existing module constants, so an unregistered
merchant behaves identically to the pre-merchant-config system.

SQLite-backed via MERCHANT_CONFIG_DB_PATH, mirroring api/jobs.py's
pattern (":memory:" for tests, see tests/conftest.py). See
docs/DECISIONS.md for the design history.
"""

import os
import sqlite3
import threading
from dataclasses import dataclass

from escalation import HIGH_VALUE_THRESHOLD
from matcher import DATE_WINDOW_DAYS


@dataclass
class MerchantConfig:
    merchant_id: str
    date_window_days: int = DATE_WINDOW_DAYS
    escalation_threshold: float = HIGH_VALUE_THRESHOLD


# Real deployments get a file next to this module; tests set ":memory:".
MERCHANT_CONFIG_DB_PATH = os.environ.get(
    "MERCHANT_CONFIG_DB_PATH", os.path.join(os.path.dirname(__file__), "merchant_config.db")
)

_conn = sqlite3.connect(MERCHANT_CONFIG_DB_PATH, check_same_thread=False)
_lock = threading.Lock()

with _lock:
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS merchant_configs (
            merchant_id TEXT PRIMARY KEY,
            date_window_days INTEGER NOT NULL,
            escalation_threshold REAL NOT NULL
        )
    """)
    _conn.commit()


def register_merchant_config(config):
    """Stores (or overwrites) a MerchantConfig by merchant_id."""
    with _lock:
        _conn.execute(
            "INSERT INTO merchant_configs (merchant_id, date_window_days, escalation_threshold) VALUES (?, ?, ?) "
            "ON CONFLICT(merchant_id) DO UPDATE SET date_window_days = excluded.date_window_days, "
            "escalation_threshold = excluded.escalation_threshold",
            (config.merchant_id, config.date_window_days, config.escalation_threshold),
        )
        _conn.commit()


def get_merchant_config(merchant_id):
    """Returns the merchant's config, or one built from global defaults
    if unregistered - callers never need to branch on whether a merchant
    has config."""
    with _lock:
        row = _conn.execute(
            "SELECT date_window_days, escalation_threshold FROM merchant_configs WHERE merchant_id = ?",
            (merchant_id,),
        ).fetchone()
    if row is not None:
        return MerchantConfig(merchant_id=merchant_id, date_window_days=row[0], escalation_threshold=row[1])
    return MerchantConfig(merchant_id=merchant_id)


def is_merchant_known(merchant_id):
    """Whether this merchant was explicitly registered, as opposed to
    falling back to global defaults."""
    with _lock:
        row = _conn.execute("SELECT 1 FROM merchant_configs WHERE merchant_id = ?", (merchant_id,)).fetchone()
    return row is not None
