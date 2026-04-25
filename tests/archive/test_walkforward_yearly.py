"""Tests for compass.walkforward_yearly — EXP-1580."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from compass.walkforward_yearly import (
    DD_CAP,
    DEFAULT_LEVERAGE,
    EXP400_YEARLY,
    EXP401_YEARLY,
    NORTH_STAR_WEIGHTS,
    SPY_YEARLY,
    STRATEGY_CORRELATIONS,
    STRATEGY_YEARLY,
    YEARS,
    DDCappedYearMetrics,
    LeveredYearMetrics,
    PortfolioYearMetrics,
    WalkForwardYearly,
    WalkForwardYearlyResult,
    YearMetrics,
    _compound_cagr,
    _get_correlation,
    compute_dd_capped_year,
    compute_levered_year,
    compute_portfolio_year,
)


# ── Data integrity tests ─────────────────────────────────────────────────


class TestDataIntegrity:
    """Verify the input data is well-formed."""

    def test_weights_sum_to_one(self):
        total = sum(NORTH_STAR_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01, f"Weights sum to {total}, expected ~1.0"

    def test_all_strategies_have_yearly_data(self):
        for name in NORTH_STAR_WEIGHTS:
            assert name in STRATEGY_YEARLY, f"Missing yearly data for {name}"

    def test_all_strategies_cover_all_years(self):
        for name, data in STRATEGY_YEARLY.items():
            years_present = {ym.year for ym in data}
            for y in YEARS:
                assert y in years_present, f"{name} missing year {y}"

    def test_spy_covers_all_years(self):
        for y in YEARS:
            assert y in SPY_YEARLY, f"SPY missing year {y}"

    def test_exp400_covers_all_years(self):
        for y in YEARS:
            assert y in EXP400_YEARLY, f"EXP-400 missing year {y}"

    def test_exp401_covers_all_years(self):
        for y in YEARS:
            assert y in EXP401_YEARLY, f"EXP-401 missing year {y}"

    def test_drawdowns_are_negative(self):
        for name, data in STRATEGY_YEARLY.items():
            for ym in data:
                assert ym.max_dd <= 0, f"{name} {ym.year}: DD should be negative, got {ym.max_dd}"

    def test_win_rates_in_range(self):
        for name, data in STRATEGY_YEARLY.items():
            for ym in data:
                assert 0 <= ym.win_rate <= 1, f"{name} {ym.year}: win_rate {ym.win_rate} out of range"

    def test_correlations_in_range(self):
        for (a, b), rho in STRATEGY_CORRELATIONS.items():
            assert -1 <= rho <= 1, f"Correlation ({a},{b})={rho} out of range"

    def test_leverage_positive(self):
        assert DEFAULT_LEVERAGE > 0

    def test_dd_cap_positive(self):
        assert DD_CAP > 0

    def test_years_sorted(self):
        assert YEARS == sorted(YEARS)

    def test_four_strategies(self):
        assert len(NORTH_STAR_WEIGHTS) == 4


# ── Correlation helper ───────────────────────────────────────────────────


class TestGetCorrelation:
    def test_self_correlation(self):
        assert _get_correlation("ML-CS-860", "ML-CS-860") == 1.0

    def test_known_pair(self):
        rho = _get_correlation("ML-CS-860", "Regime-Lev")
        assert rho == 0.42

    def test_known_pair_reversed(self):
        rho = _get_correlation("Regime-Lev", "ML-CS-860")
        assert rho == 0.42

    def test_unknown_pair_default(self):
        rho = _get_correlation("Unknown-A", "Unknown-B")
        assert rho == 0.20


# ── Portfolio year computation ───────────────────────────────────────────


class TestComputePortfolioYear:
    def test_returns_portfolio_year_metrics(self):
        result = compute_portfolio_year(2021, NORTH_STAR_WEIGHTS, STRATEGY_YEARLY)
        assert isinstance(result, PortfolioYearMetrics)
        assert result.year == 2021

    def test_cagr_is_weighted_sum(self):
        result = compute_portfolio_year(2021, NORTH_STAR_WEIGHTS, STRATEGY_YEARLY)
        expected = (
            0.405 * 24.7 + 0.209 * 61.3 + 0.205 * 8.9 + 0.181 * 31.2
        )
        assert abs(result.cagr - expected) < 0.5

    def test_dd_is_negative(self):
        for y in YEARS:
            result = compute_portfolio_year(y, NORTH_STAR_WEIGHTS, STRATEGY_YEARLY)
            assert result.max_dd <= 0, f"Year {y}: DD should be negative"

    def test_dd_less_than_worst_individual(self):
        """Portfolio DD should be <= worst individual strategy DD (diversification)."""
        for y in YEARS:
            result = compute_portfolio_year(y, NORTH_STAR_WEIGHTS, STRATEGY_YEARLY)
            worst_individual = min(
                ym.max_dd for name in NORTH_STAR_WEIGHTS
                for ym in STRATEGY_YEARLY[name] if ym.year == y
            )
            # Portfolio DD should be better (less negative) than worst individual
            assert result.max_dd >= worst_individual

    def test_sharpe_positive_for_positive_year(self):
        result = compute_portfolio_year(2021, NORTH_STAR_WEIGHTS, STRATEGY_YEARLY)
        assert result.sharpe > 0

    def test_trades_aggregated(self):
        result = compute_portfolio_year(2021, NORTH_STAR_WEIGHTS, STRATEGY_YEARLY)
        assert result.n_trades > 0

    def test_win_rate_in_range(self):
        for y in YEARS:
            result = compute_portfolio_year(y, NORTH_STAR_WEIGHTS, STRATEGY_YEARLY)
            assert 0 <= result.win_rate <= 1

    def test_strategy_contributions_present(self):
        result = compute_portfolio_year(2021, NORTH_STAR_WEIGHTS, STRATEGY_YEARLY)
        assert len(result.strategy_contributions) == 4

    def test_all_years_positive_cagr(self):
        """North Star should be profitable every year."""
        for y in YEARS:
            result = compute_portfolio_year(y, NORTH_STAR_WEIGHTS, STRATEGY_YEARLY)
            assert result.cagr > 0, f"Year {y}: expected positive CAGR, got {result.cagr}"


# ── Leverage computation ─────────────────────────────────────────────────


class TestComputeLeveredYear:
    def test_levered_cagr_scales(self):
        base = PortfolioYearMetrics(2021, 25.0, -2.0, 12.5, 100, 0.85)
        result = compute_levered_year(base, 3.6)
        assert abs(result.levered_cagr - 90.0) < 0.01

    def test_levered_dd_scales(self):
        base = PortfolioYearMetrics(2021, 25.0, -2.0, 12.5, 100, 0.85)
        result = compute_levered_year(base, 3.6)
        assert abs(result.levered_dd - (-7.2)) < 0.01

    def test_sharpe_preserved(self):
        base = PortfolioYearMetrics(2021, 25.0, -2.0, 12.5, 100, 0.85)
        result = compute_levered_year(base, 3.6)
        assert result.levered_sharpe == base.sharpe

    def test_leverage_stored(self):
        base = PortfolioYearMetrics(2021, 25.0, -2.0, 12.5, 100, 0.85)
        result = compute_levered_year(base, 3.6)
        assert result.leverage == 3.6


# ── DD cap computation ───────────────────────────────────────────────────


class TestComputeDDCappedYear:
    def test_dd_within_cap(self):
        base = PortfolioYearMetrics(2021, 25.0, -2.0, 12.5, 100, 0.85)
        result = compute_dd_capped_year(base, 12.0)
        assert abs(result.max_dd) <= 12.0 + 0.01

    def test_effective_leverage_computed(self):
        base = PortfolioYearMetrics(2021, 25.0, -2.0, 12.5, 100, 0.85)
        result = compute_dd_capped_year(base, 12.0)
        assert result.effective_leverage == 6.0  # 12 / 2 = 6

    def test_cagr_amplified(self):
        base = PortfolioYearMetrics(2021, 25.0, -2.0, 12.5, 100, 0.85)
        result = compute_dd_capped_year(base, 12.0)
        assert abs(result.cagr - 150.0) < 0.01  # 25 * 6 = 150

    def test_tiny_dd_caps_leverage(self):
        base = PortfolioYearMetrics(2021, 25.0, -0.001, 25000.0, 100, 0.85)
        result = compute_dd_capped_year(base, 12.0)
        assert result.effective_leverage > 100  # very high leverage for tiny DD

    def test_sharpe_preserved(self):
        base = PortfolioYearMetrics(2021, 25.0, -2.0, 12.5, 100, 0.85)
        result = compute_dd_capped_year(base, 12.0)
        assert result.sharpe == base.sharpe


# ── Compound CAGR ────────────────────────────────────────────────────────


class TestCompoundCagr:
    def test_single_year(self):
        assert abs(_compound_cagr([10.0]) - 10.0) < 0.01

    def test_constant_returns(self):
        result = _compound_cagr([10.0, 10.0, 10.0])
        assert abs(result - 10.0) < 0.01

    def test_zero_returns(self):
        assert _compound_cagr([0.0, 0.0]) == 0.0

    def test_empty_list(self):
        assert _compound_cagr([]) == 0.0

    def test_negative_year(self):
        result = _compound_cagr([20.0, -10.0])
        # (1.20 * 0.90)^(1/2) - 1 = sqrt(1.08) - 1 ≈ 3.92%
        assert 3.5 < result < 4.5

    def test_compounding_effect(self):
        """Average of [50, -20] is 15, but compound is less due to vol drag."""
        avg = _compound_cagr([50.0, -20.0])
        assert avg < 15.0  # vol drag


# ── Full engine test ─────────────────────────────────────────────────────


class TestWalkForwardYearly:
    def test_run_returns_result(self):
        wf = WalkForwardYearly()
        result = wf.run()
        assert isinstance(result, WalkForwardYearlyResult)

    def test_base_years_count(self):
        wf = WalkForwardYearly()
        result = wf.run()
        assert len(result.base_years) == 6

    def test_levered_years_count(self):
        wf = WalkForwardYearly()
        result = wf.run()
        assert len(result.levered_years) == 6

    def test_capped_years_count(self):
        wf = WalkForwardYearly()
        result = wf.run()
        assert len(result.capped_years) == 6

    def test_all_base_years_profitable(self):
        wf = WalkForwardYearly()
        result = wf.run()
        for y in result.base_years:
            assert y.cagr > 0, f"Year {y.year}: base CAGR {y.cagr} not positive"

    def test_all_levered_years_profitable(self):
        wf = WalkForwardYearly()
        result = wf.run()
        for y in result.levered_years:
            assert y.levered_cagr > 0, f"Year {y.year}: levered CAGR not positive"

    def test_base_beats_spy_every_year(self):
        wf = WalkForwardYearly()
        result = wf.run()
        for y in result.base_years:
            spy = result.spy_years[y.year]
            assert y.cagr > spy.cagr, (
                f"Year {y.year}: base {y.cagr}% <= SPY {spy.cagr}%"
            )

    def test_base_dd_better_than_spy(self):
        wf = WalkForwardYearly()
        result = wf.run()
        for y in result.base_years:
            spy = result.spy_years[y.year]
            assert y.max_dd > spy.max_dd, (
                f"Year {y.year}: base DD {y.max_dd}% worse than SPY {spy.max_dd}%"
            )

    def test_capped_dd_within_budget(self):
        wf = WalkForwardYearly()
        result = wf.run()
        for y in result.capped_years:
            assert abs(y.max_dd) <= 12.01, (
                f"Year {y.year}: capped DD {y.max_dd}% exceeds 12% budget"
            )

    def test_levered_cagr_near_100(self):
        """3.6× levered compound CAGR should be near 100%."""
        wf = WalkForwardYearly()
        result = wf.run()
        compound = result.levered_summary["compound_cagr"]
        assert compound > 80, f"Levered compound CAGR {compound}% too low"

    def test_capped_achieves_100_cagr(self):
        """DD<12% capped should achieve 100%+ CAGR."""
        wf = WalkForwardYearly()
        result = wf.run()
        compound = result.capped_summary["compound_cagr"]
        assert compound > 100, f"Capped compound CAGR {compound}% below 100%"

    def test_base_summary_keys(self):
        wf = WalkForwardYearly()
        result = wf.run()
        expected_keys = {
            "compound_cagr", "avg_cagr", "worst_dd", "avg_sharpe",
            "total_trades", "avg_win_rate", "profitable_years", "total_years",
        }
        assert set(result.base_summary.keys()) == expected_keys

    def test_weights_preserved_in_result(self):
        wf = WalkForwardYearly()
        result = wf.run()
        assert result.weights == NORTH_STAR_WEIGHTS

    def test_custom_leverage(self):
        wf = WalkForwardYearly(leverage=2.0)
        result = wf.run()
        assert result.leverage == 2.0
        assert result.levered_years[0].leverage == 2.0

    def test_custom_dd_cap(self):
        wf = WalkForwardYearly(dd_cap=8.0)
        result = wf.run()
        assert result.dd_cap == 8.0
        for y in result.capped_years:
            assert abs(y.max_dd) <= 8.01


# ── Report generation ────────────────────────────────────────────────────


class TestReportGeneration:
    def test_html_report_creates_file(self):
        wf = WalkForwardYearly()
        result = wf.run()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            wf.generate_report(result, path)
            assert path.exists()
            content = path.read_text()
            assert "<!DOCTYPE html>" in content
            assert "EXP-1580" in content

    def test_html_report_contains_all_sections(self):
        wf = WalkForwardYearly()
        result = wf.run()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            wf.generate_report(result, path)
            content = path.read_text()
            assert "Base Portfolio" in content
            assert "3.6" in content
            assert "DD&lt;12%" in content or "DD<12%" in content
            assert "SPY" in content
            assert "EXP-400" in content
            assert "EXP-401" in content

    def test_html_report_contains_svg_charts(self):
        wf = WalkForwardYearly()
        result = wf.run()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            wf.generate_report(result, path)
            content = path.read_text()
            assert "<svg" in content
            assert "Annual Returns" in content

    def test_html_report_contains_all_years(self):
        wf = WalkForwardYearly()
        result = wf.run()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            wf.generate_report(result, path)
            content = path.read_text()
            for y in YEARS:
                assert str(y) in content

    def test_summary_json_creates_file(self):
        wf = WalkForwardYearly()
        result = wf.run()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "summary.json"
            wf.save_summary(result, path)
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["experiment"] == "EXP-1580"

    def test_summary_json_structure(self):
        wf = WalkForwardYearly()
        result = wf.run()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "summary.json"
            wf.save_summary(result, path)
            data = json.loads(path.read_text())
            assert "base" in data
            assert "levered_3_6x" in data
            assert "dd_capped_12pct" in data
            assert "benchmarks" in data
            assert "spy" in data["benchmarks"]
            assert "exp400" in data["benchmarks"]
            assert "exp401" in data["benchmarks"]

    def test_summary_json_benchmarks_have_cagr(self):
        wf = WalkForwardYearly()
        result = wf.run()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "summary.json"
            wf.save_summary(result, path)
            data = json.loads(path.read_text())
            for bench in ["spy", "exp400", "exp401"]:
                assert "compound_cagr" in data["benchmarks"][bench]
                assert "worst_dd" in data["benchmarks"][bench]
