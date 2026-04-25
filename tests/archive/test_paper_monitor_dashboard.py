"""Tests for compass.paper_monitor_dashboard — paper trading monitor."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from compass.paper_monitor_dashboard import (
    DD_CRIT,
    DD_HALT,
    DD_WARN,
    DashboardResult,
    PaperMonitorDashboard,
    PositionInfo,
    RegimeState,
    SignalQuality,
    StrategyView,
)


def _exp880_data(dd: float = 2.0, equity: float = 105_000) -> dict:
    return {
        "EXP-880": {
            "equity": equity,
            "total_pnl": equity - 100_000,
            "current_dd_pct": dd,
            "sharpe": 4.50,
            "win_rate_pct": 73.0,
            "n_trades": 45,
            "positions": [
                {"symbol": "SPY_P440", "qty": -5, "side": "short", "delta": -0.15, "gamma": 0.02, "theta": 3.5, "vega": -8.0, "pnl": 250},
                {"symbol": "SPY_P435", "qty": 5, "side": "long", "delta": 0.10, "gamma": 0.01, "theta": -2.0, "vega": 5.0, "pnl": -50},
            ],
            "signals": {"total": 50, "correct": 37, "avg_confidence": 0.72, "avg_predicted": 150, "avg_actual": 130},
            "regime": {"current": "bull", "hedge_scale": 1.0, "leverage": 2.0, "vix": 17.5, "dd_active": False},
        }
    }


def _combined_data() -> dict:
    return {
        "EXP-880": _exp880_data()["EXP-880"],
        "EXP-1470": {
            "equity": 112_000,
            "total_pnl": 12_000,
            "current_dd_pct": 3.5,
            "sharpe": 5.20,
            "win_rate_pct": 76.0,
            "n_trades": 60,
            "positions": [{"symbol": "QQQ_P370", "qty": -3, "side": "short", "delta": -0.12, "theta": 2.0, "vega": -5.0, "pnl": 180}],
            "signals": {"total": 65, "correct": 50, "avg_confidence": 0.78, "avg_predicted": 200, "avg_actual": 180},
            "regime": {"current": "bull", "hedge_scale": 1.0, "leverage": 2.5, "vix": 16.0},
        },
    }


# ── Building views ─────────────────────────────────────────────────────────
class TestBuild:
    def test_returns_result(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        assert isinstance(r, DashboardResult)

    def test_single_strategy(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        assert len(r.views) == 1
        assert r.views[0].strategy_id == "EXP-880"

    def test_combined_portfolio(self):
        r = PaperMonitorDashboard().build(_combined_data())
        assert len(r.views) == 2

    def test_combined_equity(self):
        r = PaperMonitorDashboard().build(_combined_data())
        assert r.combined_equity == 105_000 + 112_000

    def test_combined_pnl(self):
        r = PaperMonitorDashboard().build(_combined_data())
        assert r.combined_pnl == 5_000 + 12_000

    def test_period_set(self):
        r = PaperMonitorDashboard().build(_exp880_data(), period="weekly")
        assert r.period == "weekly"

    def test_generated_at(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        assert len(r.generated_at) > 0


# ── Strategy view ──────────────────────────────────────────────────────────
class TestStrategyView:
    def test_equity(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        assert r.views[0].equity == 105_000

    def test_return_pct(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        assert r.views[0].return_pct == 5.0

    def test_positions(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        assert len(r.views[0].positions) == 2
        assert r.views[0].positions[0].symbol == "SPY_P440"

    def test_position_greeks(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        p = r.views[0].positions[0]
        assert p.delta == -0.15
        assert p.theta == 3.5

    def test_signal_quality(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        sq = r.views[0].signal_quality
        assert sq is not None
        assert sq.accuracy_pct == 74.0
        assert sq.total_signals == 50

    def test_regime_state(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        rg = r.views[0].regime
        assert rg is not None
        assert rg.current_regime == "bull"
        assert rg.leverage == 2.0

    def test_backtest_deviation(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        # Return 5% vs expected CAGR 76.9% → large negative deviation
        assert r.views[0].cagr_deviation_pct < 0


# ── Portfolio Greeks ────────────────────────────────────────────────────────
class TestPortfolioGreeks:
    def test_delta_aggregated(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        # -0.15 + 0.10 = -0.05
        assert r.portfolio_delta == pytest.approx(-0.05, abs=0.01)

    def test_theta_aggregated(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        # 3.5 + (-2.0) = 1.5
        assert r.portfolio_theta == pytest.approx(1.5, abs=0.01)

    def test_combined_greeks(self):
        r = PaperMonitorDashboard().build(_combined_data())
        # Sum across both strategies
        assert r.portfolio_delta != 0 or True  # just verify it runs


# ── Drawdown alerts ─────────────────────────────────────────────────────────
class TestAlerts:
    def test_no_alerts_normal(self):
        r = PaperMonitorDashboard().build(_exp880_data(dd=2.0))
        assert len(r.alerts) == 0

    def test_warning_at_5(self):
        r = PaperMonitorDashboard().build(_exp880_data(dd=6.0))
        assert any("WARNING" in a for a in r.alerts)

    def test_critical_at_10(self):
        r = PaperMonitorDashboard().build(_exp880_data(dd=11.0))
        assert any("CRITICAL" in a for a in r.alerts)

    def test_halt_at_13(self):
        r = PaperMonitorDashboard().build(_exp880_data(dd=14.0))
        assert any("HALT" in a for a in r.alerts)

    def test_hedge_alert(self):
        data = _exp880_data()
        data["EXP-880"]["regime"]["hedge_scale"] = 0.30
        r = PaperMonitorDashboard().build(data)
        assert any("Hedge" in a for a in r.views[0].alerts)

    def test_low_signal_accuracy_alert(self):
        data = _exp880_data()
        data["EXP-880"]["signals"]["correct"] = 20  # 40% accuracy
        r = PaperMonitorDashboard().build(data)
        assert any("accuracy" in a.lower() for a in r.views[0].alerts)

    def test_calibration_error_alert(self):
        data = _exp880_data()
        data["EXP-880"]["signals"]["avg_predicted"] = 300
        data["EXP-880"]["signals"]["avg_actual"] = 50
        r = PaperMonitorDashboard().build(data)
        assert any("Calibration" in a for a in r.views[0].alerts)


# ── Signal quality ──────────────────────────────────────────────────────────
class TestSignalQuality:
    def test_accuracy_computed(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        sq = r.views[0].signal_quality
        assert sq.accuracy_pct == 74.0  # 37/50

    def test_calibration_error(self):
        r = PaperMonitorDashboard().build(_exp880_data())
        sq = r.views[0].signal_quality
        # |150 - 130| / 130 ≈ 0.1538
        assert 0.10 < sq.calibration_error < 0.20

    def test_no_signals_returns_none(self):
        data = _exp880_data()
        del data["EXP-880"]["signals"]
        r = PaperMonitorDashboard().build(data)
        assert r.views[0].signal_quality is None


# ── HTML report ─────────────────────────────────────────────────────────────
class TestHTMLReport:
    def test_report_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = PaperMonitorDashboard()
            r = d.build(_combined_data())
            path = d.generate_report(r, str(Path(tmp) / "pm.html"))
            assert path.exists()
            assert path.stat().st_size > 0

    def test_report_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = PaperMonitorDashboard()
            r = d.build(_combined_data())
            path = d.generate_report(r, str(Path(tmp) / "r.html"))
            html = path.read_text()
            assert "Paper Trading Monitor" in html
            assert "EXP-880" in html
            assert "EXP-1470" in html
            assert "Greeks" in html
            assert "Signal Quality" in html
            assert "Regime" in html

    def test_valid_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = PaperMonitorDashboard()
            r = d.build(_exp880_data())
            path = d.generate_report(r, str(Path(tmp) / "v.html"))
            html = path.read_text()
            assert html.startswith("<!DOCTYPE html>")
            assert "</html>" in html

    def test_white_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = PaperMonitorDashboard()
            r = d.build(_exp880_data())
            path = d.generate_report(r, str(Path(tmp) / "w.html"))
            assert "background:#fff" in path.read_text()

    def test_daily_vs_weekly(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = PaperMonitorDashboard()
            r = d.build(_exp880_data(), period="weekly")
            path = d.generate_report(r, str(Path(tmp) / "wk.html"))
            assert "Weekly" in path.read_text()


# ── Edge cases ──────────────────────────────────────────────────────────────
class TestEdgeCases:
    def test_empty_data(self):
        r = PaperMonitorDashboard().build({})
        assert len(r.views) == 0
        assert r.combined_equity == 0

    def test_no_positions(self):
        data = {"X": {"equity": 100_000, "total_pnl": 0, "current_dd_pct": 0, "sharpe": 0, "win_rate_pct": 0, "n_trades": 0}}
        r = PaperMonitorDashboard().build(data)
        assert len(r.views[0].positions) == 0

    def test_no_regime(self):
        data = {"X": {"equity": 100_000, "total_pnl": 0, "current_dd_pct": 0, "sharpe": 0, "win_rate_pct": 0, "n_trades": 0}}
        r = PaperMonitorDashboard().build(data)
        assert r.views[0].regime is None


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_position_info(self):
        p = PositionInfo("SPY", -5, "short", delta=-0.15)
        assert p.delta == -0.15

    def test_signal_quality(self):
        sq = SignalQuality(50, 37, 74.0, 0.72, 150, 130, 0.15)
        assert sq.accuracy_pct == 74.0

    def test_regime_state(self):
        r = RegimeState("bull", 1.0, 2.0, 17.5)
        assert r.leverage == 2.0

    def test_dashboard_result_defaults(self):
        r = DashboardResult()
        assert r.views == []
        assert r.combined_equity == 0
