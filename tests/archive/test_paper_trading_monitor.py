"""Tests for compass/paper_trading_monitor.py — 50+ tests."""

import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compass.paper_trading_monitor import (
    Trade, Alert, BacktestBenchmark, PreflightResult,
    PaperTradingMonitor,
    check_ironvault_freshness, check_alpaca_connectivity,
    check_polygon_status, check_config_exists, check_python_deps,
    run_preflight,
)


# ═══════════════════════════════════════════════════════════════════════════
# Trade dataclass
# ═══════════════════════════════════════════════════════════════════════════

class TestTrade:
    def test_create_trade(self):
        t = Trade(trade_id="T001", entry_date="2024-01-15", ticker="SPY")
        assert t.trade_id == "T001"
        assert t.status == "open"
        assert t.pnl == 0.0

    def test_trade_defaults(self):
        t = Trade(trade_id="T002", entry_date="2024-01-15")
        assert t.ticker == "SPY"
        assert t.contracts == 1
        assert t.exit_date == ""

    def test_trade_with_pnl(self):
        t = Trade(trade_id="T003", entry_date="2024-01-15",
                  exit_date="2024-01-22", pnl=250.0, status="closed")
        assert t.pnl == 250.0
        assert t.status == "closed"


# ═══════════════════════════════════════════════════════════════════════════
# BacktestBenchmark
# ═══════════════════════════════════════════════════════════════════════════

class TestBenchmark:
    def test_default_values(self):
        b = BacktestBenchmark()
        assert b.sharpe == 4.10
        assert b.cagr == 0.556
        assert b.max_dd == 0.072
        assert b.win_rate == 0.75
        assert b.hedge_cost_annual == 0.0436

    def test_custom_values(self):
        b = BacktestBenchmark(sharpe=3.0, cagr=0.30)
        assert b.sharpe == 3.0
        assert b.cagr == 0.30


# ═══════════════════════════════════════════════════════════════════════════
# PaperTradingMonitor — core
# ═══════════════════════════════════════════════════════════════════════════

class TestMonitorCore:
    def _make_monitor(self):
        return PaperTradingMonitor(capital=100_000)

    def test_init(self):
        m = self._make_monitor()
        assert m.capital == 100_000
        assert len(m.trades) == 0
        assert len(m.alerts) == 0

    def test_add_trade(self):
        m = self._make_monitor()
        m.add_trade(Trade(trade_id="T1", entry_date="2024-01-15"))
        assert len(m.trades) == 1
        assert len(m.open_trades) == 1
        assert len(m.closed_trades) == 0

    def test_close_trade(self):
        m = self._make_monitor()
        m.add_trade(Trade(trade_id="T1", entry_date="2024-01-15"))
        m.close_trade("T1", "2024-01-22", 300.0, "profit_target")
        assert len(m.open_trades) == 0
        assert len(m.closed_trades) == 1
        assert m.closed_trades[0].pnl == 300.0
        assert m.closed_trades[0].exit_reason == "profit_target"

    def test_close_nonexistent(self):
        m = self._make_monitor()
        m.close_trade("NOPE", "2024-01-22", 100.0)  # should not crash

    def test_multiple_trades(self):
        m = self._make_monitor()
        for i in range(10):
            m.add_trade(Trade(trade_id=f"T{i}", entry_date=f"2024-01-{15+i:02d}",
                              pnl=100.0 * (1 if i % 3 != 0 else -1), status="closed",
                              exit_date=f"2024-01-{20+i:02d}"))
        assert len(m.closed_trades) == 10


