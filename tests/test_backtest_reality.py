"""Tests for compass/backtest_reality.py — backtest reality checker."""
from __future__ import annotations
import numpy as np
import pytest
from compass.backtest_reality import (
    BacktestConfig, BacktestRealityChecker, BiasFlag, CapacityCheck,
    ComplexityMetrics, CredibilityScore, DegradationResult,
    SensitivityPoint,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _good_returns(n=500, seed=42):
    return np.random.RandomState(seed).normal(0.001, 0.01, n)

def _make_config(**overrides):
    defaults = dict(
        daily_returns=_good_returns(),
        is_fraction=0.7,
        commission_per_trade=0.65,
        assumed_slippage_bps=5.0,
        realistic_slippage_bps=5.0,
        assumed_fill_rate=0.90,
        realistic_fill_rate=0.85,
        avg_trade_contracts=5.0,
        avg_daily_volume=5000.0,
        max_participation=0.02,
        n_free_params=10,
        n_rules=5,
        lookback_days=60,
        n_assets_traded=1,
        n_assets_universe=1,
        uses_close_price_for_signal=False,
        signal_generated_before_trade=True,
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)

def _make_checker(**overrides):
    return BacktestRealityChecker(_make_config(**overrides))

# ── Dataclass tests ──────────────────────────────────────────────────────

class TestDataclasses:
    def test_bias_flag(self):
        f = BiasFlag("test", "cost", "warning", False, 5, "detail")
        assert f.score_penalty == pytest.approx(5)
    def test_sensitivity_point(self):
        s = SensitivityPoint("p", 1.0, 1.1, 2.0, 1.8, -0.1)
        assert s.change_pct == pytest.approx(-0.1)
    def test_degradation(self):
        d = DegradationResult(2.0, 1.5, 0.75, 0.3, 0.2, -0.1, -0.15, True)
        assert d.degradation_ratio == pytest.approx(0.75)
    def test_capacity(self):
        c = CapacityCheck(5, 5000, 0.001, 0.02, True, 0.1)
        assert c.passed
    def test_complexity(self):
        c = ComplexityMetrics(10, 500, 0.02, 5, 490, 30)
        assert c.params_per_point == pytest.approx(0.02)
    def test_credibility(self):
        c = CredibilityScore(85, "A", 0, 1, 10, ["issue"])
        assert c.grade == "A"
    def test_config_defaults(self):
        c = BacktestConfig(daily_returns=np.array([0.01]))
        assert c.is_fraction == 0.7

# ── Look-ahead bias ─────────────────────────────────────────────────────

class TestLookAhead:
    def test_close_price_flag(self):
        ch = _make_checker(uses_close_price_for_signal=True); ch.check()
        flag = [f for f in ch.flags if f.name == "close_price_signal"][0]
        assert not flag.passed
        assert flag.severity == "critical"

    def test_no_close_price_passes(self):
        ch = _make_checker(uses_close_price_for_signal=False); ch.check()
        flag = [f for f in ch.flags if f.name == "close_price_signal"][0]
        assert flag.passed

    def test_signal_timing_flag(self):
        ch = _make_checker(signal_generated_before_trade=False); ch.check()
        flag = [f for f in ch.flags if f.name == "signal_timing"][0]
        assert not flag.passed

    def test_signal_timing_ok(self):
        ch = _make_checker(signal_generated_before_trade=True); ch.check()
        flag = [f for f in ch.flags if f.name == "signal_timing"][0]
        assert flag.passed

    def test_lookback_warning(self):
        ch = _make_checker(lookback_days=400, daily_returns=_good_returns(500)); ch.check()
        flag = [f for f in ch.flags if f.name == "lookback_ratio"][0]
        assert not flag.passed

# ── Survivorship bias ────────────────────────────────────────────────────

class TestSurvivorship:
    def test_narrow_selection_warned(self):
        ch = _make_checker(n_assets_traded=1, n_assets_universe=500); ch.check()
        flag = [f for f in ch.flags if f.name == "survivorship_selection"][0]
        assert not flag.passed

    def test_broad_selection_ok(self):
        ch = _make_checker(n_assets_traded=100, n_assets_universe=500); ch.check()
        flag = [f for f in ch.flags if f.name == "survivorship_selection"][0]
        assert flag.passed

# ── Transaction costs ────────────────────────────────────────────────────

class TestCosts:
    def test_low_slippage_critical(self):
        ch = _make_checker(assumed_slippage_bps=1.0, realistic_slippage_bps=5.0); ch.check()
        flag = [f for f in ch.flags if f.name == "slippage_underestimate"][0]
        assert not flag.passed
        assert flag.severity == "critical"

    def test_realistic_slippage_ok(self):
        ch = _make_checker(assumed_slippage_bps=5.0, realistic_slippage_bps=5.0); ch.check()
        flag = [f for f in ch.flags if f.name == "slippage_realistic"][0]
        assert flag.passed

    def test_zero_commission_warned(self):
        ch = _make_checker(commission_per_trade=0); ch.check()
        flag = [f for f in ch.flags if f.name == "zero_commission"][0]
        assert not flag.passed

    def test_nonzero_commission_ok(self):
        ch = _make_checker(commission_per_trade=0.65); ch.check()
        flag = [f for f in ch.flags if f.name == "commission_present"][0]
        assert flag.passed

# ── Fill realism ─────────────────────────────────────────────────────────

class TestFills:
    def test_perfect_fill_warned(self):
        ch = _make_checker(assumed_fill_rate=1.0); ch.check()
        flag = [f for f in ch.flags if f.name == "perfect_fills"][0]
        assert not flag.passed

    def test_realistic_fill_ok(self):
        ch = _make_checker(assumed_fill_rate=0.85); ch.check()
        flag = [f for f in ch.flags if f.name == "fill_rate_ok"][0]
        assert flag.passed

# ── Capacity ─────────────────────────────────────────────────────────────

class TestCapacity:
    def test_over_capacity_flagged(self):
        ch = _make_checker(avg_trade_contracts=200, avg_daily_volume=5000,
                           max_participation=0.02); ch.check()
        assert not ch.capacity.passed
        cap_flags = [f for f in ch.flags if f.name == "capacity_exceeded"]
        assert len(cap_flags) == 1

    def test_within_capacity_ok(self):
        ch = _make_checker(avg_trade_contracts=5, avg_daily_volume=5000); ch.check()
        assert ch.capacity.passed

    def test_participation_rate(self):
        ch = _make_checker(avg_trade_contracts=50, avg_daily_volume=5000); ch.check()
        assert ch.capacity.participation_rate == pytest.approx(0.01)

# ── Degradation ──────────────────────────────────────────────────────────

class TestDegradation:
    def test_degradation_computed(self):
        ch = _make_checker(); ch.check()
        assert ch.degradation is not None

    def test_ratio_range(self):
        ch = _make_checker(); ch.check()
        # With normal data, ratio should be positive
        assert ch.degradation.degradation_ratio != 0

    def test_severe_overfit(self):
        rng = np.random.RandomState(42)
        # IS: great returns, OOS: flat
        is_ret = rng.normal(0.005, 0.005, 350)
        oos_ret = rng.normal(0.0, 0.01, 150)
        combined = np.concatenate([is_ret, oos_ret])
        ch = _make_checker(daily_returns=combined); ch.check()
        assert ch.degradation.degradation_ratio < 1.0

    def test_short_data_ok(self):
        ch = _make_checker(daily_returns=np.array([0.01] * 15)); ch.check()
        assert ch.degradation is not None

# ── Complexity ───────────────────────────────────────────────────────────

class TestComplexity:
    def test_high_params_flagged(self):
        ch = _make_checker(n_free_params=100, daily_returns=_good_returns(500)); ch.check()
        flag = [f for f in ch.flags if f.name == "params_data_ratio"][0]
        assert not flag.passed

    def test_low_params_ok(self):
        ch = _make_checker(n_free_params=5); ch.check()
        flag = [f for f in ch.flags if f.name == "params_data_ratio"][0]
        assert flag.passed

    def test_degrees_of_freedom(self):
        ch = _make_checker(n_free_params=10); ch.check()
        assert ch.complexity.degrees_of_freedom == len(ch.returns) - 10

# ── Sensitivity ──────────────────────────────────────────────────────────

class TestSensitivity:
    def test_default_sensitivity(self):
        ch = _make_checker(); ch.check()
        assert len(ch.sensitivity) == 4  # return ±10%, vol ±10%

    def test_sensitivity_fields(self):
        ch = _make_checker(); ch.check()
        for s in ch.sensitivity:
            assert isinstance(s.base_sharpe, float)
            assert isinstance(s.perturbed_sharpe, float)

    def test_custom_sensitivity(self):
        def my_fn(param, val):
            return _good_returns() * val
        config = _make_config(
            param_values={"alpha": 1.0},
            param_sensitivity_fn=my_fn,
        )
        ch = BacktestRealityChecker(config); ch.check()
        params = {s.param for s in ch.sensitivity}
        assert "alpha" in params

# ── Overfit indicators ───────────────────────────────────────────────────

class TestOverfitIndicators:
    def test_short_data_flagged(self):
        ch = _make_checker(daily_returns=np.array([0.01] * 30)); ch.check()
        flag = [f for f in ch.flags if f.name == "insufficient_data"][0]
        assert not flag.passed

    def test_suspicious_sharpe(self):
        # Very high Sharpe
        rets = np.random.RandomState(42).normal(0.01, 0.005, 500)
        ch = _make_checker(daily_returns=rets); ch.check()
        flag = [f for f in ch.flags if f.name == "suspicious_sharpe"]
        assert len(flag) > 0

# ── Credibility score ────────────────────────────────────────────────────

class TestCredibility:
    def test_good_config_high_score(self):
        ch = _make_checker(); ch.check()
        assert ch.credibility.score >= 60

    def test_bad_config_low_score(self):
        ch = _make_checker(
            uses_close_price_for_signal=True,
            signal_generated_before_trade=False,
            assumed_slippage_bps=0.5,
            realistic_slippage_bps=5.0,
            assumed_fill_rate=1.0,
            avg_trade_contracts=200,
            n_free_params=100,
        ); ch.check()
        assert ch.credibility.score < 40

    def test_grade_range(self):
        ch = _make_checker(); ch.check()
        assert ch.credibility.grade in ("A", "B", "C", "D", "F")

    def test_critical_count(self):
        ch = _make_checker(uses_close_price_for_signal=True); ch.check()
        assert ch.credibility.n_critical >= 1

    def test_passed_count(self):
        ch = _make_checker(); ch.check()
        assert ch.credibility.n_passed > 0

    def test_top_issues_list(self):
        ch = _make_checker(); ch.check()
        assert isinstance(ch.credibility.top_issues, list)

# ── Pipeline ─────────────────────────────────────────────────────────────

class TestPipeline:
    def test_check_keys(self):
        ch = _make_checker()
        result = ch.check()
        expected = {"flags", "sensitivity", "degradation", "capacity",
                    "complexity", "credibility"}
        assert set(result.keys()) == expected

# ── Report ───────────────────────────────────────────────────────────────

class TestReport:
    def test_html(self, tmp_path):
        ch = _make_checker()
        path = ch.generate_report(str(tmp_path / "r.html"))
        c = open(path).read()
        assert "<!DOCTYPE html>" in c and "Reality Check" in c

    def test_sections(self, tmp_path):
        ch = _make_checker()
        path = ch.generate_report(str(tmp_path / "r.html"))
        c = open(path).read()
        assert "Bias Flags" in c and "Degradation" in c and "Sensitivity" in c
        assert "Complexity" in c and "Capacity" in c

    def test_charts(self, tmp_path):
        ch = _make_checker()
        path = ch.generate_report(str(tmp_path / "r.html"))
        assert "data:image/png;base64," in open(path).read()

    def test_auto_check(self, tmp_path):
        ch = _make_checker()
        assert ch.credibility is None
        ch.generate_report(str(tmp_path / "r.html"))
        assert ch.credibility is not None

    def test_default_path(self):
        ch = _make_checker()
        path = ch.generate_report()
        assert "backtest_reality.html" in path
