"""Tests for compass/backtest_vs_live_tracker.py."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.backtest_vs_live_tracker import (
    BacktestBaseline,
    BacktestVsLiveTracker,
    DriftAlert,
    DriftMetric,
    LiveTrade,
    TrackerResult,
    assess_health,
    build_live_equity,
    compute_drift,
    compute_live_metrics,
    generate_alerts,
    load_trades_from_dataframe,
    load_trades_from_db,
    project_backtest_equity,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_trades_df(n: int = 40, seed: int = 42, win_rate: float = 0.85) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2026-04-01", periods=n)
    wins = rng.random(n) < win_rate
    pnl = np.where(wins, rng.uniform(200, 1500, n), rng.uniform(-800, -100, n))
    return pd.DataFrame({
        "entry_date": dates,
        "exit_date": dates + pd.Timedelta(days=3),
        "pnl": pnl,
        "strategy_type": "CS",
        "regime": rng.choice(["bull", "sideways"], n),
    })


def _make_bad_trades_df(n: int = 30, seed: int = 99) -> pd.DataFrame:
    """Trades that are much worse than backtest baseline."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2026-04-01", periods=n)
    wins = rng.random(n) < 0.40  # terrible win rate
    pnl = np.where(wins, rng.uniform(50, 300, n), rng.uniform(-500, -100, n))
    return pd.DataFrame({
        "entry_date": dates,
        "exit_date": dates + pd.Timedelta(days=3),
        "pnl": pnl,
        "strategy_type": "CS",
        "regime": "bear",
    })


