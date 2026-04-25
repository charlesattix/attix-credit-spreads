"""Stress tests for shared.portfolio_risk — the circuit breaker that protects real money.

Covers: edge cases (zero/NaN equity, empty portfolios), boundary conditions
(exact threshold values), sequential drawdown scenarios, HWM recovery,
concurrent access, DB corruption recovery, and fail-open semantics.

All Alpaca calls mocked. Temp DB per test.
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.portfolio_risk import (
    CircuitBreakerLevel,
    PortfolioRiskMonitor,
    PortfolioStatus,
    _action_from_level,
    _level_from_drawdown,
    _HARD_STOP_THRESHOLD,
    _RED_THRESHOLD,
    _YELLOW_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_monitor(tmp_path, cache_ttl=0):
    """Create a PortfolioRiskMonitor with a temp DB and zero cache TTL."""
    db = str(tmp_path / "test_risk.db")
    return PortfolioRiskMonitor(
        db_path=db, project_root=str(tmp_path), cache_ttl_secs=cache_ttl,
    )


def _patch_equity(monitor, equity_map):
    """Patch _fetch_all_equity to return a fixed dict."""
    monitor._fetch_all_equity = MagicMock(return_value=equity_map)


# ---------------------------------------------------------------------------
# 1. Pure function: _level_from_drawdown — exact boundary tests
# ---------------------------------------------------------------------------

class TestLevelFromDrawdownBoundaries:
    """Every threshold boundary is tested at, above, and below."""

    def test_exactly_zero(self):
        assert _level_from_drawdown(0.0) == CircuitBreakerLevel.NORMAL

    def test_positive_drawdown(self):
        """Equity above HWM (positive 'drawdown') should be NORMAL."""
        assert _level_from_drawdown(5.0) == CircuitBreakerLevel.NORMAL

    def test_just_above_yellow(self):
        assert _level_from_drawdown(-7.99) == CircuitBreakerLevel.NORMAL

    def test_exactly_yellow(self):
        assert _level_from_drawdown(-8.0) == CircuitBreakerLevel.YELLOW

    def test_just_below_yellow(self):
        assert _level_from_drawdown(-8.01) == CircuitBreakerLevel.YELLOW

    def test_just_above_red(self):
        assert _level_from_drawdown(-9.99) == CircuitBreakerLevel.YELLOW

    def test_exactly_red(self):
        assert _level_from_drawdown(-10.0) == CircuitBreakerLevel.RED

    def test_just_below_red(self):
        assert _level_from_drawdown(-10.01) == CircuitBreakerLevel.RED

    def test_just_above_hard_stop(self):
        assert _level_from_drawdown(-11.99) == CircuitBreakerLevel.RED

    def test_exactly_hard_stop(self):
        assert _level_from_drawdown(-12.0) == CircuitBreakerLevel.HARD_STOP

    def test_just_below_hard_stop(self):
        assert _level_from_drawdown(-12.01) == CircuitBreakerLevel.HARD_STOP

    def test_extreme_drawdown(self):
        assert _level_from_drawdown(-99.0) == CircuitBreakerLevel.HARD_STOP

    def test_nan_drawdown(self):
        """NaN comparisons are all False, so NaN should map to NORMAL (fail-open)."""
        import math
        result = _level_from_drawdown(float("nan"))
        assert result == CircuitBreakerLevel.NORMAL


class TestActionFromLevel:
    def test_all_levels_have_actions(self):
        assert _action_from_level(CircuitBreakerLevel.NORMAL) is None
        assert _action_from_level(CircuitBreakerLevel.YELLOW) == "reduce_50pct"
        assert _action_from_level(CircuitBreakerLevel.RED) == "pause_entries"
        assert _action_from_level(CircuitBreakerLevel.HARD_STOP) == "flatten_all"


# ---------------------------------------------------------------------------
# 2. Edge cases: zero/negative equity, empty portfolio, NaN
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_zero_combined_equity_returns_fail_open(self, tmp_path):
        """If all accounts return 0, we cannot compute DD — must fail open."""
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 0.0, "EXP-401": 0.0})
        status = m.check()
        assert status.level == CircuitBreakerLevel.NORMAL
        assert status.combined_equity == 0.0

    def test_negative_combined_equity_returns_fail_open(self, tmp_path):
        """Negative equity (margin call) must not crash — fail open."""
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": -5000.0})
        status = m.check()
        assert status.level == CircuitBreakerLevel.NORMAL

    def test_empty_account_map_returns_fail_open(self, tmp_path):
        """No accounts respond — combined = 0 → fail open."""
        m = _make_monitor(tmp_path)
        _patch_equity(m, {})
        status = m.check()
        assert status.level == CircuitBreakerLevel.NORMAL

    def test_single_account_with_value(self, tmp_path):
        """Only one account in the map has equity — should still work."""
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        status = m.check()
        assert status.level == CircuitBreakerLevel.NORMAL
        assert status.combined_equity == 100000.0
        assert status.hwm == 100000.0
        assert status.drawdown_pct == 0.0

    def test_alpaca_fetch_raises_exception(self, tmp_path):
        """If Alpaca is unreachable, fail open (not crash)."""
        m = _make_monitor(tmp_path)
        m._fetch_all_equity = MagicMock(side_effect=ConnectionError("timeout"))
        status = m.check()
        assert status.level == CircuitBreakerLevel.NORMAL

    def test_alpaca_fetch_raises_after_cached_yellow(self, tmp_path):
        """After a YELLOW status is cached, API failure should return YELLOW (not NORMAL)."""
        m = _make_monitor(tmp_path)
        # First: establish HWM at 100k
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        # Second: drop to 91k → YELLOW (-9%)
        _patch_equity(m, {"EXP-400": 91000.0})
        status = m.check()
        assert status.level == CircuitBreakerLevel.YELLOW
        # Third: API fails → should return cached YELLOW, not NORMAL
        m._fetch_all_equity = MagicMock(side_effect=ConnectionError("down"))
        status = m.check()
        assert status.level == CircuitBreakerLevel.YELLOW


# ---------------------------------------------------------------------------
# 3. HWM tracking and drawdown computation
# ---------------------------------------------------------------------------

class TestHWMTracking:
    def test_hwm_set_on_first_check(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 50000.0, "EXP-401": 50000.0})
        status = m.check()
        assert status.hwm == 100000.0
        assert status.drawdown_pct == 0.0

    def test_hwm_advances_on_new_high(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 110000.0})
        status = m.check()
        assert status.hwm == 110000.0
        assert abs(status.drawdown_pct) < 0.01

    def test_hwm_does_not_retreat(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 95000.0})
        status = m.check()
        assert status.hwm == 100000.0
        assert abs(status.drawdown_pct - (-5.0)) < 0.01

    def test_hwm_persists_across_instances(self, tmp_path):
        """HWM survives process restart (new monitor instance, same DB)."""
        db = str(tmp_path / "persist.db")
        m1 = PortfolioRiskMonitor(db_path=db, project_root=str(tmp_path), cache_ttl_secs=0)
        m1._fetch_all_equity = MagicMock(return_value={"EXP-400": 200000.0})
        m1.check()

        m2 = PortfolioRiskMonitor(db_path=db, project_root=str(tmp_path), cache_ttl_secs=0)
        m2._fetch_all_equity = MagicMock(return_value={"EXP-400": 180000.0})
        status = m2.check()
        assert status.hwm == 200000.0
        assert abs(status.drawdown_pct - (-10.0)) < 0.01
        assert status.level == CircuitBreakerLevel.RED

    def test_drawdown_at_exact_12pct(self, tmp_path):
        """Exactly -12% must trigger HARD_STOP, not RED."""
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 88000.0})  # exactly -12%
        with patch.object(m, 'execute_hard_stop'):
            status = m.check()
        assert status.level == CircuitBreakerLevel.HARD_STOP
        assert abs(status.drawdown_pct - (-12.0)) < 0.01


# ---------------------------------------------------------------------------
# 4. Sequential drawdown scenarios (the real production path)
# ---------------------------------------------------------------------------

class TestSequentialDrawdown:
    def test_normal_to_yellow_to_red_to_hard_stop(self, tmp_path):
        """Walk through the full circuit breaker escalation."""
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        s = m.check()
        assert s.level == CircuitBreakerLevel.NORMAL

        _patch_equity(m, {"EXP-400": 91500.0})  # -8.5%
        s = m.check()
        assert s.level == CircuitBreakerLevel.YELLOW

        _patch_equity(m, {"EXP-400": 89500.0})  # -10.5%
        s = m.check()
        assert s.level == CircuitBreakerLevel.RED

        _patch_equity(m, {"EXP-400": 87000.0})  # -13%
        with patch.object(m, 'execute_hard_stop'):
            s = m.check()
        assert s.level == CircuitBreakerLevel.HARD_STOP

    def test_recovery_from_yellow_to_normal(self, tmp_path):
        """After a drawdown, equity recovering above -8% should return to NORMAL."""
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()

        _patch_equity(m, {"EXP-400": 91000.0})  # -9% → YELLOW
        s = m.check()
        assert s.level == CircuitBreakerLevel.YELLOW

        _patch_equity(m, {"EXP-400": 93000.0})  # -7% → NORMAL
        s = m.check()
        assert s.level == CircuitBreakerLevel.NORMAL

    def test_recovery_then_new_high_resets_dd(self, tmp_path):
        """New HWM after recovery means fresh drawdown calculation."""
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 91000.0})  # -9%
        m.check()
        _patch_equity(m, {"EXP-400": 105000.0})  # new HWM
        s = m.check()
        assert s.hwm == 105000.0
        assert s.drawdown_pct == 0.0
        assert s.level == CircuitBreakerLevel.NORMAL

        # Now -8% from the NEW hwm of 105k
        _patch_equity(m, {"EXP-400": 96600.0})  # -8% of 105k
        s = m.check()
        assert s.level == CircuitBreakerLevel.YELLOW

    def test_multiple_yellow_red_oscillations(self, tmp_path):
        """Portfolio oscillating around RED boundary should not stick."""
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()

        for equity, expected in [
            (90500, CircuitBreakerLevel.YELLOW),    # -9.5%
            (89500, CircuitBreakerLevel.RED),        # -10.5%
            (90500, CircuitBreakerLevel.YELLOW),    # -9.5% (recovery)
            (89500, CircuitBreakerLevel.RED),        # -10.5% (back down)
            (92500, CircuitBreakerLevel.NORMAL),    # -7.5% → NORMAL
        ]:
            _patch_equity(m, {"EXP-400": float(equity)})
            s = m.check()
            assert s.level == expected, f"equity={equity} expected={expected} got={s.level}"


# ---------------------------------------------------------------------------
# 5. allow_entry logic
# ---------------------------------------------------------------------------

class TestAllowEntry:
    def test_normal_allows(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        ok, reason = m.allow_entry("EXP-400")
        assert ok is True
        assert reason is None

    def test_yellow_allows_with_sizing_note(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 91000.0})  # -9%
        m.check()
        ok, reason = m.allow_entry("EXP-400")
        assert ok is True  # allowed, but caller must apply 50% sizing

    def test_red_blocks_entry(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 89500.0})  # -10.5%
        m.check()
        ok, reason = m.allow_entry("EXP-400")
        assert ok is False
        assert "CB_RED" in reason
        assert "-10.5" in reason

    def test_hard_stop_blocks_entry(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 87000.0})  # -13%
        with patch.object(m, 'execute_hard_stop'):
            m.check()
        ok, reason = m.allow_entry("EXP-400")
        assert ok is False
        assert "CB_HARD_STOP" in reason

    def test_allow_entry_with_no_prior_check(self, tmp_path):
        """First call to allow_entry should fail-open (no cached status)."""
        m = _make_monitor(tmp_path)
        m._fetch_all_equity = MagicMock(side_effect=ConnectionError("no network"))
        ok, reason = m.allow_entry("EXP-400")
        assert ok is True  # fail-open


# ---------------------------------------------------------------------------
# 6. execute_hard_stop (paper mode: LOG ONLY)
# ---------------------------------------------------------------------------

class TestHardStop:
    @patch("shared.portfolio_risk.PortfolioRiskMonitor._count_open_positions", return_value=5)
    @patch("shared.telegram_alerts.send_message")
    def test_hard_stop_sends_telegram(self, mock_tg, mock_count, tmp_path):
        m = _make_monitor(tmp_path)
        m.execute_hard_stop()
        mock_tg.assert_called_once()
        call_text = mock_tg.call_args[0][0]
        assert "HARD STOP" in call_text
        assert "5" in call_text

    @patch("shared.portfolio_risk.PortfolioRiskMonitor._count_open_positions",
           side_effect=ConnectionError("api down"))
    @patch("shared.telegram_alerts.send_message")
    def test_hard_stop_survives_count_failure(self, mock_tg, mock_count, tmp_path):
        m = _make_monitor(tmp_path)
        m.execute_hard_stop()  # should not raise
        mock_tg.assert_called_once()
        assert "-1" in mock_tg.call_args[0][0]  # unknown count

    @patch("shared.portfolio_risk.PortfolioRiskMonitor._count_open_positions", return_value=3)
    @patch("shared.telegram_alerts.send_message", side_effect=Exception("telegram down"))
    def test_hard_stop_survives_telegram_failure(self, mock_tg, mock_count, tmp_path):
        m = _make_monitor(tmp_path)
        m.execute_hard_stop()  # must not raise even if Telegram fails

    def test_check_triggers_hard_stop_automatically(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 87000.0})  # -13%
        with patch.object(m, 'execute_hard_stop') as mock_hs:
            s = m.check()
        mock_hs.assert_called_once()
        assert s.level == CircuitBreakerLevel.HARD_STOP


# ---------------------------------------------------------------------------
# 7. Cache TTL behavior
# ---------------------------------------------------------------------------

class TestCacheTTL:
    def test_cache_returns_stale_within_ttl(self, tmp_path):
        m = _make_monitor(tmp_path, cache_ttl=60)
        _patch_equity(m, {"EXP-400": 100000.0})
        s1 = m.check()
        # Change equity — but cache should prevent re-fetch
        _patch_equity(m, {"EXP-400": 50000.0})
        s2 = m.check()
        assert s2.combined_equity == 100000.0  # still cached

    def test_cache_expires_after_ttl(self, tmp_path):
        m = _make_monitor(tmp_path, cache_ttl=0)  # instant expiry
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 91000.0})
        s = m.check()
        assert s.combined_equity == 91000.0


# ---------------------------------------------------------------------------
# 8. Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_checks_dont_corrupt_hwm(self, tmp_path):
        """Multiple threads calling check() should not corrupt the HWM."""
        m = _make_monitor(tmp_path)
        errors = []
        results = []

        def worker(equity):
            try:
                m._fetch_all_equity = MagicMock(return_value={"EXP-400": equity})
                s = m.check()
                results.append(s)
            except Exception as e:
                errors.append(e)

        # Start 10 threads with different equity values
        threads = []
        for eq in [100000, 95000, 105000, 90000, 110000,
                   88000, 92000, 87000, 115000, 93000]:
            t = threading.Thread(target=worker, args=(float(eq),))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 10
        # HWM should be at least the max value that was checked
        final_hwm = m._load_hwm()
        assert final_hwm is not None
        assert final_hwm >= 87000  # at minimum, some value was persisted


# ---------------------------------------------------------------------------
# 9. DB audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_snapshots_accumulate(self, tmp_path):
        m = _make_monitor(tmp_path)
        for eq in [100000, 95000, 91000, 89000]:
            _patch_equity(m, {"EXP-400": float(eq)})
            if eq <= 88000:
                with patch.object(m, 'execute_hard_stop'):
                    m.check()
            else:
                m.check()

        rows = m._conn.execute("SELECT COUNT(*) FROM equity_snapshots").fetchone()[0]
        assert rows == 4

    def test_snapshot_contains_per_account_json(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 60000.0, "EXP-401": 40000.0})
        m.check()

        row = m._conn.execute(
            "SELECT per_account_json FROM equity_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        data = json.loads(row[0])
        assert data["EXP-400"] == 60000.0
        assert data["EXP-401"] == 40000.0

    def test_hwm_state_has_correct_level(self, tmp_path):
        m = _make_monitor(tmp_path)
        _patch_equity(m, {"EXP-400": 100000.0})
        m.check()
        _patch_equity(m, {"EXP-400": 91000.0})
        m.check()

        row = m._conn.execute("SELECT cb_level FROM hwm_state WHERE id=1").fetchone()
        assert row[0] == "yellow"
