"""Tests for compass.experiment_dashboard – unified experiment dashboard."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.experiment_dashboard import (
    DEFAULT_TARGETS,
    GREEN,
    RED,
    YELLOW,
    DashboardResult,
    ExperimentDashboard,
    ExperimentStatus,
    PortfolioOverview,
    TrafficLight,
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _make_experiments(n: int = 3) -> list[dict]:
    return [
        {
            "experiment_id": f"EXP-{i+1}",
            "name": f"Strategy {i+1}",
            "ticker": ["SPY", "QQQ", "IWM"][i % 3],
        }
        for i in range(n)
    ]


def _make_backtest_results(n: int = 3, sharpe: float = 2.0) -> dict:
    return {
        f"EXP-{i+1}": {
            "sharpe": sharpe - i * 0.3,
            "max_dd_pct": 10.0 + i * 2,
            "win_rate": 75.0 - i * 5,
            "return_pct": 30.0 - i * 5,
            "total_trades": 100 + i * 20,
            "profit_factor": 2.0 - i * 0.2,
            "capacity_estimate": 500_000 - i * 100_000,
        }
        for i in range(n)
    }


def _make_model_snapshots(n: int = 3) -> dict:
    return {
        f"EXP-{i+1}": {
            "rolling_auc": 0.65 - i * 0.05,
            "days_since_retrain": 10 + i * 10,
            "should_retrain": i > 1,
        }
        for i in range(n)
    }


def _make_stress_results(n: int = 3) -> dict:
    return {
        f"EXP-{i+1}": {
            "hedged_dd": 8.0 + i * 2,
            "unhedged_dd": 15.0 + i * 3,
            "hedge_status": "active" if i < 2 else "inactive",
        }
        for i in range(n)
    }


def _make_signal_decay(n: int = 3) -> dict:
    return {
        f"EXP-{i+1}": {
            "half_life_hours": 24.0 - i * 5,
            "optimal_period": "1d",
            "snr": 0.05 - i * 0.01,
        }
        for i in range(n)
    }


def _make_execution_stats(n: int = 3) -> dict:
    return {
        f"EXP-{i+1}": {
            "fill_rate": 0.95 - i * 0.05,
            "avg_slippage": 0.01 + i * 0.005,
            "execution_score": 90.0 - i * 10,
        }
        for i in range(n)
    }


def _make_corr_matrix(n: int = 3) -> pd.DataFrame:
    ids = [f"EXP-{i+1}" for i in range(n)]
    data = np.eye(n) * 0.7 + 0.3  # moderate correlation
    np.fill_diagonal(data, 1.0)
    return pd.DataFrame(data, index=ids, columns=ids)


# ── Constructor ─────────────────────────────────────────────────────────────
class TestExperimentDashboardInit:
    def test_defaults(self):
        d = ExperimentDashboard()
        assert d.targets == DEFAULT_TARGETS
        assert d.yellow_tolerance == 0.20

    def test_custom_targets(self):
        d = ExperimentDashboard(targets={"sharpe": 2.0, "max_dd_pct": 10.0})
        assert d.targets["sharpe"] == 2.0

    def test_custom_tolerance(self):
        d = ExperimentDashboard(yellow_tolerance=0.10)
        assert d.yellow_tolerance == 0.10


# ── Build basics ────────────────────────────────────────────────────────────
class TestBuild:
    def test_returns_dashboard_result(self):
        result = ExperimentDashboard().build(_make_experiments())
        assert isinstance(result, DashboardResult)

    def test_experiments_populated(self):
        result = ExperimentDashboard().build(
            _make_experiments(), backtest_results=_make_backtest_results(),
        )
        assert len(result.experiments) == 3

    def test_portfolio_present(self):
        result = ExperimentDashboard().build(
            _make_experiments(), backtest_results=_make_backtest_results(),
        )
        assert result.portfolio is not None

    def test_generated_at_set(self):
        result = ExperimentDashboard().build(_make_experiments())
        assert len(result.generated_at) > 0

    def test_empty_experiments(self):
        result = ExperimentDashboard().build([])
        assert result.experiments == []

    def test_no_backtest_data(self):
        result = ExperimentDashboard().build(_make_experiments())
        assert len(result.experiments) == 3
        for e in result.experiments:
            assert e.sharpe == 0.0


# ── Experiment status cards ─────────────────────────────────────────────────
class TestExperimentStatus:
    def test_fields_populated(self):
        result = ExperimentDashboard().build(
            _make_experiments(),
            backtest_results=_make_backtest_results(),
            model_snapshots=_make_model_snapshots(),
        )
        e = result.experiments[0]
        assert e.experiment_id == "EXP-1"
        assert e.sharpe > 0
        assert e.model_auc > 0
        assert e.days_since_retrain >= 0

    def test_hedge_status_from_stress(self):
        result = ExperimentDashboard().build(
            _make_experiments(),
            stress_results=_make_stress_results(),
        )
        assert result.experiments[0].hedge_status == "active"
        assert result.experiments[2].hedge_status == "inactive"

    def test_signal_decay_integrated(self):
        result = ExperimentDashboard().build(
            _make_experiments(),
            signal_decay=_make_signal_decay(),
        )
        assert result.experiments[0].signal_half_life == 24.0

    def test_execution_quality(self):
        result = ExperimentDashboard().build(
            _make_experiments(),
            execution_stats=_make_execution_stats(),
        )
        assert result.experiments[0].execution_quality == 90.0


# ── Traffic lights ──────────────────────────────────────────────────────────
class TestTrafficLights:
    def test_lights_present(self):
        result = ExperimentDashboard().build(
            _make_experiments(), backtest_results=_make_backtest_results(),
        )
        assert len(result.experiments[0].lights) > 0

    def test_green_when_target_met(self):
        d = ExperimentDashboard(targets={"sharpe": 1.0, "max_dd_pct": 20.0,
                                         "win_rate": 60.0, "model_auc": 0.5,
                                         "max_retrain_days": 30, "min_profit_factor": 1.0})
        result = d.build(_make_experiments(1), backtest_results=_make_backtest_results(1))
        e = result.experiments[0]
        assert e.status == GREEN

    def test_red_when_failing(self):
        d = ExperimentDashboard(targets={"sharpe": 10.0, "max_dd_pct": 1.0,
                                         "win_rate": 99.0, "model_auc": 0.99,
                                         "max_retrain_days": 1, "min_profit_factor": 10.0})
        result = d.build(
            _make_experiments(1),
            backtest_results=_make_backtest_results(1),
            model_snapshots=_make_model_snapshots(1),
        )
        e = result.experiments[0]
        assert e.status == RED

    def test_yellow_within_tolerance(self):
        # Sharpe target 2.5, value ~2.0 → within 20% → YELLOW
        d = ExperimentDashboard(targets={"sharpe": 2.5, "max_dd_pct": 20.0,
                                         "win_rate": 60.0, "model_auc": 0.5,
                                         "max_retrain_days": 100, "min_profit_factor": 1.0})
        result = d.build(_make_experiments(1), backtest_results=_make_backtest_results(1))
        sharpe_light = next(l for l in result.experiments[0].lights if l.metric == "sharpe")
        assert sharpe_light.status == YELLOW

    def test_overall_red_if_any_red(self):
        d = ExperimentDashboard(targets={"sharpe": 100.0, "max_dd_pct": 20.0,
                                         "win_rate": 60.0, "model_auc": 0.5,
                                         "max_retrain_days": 100, "min_profit_factor": 1.0})
        result = d.build(_make_experiments(1), backtest_results=_make_backtest_results(1))
        assert result.experiments[0].status == RED

    def test_light_fields(self):
        result = ExperimentDashboard().build(
            _make_experiments(1), backtest_results=_make_backtest_results(1),
        )
        l = result.experiments[0].lights[0]
        assert isinstance(l.metric, str)
        assert isinstance(l.value, float)
        assert isinstance(l.target, float)
        assert l.status in [GREEN, YELLOW, RED]


# ── Portfolio overview ──────────────────────────────────────────────────────
class TestPortfolioOverview:
    def test_blended_sharpe(self):
        result = ExperimentDashboard().build(
            _make_experiments(), backtest_results=_make_backtest_results(),
        )
        assert result.portfolio.blended_sharpe > 0

    def test_experiment_counts(self):
        result = ExperimentDashboard().build(
            _make_experiments(), backtest_results=_make_backtest_results(),
        )
        p = result.portfolio
        assert p.n_experiments == 3
        assert p.n_green + p.n_yellow + p.n_red == 3

    def test_total_capacity(self):
        result = ExperimentDashboard().build(
            _make_experiments(), backtest_results=_make_backtest_results(),
        )
        assert result.portfolio.total_aum_capacity > 0

    def test_diversification_with_corr(self):
        corr = _make_corr_matrix()
        result = ExperimentDashboard().build(
            _make_experiments(),
            backtest_results=_make_backtest_results(),
            correlation_matrix=corr,
        )
        assert 0 <= result.portfolio.diversification_score <= 100

    def test_diversification_without_corr(self):
        result = ExperimentDashboard().build(_make_experiments())
        assert result.portfolio.diversification_score == 50.0  # neutral default

    def test_uncorrelated_high_score(self):
        ids = ["EXP-1", "EXP-2", "EXP-3"]
        corr = pd.DataFrame(np.eye(3), index=ids, columns=ids)
        result = ExperimentDashboard().build(
            _make_experiments(),
            correlation_matrix=corr,
        )
        assert result.portfolio.diversification_score == 100.0

    def test_correlated_low_score(self):
        ids = ["EXP-1", "EXP-2", "EXP-3"]
        corr = pd.DataFrame(np.ones((3, 3)), index=ids, columns=ids)
        result = ExperimentDashboard().build(
            _make_experiments(),
            correlation_matrix=corr,
        )
        assert result.portfolio.diversification_score == 0.0

    def test_overall_red_if_any_experiment_red(self):
        d = ExperimentDashboard(targets={"sharpe": 100.0, "max_dd_pct": 1.0,
                                         "win_rate": 99.0, "model_auc": 0.99,
                                         "max_retrain_days": 1, "min_profit_factor": 10.0})
        result = d.build(
            _make_experiments(),
            backtest_results=_make_backtest_results(),
            model_snapshots=_make_model_snapshots(),
        )
        assert result.portfolio.overall_status == RED


# ── HTML report ─────────────────────────────────────────────────────────────
class TestHTMLReport:
    def test_report_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = ExperimentDashboard()
            result = d.build(
                _make_experiments(),
                backtest_results=_make_backtest_results(),
                model_snapshots=_make_model_snapshots(),
                stress_results=_make_stress_results(),
                signal_decay=_make_signal_decay(),
                execution_stats=_make_execution_stats(),
                correlation_matrix=_make_corr_matrix(),
            )
            path = d.generate_report(result, output_path=Path(tmp) / "d.html")
            assert path.exists()
            assert path.stat().st_size > 0

    def test_report_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = ExperimentDashboard()
            result = d.build(
                _make_experiments(),
                backtest_results=_make_backtest_results(),
                model_snapshots=_make_model_snapshots(),
            )
            path = d.generate_report(result, output_path=Path(tmp) / "r.html")
            html = path.read_text()
            assert "Experiment Dashboard" in html
            assert "Experiment Status Cards" in html
            assert "Experiment Detail" in html
            assert "Traffic Light" in html
            assert "Blended Sharpe" in html
            assert "Diversification" in html

    def test_report_valid_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = ExperimentDashboard()
            result = d.build(_make_experiments())
            path = d.generate_report(result, output_path=Path(tmp) / "v.html")
            html = path.read_text()
            assert html.startswith("<!DOCTYPE html>")
            assert "</html>" in html

    def test_report_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = ExperimentDashboard()
            result = DashboardResult(generated_at="2024-01-01T00:00:00+00:00")
            path = d.generate_report(result, output_path=Path(tmp) / "e.html")
            assert path.exists()

    def test_report_contains_experiment_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = ExperimentDashboard()
            result = d.build(_make_experiments(), backtest_results=_make_backtest_results())
            path = d.generate_report(result, output_path=Path(tmp) / "ids.html")
            html = path.read_text()
            assert "EXP-1" in html
            assert "EXP-2" in html


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_traffic_light(self):
        l = TrafficLight(metric="sharpe", value=2.0, target=1.5, status=GREEN)
        assert l.status == GREEN

    def test_experiment_status_defaults(self):
        e = ExperimentStatus(experiment_id="X", name="Test", ticker="SPY", status=GREEN)
        assert e.sharpe == 0.0
        assert e.lights == []

    def test_portfolio_overview_defaults(self):
        p = PortfolioOverview()
        assert p.n_experiments == 0
        assert p.overall_status == GREEN

    def test_dashboard_result_defaults(self):
        r = DashboardResult()
        assert r.experiments == []
        assert r.portfolio is None
