"""Tests for scripts.monitor_exp880 — EXP-880 paper trading monitor."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

# Add scripts to path
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from monitor_exp880 import (
    BACKTEST_EXPECTATIONS,
    DD_CRIT_PCT,
    DD_WARN_PCT,
    AlpacaReader,
    EXP880Monitor,
    HedgeEvent,
    MonitorSnapshot,
    Position,
    TradeHistoryDB,
    TradeRecord,
    compute_deviations,
    generate_alerts,
    send_hedge_alert,
    send_trade_alert,
)


# ── AlpacaReader ────────────────────────────────────────────────────────────
class TestAlpacaReader:
    def test_not_connected_by_default(self):
        r = AlpacaReader()
        assert not r.is_connected

    def test_connect_without_keys_returns_false(self):
        r = AlpacaReader(api_key="", api_secret="")
        assert not r.connect()

    def test_mock_account(self):
        r = AlpacaReader()
        acct = r.get_account()
        assert acct["equity"] == 100_000.0

    def test_mock_positions_empty(self):
        r = AlpacaReader()
        assert r.get_positions() == []

    def test_mock_orders_empty(self):
        r = AlpacaReader()
        assert r.get_orders() == []


# ── TradeHistoryDB ──────────────────────────────────────────────────────────
class TestTradeHistoryDB:
    def test_nonexistent_db(self):
        db = TradeHistoryDB(db_path="/tmp/nonexistent_xyz.db")
        assert not db.exists
        assert db.get_trades() == []
        assert db.get_hedge_events() == []

    def test_empty_equity(self):
        db = TradeHistoryDB(db_path="/tmp/nonexistent_xyz.db")
        assert db.get_daily_equity().empty


# ── Deviation Analysis ─────────────────────────────────────────────────────
class TestDeviations:
    def test_matching_values_ok(self):
        actual = dict(BACKTEST_EXPECTATIONS)
        devs = compute_deviations(actual)
        for d in devs.values():
            assert d["severity"] == "ok"

    def test_worse_sharpe_warns(self):
        actual = dict(BACKTEST_EXPECTATIONS)
        actual["sharpe"] = 3.0  # much lower than 4.97
        devs = compute_deviations(actual)
        assert devs["sharpe"]["severity"] in ("warning", "critical")

    def test_higher_dd_warns(self):
        actual = dict(BACKTEST_EXPECTATIONS)
        actual["max_dd_pct"] = 20.0  # much higher than 10.2
        devs = compute_deviations(actual)
        assert devs["max_dd_pct"]["severity"] in ("warning", "critical")

    def test_deviation_pct_calculated(self):
        actual = {"cagr_pct": 50.0}
        devs = compute_deviations(actual, {"cagr_pct": 76.9})
        assert devs["cagr_pct"]["deviation_pct"] < 0  # worse than expected

    def test_better_than_expected_ok(self):
        actual = {"sharpe": 6.0}
        devs = compute_deviations(actual, {"sharpe": 4.97})
        assert devs["sharpe"]["severity"] == "ok"


# ── Alert Generation ────────────────────────────────────────────────────────
class TestAlerts:
    def _snap(self, dd: float = 0, scale: float = 1.0) -> MonitorSnapshot:
        return MonitorSnapshot(
            timestamp="2024-01-01",
            equity=100_000,
            current_dd_pct=dd,
            current_hedge_scale=scale,
            deviations={},
        )

    def test_no_alerts_normal(self):
        alerts = generate_alerts(self._snap(dd=2.0))
        assert len(alerts) == 0

    def test_dd_warning(self):
        alerts = generate_alerts(self._snap(dd=6.0))
        assert any("DD" in a["message"] for a in alerts)
        assert any(a["level"] == "WARNING" for a in alerts)

    def test_dd_critical(self):
        alerts = generate_alerts(self._snap(dd=11.0))
        assert any(a["level"] == "CRITICAL" for a in alerts)

    def test_dd_halt(self):
        alerts = generate_alerts(self._snap(dd=14.0))
        assert any("HALT" in a["message"] for a in alerts)

    def test_hedge_alert(self):
        alerts = generate_alerts(self._snap(scale=0.30))
        assert any("Hedge" in a["message"] for a in alerts)

    def test_deviation_alert(self):
        snap = self._snap()
        snap.deviations = {"sharpe": {"severity": "critical", "deviation_pct": -50}}
        alerts = generate_alerts(snap)
        assert any("sharpe" in a["message"] for a in alerts)


# ── Telegram Integration ───────────────────────────────────────────────────
class TestTelegram:
    def test_trade_alert_no_crash(self):
        trade = TradeRecord("t1", "2024-01-01", "2024-01-05", "SPY", "bull_put", 5, 1.0, 250, "profit_target")
        # Should not crash even without Telegram configured
        result = send_trade_alert(trade)
        assert isinstance(result, bool)

    def test_hedge_alert_no_crash(self):
        event = HedgeEvent("2024-01-01", 30.0, 0.40, "VIX spike", "high_vol", 0.05)
        result = send_hedge_alert(event)
        assert isinstance(result, bool)


# ── Monitor Core ────────────────────────────────────────────────────────────
class TestEXP880Monitor:
    def test_take_snapshot(self):
        monitor = EXP880Monitor()
        snap = monitor.take_snapshot()
        assert isinstance(snap, MonitorSnapshot)
        assert len(snap.timestamp) > 0

    def test_snapshot_equity(self):
        monitor = EXP880Monitor()
        snap = monitor.take_snapshot()
        assert snap.equity == 100_000.0  # mock Alpaca

    def test_snapshot_no_trades(self):
        monitor = EXP880Monitor()
        snap = monitor.take_snapshot()
        assert snap.total_trades == 0  # no DB

    def test_snapshot_deviations_computed(self):
        monitor = EXP880Monitor()
        snap = monitor.take_snapshot()
        assert len(snap.deviations) > 0

    def test_multiple_snapshots(self):
        monitor = EXP880Monitor()
        s1 = monitor.take_snapshot()
        s2 = monitor.take_snapshot()
        assert len(monitor._snapshots) == 2

    def test_generate_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = EXP880Monitor()
            snap = monitor.take_snapshot()
            path = monitor.generate_report(snap, output_path=str(Path(tmp) / "r.html"))
            assert path.exists()
            assert path.stat().st_size > 0

    def test_report_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = EXP880Monitor()
            snap = monitor.take_snapshot()
            path = monitor.generate_report(snap, output_path=str(Path(tmp) / "r.html"))
            html = path.read_text()
            assert "EXP-880" in html
            assert "Equity" in html
            assert "Drawdown" in html
            assert "Deviation" in html
            assert "Hedge" in html

    def test_report_valid_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = EXP880Monitor()
            snap = monitor.take_snapshot()
            path = monitor.generate_report(snap, output_path=str(Path(tmp) / "v.html"))
            html = path.read_text()
            assert html.startswith("<!DOCTYPE html>")
            assert "</html>" in html

    def test_report_white_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = EXP880Monitor()
            snap = monitor.take_snapshot()
            path = monitor.generate_report(snap, output_path=str(Path(tmp) / "w.html"))
            html = path.read_text()
            assert "background:#fff" in html

    def test_run_alerts_returns_count(self):
        monitor = EXP880Monitor()
        snap = monitor.take_snapshot()
        n = monitor.run_alerts(snap)
        assert isinstance(n, int)
        assert n >= 0


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_position(self):
        p = Position("SPY", 10, "long", 450.0, 455.0, 500.0, 4550.0)
        assert p.symbol == "SPY"

    def test_trade_record(self):
        t = TradeRecord("t1", "2024-01-01", "2024-01-05", "SPY", "bull_put", 5, 1.0, 250, "profit_target")
        assert t.pnl == 250

    def test_hedge_event(self):
        h = HedgeEvent("2024-01-01", 30.0, 0.40, "VIX spike")
        assert h.scale_factor == 0.40

    def test_monitor_snapshot_defaults(self):
        s = MonitorSnapshot(timestamp="now")
        assert s.equity == 0.0
        assert s.alerts == []
        assert s.positions == []