# ═══════════════════════════════════════════════════════════════════════════
# Metrics computation
# ═══════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def _monitor_with_trades(self, pnls):
        m = PaperTradingMonitor(capital=100_000)
        for i, pnl in enumerate(pnls):
            m.add_trade(Trade(
                trade_id=f"T{i}", entry_date=f"2024-{(i//28)+1:02d}-{(i%28)+1:02d}",
                exit_date=f"2024-{(i//28)+1:02d}-{(i%28)+5:02d}",
                pnl=pnl, status="closed",
            ))
        return m

    def test_empty_metrics(self):
        m = PaperTradingMonitor(capital=100_000)
        metrics = m.compute_metrics()
        assert metrics["n_trades"] == 0
        assert metrics["total_pnl"] == 0
        assert metrics["equity"] == 100_000

    def test_positive_pnl(self):
        m = self._monitor_with_trades([200, 300, 150, -50, 100])
        metrics = m.compute_metrics()
        assert metrics["n_trades"] == 5
        assert metrics["total_pnl"] == 700
        assert metrics["win_rate"] == 0.8
        assert metrics["equity"] == 100_700

    def test_all_losses(self):
        m = self._monitor_with_trades([-100, -200, -50])
        metrics = m.compute_metrics()
        assert metrics["total_pnl"] == -350
        assert metrics["win_rate"] == 0.0
        assert metrics["max_dd"] > 0

    def test_drawdown_calculation(self):
        m = self._monitor_with_trades([500, -300, -200, 100])
        metrics = m.compute_metrics()
        # Peak = 100500, trough = 100000
        assert metrics["max_dd"] > 0.003  # at least 0.3%

    def test_sharpe_positive(self):
        m = self._monitor_with_trades([100] * 20)
        metrics = m.compute_metrics()
        # All wins, no variance → sharpe = 0 (std=0)
        # Actually std=0 → sharpe=0
        assert metrics["sharpe"] == 0.0

    def test_sharpe_mixed(self):
        m = self._monitor_with_trades([200, -50, 300, -100, 150, 250, -30, 180])
        metrics = m.compute_metrics()
        assert metrics["sharpe"] > 0  # positive mean with variance

    def test_open_trades_count(self):
        m = PaperTradingMonitor(capital=100_000)
        m.add_trade(Trade(trade_id="T1", entry_date="2024-01-15", status="open"))
        m.add_trade(Trade(trade_id="T2", entry_date="2024-01-15",
                          exit_date="2024-01-20", pnl=100, status="closed"))
        metrics = m.compute_metrics()
        assert metrics["n_trades"] == 1  # only closed
        assert metrics["n_open"] == 1

    def test_peak_equity(self):
        m = self._monitor_with_trades([1000, -500, 200])
        metrics = m.compute_metrics()
        assert metrics["peak_equity"] == 101_000


# ═══════════════════════════════════════════════════════════════════════════
# Alert system
# ═══════════════════════════════════════════════════════════════════════════

class TestAlerts:
    def _monitor_with_n_trades(self, n, avg_pnl=150.0, win_pct=0.75):
        m = PaperTradingMonitor(
            benchmark=BacktestBenchmark(win_rate=0.75, avg_pnl=150.0),
            alert_threshold_sigma=2.0,
            capital=100_000,
        )
        wins = int(n * win_pct)
        for i in range(n):
            pnl = avg_pnl if i < wins else -avg_pnl * 2
            m.add_trade(Trade(
                trade_id=f"T{i}", entry_date=f"2024-{(i//28)+1:02d}-{(i%28)+1:02d}",
                exit_date=f"2024-{(i//28)+1:02d}-{(i%28)+5:02d}",
                pnl=pnl, status="closed",
            ))
        return m

    def test_no_alerts_few_trades(self):
        m = self._monitor_with_n_trades(3)
        alerts = m.check_alerts()
        assert len(alerts) == 0  # <5 trades → skip

    def test_no_alerts_matching_benchmark(self):
        m = self._monitor_with_n_trades(20, avg_pnl=150.0, win_pct=0.75)
        alerts = m.check_alerts()
        # Should have 0 or very few alerts since we match benchmark
        critical = [a for a in alerts if a.severity == "CRITICAL"]
        assert len(critical) == 0

    def test_alert_on_low_win_rate(self):
        m = self._monitor_with_n_trades(30, avg_pnl=100.0, win_pct=0.30)
        alerts = m.check_alerts()
        wr_alerts = [a for a in alerts if a.metric == "win_rate"]
        assert len(wr_alerts) > 0
        assert wr_alerts[0].actual < 0.50

    def test_alert_on_negative_avg_pnl(self):
        m = PaperTradingMonitor(
            benchmark=BacktestBenchmark(avg_pnl=150.0),
            capital=100_000,
        )
        for i in range(15):
            m.add_trade(Trade(
                trade_id=f"T{i}", entry_date=f"2024-01-{i+1:02d}",
                exit_date=f"2024-01-{i+5:02d}",
                pnl=-200.0, status="closed",
            ))
        alerts = m.check_alerts()
        pnl_alerts = [a for a in alerts if a.metric == "avg_pnl"]
        assert len(pnl_alerts) > 0

    def test_alert_on_excessive_dd(self):
        m = PaperTradingMonitor(
            benchmark=BacktestBenchmark(max_dd=0.05),
            capital=100_000,
        )
        # Create a big drawdown
        for i in range(10):
            m.add_trade(Trade(
                trade_id=f"T{i}", entry_date=f"2024-{(i//28)+1:02d}-{(i%28)+1:02d}",
                exit_date=f"2024-{(i//28)+1:02d}-{(i%28)+5:02d}",
                pnl=-2000.0, status="closed",
            ))
        alerts = m.check_alerts()
        dd_alerts = [a for a in alerts if a.metric == "max_drawdown"]
        assert len(dd_alerts) > 0

    def test_alert_severity_levels(self):
        alert = Alert(
            timestamp="2024-01-15", severity="CRITICAL",
            metric="win_rate", expected=0.75, actual=0.30,
            deviation_sigma=-3.5, message="test",
        )
        assert alert.severity == "CRITICAL"
        assert alert.deviation_sigma == -3.5

    def test_alerts_accumulate(self):
        m = self._monitor_with_n_trades(20, avg_pnl=-500.0, win_pct=0.20)
        alerts1 = m.check_alerts()
        alerts2 = m.check_alerts()
        assert len(m.alerts) == len(alerts1) + len(alerts2)


