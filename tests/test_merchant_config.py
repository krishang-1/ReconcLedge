"""Tests for agent/merchant_config.py's registry and default-fallback
behavior."""

import merchant_config
from escalation import HIGH_VALUE_THRESHOLD
from matcher import DATE_WINDOW_DAYS
from merchant_config import MerchantConfig, get_merchant_config, register_merchant_config


def test_unregistered_merchant_returns_global_defaults():
    config = get_merchant_config("never_registered_merchant")
    assert config.date_window_days == DATE_WINDOW_DAYS
    assert config.escalation_threshold == HIGH_VALUE_THRESHOLD


def test_registered_merchant_returns_its_own_config():
    register_merchant_config(MerchantConfig(merchant_id="m_test_1", date_window_days=3, escalation_threshold=1000.0))
    config = get_merchant_config("m_test_1")
    assert config.date_window_days == 3
    assert config.escalation_threshold == 1000.0


def test_registering_overwrites_not_merges():
    register_merchant_config(MerchantConfig(merchant_id="m_test_2", date_window_days=3, escalation_threshold=1000.0))
    register_merchant_config(MerchantConfig(merchant_id="m_test_2", date_window_days=10))
    config = get_merchant_config("m_test_2")
    assert config.date_window_days == 10
    assert config.escalation_threshold == HIGH_VALUE_THRESHOLD  # back to global default, not 1000.0


def test_different_merchants_are_independent():
    register_merchant_config(MerchantConfig(merchant_id="m_a", date_window_days=1))
    register_merchant_config(MerchantConfig(merchant_id="m_b", date_window_days=99))
    assert get_merchant_config("m_a").date_window_days == 1
    assert get_merchant_config("m_b").date_window_days == 99


def test_is_merchant_known():
    """New public function, replacing the direct `merchant_id in
    merchant_config._registry` reach-around api/app.py's endpoint used
    to do - found and fixed as part of the same persistence change
    (see docs/DECISIONS.md): a caller poking at another module's
    underscore-prefixed internals is a leaky abstraction, and it also
    wouldn't have made sense to reach into a SQLite connection's
    internals the same way a dict's could be reached into."""
    assert merchant_config.is_merchant_known("never_registered_for_this_check") is False
    register_merchant_config(MerchantConfig(merchant_id="known_check_merchant", date_window_days=5))
    assert merchant_config.is_merchant_known("known_check_merchant") is True


def test_registry_survives_real_concurrent_read_write_load():
    """Real bug-fix context, not just a docstring update: this module
    moved from an in-memory dict to a real SQLite-backed store (see
    docs/DECISIONS.md - a genuine inconsistency was found and fixed:
    api/jobs.py's job store and audit log both got real persistence
    during Tier 1 hardening, but this module never did, so a
    merchant's settings were silently lost on every restart while
    everything else survived one). The dict version's docstring here
    used to explain why NO lock was needed (single-key dict get/set is
    atomic under CPython's GIL). That reasoning no longer applies - the
    new version has a real lock (agent/merchant_config.py's `_lock`)
    protecting genuine SQLite file I/O, a different risk shape than a
    GIL-atomic dict operation. This test still verifies the same real
    property under the new mechanism: concurrent reads and writes
    across shared merchant_ids produce no exceptions and no
    corrupted/torn reads."""
    import random
    import threading

    errors = []

    def writer(merchant_id, n):
        for _ in range(n):
            try:
                register_merchant_config(MerchantConfig(merchant_id=merchant_id, date_window_days=random.randint(0, 10)))
            except Exception as e:
                errors.append(("write", e))

    def reader(merchant_id, n):
        for _ in range(n):
            try:
                cfg = get_merchant_config(merchant_id)
                assert cfg.date_window_days is not None
            except Exception as e:
                errors.append(("read", e))

    threads = []
    for i in range(10):
        threads.append(threading.Thread(target=writer, args=(f"concurrent_test_m{i % 3}", 200)))
        threads.append(threading.Thread(target=reader, args=(f"concurrent_test_m{i % 3}", 200)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []


def test_config_persists_across_a_fresh_module_reimport():
    """The actual point of this whole change, verified directly rather
    than just inferred from the schema existing: a real SQLite
    connection, unlike the old in-memory dict, keeps its data after
    the module is reloaded - simulating what a process restart would
    look like for a file-backed (non-":memory:") database. Uses a real
    temp file rather than the test suite's own ":memory:" connection
    (see tests/conftest.py), since ":memory:" is intentionally
    non-persistent even across a reconnect within the same process -
    this test needs a genuine file to prove real persistence, not the
    isolated-per-test-run behavior the rest of the suite deliberately
    wants."""
    import importlib
    import os
    import sqlite3
    import tempfile

    import merchant_config as mc_module

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "persistence_check.db")
        original_path = os.environ.get("MERCHANT_CONFIG_DB_PATH")
        os.environ["MERCHANT_CONFIG_DB_PATH"] = db_path
        try:
            importlib.reload(mc_module)
            mc_module.register_merchant_config(
                mc_module.MerchantConfig(merchant_id="persists_across_restart", date_window_days=9, escalation_threshold=42.0)
            )
            mc_module._conn.close()  # simulates the connection a real process restart would drop

            # A fresh connection to the SAME file, not the module's own
            # (now-closed) one - proves the DATA survived, not just that
            # the Python object happened to still be reachable in memory.
            fresh_conn = sqlite3.connect(db_path)
            row = fresh_conn.execute(
                "SELECT date_window_days, escalation_threshold FROM merchant_configs WHERE merchant_id = ?",
                ("persists_across_restart",),
            ).fetchone()
            fresh_conn.close()
            assert row == (9, 42.0)
        finally:
            if original_path is not None:
                os.environ["MERCHANT_CONFIG_DB_PATH"] = original_path
            else:
                os.environ.pop("MERCHANT_CONFIG_DB_PATH", None)
            importlib.reload(mc_module)  # restore the module to its normal :memory: test state
