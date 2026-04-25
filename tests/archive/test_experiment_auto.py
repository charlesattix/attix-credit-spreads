"""Tests for compass/experiment_auto.py — Automated Experiment Pipeline.

61+ tests covering: ExperimentSpec, metrics, walk-forward, North Star,
success criteria, AutoPipeline, BatchQueue, HTML reports, edge cases.
"""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.experiment_auto import (
    ExperimentSpec, ExperimentResult, NorthStarCheck, WalkForwardFold,
    QueueItem, Status, AutoPipeline, BatchQueue,
    compute_sharpe, compute_cagr, compute_max_dd, compute_sortino,
    compute_profit_factor, walk_forward_validate,
    evaluate_north_star, evaluate_criteria,
    build_experiment_html, build_batch_html,
    NORTH_STAR, TRADING_DAYS,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def basic_spec():
    return ExperimentSpec(
        experiment_id="EXP-TEST-001",
        name="Test Credit Spread",
        hypothesis="Selling OTM puts generates alpha from mean reversion",
        strategy_class="credit_spread",
        ticker="SPY",
        params={"otm_pct": 0.05, "spread_width": 5},
    )


@pytest.fixture
def custom_spec():
    def runner(spec):
        return {
            "n_trades": 50, "total_pnl": 15000, "cagr": 0.60,
            "sharpe": 3.5, "max_dd": 0.08, "win_rate": 0.72,
            "spy_corr": 0.10,
            "trades": [{"entry_date": f"2020-{m:02d}-01", "exit_date": f"2020-{m:02d}-15",
                        "pnl": 300} for m in range(1, 13)]
                     + [{"entry_date": f"2021-{m:02d}-01", "exit_date": f"2021-{m:02d}-15",
                         "pnl": 250} for m in range(1, 13)]
                     + [{"entry_date": f"2023-{m:02d}-01", "exit_date": f"2023-{m:02d}-15",
                         "pnl": 400} for m in range(1, 13)],
            "yearly": {2020: {"pnl": 3600}, 2021: {"pnl": 3000}, 2023: {"pnl": 4800}},
        }
    return ExperimentSpec(
        experiment_id="EXP-CUSTOM-001",
        name="Custom Runner Test",
        hypothesis="Custom strategies work with pipeline",
        strategy_class="custom",
        ticker="SPY",
        custom_runner=runner,
    )


@pytest.fixture
def pipeline():
    return AutoPipeline()


@pytest.fixture
def result(pipeline, basic_spec):
    return pipeline.run(basic_spec)


# ═══════════════════════════════════════════════════════════════════════════
# ExperimentSpec Tests (10 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestExperimentSpec:
    def test_create_basic(self, basic_spec):
        assert basic_spec.experiment_id == "EXP-TEST-001"
        assert basic_spec.hypothesis != ""

    def test_requires_experiment_id(self):
        with pytest.raises(ValueError, match="experiment_id"):
            ExperimentSpec(experiment_id="", name="X", hypothesis="H",
                          strategy_class="cs", ticker="SPY")

    def test_requires_name(self):
        with pytest.raises(ValueError, match="name"):
            ExperimentSpec(experiment_id="E", name="", hypothesis="H",
                          strategy_class="cs", ticker="SPY")

    def test_requires_hypothesis(self):
        with pytest.raises(ValueError, match="hypothesis"):
            ExperimentSpec(experiment_id="E", name="N", hypothesis="",
                          strategy_class="cs", ticker="SPY")

    def test_requires_strategy_class(self):
        with pytest.raises(ValueError, match="strategy_class"):
            ExperimentSpec(experiment_id="E", name="N", hypothesis="H",
                          strategy_class="", ticker="SPY")

    def test_requires_ticker(self):
        with pytest.raises(ValueError, match="ticker"):
            ExperimentSpec(experiment_id="E", name="N", hypothesis="H",
                          strategy_class="cs", ticker="")

    def test_requires_positive_capital(self):
        with pytest.raises(ValueError, match="capital"):
            ExperimentSpec(experiment_id="E", name="N", hypothesis="H",
                          strategy_class="cs", ticker="SPY", capital=-100)

    def test_default_success_criteria(self, basic_spec):
        assert "min_sharpe" in basic_spec.success_criteria
        assert basic_spec.success_criteria["min_sharpe"] == NORTH_STAR["min_sharpe"]

    def test_custom_success_criteria(self):
        spec = ExperimentSpec(
            experiment_id="E", name="N", hypothesis="H",
            strategy_class="cs", ticker="SPY",
            success_criteria={"min_sharpe": 2.0, "max_dd": 0.15},
        )
        assert spec.success_criteria["min_sharpe"] == 2.0

    def test_default_dates(self, basic_spec):
        assert basic_spec.start_date == "2020-01-01"
        assert basic_spec.end_date == "2025-12-31"


# ═══════════════════════════════════════════════════════════════════════════
# Metric Computation Tests (12 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestMetrics:
    def test_sharpe_positive(self):
        rng = np.random.RandomState(1)
        assert compute_sharpe(rng.normal(1, 2, 100)) > 0

    def test_sharpe_negative(self):
        rng = np.random.RandomState(1)
        assert compute_sharpe(rng.normal(-1, 2, 100)) < 0

    def test_sharpe_empty(self):
        assert compute_sharpe(np.array([])) == 0

    def test_sharpe_single(self):
        assert compute_sharpe(np.array([1.0])) == 0

    def test_sharpe_constant(self):
        # Zero std → returns 0 (not inf)
        assert compute_sharpe(np.array([1, 1, 1, 1])) == 0

    def test_cagr_positive(self):
        assert compute_cagr(50_000, 100_000, 3) > 0

    def test_cagr_total_loss(self):
        assert compute_cagr(-100_000, 100_000, 3) == -1.0

    def test_cagr_zero_years(self):
        assert compute_cagr(10_000, 100_000, 0) == -1.0

    def test_max_dd_no_drawdown(self):
        eq = np.array([100, 101, 102, 103])
        assert compute_max_dd(eq) == 0

    def test_max_dd_with_drawdown(self):
        eq = np.array([100, 110, 90, 95])
        dd = compute_max_dd(eq)
        assert dd > 0
        assert abs(dd - 20 / 110) < 0.01

    def test_sortino_positive(self):
        rng = np.random.RandomState(1)
        assert compute_sortino(rng.normal(1, 2, 100)) > 0

    def test_profit_factor(self):
        pnls = np.array([100, 200, -50, -100, 300])
        pf = compute_profit_factor(pnls)
        assert pf == 600 / 150


# ═══════════════════════════════════════════════════════════════════════════
# Walk-Forward Tests (8 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestWalkForward:
    def _make_trades(self, years, pnl=300):
        rng = np.random.RandomState(42)
        trades = []
        for yr in years:
            for m in range(1, 13):
                trades.append({
                    "entry_date": f"{yr}-{m:02d}-01",
                    "exit_date": f"{yr}-{m:02d}-15",
                    "pnl": pnl + rng.normal(0, 50),  # add noise so std != 0
                })
        return trades

    def test_empty_trades(self):
        is_sh, oos_sh, wf, folds = walk_forward_validate([])
        assert is_sh == 0 and oos_sh == 0

    def test_basic_wf(self):
        trades = self._make_trades([2020, 2021, 2022, 2023, 2024])
        is_sh, oos_sh, wf, folds = walk_forward_validate(trades, oos_start_year=2023)
        assert is_sh > 0
        assert oos_sh > 0

    def test_folds_created(self):
        trades = self._make_trades([2020, 2021, 2022, 2023])
        _, _, _, folds = walk_forward_validate(trades)
        assert len(folds) >= 2

    def test_fold_has_periods(self):
        trades = self._make_trades([2020, 2021, 2022])
        _, _, _, folds = walk_forward_validate(trades)
        if folds:
            assert folds[0].train_period != ""
            assert folds[0].test_period != ""

    def test_wf_ratio_computed(self):
        trades = self._make_trades([2020, 2021, 2022, 2023, 2024])
        is_sh, oos_sh, wf, _ = walk_forward_validate(trades, oos_start_year=2023)
        if is_sh > 0.01:
            assert abs(wf - oos_sh / is_sh) < 0.01

    def test_no_date_column(self):
        trades = [{"pnl": 100}]  # missing dates
        is_sh, oos_sh, wf, folds = walk_forward_validate(trades)
        assert is_sh == 0

    def test_negative_pnl_trades(self):
        trades = self._make_trades([2020, 2021, 2022, 2023], pnl=-200)
        is_sh, oos_sh, wf, folds = walk_forward_validate(trades)
        assert oos_sh < 0

    def test_single_year(self):
        trades = self._make_trades([2023])
        _, _, _, folds = walk_forward_validate(trades)
        assert len(folds) == 0


# ═══════════════════════════════════════════════════════════════════════════
# North Star Tests (8 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestNorthStar:
    def _make_result(self, **kwargs):
        spec = ExperimentSpec(experiment_id="E", name="N", hypothesis="H",
                              strategy_class="cs", ticker="SPY")
        r = ExperimentResult(spec=spec)
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r

    def test_all_pass_tier1(self):
        r = self._make_result(cagr=1.2, sharpe=7.0, max_dd=0.08,
                              profitable_years=6, total_years=6,
                              n_trades=50, spy_corr=0.1, oos_sharpe=2.0,
                              win_rate=0.70)
        evaluate_north_star(r)
        assert r.tier == 1
        assert r.north_star_passed == r.north_star_total

    def test_all_fail_tier4(self):
        r = self._make_result(cagr=-0.5, sharpe=-1.0, max_dd=0.50,
                              profitable_years=0, total_years=6,
                              n_trades=5, spy_corr=0.8, oos_sharpe=-0.5,
                              win_rate=0.30)
        evaluate_north_star(r)
        assert r.tier == 4
        assert r.north_star_passed == 0

    def test_checks_count(self):
        r = self._make_result()
        evaluate_north_star(r)
        assert r.north_star_total == 8

    def test_tier2_most_pass(self):
        r = self._make_result(cagr=1.2, sharpe=7.0, max_dd=0.08,
                              profitable_years=6, total_years=6,
                              n_trades=50, spy_corr=0.6,  # fails corr
                              oos_sharpe=2.0, win_rate=0.70)
        evaluate_north_star(r)
        assert r.tier <= 2

    def test_verdict_string(self):
        r = self._make_result()
        evaluate_north_star(r)
        assert "TIER" in r.verdict

    def test_cagr_check_correct(self):
        r = self._make_result(cagr=0.50)  # below 100% target
        evaluate_north_star(r)
        cagr_check = next(c for c in r.north_star_checks if c.name == "CAGR")
        assert not cagr_check.passed

    def test_dd_check_inverted(self):
        r = self._make_result(max_dd=0.05)  # below 12% = pass
        evaluate_north_star(r)
        dd_check = next(c for c in r.north_star_checks if c.name == "Max DD")
        assert dd_check.passed

    def test_dd_check_fail(self):
        r = self._make_result(max_dd=0.20)  # above 12% = fail
        evaluate_north_star(r)
        dd_check = next(c for c in r.north_star_checks if c.name == "Max DD")
        assert not dd_check.passed


# ═══════════════════════════════════════════════════════════════════════════
# Success Criteria Tests (5 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestSuccessCriteria:
    def _make_result(self, criteria, **kwargs):
        spec = ExperimentSpec(experiment_id="E", name="N", hypothesis="H",
                              strategy_class="cs", ticker="SPY",
                              success_criteria=criteria)
        r = ExperimentResult(spec=spec)
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r

    def test_all_criteria_met(self):
        r = self._make_result({"min_sharpe": 2.0, "max_dd": 0.15},
                              sharpe=3.0, max_dd=0.10)
        evaluate_criteria(r)
        assert r.criteria_met

    def test_sharpe_fails(self):
        r = self._make_result({"min_sharpe": 5.0}, sharpe=2.0)
        evaluate_criteria(r)
        assert not r.criteria_met

    def test_dd_fails(self):
        r = self._make_result({"max_dd": 0.10}, max_dd=0.20)
        evaluate_criteria(r)
        assert not r.criteria_checks.get("max_dd", True)

    def test_empty_criteria(self):
        spec = ExperimentSpec(experiment_id="E", name="N", hypothesis="H",
                              strategy_class="cs", ticker="SPY",
                              success_criteria={})
        # Override the __post_init__ default
        spec.success_criteria = {}
        r = ExperimentResult(spec=spec)
        evaluate_criteria(r)
        assert not r.criteria_met  # empty = not met

    def test_multiple_criteria(self):
        r = self._make_result(
            {"min_sharpe": 2.0, "max_dd": 0.15, "min_win_rate": 0.60},
            sharpe=3.0, max_dd=0.10, win_rate=0.70)
        evaluate_criteria(r)
        assert r.criteria_met
        assert len(r.criteria_checks) == 3


# ═══════════════════════════════════════════════════════════════════════════
# AutoPipeline Tests (8 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoPipeline:
    def test_run_basic(self, result):
        assert result.status == Status.COMPLETED
        assert result.n_trades > 0

    def test_metrics_computed(self, result):
        assert result.sharpe != 0
        assert result.cagr != 0
        assert result.max_dd > 0
        assert result.win_rate > 0

    def test_north_star_evaluated(self, result):
        assert result.north_star_total == 8
        assert result.tier in [1, 2, 3, 4]

    def test_walk_forward_run(self, result):
        # Should have folds since we generate multi-year trades
        assert isinstance(result.wf_folds, list)

    def test_yearly_computed(self, result):
        assert len(result.yearly) >= 1

    def test_equity_curve(self, result):
        assert len(result.equity_curve) > 0

    def test_custom_runner(self, pipeline, custom_spec):
        r = pipeline.run(custom_spec)
        assert r.status == Status.COMPLETED
        assert r.n_trades > 0
        assert r.cagr > 0

    def test_run_time_tracked(self, result):
        assert result.run_time_seconds > 0
        assert result.timestamp != ""


# ═══════════════════════════════════════════════════════════════════════════
# BatchQueue Tests (10 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestBatchQueue:
    def _make_specs(self, n):
        return [ExperimentSpec(
            experiment_id=f"EXP-BATCH-{i:03d}",
            name=f"Batch Test {i}",
            hypothesis=f"Hypothesis {i}",
            strategy_class="credit_spread",
            ticker="SPY",
        ) for i in range(n)]

    def test_create_queue(self):
        q = BatchQueue(self._make_specs(3))
        assert len(q.items) == 3

    def test_all_pending_initially(self):
        q = BatchQueue(self._make_specs(3))
        assert len(q.pending) == 3
        assert len(q.completed) == 0

    def test_run_all(self):
        q = BatchQueue(self._make_specs(3))
        results = q.run_all()
        assert len(results) == 3
        assert all(r.status in (Status.COMPLETED, Status.FAILED) for r in results)

    def test_completed_tracked(self):
        q = BatchQueue(self._make_specs(2))
        q.run_all()
        assert len(q.completed) >= 1

    def test_run_single(self):
        specs = self._make_specs(3)
        q = BatchQueue(specs)
        r = q.run_single("EXP-BATCH-001")
        assert r is not None
        assert r.spec.experiment_id == "EXP-BATCH-001"

    def test_run_single_not_found(self):
        q = BatchQueue(self._make_specs(2))
        assert q.run_single("NONEXISTENT") is None

    def test_summary(self):
        q = BatchQueue(self._make_specs(3))
        q.run_all()
        s = q.summary()
        assert s["total"] == 3
        assert s["completed"] >= 1
        assert "best_sharpe" in s
        assert "avg_sharpe" in s

    def test_summary_has_tiers(self):
        q = BatchQueue(self._make_specs(3))
        q.run_all()
        s = q.summary()
        assert "tier_1" in s and "tier_4" in s

    def test_empty_queue(self):
        q = BatchQueue([])
        results = q.run_all()
        assert len(results) == 0
        assert q.summary()["total"] == 0

    def test_batch_report(self, tmp_path):
        q = BatchQueue(self._make_specs(2))
        q.run_all()
        out = tmp_path / "batch.html"
        q.generate_batch_report(str(out))
        assert out.exists()
        assert "Batch Experiment" in out.read_text()


# ═══════════════════════════════════════════════════════════════════════════
# HTML Report Tests (6 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestHTMLReports:
    def test_experiment_html(self, result):
        html = build_experiment_html(result)
        assert "<!DOCTYPE html>" in html
        assert result.spec.experiment_id in html

    def test_report_contains_hypothesis(self, result):
        html = build_experiment_html(result)
        assert "Hypothesis" in html

    def test_report_contains_north_star(self, result):
        html = build_experiment_html(result)
        assert "North Star" in html

    def test_report_contains_criteria(self, result):
        html = build_experiment_html(result)
        assert "Success Criteria" in html

    def test_pipeline_generate_report(self, pipeline, basic_spec, tmp_path):
        r = pipeline.run(basic_spec)
        out = tmp_path / "exp_report.html"
        path = pipeline.generate_report(r, str(out))
        assert Path(path).exists()

    def test_batch_html(self):
        q = BatchQueue([ExperimentSpec(
            experiment_id="E", name="N", hypothesis="H",
            strategy_class="cs", ticker="SPY")])
        q.run_all()
        html = build_batch_html(q)
        assert "<!DOCTYPE html>" in html


# ═══════════════════════════════════════════════════════════════════════════
# Registry Tests (3 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistry:
    def test_update_registry(self, pipeline, basic_spec, tmp_path):
        r = pipeline.run(basic_spec)
        reg = tmp_path / "REGISTRY.md"
        reg.write_text("# Registry\n\n")
        result = pipeline.update_registry(r, str(reg))
        assert result
        assert basic_spec.experiment_id in reg.read_text()

    def test_no_duplicate(self, pipeline, basic_spec, tmp_path):
        r = pipeline.run(basic_spec)
        reg = tmp_path / "REGISTRY.md"
        reg.write_text(f"# Registry\n{basic_spec.experiment_id}\n")
        result = pipeline.update_registry(r, str(reg))
        assert not result  # already exists

    def test_missing_registry(self, pipeline, basic_spec, tmp_path):
        r = pipeline.run(basic_spec)
        result = pipeline.update_registry(r, str(tmp_path / "nonexistent.md"))
        assert not result


# ═══════════════════════════════════════════════════════════════════════════
# Status Enum Tests (2 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestStatus:
    def test_values(self):
        assert Status.PENDING.value == "pending"
        assert Status.COMPLETED.value == "completed"
        assert Status.FAILED.value == "failed"

    def test_comparison(self):
        assert Status.PENDING != Status.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases (5 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_unknown_strategy_class(self):
        spec = ExperimentSpec(
            experiment_id="E", name="N", hypothesis="H",
            strategy_class="unknown_exotic", ticker="SPY")
        p = AutoPipeline()
        r = p.run(spec)
        assert r.status == Status.COMPLETED  # falls back to default profile

    def test_very_short_date_range(self):
        spec = ExperimentSpec(
            experiment_id="E", name="N", hypothesis="H",
            strategy_class="credit_spread", ticker="SPY",
            start_date="2024-01-01", end_date="2024-06-30")
        p = AutoPipeline()
        r = p.run(spec)
        assert r.n_trades > 0

    def test_large_capital(self):
        spec = ExperimentSpec(
            experiment_id="E", name="N", hypothesis="H",
            strategy_class="credit_spread", ticker="SPY",
            capital=10_000_000)
        p = AutoPipeline()
        r = p.run(spec)
        assert r.status == Status.COMPLETED

    def test_tags_preserved(self):
        spec = ExperimentSpec(
            experiment_id="E", name="N", hypothesis="H",
            strategy_class="cs", ticker="SPY",
            tags=["vol", "theta"])
        assert "vol" in spec.tags

    def test_queue_item_defaults(self):
        spec = ExperimentSpec(experiment_id="E", name="N", hypothesis="H",
                              strategy_class="cs", ticker="SPY")
        item = QueueItem(spec=spec)
        assert item.status == Status.PENDING
        assert item.result is None