# ═══════════════════════════════════════════════════════════════════════════
# Status report
# ═══════════════════════════════════════════════════════════════════════════

class TestStatusReport:
    def test_empty_report(self):
        m = PaperTradingMonitor()
        report = m.status_report()
        assert "Paper Trading Monitor" in report
        assert "0 closed" in report

    def test_report_with_trades(self):
        m = PaperTradingMonitor()
        for i in range(5):
            m.add_trade(Trade(
                trade_id=f"T{i}", entry_date=f"2024-01-{i+10:02d}",
                exit_date=f"2024-01-{i+15:02d}", pnl=100.0, status="closed",
            ))
        report = m.status_report()
        assert "5 closed" in report
        assert "$500" in report

    def test_report_includes_alerts(self):
        m = PaperTradingMonitor(
            benchmark=BacktestBenchmark(win_rate=0.90, avg_pnl=500),
        )
        for i in range(10):
            m.add_trade(Trade(
                trade_id=f"T{i}", entry_date=f"2024-01-{i+1:02d}",
                exit_date=f"2024-01-{i+5:02d}", pnl=-300, status="closed",
            ))
        m.check_alerts()
        report = m.status_report()
        assert "Alert" in report or "alert" in report.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_save_state(self, tmp_path):
        m = PaperTradingMonitor(capital=50_000)
        m.add_trade(Trade(trade_id="T1", entry_date="2024-01-15",
                          exit_date="2024-01-20", pnl=300, status="closed"))
        path = tmp_path / "monitor_state.json"
        m.save_state(path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["capital"] == 50_000
        assert len(data["trades"]) == 1
        assert data["trades"][0]["pnl"] == 300

    def test_load_state(self, tmp_path):
        m = PaperTradingMonitor(capital=75_000)
        m.add_trade(Trade(trade_id="T1", entry_date="2024-01-15",
                          exit_date="2024-01-20", pnl=500, status="closed"))
        m.add_trade(Trade(trade_id="T2", entry_date="2024-01-22", status="open"))
        path = tmp_path / "state.json"
        m.save_state(path)

        loaded = PaperTradingMonitor.load_state(path)
        assert loaded.capital == 75_000
        assert len(loaded.trades) == 2
        assert loaded.closed_trades[0].pnl == 500

    def test_save_includes_metrics(self, tmp_path):
        m = PaperTradingMonitor()
        m.add_trade(Trade(trade_id="T1", entry_date="2024-01-15",
                          exit_date="2024-01-20", pnl=200, status="closed"))
        path = tmp_path / "state.json"
        m.save_state(path)
        data = json.loads(path.read_text())
        assert "metrics" in data
        assert data["metrics"]["total_pnl"] == 200

    def test_round_trip(self, tmp_path):
        m = PaperTradingMonitor(
            benchmark=BacktestBenchmark(sharpe=5.0, cagr=0.80),
            capital=200_000,
        )
        for i in range(8):
            m.add_trade(Trade(
                trade_id=f"T{i}", entry_date=f"2024-02-{i+1:02d}",
                exit_date=f"2024-02-{i+5:02d}", pnl=150.0 * (1 if i % 2 == 0 else -0.5),
                status="closed",
            ))
        path = tmp_path / "round_trip.json"
        m.save_state(path)
        loaded = PaperTradingMonitor.load_state(path)
        assert len(loaded.trades) == 8
        orig_metrics = m.compute_metrics()
        loaded_metrics = loaded.compute_metrics()
        assert orig_metrics["total_pnl"] == loaded_metrics["total_pnl"]


# ═══════════════════════════════════════════════════════════════════════════
# Pre-flight checks
# ═══════════════════════════════════════════════════════════════════════════

class TestPreflight:
    def test_python_deps(self):
        result = check_python_deps()
        assert result.passed is True
        assert result.name == "Python Deps"

    def test_ironvault_freshness_real_db(self):
        db = ROOT / "data" / "options_cache.db"
        if db.exists():
            result = check_ironvault_freshness(str(db))
            assert result.passed is True
            assert "MB" in result.message or "current" in result.message.lower() or "old" in result.message.lower()

    def test_ironvault_missing_db(self):
        result = check_ironvault_freshness("/nonexistent/db.sqlite")
        assert result.passed is False
        assert result.severity == "CRITICAL"

    def test_ironvault_small_db(self, tmp_path):
        tiny_db = tmp_path / "tiny.db"
        tiny_db.write_bytes(b"x" * 100)
        result = check_ironvault_freshness(str(tiny_db))
        assert result.passed is False

    def test_alpaca_no_env(self):
        result = check_alpaca_connectivity("/nonexistent/.env.ultimate")
        # Will fail unless ALPACA_API_KEY is in environment
        if not os.getenv("ALPACA_API_KEY"):
            assert result.passed is False

    def test_alpaca_with_env(self, tmp_path):
        env = tmp_path / ".env.test"
        env.write_text("ALPACA_API_KEY=PK1234567890ABCDEFGHIJ\nALPACA_API_SECRET=secret\n")
        result = check_alpaca_connectivity(str(env))
        assert result.passed is True

    def test_polygon_status(self):
        result = check_polygon_status()
        # May pass or fail depending on env, just check it returns a result
        assert isinstance(result, PreflightResult)

    def test_config_exists(self):
        result = check_config_exists(str(ROOT / "configs" / "paper_ultimate_v4.yaml"))
        # May or may not exist
        assert isinstance(result, PreflightResult)

    def test_config_missing(self):
        result = check_config_exists("/nonexistent/config.yaml")
        assert result.passed is False

    def test_run_preflight(self):
        results = run_preflight()
        assert len(results) >= 4  # at least 4 checks
        assert all(isinstance(r, PreflightResult) for r in results)

    def test_preflight_result_dataclass(self):
        r = PreflightResult("test", True, "all good")
        assert r.name == "test"
        assert r.passed is True
        assert r.severity == "INFO"


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_single_trade(self):
        m = PaperTradingMonitor()
        m.add_trade(Trade(trade_id="T1", entry_date="2024-06-15",
                          exit_date="2024-06-20", pnl=500, status="closed"))
        metrics = m.compute_metrics()
        assert metrics["n_trades"] == 1
        assert metrics["total_pnl"] == 500

    def test_zero_pnl_trades(self):
        m = PaperTradingMonitor()
        for i in range(5):
            m.add_trade(Trade(trade_id=f"T{i}", entry_date=f"2024-01-{i+1:02d}",
                              exit_date=f"2024-01-{i+5:02d}", pnl=0, status="closed"))
        metrics = m.compute_metrics()
        assert metrics["total_pnl"] == 0
        assert metrics["win_rate"] == 0.0  # 0 is not > 0

    def test_large_loss(self):
        m = PaperTradingMonitor(capital=100_000)
        m.add_trade(Trade(trade_id="T0", entry_date="2024-01-10",
                          exit_date="2024-01-12", pnl=1000, status="closed"))
        m.add_trade(Trade(trade_id="T1", entry_date="2024-01-15",
                          exit_date="2024-01-20", pnl=-50_000, status="closed"))
        metrics = m.compute_metrics()
        assert metrics["equity"] == 51_000  # 100K + 1K - 50K
        assert metrics["max_dd"] > 0.3  # peak was 101K, trough 51K → ~49.5% DD

    def test_benchmark_hedge_cost(self):
        b = BacktestBenchmark(hedge_cost_annual=0.0436)
        assert b.hedge_cost_annual == 0.0436  # 4.36% annual

    def test_alert_threshold_custom(self):
        m = PaperTradingMonitor(alert_threshold_sigma=1.0)
        assert m.alert_sigma == 1.0

    def test_mixed_open_closed(self):
        m = PaperTradingMonitor()
        m.add_trade(Trade(trade_id="T1", entry_date="2024-01-15",
                          pnl=0, status="open"))
        m.add_trade(Trade(trade_id="T2", entry_date="2024-01-16",
                          exit_date="2024-01-20", pnl=300, status="closed"))
        m.add_trade(Trade(trade_id="T3", entry_date="2024-01-17",
                          pnl=0, status="open"))
        assert len(m.open_trades) == 2
        assert len(m.closed_trades) == 1
        metrics = m.compute_metrics()
        assert metrics["n_trades"] == 1
        assert metrics["n_open"] == 2

    def test_save_creates_parent_dirs(self, tmp_path):
        m = PaperTradingMonitor()
        m.add_trade(Trade(trade_id="T1", entry_date="2024-01-15",
                          exit_date="2024-01-20", pnl=100, status="closed"))
        path = tmp_path / "deep" / "nested" / "state.json"
        m.save_state(path)
        assert path.exists()

    def test_preflight_returns_list(self):
        results = run_preflight(env_file="/nonexistent", config_path="/nonexistent")
        assert isinstance(results, list)
        assert len(results) >= 4
        # At least python deps should pass
        py_check = next(r for r in results if r.name == "Python Deps")
        assert py_check.passed is True
