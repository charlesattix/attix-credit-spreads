"""Tests for compass/strategy_screener.py."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.strategy_screener import (
    NorthStarCriteria, StrategySpec, WFFold, SensitivityResult, ScreenResult,
    corrected_sharpe, compute_metrics, yearly_returns, years_profitable_fraction,
    walk_forward, parameter_sensitivity, scan_rule_zero, grade_strategy,
    screen, format_result, TRADING_DAYS,
)


# ─── Helpers (deterministic, NOT used as backtest results) ─────────────────

def _det_prices(n=2000, drift=0.0004, vol=0.01, seed=1):
    """Deterministic price series for unit-testing screener mechanics only.

    These are NEVER reported as a strategy result — Rule Zero applies to
    backtest *results*, not to test fixtures for the screener machinery.
    """
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2018-01-02", periods=n)
    rets = rng.normal(drift, vol, n)
    return pd.DataFrame({"SPY": 100 * np.cumprod(1 + rets)}, index=idx)


def _good_strategy(prices, params):
    """Buy-the-dip-style: positive expected return, simple."""
    sma = prices["SPY"].rolling(params.get("lookback", 20)).mean()
    z = (prices["SPY"] - sma) / prices["SPY"].rolling(20).std()
    signal = (-z.shift(1)).clip(-1, 1).fillna(0)
    spy_rets = prices["SPY"].pct_change().fillna(0)
    return signal * spy_rets * params.get("scale", 1.0)


def _trash_strategy(prices, params):
    """Always loses — used for FAIL grading."""
    spy_rets = prices["SPY"].pct_change().fillna(0)
    return -spy_rets - 0.0005  # short SPY + cost


def _zero_strategy(prices, params):
    return pd.Series(0.0, index=prices.index)


def _violator_strategy(prices, params):
    """Synthetic-data tell — should be caught by Rule Zero scanner."""
    n = len(prices)
    fake = np.random.normal(0.001, 0.01, n)  # forbidden
    return pd.Series(fake, index=prices.index)


# ═══════════════════════════════════════════════════════════════════════════

class TestSharpe:
    def test_formula_matches_canonical(self):
        rets = np.array([0.01, -0.005, 0.008, 0.003, -0.002])
        expected = rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
        assert abs(corrected_sharpe(rets) - expected) < 1e-6

    def test_empty(self):
        assert corrected_sharpe(np.array([])) == 0.0

    def test_constant_zero(self):
        assert corrected_sharpe(np.full(100, 0.001)) == 0.0


class TestMetrics:
    def test_all_fields_present(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(0.001, 0.005, 252))
        for k in ("cagr", "sharpe", "sortino", "dd", "calmar", "vol"):
            assert k in m

    def test_positive_drift(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(0.001, 0.005, 252))
        assert m["cagr"] > 0
        assert m["sharpe"] > 0

    def test_negative_drift(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(-0.002, 0.005, 252))
        assert m["cagr"] < 0


class TestYearlyReturns:
    def test_groups_by_year(self):
        idx = pd.bdate_range("2020-01-02", "2022-12-31")
        s = pd.Series(0.001, index=idx)
        y = yearly_returns(s)
        assert set(y.keys()) == {2020, 2021, 2022}

    def test_profitable_fraction(self):
        y = {2020: 0.1, 2021: -0.05, 2022: 0.2, 2023: 0.0}
        assert years_profitable_fraction(y) == 0.5

    def test_empty(self):
        assert yearly_returns(pd.Series(dtype=float)) == {}
        assert years_profitable_fraction({}) == 0.0


class TestWalkForward:
    def test_produces_folds(self):
        idx = pd.bdate_range("2018-01-02", periods=2000)
        rets = pd.Series(np.random.RandomState(1).normal(0.0005, 0.01, 2000), index=idx)
        folds = walk_forward(rets)
        assert len(folds) >= 3
        for f in folds:
            assert isinstance(f, WFFold)
            assert f.train_start < f.train_end
            assert f.test_start <= f.test_end

    def test_insufficient_data(self):
        idx = pd.bdate_range("2020-01-02", periods=50)
        rets = pd.Series(0.001, index=idx)
        assert walk_forward(rets) == []


class TestSensitivity:
    def test_detects_robust_param(self):
        prices = _det_prices(2000)
        spec = StrategySpec(
            name="t", data_source="test", loader=lambda: prices,
            signal_fn=_good_strategy,
            default_params={"lookback": 20, "scale": 1.0},
            param_grid={"lookback": [15, 20, 25, 30]},
        )
        sens = parameter_sensitivity(spec, prices)
        assert len(sens) == 1
        assert sens[0].param == "lookback"
        assert len(sens[0].sharpes) == 4

    def test_detects_cliff(self):
        prices = _det_prices(2000)

        def cliffy(prices, params):
            if params.get("k", 1) == 99:
                return _good_strategy(prices, params)
            return _trash_strategy(prices, params)

        spec = StrategySpec(
            name="t", data_source="test", loader=lambda: prices,
            signal_fn=cliffy, default_params={"k": 1},
            param_grid={"k": [1, 5, 99]},
        )
        sens = parameter_sensitivity(spec, prices)
        assert sens[0].cliff_detected


class TestRuleZeroScanner:
    def test_clean_function(self):
        clean, warn = scan_rule_zero(_good_strategy)
        assert clean
        assert warn == []

    def test_catches_np_random(self):
        clean, warn = scan_rule_zero(_violator_strategy)
        assert not clean
        assert any("np" in w and "random" in w for w in warn)

    def test_lambda_unintrospectable(self):
        # lambdas may or may not be introspectable; should not crash
        f = lambda p, x: pd.Series(0.0, index=p.index)
        clean, warn = scan_rule_zero(f)
        assert isinstance(clean, bool)


class TestGrading:
    def test_pass_path(self):
        m = {"cagr": 0.15, "sharpe": 1.5, "dd": 0.10, "sortino": 2.0,
             "calmar": 1.5, "vol": 0.10}
        grade, fails, _ = grade_strategy(
            m, corr=0.1, yearly={2020: 0.1, 2021: 0.2, 2022: 0.05},
            avg_oos_sharpe=1.2, sensitivity=[],
            rule_zero_clean=True, crit=NorthStarCriteria(),
        )
        assert grade == "PASS"
        assert fails == []

    def test_fail_on_low_sharpe(self):
        m = {"cagr": 0.15, "sharpe": 0.3, "dd": 0.10, "sortino": 0.5,
             "calmar": 1.5, "vol": 0.10}
        grade, fails, _ = grade_strategy(
            m, corr=0.1, yearly={2020: 0.1, 2021: 0.2, 2022: 0.05},
            avg_oos_sharpe=0.2, sensitivity=[],
            rule_zero_clean=True, crit=NorthStarCriteria(),
        )
        assert grade == "FAIL"
        assert any("Sharpe" in f for f in fails)

    def test_fail_on_high_corr(self):
        m = {"cagr": 0.15, "sharpe": 1.5, "dd": 0.10, "sortino": 2.0,
             "calmar": 1.5, "vol": 0.10}
        grade, fails, _ = grade_strategy(
            m, corr=0.9, yearly={2020: 0.1, 2021: 0.2, 2022: 0.05},
            avg_oos_sharpe=1.2, sensitivity=[],
            rule_zero_clean=True, crit=NorthStarCriteria(),
        )
        assert any("corr to EXP-1220" in f for f in fails)

    def test_rule_zero_violation_hard_fails(self):
        m = {"cagr": 0.5, "sharpe": 5.0, "dd": 0.05, "sortino": 6.0,
             "calmar": 10.0, "vol": 0.10}
        grade, fails, _ = grade_strategy(
            m, corr=0.1, yearly={2020: 0.5, 2021: 0.5, 2022: 0.5},
            avg_oos_sharpe=4.0, sensitivity=[],
            rule_zero_clean=False, crit=NorthStarCriteria(),
        )
        assert any("RULE ZERO" in f for f in fails)
        assert grade == "FAIL"

    def test_conditional_one_fail(self):
        m = {"cagr": 0.15, "sharpe": 1.5, "dd": 0.30, "sortino": 2.0,  # DD too high
             "calmar": 0.5, "vol": 0.10}
        grade, fails, _ = grade_strategy(
            m, corr=0.1, yearly={2020: 0.1, 2021: 0.2, 2022: 0.05},
            avg_oos_sharpe=1.2, sensitivity=[],
            rule_zero_clean=True, crit=NorthStarCriteria(),
        )
        assert grade == "CONDITIONAL"


class TestEndToEndScreen:
    def test_full_screen_runs(self):
        prices = _det_prices(2000)
        spec = StrategySpec(
            name="EXP-TEST", data_source="test fixture (mechanics check)",
            loader=lambda: prices, signal_fn=_good_strategy,
            default_params={"lookback": 20, "scale": 1.0},
            param_grid={"lookback": [10, 20, 40]},
        )
        result = screen(spec)
        assert isinstance(result, ScreenResult)
        assert result.grade in ("PASS", "CONDITIONAL", "FAIL")
        assert result.n_days > 0
        assert result.rule_zero_clean
        assert len(result.wf_folds) >= 1
        assert len(result.sensitivity) == 1

    def test_violator_hard_fails(self):
        prices = _det_prices(1000)
        spec = StrategySpec(
            name="EXP-BAD", data_source="banned",
            loader=lambda: prices, signal_fn=_violator_strategy,
            default_params={}, param_grid={},
        )
        result = screen(spec)
        assert not result.rule_zero_clean
        assert result.grade == "FAIL"
        assert any("RULE ZERO" in f for f in result.fail_reasons)

    def test_zero_strategy_grades_fail(self):
        prices = _det_prices(2000)
        spec = StrategySpec(
            name="EXP-ZERO", data_source="test", loader=lambda: prices,
            signal_fn=_zero_strategy, default_params={}, param_grid={},
        )
        result = screen(spec)
        assert result.grade == "FAIL"
        assert result.sharpe == 0.0

    def test_correlation_to_reference(self):
        prices = _det_prices(2000)
        # Build a "EXP-1220" reference that perfectly correlates with strategy
        ref = _good_strategy(prices, {"lookback": 20, "scale": 1.0})
        spec = StrategySpec(
            name="EXP-CORR", data_source="test", loader=lambda: prices,
            signal_fn=_good_strategy,
            default_params={"lookback": 20, "scale": 1.0}, param_grid={},
        )
        result = screen(spec, exp1220_returns=ref)
        assert result.corr_to_exp1220 is not None
        assert abs(result.corr_to_exp1220 - 1.0) < 0.001

    def test_format_result_renders(self):
        prices = _det_prices(2000)
        spec = StrategySpec(
            name="EXP-FMT", data_source="test", loader=lambda: prices,
            signal_fn=_good_strategy,
            default_params={"lookback": 20, "scale": 1.0}, param_grid={},
        )
        result = screen(spec)
        s = format_result(result)
        assert "EXP-FMT" in s
        assert "CAGR" in s
        assert "Sharpe" in s


class TestNorthStarCriteria:
    def test_defaults(self):
        c = NorthStarCriteria()
        assert c.min_cagr_pct == 8.0
        assert c.min_sharpe == 1.0
        assert c.max_dd_pct == 25.0
        assert c.max_corr_to_exp1220 == 0.30

    def test_custom(self):
        c = NorthStarCriteria(min_sharpe=2.5)
        assert c.min_sharpe == 2.5