def _create_test_db(db_path: Path, n: int = 20, seed: int = 42):
    """Create a SQLite DB with trades table."""
    rng = np.random.RandomState(seed)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            entry_date TEXT,
            exit_date TEXT,
            pnl REAL,
            strategy_type TEXT,
            regime TEXT
        )
    """)
    dates = pd.bdate_range("2026-04-01", periods=n)
    for i in range(n):
        win = rng.random() < 0.85
        pnl = rng.uniform(200, 1200) if win else rng.uniform(-600, -100)
        conn.execute(
            "INSERT INTO trades (entry_date, exit_date, pnl, strategy_type, regime) VALUES (?,?,?,?,?)",
            (str(dates[i].date()), str((dates[i] + pd.Timedelta(days=3)).date()), pnl, "CS", "bull"),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def baseline():
    return BacktestBaseline()


@pytest.fixture
def good_trades():
    return _make_trades_df()


@pytest.fixture
def bad_trades():
    return _make_bad_trades_df()


# ── Baseline tests ───────────────────────────────────────────────────────


class TestBaseline:
    def test_defaults(self):
        bl = BacktestBaseline()
        assert bl.cagr_pct == 76.9
        assert bl.sharpe == 4.97
        assert bl.max_dd_pct == 10.2
        assert bl.win_rate == 0.87

    def test_custom(self):
        bl = BacktestBaseline(experiment_id="TEST", cagr_pct=50.0, sharpe=3.0)
        assert bl.experiment_id == "TEST"
        assert bl.cagr_pct == 50.0


# ── Trade loading tests ──────────────────────────────────────────────────


class TestTradeLoading:
    def test_from_dataframe(self, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        assert len(trades) == 40
        assert all(isinstance(t, LiveTrade) for t in trades)

    def test_from_dataframe_has_pnl(self, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        assert any(t.pnl != 0 for t in trades)

    def test_from_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _create_test_db(db_path, n=15)
            trades = load_trades_from_db(db_path)
            assert len(trades) == 15
            assert all(isinstance(t, LiveTrade) for t in trades)

    def test_from_missing_db(self):
        trades = load_trades_from_db(Path("/nonexistent/db.sqlite"))
        assert trades == []

    def test_from_empty_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE other (id INTEGER)")
            conn.close()
            trades = load_trades_from_db(db_path)
            assert trades == []


# ── Live metrics tests ───────────────────────────────────────────────────


class TestLiveMetrics:
    def test_compute_basic(self, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        m = compute_live_metrics(trades, 100_000)
        assert m["n_trades"] == 40
        assert m["total_pnl"] != 0
        assert 0 <= m["win_rate"] <= 1

    def test_sharpe_positive_for_good(self, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        m = compute_live_metrics(trades, 100_000)
        assert m["sharpe"] > 0

    def test_max_dd_non_negative(self, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        m = compute_live_metrics(trades, 100_000)
        assert m["max_dd_pct"] >= 0

    def test_empty_trades(self):
        m = compute_live_metrics([], 100_000)
        assert m["n_trades"] == 0
        assert m["sharpe"] == 0

    def test_profit_factor_positive(self, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        m = compute_live_metrics(trades, 100_000)
        assert m["profit_factor"] > 0


# ── Drift computation tests ──────────────────────────────────────────────


class TestDrift:
    def test_good_performance_within_tolerance(self, baseline, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        live = compute_live_metrics(trades, baseline.capital)
        drifts = compute_drift(baseline, live, 30.0)
        assert len(drifts) == 5
        assert all(isinstance(d, DriftMetric) for d in drifts)

    def test_bad_performance_outside_tolerance(self, baseline, bad_trades):
        trades = load_trades_from_dataframe(bad_trades)
        live = compute_live_metrics(trades, baseline.capital)
        drifts = compute_drift(baseline, live, 30.0)
        # Bad trades should have at least some metrics outside tolerance
        outside = [d for d in drifts if not d.within_tolerance]
        assert len(outside) > 0

    def test_drift_has_all_metrics(self, baseline, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        live = compute_live_metrics(trades, baseline.capital)
        drifts = compute_drift(baseline, live)
        names = {d.metric_name for d in drifts}
        assert "win_rate" in names
        assert "sharpe" in names
        assert "max_dd_pct" in names

    def test_perfect_match_all_within(self):
        bl = BacktestBaseline(win_rate=0.80, sharpe=3.0, max_dd_pct=5.0,
                              profit_factor=2.0, avg_pnl_per_trade=500)
        live = {"win_rate": 0.80, "sharpe": 3.0, "max_dd_pct": 5.0,
                "profit_factor": 2.0, "avg_pnl": 500}
        drifts = compute_drift(bl, live)
        assert all(d.within_tolerance for d in drifts)
        assert all(abs(d.relative_diff_pct) < 0.01 for d in drifts)


# ── Alert generation tests ───────────────────────────────────────────────


class TestAlerts:
    def test_no_alerts_good_performance(self):
        drifts = [
            DriftMetric("win_rate", 0.87, 0.85, -0.02, -2.3, True, 30),
            DriftMetric("sharpe", 4.97, 4.50, -0.47, -9.5, True, 30),
        ]
        alerts = generate_alerts(drifts)
        assert len(alerts) == 0

    def test_alert_on_bad_drift(self):
        drifts = [
            DriftMetric("win_rate", 0.87, 0.50, -0.37, -42.5, False, 30),
            DriftMetric("sharpe", 4.97, 1.0, -3.97, -79.9, False, 30),
        ]
        alerts = generate_alerts(drifts, warning_pct=30, critical_pct=50)
        assert len(alerts) == 2
        critical = [a for a in alerts if a.severity == "critical"]
        assert len(critical) >= 1  # sharpe dropped >50%

    def test_no_alert_when_outperforming(self):
        drifts = [
            DriftMetric("sharpe", 4.97, 8.0, 3.03, 61.0, True, 30),  # better!
        ]
        alerts = generate_alerts(drifts)
        assert len(alerts) == 0  # outperforming is not an alert

    def test_dd_alert_when_higher(self):
        drifts = [
            DriftMetric("max_dd_pct", 10.2, 18.0, 7.8, 76.5, False, 30),
        ]
        alerts = generate_alerts(drifts, warning_pct=30, critical_pct=50)
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_alert_severity_levels(self):
        drifts = [
            DriftMetric("win_rate", 0.87, 0.60, -0.27, -31.0, False, 30),  # 31% → warning
            DriftMetric("sharpe", 4.97, 1.0, -3.97, -79.9, False, 30),     # 80% → critical
        ]
        alerts = generate_alerts(drifts, warning_pct=30, critical_pct=50)
        severities = {a.severity for a in alerts}
        assert "warning" in severities
        assert "critical" in severities


# ── Health assessment tests ──────────────────────────────────────────────


class TestHealth:
    def test_healthy(self):
        assert assess_health([]) == "healthy"

    def test_degraded_on_warnings(self):
        alerts = [
            DriftAlert("a", "warning", "msg", 1, 0.5, -50),
            DriftAlert("b", "warning", "msg", 1, 0.5, -50),
            DriftAlert("c", "warning", "msg", 1, 0.5, -50),
        ]
        assert assess_health(alerts) == "degraded"

    def test_degraded_on_one_critical(self):
        alerts = [DriftAlert("a", "critical", "msg", 1, 0.5, -80)]
        assert assess_health(alerts) == "degraded"

    def test_critical_on_two_critical(self):
        alerts = [
            DriftAlert("a", "critical", "msg", 1, 0.5, -80),
            DriftAlert("b", "critical", "msg", 1, 0.5, -80),
        ]
        assert assess_health(alerts) == "critical"


# ── Equity curve tests ───────────────────────────────────────────────────


class TestEquityCurves:
    def test_backtest_projection_length(self, baseline):
        eq = project_backtest_equity(baseline, 20)
        assert len(eq) == 21  # capital + 20 trades

    def test_backtest_projection_starts_at_capital(self, baseline):
        eq = project_backtest_equity(baseline, 10)
        assert eq[0] == baseline.capital

    def test_live_equity_length(self, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        eq = build_live_equity(trades, 100_000)
        assert len(eq) == len(trades) + 1

    def test_live_equity_starts_at_capital(self, good_trades):
        trades = load_trades_from_dataframe(good_trades)
        eq = build_live_equity(trades, 100_000)
        assert eq[0] == 100_000

    def test_zero_trades(self, baseline):
        eq = project_backtest_equity(baseline, 0)
        assert eq == [baseline.capital]


# ── Full tracker tests ───────────────────────────────────────────────────


class TestFullTracker:
    def test_evaluate_good(self, good_trades):
        tracker = BacktestVsLiveTracker(trades_df=good_trades)
        result = tracker.evaluate()
        assert isinstance(result, TrackerResult)
        assert result.live_n_trades == 40
        assert result.overall_health in ("healthy", "degraded", "critical")

    def test_evaluate_bad_triggers_alerts(self, bad_trades):
        tracker = BacktestVsLiveTracker(trades_df=bad_trades)
        result = tracker.evaluate()
        assert result.n_alerts > 0
        assert result.overall_health in ("degraded", "critical")

    def test_evaluate_from_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _create_test_db(db_path, n=25)
            tracker = BacktestVsLiveTracker(db_path=db_path)
            result = tracker.evaluate()
            assert result.live_n_trades == 25

    def test_evaluate_empty(self):
        tracker = BacktestVsLiveTracker(trades_df=pd.DataFrame(columns=["entry_date", "exit_date", "pnl"]))
        result = tracker.evaluate()
        assert result.live_n_trades == 0
        assert result.overall_health == "healthy"

    def test_custom_baseline(self, good_trades):
        bl = BacktestBaseline(sharpe=1.0, win_rate=0.50, max_dd_pct=20.0)
        tracker = BacktestVsLiveTracker(baseline=bl, trades_df=good_trades)
        result = tracker.evaluate()
        # Good trades against low baseline should be healthy
        assert result.overall_health == "healthy"

    def test_custom_tolerance(self, bad_trades):
        # Very tight tolerance should trigger more alerts
        tracker = BacktestVsLiveTracker(
            trades_df=bad_trades, tolerance_pct=10.0, warning_pct=10.0,
        )
        r_tight = tracker.evaluate()

        tracker2 = BacktestVsLiveTracker(
            trades_df=bad_trades, tolerance_pct=90.0, warning_pct=90.0,
        )
        r_loose = tracker2.evaluate()

        assert r_tight.n_alerts >= r_loose.n_alerts

    def test_equity_curves_populated(self, good_trades):
        tracker = BacktestVsLiveTracker(trades_df=good_trades)
        result = tracker.evaluate()
        assert len(result.backtest_equity) > 0
        assert len(result.live_equity) > 0
        assert len(result.live_equity) == result.live_n_trades + 1


# ── HTML report tests ────────────────────────────────────────────────────


class TestHTMLReport:
    def test_generates_file(self, good_trades):
        tracker = BacktestVsLiveTracker(trades_df=good_trades)
        result = tracker.evaluate()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "cmp.html"
            path = BacktestVsLiveTracker.generate_report(result, out)
            assert path.exists()
            content = path.read_text()
            assert "Backtest vs Live" in content

    def test_contains_equity_chart(self, good_trades):
        tracker = BacktestVsLiveTracker(trades_df=good_trades)
        result = tracker.evaluate()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            BacktestVsLiveTracker.generate_report(result, out)
            content = out.read_text()
            assert "<svg" in content
            assert "Backtest" in content
            assert "Live" in content

    def test_contains_drift_table(self, good_trades):
        tracker = BacktestVsLiveTracker(trades_df=good_trades)
        result = tracker.evaluate()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            BacktestVsLiveTracker.generate_report(result, out)
            content = out.read_text()
            assert "Drift" in content
            assert "win_rate" in content

    def test_contains_health_status(self, good_trades):
        tracker = BacktestVsLiveTracker(trades_df=good_trades)
        result = tracker.evaluate()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            BacktestVsLiveTracker.generate_report(result, out)
            content = out.read_text()
            assert result.overall_health.upper() in content

    def test_default_path(self, good_trades):
        tracker = BacktestVsLiveTracker(trades_df=good_trades)
        result = tracker.evaluate()
        path = BacktestVsLiveTracker.generate_report(result)
        assert path.exists()
        assert "backtest_vs_live.html" in str(path)
        path.unlink(missing_ok=True)
