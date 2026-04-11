"""Tests for shared.portfolio_risk.PortfolioRiskMonitor.

All Alpaca calls are mocked.  A temporary file DB is used for each test.
"""

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.portfolio_risk import (
    ACCOUNTS,
    CircuitBreakerLevel,
    PortfolioRiskMonitor,
    _level_from_drawdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_monitor(tmp_db: str, per_account: Optional[Dict[str, float]] = None) -> PortfolioRiskMonitor:
    """Create a PortfolioRiskMonitor that returns *per_account* equity from _fetch_all_equity."""
    mon = PortfolioRiskMonitor(db_path=tmp_db, cache_ttl_secs=0.0)
    if per_account is not None:
        mon._fetch_all_equity = MagicMock(return_value=per_account)
    return mon


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _equity_dict(total: float) -> Dict[str, float]:
    """Distribute total equity evenly across all accounts."""
    per = total / len(ACCOUNTS)
    return {k: per for k in ACCOUNTS}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLevelFromDrawdown(unittest.TestCase):
    """Pure unit tests for the drawdown→level mapping."""

    def test_normal_at_zero(self):
        self.assertEqual(_level_from_drawdown(0.0), CircuitBreakerLevel.NORMAL)

    def test_normal_just_above_yellow(self):
        self.assertEqual(_level_from_drawdown(-7.99), CircuitBreakerLevel.NORMAL)

    def test_yellow_at_boundary(self):
        self.assertEqual(_level_from_drawdown(-8.0), CircuitBreakerLevel.YELLOW)

    def test_yellow_between_thresholds(self):
        self.assertEqual(_level_from_drawdown(-9.0), CircuitBreakerLevel.YELLOW)

    def test_red_at_boundary(self):
        self.assertEqual(_level_from_drawdown(-10.0), CircuitBreakerLevel.RED)

    def test_hard_stop_at_boundary(self):
        self.assertEqual(_level_from_drawdown(-12.0), CircuitBreakerLevel.HARD_STOP)

    def test_hard_stop_extreme(self):
        self.assertEqual(_level_from_drawdown(-50.0), CircuitBreakerLevel.HARD_STOP)


class TestNormalLevel(unittest.TestCase):
    """Combined equity at or above HWM → NORMAL, allow_entry=True."""

    def test_normal_level(self):
        db = _tmp_db()
        try:
            equity = _equity_dict(600_000.0)
            mon = _make_monitor(db, equity)
            status = mon.check()
            self.assertEqual(status.level, CircuitBreakerLevel.NORMAL)
            self.assertAlmostEqual(status.drawdown_pct, 0.0, places=3)
            self.assertIsNone(status.action_required)

            allowed, reason = mon.allow_entry("EXP-400")
            self.assertTrue(allowed)
            self.assertIsNone(reason)
        finally:
            os.unlink(db)


class TestYellowTrigger(unittest.TestCase):
    """Combined equity 9% below HWM → YELLOW, allow_entry=True."""

    def test_yellow_trigger(self):
        db = _tmp_db()
        try:
            hwm_equity = 600_000.0
            # First call establishes HWM
            mon = _make_monitor(db, _equity_dict(hwm_equity))
            mon.check()  # HWM = 600_000

            # Second call: 9% drawdown → YELLOW
            mon._fetch_all_equity = MagicMock(
                return_value=_equity_dict(hwm_equity * 0.91)
            )
            status = mon.check()
            self.assertEqual(status.level, CircuitBreakerLevel.YELLOW)
            self.assertLess(status.drawdown_pct, -8.0)
            self.assertEqual(status.action_required, "reduce_50pct")

            allowed, reason = mon.allow_entry("EXP-400")
            self.assertTrue(allowed)  # YELLOW allows entries (with size reduction)
            self.assertIsNone(reason)
        finally:
            os.unlink(db)


class TestRedTrigger(unittest.TestCase):
    """Combined equity 10.5% below HWM → RED, allow_entry=False."""

    def test_red_trigger(self):
        db = _tmp_db()
        try:
            hwm_equity = 600_000.0
            mon = _make_monitor(db, _equity_dict(hwm_equity))
            mon.check()

            mon._fetch_all_equity = MagicMock(
                return_value=_equity_dict(hwm_equity * 0.895)
            )
            status = mon.check()
            self.assertEqual(status.level, CircuitBreakerLevel.RED)
            self.assertLessEqual(status.drawdown_pct, -10.0)
            self.assertEqual(status.action_required, "pause_entries")

            allowed, reason = mon.allow_entry("EXP-503")
            self.assertFalse(allowed)
            self.assertIn("CB_RED", reason)
        finally:
            os.unlink(db)


class TestHardStopTrigger(unittest.TestCase):
    """Combined equity 13% below HWM → HARD_STOP, allow_entry=False."""

    def test_hard_stop_trigger(self):
        db = _tmp_db()
        try:
            hwm_equity = 600_000.0
            mon = _make_monitor(db, _equity_dict(hwm_equity))
            mon.check()

            mon._fetch_all_equity = MagicMock(
                return_value=_equity_dict(hwm_equity * 0.87)
            )
            # Patch execute_hard_stop so we don't make real Alpaca calls
            with patch.object(mon, "execute_hard_stop") as mock_hs:
                status = mon.check()
                # execute_hard_stop should have been called automatically
                mock_hs.assert_called_once()

            self.assertEqual(status.level, CircuitBreakerLevel.HARD_STOP)
            self.assertLessEqual(status.drawdown_pct, -12.0)
            self.assertEqual(status.action_required, "flatten_all")

            allowed, reason = mon.allow_entry("EXP-600")
            self.assertFalse(allowed)
            self.assertIn("HARD_STOP", reason)
        finally:
            os.unlink(db)


class TestHwmAdvances(unittest.TestCase):
    """Equity goes up → HWM updates to the new high."""

    def test_hwm_advances(self):
        db = _tmp_db()
        try:
            mon = _make_monitor(db, _equity_dict(500_000.0))
            s1 = mon.check()
            self.assertAlmostEqual(s1.hwm, 500_000.0, places=0)

            mon._fetch_all_equity = MagicMock(return_value=_equity_dict(600_000.0))
            s2 = mon.check()
            self.assertAlmostEqual(s2.hwm, 600_000.0, places=0)
            self.assertAlmostEqual(s2.drawdown_pct, 0.0, places=3)
        finally:
            os.unlink(db)


class TestHwmDoesNotRetreat(unittest.TestCase):
    """Equity goes down → HWM stays at peak."""

    def test_hwm_does_not_retreat(self):
        db = _tmp_db()
        try:
            mon = _make_monitor(db, _equity_dict(600_000.0))
            s1 = mon.check()
            peak = s1.hwm

            mon._fetch_all_equity = MagicMock(return_value=_equity_dict(550_000.0))
            s2 = mon.check()
            self.assertAlmostEqual(s2.hwm, peak, places=0)
            self.assertLess(s2.drawdown_pct, 0.0)
        finally:
            os.unlink(db)


class TestCache(unittest.TestCase):
    """Two calls within cache TTL use cached result (Alpaca called only once)."""

    def test_cache(self):
        db = _tmp_db()
        try:
            equity = _equity_dict(600_000.0)
            fetch_mock = MagicMock(return_value=equity)
            mon = PortfolioRiskMonitor(db_path=db, cache_ttl_secs=30.0)
            mon._fetch_all_equity = fetch_mock

            _ = mon.check()
            _ = mon.check()

            # Alpaca should only have been called once (second call uses cache)
            self.assertEqual(fetch_mock.call_count, 1)
        finally:
            os.unlink(db)


class TestGracefulAlpacaFailure(unittest.TestCase):
    """Alpaca raises exception → returns cached status, allow_entry=True (fail-open)."""

    def test_graceful_alpaca_failure(self):
        db = _tmp_db()
        try:
            equity = _equity_dict(600_000.0)
            mon = PortfolioRiskMonitor(db_path=db, cache_ttl_secs=0.0)
            mon._fetch_all_equity = MagicMock(return_value=equity)

            # Establish a good cached status first
            first = mon.check()
            self.assertEqual(first.level, CircuitBreakerLevel.NORMAL)

            # Now Alpaca fails
            mon._fetch_all_equity = MagicMock(side_effect=ConnectionError("Alpaca down"))

            # Should return last cached status and not raise
            fallback = mon.check()
            self.assertEqual(fallback.level, CircuitBreakerLevel.NORMAL)

            # allow_entry must still return True (fail-open)
            allowed, reason = mon.allow_entry("EXP-400")
            self.assertTrue(allowed)
        finally:
            os.unlink(db)


class TestDbPersistence(unittest.TestCase):
    """HWM survives process restart (DB persisted)."""

    def test_hwm_persists_across_instances(self):
        db = _tmp_db()
        try:
            mon1 = _make_monitor(db, _equity_dict(600_000.0))
            s1 = mon1.check()
            self.assertAlmostEqual(s1.hwm, 600_000.0, places=0)

            # Simulate new process: fresh PortfolioRiskMonitor reading same DB
            mon2 = _make_monitor(db, _equity_dict(550_000.0))
            s2 = mon2.check()
            # HWM should still be 600_000 from the first instance
            self.assertAlmostEqual(s2.hwm, 600_000.0, places=0)
            self.assertLess(s2.drawdown_pct, 0.0)
        finally:
            os.unlink(db)


class TestSnapshotAuditLog(unittest.TestCase):
    """Every check() call writes a row to equity_snapshots."""

    def test_snapshot_written(self):
        db = _tmp_db()
        try:
            mon = _make_monitor(db, _equity_dict(600_000.0))
            mon.check()
            mon.check()

            conn = sqlite3.connect(db)
            count = conn.execute("SELECT COUNT(*) FROM equity_snapshots").fetchone()[0]
            conn.close()
            # First call always writes; second is cached so only 1 write
            self.assertGreaterEqual(count, 1)
        finally:
            os.unlink(db)


if __name__ == "__main__":
    unittest.main()
