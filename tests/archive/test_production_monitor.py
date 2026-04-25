"""
Comprehensive tests for compass/production_monitor.py.

Covers:
  - Position tracking (add, remove, recalc)
  - P&L computation (daily, realized, unrealized, total)
  - Drawdown tracking (current, max, recovery)
  - Strategy attribution (per-strategy P&L, win rate, capital at risk)
  - Risk budget utilization
  - VaR utilization
  - Correlation monitoring
  - Portfolio Greeks aggregation
  - Health score computation
  - Alert engine (DD, daily loss, correlation, position limit)
  - Telegram alert delivery + cooldowns
  - Dashboard HTML generation
  - State persistence
  - Snapshot completeness
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compass.production_monitor import (
    CorrelationSnapshot,
    DashboardAlertEngine,
    DashboardSnapshot,
    DrawdownState,
    MonitorDashboardConfig,
    PositionRecord,
    ProductionMonitorDashboard,
    RiskBudgetState,
    StrategyAttribution,
    TelegramAlert,
    VaRState,
    _build_dashboard_html,
    _fd,
    _fp,
    _fr,
    _gauge_svg,
    _health_color,
    _pnl_color,
    _sev_color,
    _stat_card,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_config(**overrides) -> MonitorDashboardConfig:
    defaults = dict(
        initial_capital=100_000,
        dd_warning_pct=0.05,
        dd_critical_pct=0.10,
        daily_loss_warning=2_000,
        daily_loss_critical=5_000,
        max_positions=10,
        max_positions_per_strategy=5,
        max_capital_at_risk_pct=0.40,
        var_limit=10_000,
        correlation_spike_threshold=0.80,
        telegram_enabled=True,
        alert_cooldown_seconds=0,  # no cooldown for tests
    )
    defaults.update(overrides)
    return MonitorDashboardConfig(**defaults)


def _make_position(
    pid: str = "P1",
    strategy: str = "iron_condor",
    ticker: str = "SPY",
    unrealized_pnl: float = 100.0,
    max_loss: float = 500.0,
    **kwargs,
) -> PositionRecord:
    defaults = dict(
        position_id=pid,
        strategy=strategy,
        ticker=ticker,
        direction="bull_put",
        contracts=1,
        entry_credit=1.50,
        max_loss=max_loss,
        current_value=1.20,
        unrealized_pnl=unrealized_pnl,
        entry_date="2026-03-28",
        expiration_date="2026-04-18",
        dte=21,
        delta=-0.10,
        gamma=0.02,
        theta=0.08,
        vega=-0.15,
        margin_required=1000.0,
    )
    defaults.update(kwargs)
    return PositionRecord(**defaults)


def _make_monitor(**config_overrides) -> ProductionMonitorDashboard:
    sent: list[str] = []
    config = _make_config(**config_overrides)
    return ProductionMonitorDashboard(
        config=config,
        send_fn=lambda msg: (sent.append(msg), True)[1],
    )


# ── Position tracking tests ─────────────────────────────────────────────


class TestPositionTracking:
    def test_add_position(self):
        m = _make_monitor()
        m.record_position(_make_position("P1"))
        snap = m.snapshot()
        assert snap.n_open_positions == 1
        assert len(snap.positions) == 1
        assert snap.positions[0].position_id == "P1"

    def test_add_multiple_positions(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", ticker="SPY"))
        m.record_position(_make_position("P2", ticker="QQQ"))
        m.record_position(_make_position("P3", ticker="IWM"))
        snap = m.snapshot()
        assert snap.n_open_positions == 3

    def test_remove_position(self):
        m = _make_monitor()
        m.record_position(_make_position("P1"))
        m.remove_position("P1", realized_pnl=200.0, won=True)
        snap = m.snapshot()
        assert snap.n_open_positions == 0
        assert snap.realized_pnl == 200.0

    def test_remove_nonexistent_position(self):
        m = _make_monitor()
        m.remove_position("NOPE")  # should not raise
        snap = m.snapshot()
        assert snap.n_open_positions == 0

    def test_position_overwrite(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=100))
        m.record_position(_make_position("P1", unrealized_pnl=200))
        snap = m.snapshot()
        assert snap.n_open_positions == 1
        assert snap.unrealized_pnl == 200.0

    def test_multiple_strategies(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", strategy="iron_condor"))
        m.record_position(_make_position("P2", strategy="put_spread"))
        snap = m.snapshot()
        assert "iron_condor" in snap.strategy_attribution
        assert "put_spread" in snap.strategy_attribution


# ── P&L tests ────────────────────────────────────────────────────────────


class TestPnL:
    def test_unrealized_pnl(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=100))
        m.record_position(_make_position("P2", unrealized_pnl=250))
        snap = m.snapshot()
        assert snap.unrealized_pnl == 350.0

    def test_realized_pnl_on_close(self):
        m = _make_monitor()
        m.record_position(_make_position("P1"))
        m.remove_position("P1", realized_pnl=300, won=True)
        snap = m.snapshot()
        assert snap.realized_pnl == 300.0

    def test_total_pnl(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=100))
        m.remove_position("P1", realized_pnl=200, won=True)
        m.record_position(_make_position("P2", unrealized_pnl=50))
        snap = m.snapshot()
        assert snap.total_pnl == 250.0  # 200 realized + 50 unrealized

    def test_daily_pnl_update(self):
        m = _make_monitor()
        m.update_pnl(daily=500, unrealized=1000)
        snap = m.snapshot()
        assert snap.daily_pnl == 500.0

    def test_daily_pnl_includes_realized(self):
        m = _make_monitor()
        m.record_position(_make_position("P1"))
        m.remove_position("P1", realized_pnl=150, won=True)
        # daily_pnl incremented by realized on close
        snap = m.snapshot()
        assert snap.daily_pnl == 150.0

    def test_reset_daily(self):
        m = _make_monitor()
        m.update_pnl(daily=500, unrealized=1000)
        m.reset_daily()
        snap = m.snapshot()
        assert snap.daily_pnl == 0.0

    def test_pnl_history(self):
        m = _make_monitor()
        m.record_daily_return(100)
        m.record_daily_return(200)
        m.record_daily_return(-50)
        assert len(m._pnl_history) == 3


# ── Drawdown tests ───────────────────────────────────────────────────────


class TestDrawdown:
    def test_no_drawdown_when_profitable(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=500))
        snap = m.snapshot()
        assert snap.drawdown.drawdown_pct == 0.0

    def test_drawdown_on_loss(self):
        m = _make_monitor()
        # First go up to establish peak
        m.record_position(_make_position("P1", unrealized_pnl=5000))
        m.snapshot()  # sets peak to 105000
        # Now lose
        m.record_position(_make_position("P1", unrealized_pnl=-2000))
        snap = m.snapshot()
        # Peak was 105000, now equity = 100000 + (-2000) = 98000
        # DD = (105000 - 98000) / 105000 = 6.67%
        assert snap.drawdown.drawdown_pct > 0.06
        assert snap.drawdown.drawdown_dollar > 0

    def test_max_drawdown_tracks_worst(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=-5000))
        snap1 = m.snapshot()
        # Recover
        m.record_position(_make_position("P1", unrealized_pnl=0))
        snap2 = m.snapshot()
        # Max DD should still reflect the worst
        assert snap2.drawdown.max_drawdown_pct >= snap1.drawdown.drawdown_pct

    def test_drawdown_recovery(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=-3000))
        m.snapshot()
        m.record_position(_make_position("P1", unrealized_pnl=1000))
        snap = m.snapshot()
        # Should still show max DD from before
        assert snap.drawdown.max_drawdown_pct > 0


# ── Strategy attribution tests ───────────────────────────────────────────


class TestStrategyAttribution:
    def test_strategy_created_on_first_position(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", strategy="iron_condor"))
        assert "iron_condor" in m._strategies

    def test_win_rate(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", strategy="s1"))
        m.remove_position("P1", realized_pnl=100, won=True)
        m.record_position(_make_position("P2", strategy="s1"))
        m.remove_position("P2", realized_pnl=-50, won=False)
        strat = m._strategies["s1"]
        assert strat.win_rate == 0.5

    def test_capital_at_risk(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", strategy="s1", max_loss=500))
        m.record_position(_make_position("P2", strategy="s1", max_loss=800))
        snap = m.snapshot()
        assert snap.strategy_attribution["s1"].capital_at_risk == 1300.0

    def test_per_strategy_pnl(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", strategy="s1"))
        m.remove_position("P1", realized_pnl=200, won=True)
        m.record_position(_make_position("P2", strategy="s2"))
        m.remove_position("P2", realized_pnl=-100, won=False)
        snap = m.snapshot()
        assert snap.strategy_attribution["s1"].realized_pnl == 200
        assert snap.strategy_attribution["s2"].realized_pnl == -100

    def test_unrealized_attribution(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", strategy="s1", unrealized_pnl=300))
        m.record_position(_make_position("P2", strategy="s2", unrealized_pnl=-100))
        snap = m.snapshot()
        assert snap.strategy_attribution["s1"].unrealized_pnl == 300
        assert snap.strategy_attribution["s2"].unrealized_pnl == -100

    def test_n_trades(self):
        strat = StrategyAttribution(strategy="test", win_count=3, loss_count=2)
        assert strat.n_trades == 5
        assert strat.win_rate == 0.6

    def test_win_rate_empty(self):
        strat = StrategyAttribution(strategy="test")
        assert strat.win_rate == 0.0


# ── Risk budget tests ───────────────────────────────────────────────────


class TestRiskBudget:
    def test_risk_budget_utilization(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", max_loss=10_000))
        snap = m.snapshot()
        # max_risk = 100k * 0.4 = 40k, car = 10k → 25%
        assert abs(snap.risk_budget.utilization_pct - 0.25) < 0.01

    def test_headroom(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", max_loss=10_000))
        snap = m.snapshot()
        assert snap.risk_budget.headroom == 30_000.0

    def test_margin_tracking(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", margin_required=5000))
        m.record_position(_make_position("P2", margin_required=3000))
        snap = m.snapshot()
        assert snap.risk_budget.margin_used == 8000.0
        assert snap.risk_budget.margin_available == 92_000.0

    def test_empty_risk_budget(self):
        m = _make_monitor()
        snap = m.snapshot()
        assert snap.risk_budget.utilization_pct == 0.0
        assert snap.risk_budget.capital_at_risk == 0.0


# ── VaR tests ───────────────────────────────────────────────────────────


class TestVaR:
    def test_var_update(self):
        m = _make_monitor()
        m.update_var(var_95=5000, var_99=7500)
        snap = m.snapshot()
        assert snap.var_state.current_var_95 == 5000
        assert snap.var_state.current_var_99 == 7500

    def test_var_utilization(self):
        m = _make_monitor()
        m.update_var(var_95=7000, var_99=9500)
        snap = m.snapshot()
        # limit=10000, util = 7000/10000 = 0.70
        assert abs(snap.var_state.utilization_pct - 0.70) < 0.01

    def test_var_zero_limit(self):
        m = _make_monitor(var_limit=0)
        m.update_var(var_95=5000, var_99=7500)
        snap = m.snapshot()
        assert snap.var_state.utilization_pct == 0.0


# ── Correlation tests ───────────────────────────────────────────────────


class TestCorrelation:
    def test_update_correlation(self):
        m = _make_monitor()
        m.update_correlation({"A|B": 0.75, "A|C": 0.30})
        snap = m.snapshot()
        assert snap.correlation.max_correlation == 0.75
        assert snap.correlation.max_pair == "A|B"

    def test_avg_correlation(self):
        m = _make_monitor()
        m.update_correlation({"A|B": 0.60, "A|C": 0.40})
        snap = m.snapshot()
        assert abs(snap.correlation.avg_correlation - 0.50) < 0.01

    def test_empty_correlation(self):
        m = _make_monitor()
        m.update_correlation({})
        snap = m.snapshot()
        assert snap.correlation.max_correlation == 0.0

    def test_negative_correlation(self):
        m = _make_monitor()
        m.update_correlation({"A|B": -0.90, "A|C": 0.30})
        snap = m.snapshot()
        assert snap.correlation.max_correlation == 0.90  # absolute


# ── Portfolio Greeks tests ───────────────────────────────────────────────


class TestPortfolioGreeks:
    def test_aggregate_greeks(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", delta=-0.10, gamma=0.02, theta=0.08, vega=-0.15, contracts=2))
        m.record_position(_make_position("P2", delta=0.05, gamma=0.01, theta=0.12, vega=-0.10, contracts=3))
        snap = m.snapshot()
        # P1: delta=-0.10*2=-0.20, P2: delta=0.05*3=0.15 → total=-0.05
        assert abs(snap.portfolio_delta - (-0.05)) < 0.001

    def test_greeks_empty(self):
        m = _make_monitor()
        snap = m.snapshot()
        assert snap.portfolio_delta == 0.0
        assert snap.portfolio_gamma == 0.0
        assert snap.portfolio_theta == 0.0
        assert snap.portfolio_vega == 0.0


# ── Health score tests ───────────────────────────────────────────────────


class TestHealthScore:
    def test_perfect_health(self):
        m = _make_monitor()
        m.update_pnl(daily=100, unrealized=0)
        snap = m.snapshot()
        assert snap.health_score == 100.0

    def test_degraded_health_on_drawdown(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=-8000))
        snap = m.snapshot()
        assert snap.health_score < 100.0

    def test_health_never_negative(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=-50000, max_loss=50000))
        m.update_pnl(daily=-50000, unrealized=-50000)
        m.update_var(var_95=20000, var_99=30000)
        snap = m.snapshot()
        assert snap.health_score >= 0.0

    def test_health_never_above_100(self):
        m = _make_monitor()
        snap = m.snapshot()
        assert snap.health_score <= 100.0


# ── Alert engine tests ───────────────────────────────────────────────────


class TestAlertEngine:
    def test_dd_critical_alert(self):
        config = _make_config(alert_cooldown_seconds=0)
        sent = []
        engine = DashboardAlertEngine(config, send_fn=lambda m: (sent.append(m), True)[1])
        dd = DrawdownState(drawdown_pct=0.12, drawdown_dollar=12000, max_drawdown_pct=0.12, days_in_drawdown=3)
        alert = engine.check_drawdown(dd)
        assert alert is not None
        assert alert.severity == "CRITICAL"
        assert "DRAWDOWN" in alert.title

    def test_dd_warning_alert(self):
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        dd = DrawdownState(drawdown_pct=0.07, drawdown_dollar=7000)
        alert = engine.check_drawdown(dd)
        assert alert is not None
        assert alert.severity == "WARNING"

    def test_dd_no_alert_when_ok(self):
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        dd = DrawdownState(drawdown_pct=0.02, drawdown_dollar=2000)
        alert = engine.check_drawdown(dd)
        assert alert is None

    def test_daily_loss_critical(self):
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        alert = engine.check_daily_loss(-6000)
        assert alert is not None
        assert alert.severity == "CRITICAL"

    def test_daily_loss_warning(self):
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        alert = engine.check_daily_loss(-3000)
        assert alert is not None
        assert alert.severity == "WARNING"

    def test_daily_loss_no_alert_when_profitable(self):
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        alert = engine.check_daily_loss(500)
        assert alert is None

    def test_correlation_spike_alert(self):
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        corr = CorrelationSnapshot(
            strategy_pairs={"A|B": 0.85},
            max_correlation=0.85,
            max_pair="A|B",
            avg_correlation=0.85,
        )
        alert = engine.check_correlation(corr)
        assert alert is not None
        assert "Correlation" in alert.title

    def test_correlation_no_alert_below_threshold(self):
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        corr = CorrelationSnapshot(max_correlation=0.50, max_pair="A|B")
        alert = engine.check_correlation(corr)
        assert alert is None

    def test_position_limit_breach(self):
        config = _make_config(max_positions=5, alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        alert = engine.check_position_limit(5, {})
        assert alert is not None
        assert alert.severity == "CRITICAL"

    def test_strategy_position_limit(self):
        config = _make_config(max_positions_per_strategy=3, alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        alert = engine.check_position_limit(3, {"s1": 3})
        assert alert is not None
        assert alert.severity == "WARNING"

    def test_no_position_limit_alert_when_ok(self):
        config = _make_config(max_positions=10, alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config)
        alert = engine.check_position_limit(3, {"s1": 2})
        assert alert is None


# ── Telegram delivery + cooldown tests ───────────────────────────────────


class TestTelegramDelivery:
    def test_alert_delivered(self):
        sent = []
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config, send_fn=lambda m: (sent.append(m), True)[1])
        alert = TelegramAlert(
            timestamp="2026-04-03 10:00:00",
            severity="WARNING",
            category="dd_breach",
            title="Test Alert",
            message="Test message",
        )
        result = engine.send(alert)
        assert result is True
        assert alert.delivered is True
        assert len(sent) == 1

    def test_cooldown_prevents_duplicate(self):
        sent = []
        config = _make_config(alert_cooldown_seconds=9999)
        engine = DashboardAlertEngine(config, send_fn=lambda m: (sent.append(m), True)[1])
        a1 = TelegramAlert(timestamp="t1", severity="WARNING", category="dd_breach", title="A1", message="m1")
        a2 = TelegramAlert(timestamp="t2", severity="WARNING", category="dd_breach", title="A2", message="m2")
        engine.send(a1)
        result = engine.send(a2)
        assert result is False
        assert len(sent) == 1  # only first sent

    def test_different_categories_not_cooled(self):
        sent = []
        config = _make_config(alert_cooldown_seconds=9999)
        engine = DashboardAlertEngine(config, send_fn=lambda m: (sent.append(m), True)[1])
        a1 = TelegramAlert(timestamp="t1", severity="WARNING", category="dd_breach", title="A1", message="m1")
        a2 = TelegramAlert(timestamp="t2", severity="WARNING", category="daily_loss", title="A2", message="m2")
        engine.send(a1)
        engine.send(a2)
        assert len(sent) == 2

    def test_reset_cooldowns(self):
        sent = []
        config = _make_config(alert_cooldown_seconds=9999)
        engine = DashboardAlertEngine(config, send_fn=lambda m: (sent.append(m), True)[1])
        a1 = TelegramAlert(timestamp="t1", severity="WARNING", category="dd_breach", title="A1", message="m1")
        engine.send(a1)
        engine.reset_cooldowns()
        a2 = TelegramAlert(timestamp="t2", severity="WARNING", category="dd_breach", title="A2", message="m2")
        engine.send(a2)
        assert len(sent) == 2

    def test_alert_history(self):
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config, send_fn=lambda m: True)
        for i in range(5):
            a = TelegramAlert(
                timestamp=f"t{i}", severity="INFO", category=f"cat{i}",
                title=f"Alert {i}", message=f"msg {i}",
            )
            engine.send(a)
        assert len(engine.history) == 5

    def test_disabled_telegram(self):
        sent = []
        config = _make_config(telegram_enabled=False, alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config, send_fn=lambda m: (sent.append(m), True)[1])
        a = TelegramAlert(timestamp="t", severity="INFO", category="test", title="T", message="M")
        result = engine.send(a)
        assert result is False
        assert len(sent) == 0

    def test_telegram_format(self):
        alert = TelegramAlert(
            timestamp="2026-04-03 10:00:00",
            severity="CRITICAL",
            category="dd_breach",
            title="DD CRITICAL",
            message="Drawdown exceeded 10%",
            value=0.12,
            threshold=0.10,
        )
        fmt = alert.telegram_format()
        assert "CRITICAL" in fmt
        assert "DD CRITICAL" in fmt
        assert "0.1200" in fmt


# ── Run all checks integration test ─────────────────────────────────────


class TestRunAllChecks:
    def test_multiple_alerts_fired(self):
        sent = []
        config = _make_config(alert_cooldown_seconds=0, dd_warning_pct=0.03)
        engine = DashboardAlertEngine(config, send_fn=lambda m: (sent.append(m), True)[1])
        dd = DrawdownState(drawdown_pct=0.04, drawdown_dollar=4000)
        corr = CorrelationSnapshot(max_correlation=0.90, max_pair="A|B")
        alerts = engine.run_all_checks(
            dd=dd,
            daily_pnl=-3000,
            corr=corr,
            n_positions=3,
            per_strategy={"s1": 2},
        )
        # DD warning + daily loss warning + correlation spike = 3
        assert len(alerts) == 3
        assert len(sent) == 3

    def test_no_alerts_when_healthy(self):
        sent = []
        config = _make_config(alert_cooldown_seconds=0)
        engine = DashboardAlertEngine(config, send_fn=lambda m: (sent.append(m), True)[1])
        dd = DrawdownState(drawdown_pct=0.01)
        corr = CorrelationSnapshot(max_correlation=0.30, max_pair="A|B")
        alerts = engine.run_all_checks(
            dd=dd, daily_pnl=500, corr=corr, n_positions=3, per_strategy={"s1": 2}
        )
        assert len(alerts) == 0
        assert len(sent) == 0


# ── Dashboard HTML generation tests ─────────────────────────────────────


class TestDashboardHTML:
    def _make_snap(self) -> DashboardSnapshot:
        return DashboardSnapshot(
            timestamp="2026-04-03 10:00:00",
            positions=[
                _make_position("P1", strategy="iron_condor", ticker="SPY", unrealized_pnl=100),
                _make_position("P2", strategy="put_spread", ticker="QQQ", unrealized_pnl=-50),
            ],
            strategy_attribution={
                "iron_condor": StrategyAttribution(
                    strategy="iron_condor", n_positions=1, total_pnl=100,
                    realized_pnl=0, unrealized_pnl=100, capital_at_risk=500,
                ),
                "put_spread": StrategyAttribution(
                    strategy="put_spread", n_positions=1, total_pnl=-50,
                    realized_pnl=0, unrealized_pnl=-50, capital_at_risk=500,
                    win_count=2, loss_count=1,
                ),
            },
            daily_pnl=300.0,
            unrealized_pnl=50.0,
            realized_pnl=250.0,
            total_pnl=300.0,
            drawdown=DrawdownState(
                current_equity=100_300, peak_equity=100_500,
                drawdown_dollar=200, drawdown_pct=0.002,
                max_drawdown_pct=0.03, max_drawdown_dollar=3000,
            ),
            risk_budget=RiskBudgetState(
                total_capital=100_000, capital_at_risk=1000,
                max_allowed_risk=40_000, utilization_pct=0.025,
                margin_used=2000, margin_available=98_000,
            ),
            var_state=VaRState(
                current_var_95=5000, current_var_99=7500,
                var_limit=10000, utilization_pct=0.50,
            ),
            correlation=CorrelationSnapshot(
                strategy_pairs={"ic|ps": 0.55},
                max_correlation=0.55,
                max_pair="ic|ps",
                avg_correlation=0.55,
            ),
            alerts=[],
            alert_history=[
                TelegramAlert(
                    timestamp="2026-04-03 09:30:00", severity="WARNING",
                    category="daily_loss", title="Daily Loss Warning",
                    message="Test", delivered=True,
                ),
            ],
            n_open_positions=2,
            portfolio_delta=-0.05,
            portfolio_gamma=0.03,
            portfolio_theta=0.20,
            portfolio_vega=-0.25,
            health_score=92.0,
        )

    def test_html_contains_required_sections(self):
        snap = self._make_snap()
        html = _build_dashboard_html(snap, auto_refresh=30)
        assert "<!DOCTYPE html>" in html
        assert "Production Monitor Dashboard" in html
        assert "P&L Summary" in html
        assert "Portfolio Greeks" in html
        assert "Risk Gauges" in html
        assert "Risk Budget" in html
        assert "VaR Utilization" in html
        assert "Strategy Attribution" in html
        assert "Open Positions" in html
        assert "Strategy Correlations" in html
        assert "Alert History" in html

    def test_html_auto_refresh_meta(self):
        snap = self._make_snap()
        html = _build_dashboard_html(snap, auto_refresh=15)
        assert 'content="15"' in html

    def test_html_contains_position_data(self):
        snap = self._make_snap()
        html = _build_dashboard_html(snap)
        assert "SPY" in html
        assert "QQQ" in html
        assert "iron_condor" in html
        assert "put_spread" in html

    def test_html_contains_pnl_values(self):
        snap = self._make_snap()
        html = _build_dashboard_html(snap)
        assert "$300.00" in html  # daily PnL

    def test_html_contains_health_score(self):
        snap = self._make_snap()
        html = _build_dashboard_html(snap)
        assert "92" in html

    def test_html_contains_alert_history(self):
        snap = self._make_snap()
        html = _build_dashboard_html(snap)
        assert "Daily Loss Warning" in html

    def test_html_file_generation(self):
        m = _make_monitor()
        m.record_position(_make_position("P1"))
        snap = m.snapshot()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test_dashboard.html"
            result = ProductionMonitorDashboard.generate_dashboard(snap, out)
            assert result.exists()
            content = result.read_text()
            assert "<!DOCTYPE html>" in content

    def test_html_no_correlation_section_when_empty(self):
        snap = self._make_snap()
        snap.correlation = CorrelationSnapshot()
        html = _build_dashboard_html(snap)
        assert "Strategy Correlations" not in html


# ── State persistence tests ──────────────────────────────────────────────


class TestStatePersistence:
    def test_save_and_load_state(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=500))
        m.update_var(var_95=6000, var_99=8000)
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            m.save_state(state_path)
            assert state_path.exists()
            data = json.loads(state_path.read_text())
            assert data["n_positions"] == 1
            assert data["var"]["var_95"] == 6000
            assert "strategies" in data
            assert "alerts" in data

    def test_state_contains_all_fields(self):
        m = _make_monitor()
        m.record_position(_make_position("P1"))
        m.update_var(var_95=5000, var_99=7000)
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            m.save_state(state_path)
            data = json.loads(state_path.read_text())
            required_keys = [
                "timestamp", "daily_pnl", "unrealized_pnl", "realized_pnl",
                "total_pnl", "drawdown", "risk_budget", "var", "n_positions",
                "health_score", "strategies", "alerts",
            ]
            for key in required_keys:
                assert key in data, f"Missing key: {key}"


# ── Snapshot completeness tests ──────────────────────────────────────────


class TestSnapshotCompleteness:
    def test_snapshot_has_all_fields(self):
        m = _make_monitor()
        m.record_position(_make_position("P1"))
        m.update_var(var_95=5000, var_99=7000)
        m.update_correlation({"A|B": 0.60})
        snap = m.snapshot()

        assert snap.timestamp
        assert isinstance(snap.positions, list)
        assert isinstance(snap.strategy_attribution, dict)
        assert isinstance(snap.daily_pnl, float)
        assert isinstance(snap.unrealized_pnl, float)
        assert isinstance(snap.realized_pnl, float)
        assert isinstance(snap.total_pnl, float)
        assert isinstance(snap.drawdown, DrawdownState)
        assert isinstance(snap.risk_budget, RiskBudgetState)
        assert isinstance(snap.var_state, VaRState)
        assert isinstance(snap.correlation, CorrelationSnapshot)
        assert isinstance(snap.alerts, list)
        assert isinstance(snap.alert_history, list)
        assert isinstance(snap.n_open_positions, int)
        assert isinstance(snap.portfolio_delta, float)
        assert isinstance(snap.health_score, float)

    def test_equity_history_capped(self):
        m = _make_monitor()
        for i in range(600):
            m.record_position(_make_position(f"P{i}", unrealized_pnl=float(i)))
            m.snapshot()
        assert len(m._equity_history) <= 500


# ── HTML helper tests ────────────────────────────────────────────────────


class TestHTMLHelpers:
    def test_fd(self):
        assert _fd(1234.56) == "$1,234.56"
        assert _fd(-500.0) == "$-500.00"

    def test_fp(self):
        assert _fp(0.1234) == "12.3%"

    def test_fr(self):
        assert _fr(3.14159) == "3.14"

    def test_pnl_color(self):
        assert _pnl_color(100) == "#3fb950"
        assert _pnl_color(-100) == "#f85149"
        assert _pnl_color(0) == "#3fb950"

    def test_health_color(self):
        assert _health_color(90) == "#3fb950"
        assert _health_color(60) == "#d29922"
        assert _health_color(30) == "#f85149"

    def test_sev_color(self):
        assert _sev_color("INFO") == "#58a6ff"
        assert _sev_color("WARNING") == "#d29922"
        assert _sev_color("CRITICAL") == "#f85149"
        assert _sev_color("UNKNOWN") == "#8b949e"

    def test_gauge_svg(self):
        svg = _gauge_svg(50, 100, "Test Gauge")
        assert "<svg" in svg
        assert "Test Gauge" in svg

    def test_stat_card(self):
        card = _stat_card("Label", "$100", "#3fb950")
        assert "Label" in card
        assert "$100" in card
        assert "#3fb950" in card


# ── Edge case tests ──────────────────────────────────────────────────────


class TestEdgeCases:
    def test_zero_initial_capital(self):
        m = _make_monitor(initial_capital=0)
        snap = m.snapshot()
        assert snap.health_score >= 0

    def test_massive_loss(self):
        m = _make_monitor()
        m.record_position(_make_position("P1", unrealized_pnl=-200_000, max_loss=200_000))
        snap = m.snapshot()
        assert snap.drawdown.drawdown_pct > 0
        assert snap.health_score >= 0

    def test_rapid_position_churn(self):
        m = _make_monitor()
        for i in range(100):
            m.record_position(_make_position(f"P{i}", unrealized_pnl=float(i)))
        for i in range(100):
            m.remove_position(f"P{i}", realized_pnl=float(i), won=i > 50)
        snap = m.snapshot()
        assert snap.n_open_positions == 0
        assert snap.realized_pnl > 0

    def test_default_send_fn_fallback(self):
        # Default send fn should log, not crash, when shared.telegram_alerts unavailable
        result = DashboardAlertEngine._default_send("test message")
        assert result is False  # falls back to logging


# ── Integration test ─────────────────────────────────────────────────────


class TestIntegration:
    def test_full_workflow(self):
        sent = []
        m = ProductionMonitorDashboard(
            config=_make_config(alert_cooldown_seconds=0, dd_warning_pct=0.03),
            send_fn=lambda msg: (sent.append(msg), True)[1],
        )

        # Add positions
        m.record_position(_make_position("P1", strategy="ic", unrealized_pnl=200, max_loss=500))
        m.record_position(_make_position("P2", strategy="ps", unrealized_pnl=-100, max_loss=800))

        # Update market data
        m.update_var(var_95=6000, var_99=8500)
        m.update_correlation({"ic|ps": 0.55})
        m.update_pnl(daily=100, unrealized=100)

        # Take snapshot
        snap = m.snapshot()
        assert snap.n_open_positions == 2
        assert snap.total_pnl != 0
        assert snap.var_state.current_var_95 == 6000

        # Close a position
        m.remove_position("P1", realized_pnl=180, won=True)
        snap2 = m.snapshot()
        assert snap2.n_open_positions == 1
        assert snap2.realized_pnl == 180

        # Generate dashboard
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "dashboard.html"
            ProductionMonitorDashboard.generate_dashboard(snap2, out)
            assert out.exists()
            html = out.read_text()
            assert "ic" in html
            assert "ps" in html

    def test_alert_triggered_on_snapshot(self):
        sent = []
        m = ProductionMonitorDashboard(
            config=_make_config(
                alert_cooldown_seconds=0,
                dd_critical_pct=0.10,
            ),
            send_fn=lambda msg: (sent.append(msg), True)[1],
        )
        # Create a 15% drawdown
        m.record_position(_make_position("P1", unrealized_pnl=5000))
        m.snapshot()  # peak = 105000
        m.record_position(_make_position("P1", unrealized_pnl=-10000))
        snap = m.snapshot()  # equity = 90000, dd = 15000/105000 = 14.3%
        assert snap.drawdown.drawdown_pct > 0.10
        assert len(sent) > 0  # DD alert should have fired
