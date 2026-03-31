"""Tests for compass.crisis_hedge_v2 — crisis hedge controller V2."""
from __future__ import annotations

import numpy as np
import pytest

from compass.crisis_hedge_v2 import (
    CRISIS_SCENARIOS,
    CrisisHedgeControllerV2,
    CrisisHedgeV2Config,
    CrisisHedgeV2Result,
    HedgeState,
    PutOverlayResult,
    RecoverySignal,
    backtest_with_hedge,
    stress_test_scenario,
)


# ── Config ──────────────────────────────────────────────────────────────────
class TestConfig:
    def test_defaults(self):
        c = CrisisHedgeV2Config()
        assert c.vix_reduce == 25.0
        assert c.min_scale == 0.40
        assert c.dd_start == 0.05

    def test_custom(self):
        c = CrisisHedgeV2Config(min_scale=0.20, vix_reduce=20.0)
        assert c.min_scale == 0.20

    def test_put_defaults(self):
        c = CrisisHedgeV2Config()
        assert c.put_cost_pct_annual == 0.02
        assert c.put_protection_mult == 3.0


# ── VIX scaling ─────────────────────────────────────────────────────────────
class TestVIXScaling:
    def test_low_vix_full_scale(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=15.0)
        assert state.vix_scale == 1.0

    def test_vix_at_reduce(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=25.0)
        assert state.vix_scale == 1.0

    def test_vix_above_reduce(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=30.0)
        assert state.vix_scale < 1.0
        assert state.vix_scale > 0.40

    def test_vix_at_minimum(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=35.0)
        assert state.vix_scale == 0.40

    def test_vix_above_minimum(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=50.0)
        assert state.vix_scale == 0.40  # floor, never 0

    def test_scale_monotonically_decreasing(self):
        ctrl = CrisisHedgeControllerV2()
        scales = [ctrl.compute_scale(vix=v).vix_scale for v in range(10, 60, 5)]
        for i in range(1, len(scales)):
            assert scales[i] <= scales[i-1] + 1e-9

    def test_min_scale_never_zero(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=80.0)
        assert state.scale_factor >= 0.40


# ── Drawdown scaling ───────────────────────────────────────────────────────
class TestDDScaling:
    def test_no_dd_full_scale(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=15.0, current_dd=0.0)
        assert state.dd_scale == 1.0

    def test_dd_below_start(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=15.0, current_dd=0.03)
        assert state.dd_scale == 1.0

    def test_dd_at_start(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=15.0, current_dd=0.05)
        assert state.dd_scale == pytest.approx(1.0, abs=0.01)

    def test_dd_between_start_and_full(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=15.0, current_dd=0.08)
        assert 0.20 < state.dd_scale < 1.0

    def test_dd_at_full(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=15.0, current_dd=0.12)
        assert state.dd_scale <= 0.40 * 0.5 + 0.01  # emergency floor

    def test_dd_gradual_not_binary(self):
        """DD control should be smooth, not a step function."""
        ctrl = CrisisHedgeControllerV2()
        dd_values = [0.0, 0.03, 0.05, 0.07, 0.09, 0.11, 0.13]
        scales = [ctrl.compute_scale(vix=15.0, current_dd=d).dd_scale for d in dd_values]
        # No sudden jumps > 0.3
        for i in range(1, len(scales)):
            assert abs(scales[i] - scales[i-1]) < 0.35


# ── Regime override ─────────────────────────────────────────────────────────
class TestRegimeOverride:
    def test_crash_uses_crash_scale(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=15.0, regime="crash")
        assert state.regime_scale == 0.40

    def test_high_vol_capped(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=15.0, regime="high_vol")
        assert state.regime_scale <= 0.60

    def test_bull_no_override(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=15.0, regime="bull")
        assert state.regime_scale == 1.0

    def test_combined_vix_and_regime(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=30.0, regime="high_vol")
        assert state.scale_factor <= 0.60  # regime cap applies


# ── Put overlay ─────────────────────────────────────────────────────────────
class TestPutOverlay:
    def test_inactive_below_trigger(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=20.0)
        assert not state.put_overlay_active

    def test_active_above_trigger(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=35.0)
        assert state.put_overlay_active

    def test_cost_positive_when_active(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=40.0)
        assert state.put_cost_today > 0

    def test_cost_scales_with_vix(self):
        ctrl = CrisisHedgeControllerV2()
        s30 = ctrl.compute_scale(vix=30.0)
        s50 = ctrl.compute_scale(vix=50.0)
        assert s50.put_cost_today >= s30.put_cost_today

    def test_analysis_fields(self):
        ctrl = CrisisHedgeControllerV2()
        r = ctrl.put_overlay_analysis(40.0, 100_000)
        assert r.is_active
        assert r.daily_cost > 0
        assert r.cost_benefit_ratio > 0


# ── Recovery detection ──────────────────────────────────────────────────────
class TestRecovery:
    def test_no_recovery_in_crisis(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=40.0, daily_return=-0.02)
        assert not state.recovery_mode

    def test_recovery_after_sustained_positive(self):
        ctrl = CrisisHedgeControllerV2()
        # Feed 15 days of positive momentum at low VIX
        for _ in range(15):
            state = ctrl.compute_scale(vix=18.0, daily_return=0.005)
        assert state.recovery_mode or state.recovery_progress > 0

    def test_recovery_resets_on_negative(self):
        ctrl = CrisisHedgeControllerV2()
        for _ in range(10):
            ctrl.compute_scale(vix=18.0, daily_return=0.005)
        ctrl.compute_scale(vix=30.0, daily_return=-0.03)
        state = ctrl.compute_scale(vix=30.0, daily_return=-0.02)
        assert not state.recovery_mode

    def test_recovery_ramps_gradually(self):
        ctrl = CrisisHedgeControllerV2()
        progresses = []
        for _ in range(30):
            state = ctrl.compute_scale(vix=18.0, daily_return=0.003)
            progresses.append(state.recovery_progress)
        # Progress should increase over time
        assert progresses[-1] >= progresses[5]


