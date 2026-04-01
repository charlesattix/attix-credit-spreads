"""Tests for compass.crisis_hedge_monitor — real-time hedge monitoring."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from compass.crisis_hedge_monitor import (
    ANNUAL_HEDGE_BUDGET_PCT,
    TIER_ELEVATED,
    TIER_EXTREME,
    TIER_HIGH,
    TIER_NORMAL,
    CrisisHedgeMonitor,
    DailySummary,
    DDControllerState,
    HedgeCostEntry,
    MonitorState,
    RecoveryState,
    ScaleAdjustment,
    WeeklySummary,
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _feed_normal(mon: CrisisHedgeMonitor, n: int = 10) -> None:
    """Feed normal market ticks."""
    for i in range(n):
        mon.record_tick(vix=18.0, scale_factor=1.0, current_dd=0.01, regime="bull")


def _feed_crisis(mon: CrisisHedgeMonitor, n: int = 5) -> None:
    """Feed crisis ticks."""
    for i in range(n):
        mon.record_tick(
            vix=40.0, scale_factor=0.40, current_dd=0.08,
            regime="high_vol", put_overlay_active=True, put_cost=30.0,
        )


# ── Tier classification ────────────────────────────────────────────────────
class TestTierClassification:
    def test_normal(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=15.0, scale_factor=1.0)
        assert s.tier == TIER_NORMAL

    def test_elevated(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=28.0, scale_factor=0.70)
        assert s.tier == TIER_ELEVATED

    def test_high(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=40.0, scale_factor=0.40)
        assert s.tier == TIER_HIGH

    def test_extreme(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=55.0, scale_factor=0.40)
        assert s.tier == TIER_EXTREME

    def test_boundary_25(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=25.0, scale_factor=1.0)
        assert s.tier == TIER_ELEVATED

    def test_boundary_35(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=35.0, scale_factor=0.40)
        assert s.tier == TIER_HIGH


# ── Scale adjustment logging ───────────────────────────────────────────────
class TestScaleAdjustments:
    def test_logs_on_change(self):
        mon = CrisisHedgeMonitor()
        mon.record_tick(vix=15.0, scale_factor=1.0)
        mon.record_tick(vix=30.0, scale_factor=0.60)
        assert len(mon.adjustments) == 1
        assert mon.adjustments[0].new_scale == 0.60

    def test_no_log_when_unchanged(self):
        mon = CrisisHedgeMonitor()
        mon.record_tick(vix=15.0, scale_factor=1.0)
        mon.record_tick(vix=16.0, scale_factor=1.0)
        assert len(mon.adjustments) == 0

    def test_logs_reason_with_vix(self):
        mon = CrisisHedgeMonitor()
        mon.record_tick(vix=15.0, scale_factor=1.0)
        mon.record_tick(vix=30.0, scale_factor=0.50)
        assert "VIX=" in mon.adjustments[0].reason

    def test_logs_dd_in_reason(self):
        mon = CrisisHedgeMonitor()
        mon.record_tick(vix=15.0, scale_factor=1.0)
        mon.record_tick(vix=20.0, scale_factor=0.70, current_dd=0.08)
        assert "DD=" in mon.adjustments[0].reason

    def test_multiple_adjustments(self):
        mon = CrisisHedgeMonitor()
        mon.record_tick(vix=15.0, scale_factor=1.0)
        mon.record_tick(vix=28.0, scale_factor=0.70)
        mon.record_tick(vix=40.0, scale_factor=0.40)
        mon.record_tick(vix=18.0, scale_factor=0.90)
        assert len(mon.adjustments) == 3


# ── Hedge cost tracking ────────────────────────────────────────────────────
class TestHedgeCost:
    def test_cumulative_cost(self):
        mon = CrisisHedgeMonitor()
        mon.record_tick(vix=35.0, scale_factor=0.40, put_overlay_active=True, put_cost=50.0)
        mon.record_tick(vix=35.0, scale_factor=0.40, put_overlay_active=True, put_cost=50.0)
        assert mon.cumulative_cost == 100.0

    def test_zero_cost_normal(self):
        mon = CrisisHedgeMonitor()
        _feed_normal(mon, 10)
        assert mon.cumulative_cost == 0.0

    def test_cost_vs_budget(self):
        mon = CrisisHedgeMonitor(starting_capital=100_000)
        _feed_normal(mon, 10)
        cvb = mon.cost_vs_budget(days_elapsed=10)
        assert "budget" in cvb
        assert "actual" in cvb
        assert "utilisation_pct" in cvb

    def test_budget_utilisation_increases(self):
        mon = CrisisHedgeMonitor(starting_capital=100_000)
        for _ in range(5):
            mon.record_tick(vix=35.0, scale_factor=0.40, put_cost=50.0)
        cvb = mon.cost_vs_budget(days_elapsed=5)
        assert cvb["actual"] > 0
        assert cvb["utilisation_pct"] > 0


# ── DD controller state ────────────────────────────────────────────────────
class TestDDController:
    def test_inactive_below_threshold(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=15.0, scale_factor=1.0, current_dd=0.02)
        assert not s.dd_controller.is_active

    def test_active_above_threshold(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=15.0, scale_factor=0.70, current_dd=0.08)
        assert s.dd_controller.is_active

    def test_dd_scale_calculated(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=15.0, scale_factor=0.70, current_dd=0.08)
        assert 0.0 < s.dd_controller.dd_scale < 1.0

    def test_dd_scale_1_at_zero(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=15.0, scale_factor=1.0, current_dd=0.0)
        assert s.dd_controller.dd_scale == 1.0


# ── Recovery state ──────────────────────────────────────────────────────────
class TestRecoveryState:
    def test_not_recovering_default(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(vix=30.0, scale_factor=0.50)
        assert not s.recovery.is_recovering

    def test_recovering_when_conditions_met(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(
            vix=18.0, scale_factor=0.80,
            momentum_confirmed=True, vix_normalised=True, recovery_progress=0.50,
        )
        assert s.recovery.is_recovering
        assert s.recovery.progress == 0.50

    def test_recovery_progress_tracked(self):
        mon = CrisisHedgeMonitor()
        s = mon.record_tick(
            vix=18.0, scale_factor=0.90,
            momentum_confirmed=True, vix_normalised=True,
            recovery_progress=0.75, recovery_days=15,
        )
        assert s.recovery.days_in_recovery == 15


# ── Daily summary ───────────────────────────────────────────────────────────
class TestDailySummary:
    def test_returns_summary(self):
        mon = CrisisHedgeMonitor()
        _feed_normal(mon, 10)
        ds = mon.get_daily_summary()
        assert ds is not None
        assert isinstance(ds, DailySummary)

    def test_avg_vix(self):
        mon = CrisisHedgeMonitor()
        _feed_normal(mon, 10)
        ds = mon.get_daily_summary()
        assert ds.avg_vix == pytest.approx(18.0)

    def test_tier_distribution(self):
        mon = CrisisHedgeMonitor()
        _feed_normal(mon, 5)
        _feed_crisis(mon, 3)
        ds = mon.get_daily_summary()
        assert TIER_NORMAL in ds.tier_distribution
        assert TIER_HIGH in ds.tier_distribution

    def test_n_ticks(self):
        mon = CrisisHedgeMonitor()
        _feed_normal(mon, 7)
        ds = mon.get_daily_summary()
        assert ds.n_ticks == 7

    def test_no_data_returns_none(self):
        mon = CrisisHedgeMonitor()
        assert mon.get_daily_summary("2099-01-01") is None


# ── Weekly summary ──────────────────────────────────────────────────────────
class TestWeeklySummary:
    def test_returns_summary(self):
        mon = CrisisHedgeMonitor()
        _feed_normal(mon, 10)
        ws = mon.get_weekly_summary()
        assert isinstance(ws, WeeklySummary)

    def test_annualised_cost(self):
        mon = CrisisHedgeMonitor()
        for _ in range(5):
            mon.record_tick(vix=35.0, scale_factor=0.40, put_cost=30.0)
        ws = mon.get_weekly_summary()
        assert ws.cumulative_cost_annualised_pct >= 0


# ── Reset ───────────────────────────────────────────────────────────────────
class TestReset:
    def test_clears_state(self):
        mon = CrisisHedgeMonitor()
        _feed_normal(mon, 10)
        _feed_crisis(mon, 5)
        mon.reset()
        assert mon.total_ticks == 0
        assert mon.cumulative_cost == 0
        assert len(mon.adjustments) == 0


# ── HTML report ─────────────────────────────────────────────────────────────
class TestHTMLReport:
    def test_daily_report_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            mon = CrisisHedgeMonitor()
            _feed_normal(mon, 5)
            _feed_crisis(mon, 3)
            path = mon.generate_report(str(Path(tmp) / "d.html"), period="daily")
            assert path.exists()

    def test_weekly_report_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            mon = CrisisHedgeMonitor()
            _feed_normal(mon, 10)
            path = mon.generate_report(str(Path(tmp) / "w.html"), period="weekly")
            assert path.exists()

    def test_report_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            mon = CrisisHedgeMonitor()
            _feed_normal(mon, 3)
            mon.record_tick(vix=15.0, scale_factor=1.0)
            mon.record_tick(vix=30.0, scale_factor=0.50, put_cost=20.0)
            path = mon.generate_report(str(Path(tmp) / "r.html"))
            html = path.read_text()
            assert "Crisis Hedge" in html
            assert "VIX" in html
            assert "Scale" in html
            assert "Drawdown" in html
            assert "Tier" in html

    def test_report_valid_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            mon = CrisisHedgeMonitor()
            _feed_normal(mon, 5)
            path = mon.generate_report(str(Path(tmp) / "v.html"))
            html = path.read_text()
            assert html.startswith("<!DOCTYPE html>")
            assert "</html>" in html

    def test_white_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            mon = CrisisHedgeMonitor()
            _feed_normal(mon, 3)
            path = mon.generate_report(str(Path(tmp) / "w.html"))
            html = path.read_text()
            assert "background:#fff" in html

    def test_empty_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            mon = CrisisHedgeMonitor()
            path = mon.generate_report(str(Path(tmp) / "e.html"))
            assert path.exists()


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_scale_adjustment(self):
        a = ScaleAdjustment("t", 30.0, 1.0, 0.50, TIER_ELEVATED, "VIX=30")
        assert a.new_scale == 0.50

    def test_dd_controller_state(self):
        d = DDControllerState(0.08, 0.05, 0.12, 0.60, True)
        assert d.is_active

    def test_recovery_state(self):
        r = RecoveryState(True, True, True, 0.75, 15)
        assert r.progress == 0.75

    def test_monitor_state(self):
        s = MonitorState("t", 18.0, TIER_NORMAL, 1.0,
                         DDControllerState(0, 0.05, 0.12, 1.0, False),
                         RecoveryState(False, False, False, 0, 0), False)
        assert s.tier == TIER_NORMAL
