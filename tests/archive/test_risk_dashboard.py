"""Tests for compass.risk_dashboard — portfolio risk monitoring."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.risk_dashboard import (
    STRESS_SCENARIOS,
    ConcentrationRisk,
    GreeksExposure,
    MarginState,
    RiskDashboard,
    RiskDashboardResult,
    StressResult,
    VaREstimate,
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _returns(n: int = 500, n_strats: int = 3, seed: int = 42) -> dict:
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="B")
    common = rng.randn(n) * 0.008
    return {
        f"S{i}": pd.Series(common * (0.5 + i * 0.2) + rng.randn(n) * 0.004, index=idx)
        for i in range(n_strats)
    }

def _weights(n: int = 3) -> dict:
    return {f"S{i}": 1.0 / n for i in range(n)}


# ── VaR / CVaR ──────────────────────────────────────────────────────────────
class TestVaR:
    def test_six_estimates(self):
        r = RiskDashboard().compute(_returns(), _weights())
        assert len(r.var_estimates) == 6  # 3 methods × 2 confidence levels

    def test_methods_present(self):
        r = RiskDashboard().compute(_returns(), _weights())
        methods = {v.method for v in r.var_estimates}
        assert methods == {"historical", "parametric", "monte_carlo"}

    def test_var_positive(self):
        r = RiskDashboard().compute(_returns(), _weights())
        for v in r.var_estimates:
            assert v.var > 0

    def test_cvar_geq_var(self):
        r = RiskDashboard().compute(_returns(), _weights())
        for v in r.var_estimates:
            assert v.cvar >= v.var - 1

    def test_99_geq_95(self):
        r = RiskDashboard().compute(_returns(), _weights())
        hist = [v for v in r.var_estimates if v.method == "historical"]
        var95 = next(v for v in hist if v.confidence == 0.95)
        var99 = next(v for v in hist if v.confidence == 0.99)
        assert var99.var >= var95.var - 1

    def test_scales_with_portfolio(self):
        small = RiskDashboard().compute(_returns(), _weights(), portfolio_value=50_000)
        large = RiskDashboard().compute(_returns(), _weights(), portfolio_value=200_000)
        s_var = next(v for v in small.var_estimates if v.method == "historical" and v.confidence == 0.95)
        l_var = next(v for v in large.var_estimates if v.method == "historical" and v.confidence == 0.95)
        assert l_var.var > s_var.var


# ── Stress tests ────────────────────────────────────────────────────────────
class TestStress:
    def test_four_scenarios(self):
        r = RiskDashboard().compute(_returns(), _weights())
        assert len(r.stress_results) == 4

    def test_all_negative_loss(self):
        r = RiskDashboard().compute(_returns(), _weights())
        for s in r.stress_results:
            assert s.portfolio_loss_pct < 0

    def test_covid_worst(self):
        r = RiskDashboard().compute(_returns(), _weights())
        worst = min(r.stress_results, key=lambda s: s.portfolio_loss_pct)
        assert worst.scenario == "COVID_2020"

    def test_recovery_positive(self):
        r = RiskDashboard().compute(_returns(), _weights())
        for s in r.stress_results:
            assert s.recovery_days > 0

    def test_dollar_loss_scales(self):
        small = RiskDashboard().compute(_returns(), _weights(), portfolio_value=50_000)
        large = RiskDashboard().compute(_returns(), _weights(), portfolio_value=200_000)
        s_covid = next(s for s in small.stress_results if s.scenario == "COVID_2020")
        l_covid = next(s for s in large.stress_results if s.scenario == "COVID_2020")
        assert l_covid.portfolio_loss_dollar > s_covid.portfolio_loss_dollar


# ── Greeks ──────────────────────────────────────────────────────────────────
class TestGreeks:
    def test_computed_when_provided(self):
        r = RiskDashboard().compute(
            _returns(), _weights(),
            greeks={"delta": -30, "gamma": -2, "theta": 15, "vega": 50},
        )
        assert r.greeks is not None
        assert r.greeks.delta == -30

    def test_delta_dollars(self):
        r = RiskDashboard().compute(
            _returns(), _weights(),
            greeks={"delta": -10, "gamma": 0, "theta": 0, "vega": 0},
            spy_price=450.0,
        )
        assert r.greeks.delta_dollars == -10 * 450 * 100

    def test_none_without_greeks(self):
        r = RiskDashboard().compute(_returns(), _weights())
        assert r.greeks is None


# ── Concentration ───────────────────────────────────────────────────────────
class TestConcentration:
    def test_computed(self):
        r = RiskDashboard().compute(
            _returns(), _weights(),
            positions={"SPY_PUT": 0.4, "QQQ_PUT": 0.3, "IWM_PUT": 0.2, "TLT_CALL": 0.1},
        )
        assert r.concentration is not None
        assert r.concentration.max_position_name == "SPY_PUT"

    def test_herfindahl_bounds(self):
        r = RiskDashboard().compute(
            _returns(), _weights(),
            positions={"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25},
        )
        assert 0 < r.concentration.herfindahl <= 1

    def test_single_position_concentrated(self):
        r = RiskDashboard().compute(
            _returns(), _weights(),
            positions={"A": 1.0},
        )
        assert r.concentration.is_concentrated

    def test_equal_weights_diversified(self):
        r = RiskDashboard().compute(
            _returns(), _weights(),
            positions={f"P{i}": 0.1 for i in range(10)},
        )
        assert not r.concentration.is_concentrated

    def test_sectors(self):
        r = RiskDashboard().compute(
            _returns(), _weights(),
            positions={"SPY": 0.5, "QQQ": 0.3, "TLT": 0.2},
            sectors={"SPY": "equity", "QQQ": "equity", "TLT": "bond"},
        )
        assert "equity" in r.concentration.sector_exposures

    def test_none_without_positions(self):
        r = RiskDashboard().compute(_returns(), _weights())
        assert r.concentration is None


# ── Margin ──────────────────────────────────────────────────────────────────
class TestMargin:
    def test_computed(self):
        r = RiskDashboard().compute(
            _returns(), _weights(),
            margin_required=40_000, margin_available=100_000,
        )
        assert r.margin is not None
        assert r.margin.utilisation_pct == 40.0

    def test_excess(self):
        r = RiskDashboard().compute(
            _returns(), _weights(),
            margin_required=30_000, margin_available=80_000,
        )
        assert r.margin.excess_margin == 50_000

    def test_none_when_zero(self):
        r = RiskDashboard().compute(_returns(), _weights())
        assert r.margin is None


# ── Correlation ─────────────────────────────────────────────────────────────
class TestCorrelation:
    def test_matrix_present(self):
        r = RiskDashboard().compute(_returns(n_strats=3), _weights(3))
        assert r.correlation_matrix is not None
        assert r.correlation_matrix.shape == (3, 3)

    def test_diagonal_one(self):
        r = RiskDashboard().compute(_returns(), _weights())
        diag = np.diag(r.correlation_matrix.values)
        np.testing.assert_allclose(diag, 1.0, atol=0.01)

    def test_symmetric(self):
        r = RiskDashboard().compute(_returns(), _weights())
        m = r.correlation_matrix.values
        np.testing.assert_allclose(m, m.T, atol=0.01)

    def test_single_strategy_no_matrix(self):
        ret = {"S0": pd.Series(np.random.randn(100))}
        r = RiskDashboard().compute(ret, {"S0": 1.0})
        assert r.correlation_matrix is None


# ── HTML report ─────────────────────────────────────────────────────────────
class TestHTMLReport:
    def test_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash = RiskDashboard()
            r = dash.compute(
                _returns(), _weights(),
                greeks={"delta": -20, "gamma": -1, "theta": 10, "vega": 30},
                positions={"A": 0.4, "B": 0.3, "C": 0.3},
                margin_required=40_000, margin_available=100_000,
            )
            path = dash.generate_report(r, str(Path(tmp) / "r.html"))
            assert path.exists()
            assert path.stat().st_size > 0

    def test_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash = RiskDashboard()
            r = dash.compute(
                _returns(), _weights(),
                greeks={"delta": -20, "gamma": -1, "theta": 10, "vega": 30},
                positions={"A": 0.4, "B": 0.3, "C": 0.3},
                margin_required=40_000, margin_available=100_000,
            )
            path = dash.generate_report(r, str(Path(tmp) / "s.html"))
            html = path.read_text()
            assert "VaR" in html
            assert "Stress" in html
            assert "Greeks" in html
            assert "Concentration" in html
            assert "Margin" in html
            assert "Correlation" in html

    def test_valid_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash = RiskDashboard()
            r = dash.compute(_returns(), _weights())
            path = dash.generate_report(r, str(Path(tmp) / "v.html"))
            html = path.read_text()
            assert html.startswith("<!DOCTYPE html>")
            assert "</html>" in html


# ── Edge cases ──────────────────────────────────────────────────────────────
class TestEdgeCases:
    def test_empty_returns(self):
        r = RiskDashboard().compute({}, {})
        assert r.var_estimates == []

    def test_short_series(self):
        ret = {"S0": pd.Series([0.01] * 10)}
        r = RiskDashboard().compute(ret, {"S0": 1.0})
        assert r.var_estimates == []

    def test_generated_at(self):
        r = RiskDashboard().compute(_returns(), _weights())
        assert len(r.generated_at) > 0


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_var_estimate(self):
        v = VaREstimate("historical", 0.95, 5000, 7000)
        assert v.method == "historical"

    def test_stress_result(self):
        s = StressResult("COVID", -0.34, -51.0, 51000, 300, 82)
        assert s.recovery_days == 300

    def test_result_defaults(self):
        r = RiskDashboardResult()
        assert r.var_estimates == []
        assert r.portfolio_value == 0