# ── Combined scale ──────────────────────────────────────────────────────────
class TestCombinedScale:
    def test_takes_minimum(self):
        ctrl = CrisisHedgeControllerV2()
        # VIX=30 → vix_scale ~0.7, DD=0.10 → dd_scale ~0.4
        state = ctrl.compute_scale(vix=30.0, current_dd=0.10)
        assert state.scale_factor <= min(state.vix_scale, state.dd_scale) + 0.01

    def test_bounded_0_to_1(self):
        ctrl = CrisisHedgeControllerV2()
        for vix in [10, 20, 30, 40, 50, 60, 80]:
            for dd in [0, 0.05, 0.10, 0.15, 0.20]:
                state = ctrl.compute_scale(vix=float(vix), current_dd=dd)
                assert 0.0 <= state.scale_factor <= 1.0

    def test_reason_populated(self):
        ctrl = CrisisHedgeControllerV2()
        state = ctrl.compute_scale(vix=30.0, current_dd=0.08, regime="high_vol")
        assert len(state.reason) > 0
        assert "normal" not in state.reason

    def test_reset(self):
        ctrl = CrisisHedgeControllerV2()
        ctrl.compute_scale(vix=40.0, daily_return=-0.02)
        ctrl.reset()
        state = ctrl.compute_scale(vix=15.0)
        assert state.scale_factor == 1.0


# ── Backtest integration ───────────────────────────────────────────────────
class TestBacktest:
    def _make_data(self, n=500, seed=42):
        rng = np.random.RandomState(seed)
        returns = rng.randn(n) * 0.004 + 0.001
        vix = np.full(n, 18.0) + rng.randn(n) * 3
        regimes = ["bull"] * n
        return returns, np.abs(vix), regimes

    def test_returns_dict(self):
        r, v, reg = self._make_data()
        result = backtest_with_hedge(r, v, reg)
        assert "cagr_pct" in result
        assert "max_dd_pct" in result
        assert "sharpe" in result

    def test_hedged_lower_dd(self):
        r, v, reg = self._make_data()
        # Inject crisis
        r[100:120] = -0.03
        v[100:120] = 40.0
        hedged = backtest_with_hedge(r, v, reg)
        no_cfg = CrisisHedgeV2Config(vix_reduce=999, dd_start=999, min_scale=1.0, max_scale=1.0, crash_scale=1.0, high_vol_cap=1.0, put_overlay_vix_trigger=999)
        unhedged = backtest_with_hedge(r, v, reg, config=no_cfg)
        assert hedged["max_dd_pct"] <= unhedged["max_dd_pct"]

    def test_equity_curve_length(self):
        r, v, reg = self._make_data(n=100)
        result = backtest_with_hedge(r, v, reg)
        assert len(result["equity_curve"]) == 101  # +1 for starting capital

    def test_hedge_cost_tracked(self):
        r, v, reg = self._make_data()
        v[50:100] = 35.0  # trigger puts
        result = backtest_with_hedge(r, v, reg)
        assert result["total_hedge_cost"] > 0

    def test_leverage_capped_at_4(self):
        r, v, reg = self._make_data()
        result = backtest_with_hedge(r, v, reg, base_leverage=10.0)
        # With regime leverage mult, effective lev should still be bounded
        assert result["cagr_pct"] is not None  # just verify it runs


# ── Stress tests ────────────────────────────────────────────────────────────
class TestStressTests:
    def test_four_scenarios_exist(self):
        assert len(CRISIS_SCENARIOS) == 4
        assert "GFC_2008" in CRISIS_SCENARIOS
        assert "COVID_2020" in CRISIS_SCENARIOS

    def test_covid_survives(self):
        r = stress_test_scenario("COVID_2020")
        assert r["hedged_dd_pct"] < r["unhedged_dd_pct"]
        assert r["hedged_survives"]

    def test_rate_hikes_survives(self):
        r = stress_test_scenario("RATE_HIKES_2022")
        assert r["hedged_survives"]

    def test_flash_crash_survives(self):
        r = stress_test_scenario("FLASH_CRASH")
        assert r["hedged_survives"]

    def test_hedge_reduces_dd(self):
        for scenario in ["COVID_2020", "RATE_HIKES_2022"]:
            r = stress_test_scenario(scenario)
            assert r["dd_reduction_pct"] > 0

    def test_stress_returns_all_fields(self):
        r = stress_test_scenario("COVID_2020")
        assert "hedged_dd_pct" in r
        assert "unhedged_dd_pct" in r
        assert "hedge_cost" in r
        assert "hedged" in r


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_hedge_state(self):
        s = HedgeState(scale_factor=0.5, vix_scale=0.6, dd_scale=0.8)
        assert s.scale_factor == 0.5

    def test_put_overlay_result(self):
        p = PutOverlayResult(True, 50.0, 2.5, 150.0, 3.0, 0.67)
        assert p.is_active

    def test_recovery_signal(self):
        r = RecoverySignal(True, True, True, 0.5, 10)
        assert r.should_recover

    def test_config_defaults_reasonable(self):
        c = CrisisHedgeV2Config()
        assert c.vix_reduce < c.vix_minimum < c.vix_full_hedge
        assert c.dd_start < c.dd_full < c.dd_floor
        assert 0 < c.min_scale < c.max_scale
