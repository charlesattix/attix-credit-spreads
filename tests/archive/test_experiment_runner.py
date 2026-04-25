"""Tests for compass/experiment_runner.py — the automated experiment pipeline."""

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compass.experiment_runner import (
    ExperimentSpec,
    ExperimentResult,
    ExperimentRunner,
    NorthStarCheck,
    WalkForwardWindow,
    SweepResult,
    BatchRunner,
    ParameterSweep,
    NORTH_STAR,
    _sharpe,
    _cagr,
    _check_success_criteria,
    update_json_registry,
    _max_dd,
    _spy_correlation,
    build_default_config,
    walk_forward_validate,
    evaluate_north_star,
    estimate_capacity,
)


# ═══════════════════════════════════════════════════════════════════════════
# ExperimentSpec tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExperimentSpec:
    def test_default_values(self):
        spec = ExperimentSpec(
            experiment_id="EXP-TEST",
            name="Test Strategy",
            strategy_type="credit_spread",
            ticker="SPY",
        )
        assert spec.experiment_id == "EXP-TEST"
        assert spec.capital == 100_000
        assert spec.start_date == "2020-01-01"
        assert spec.end_date == "2025-12-31"
        assert spec.validation == "walk_forward"
        assert spec.oos_start_year == 2022
        assert spec.data_source == "ironvault"
        assert spec.params == {}

    def test_custom_params(self):
        spec = ExperimentSpec(
            experiment_id="EXP-1700",
            name="Custom",
            strategy_type="pairs",
            ticker="GLD",
            capital=500_000,
            params={"lookback": 30, "z_threshold": 2.0},
        )
        assert spec.capital == 500_000
        assert spec.params["lookback"] == 30
        assert spec.params["z_threshold"] == 2.0

    def test_description(self):
        spec = ExperimentSpec(
            experiment_id="EXP-X",
            name="Test",
            strategy_type="custom",
            ticker="SPY",
            description="A test strategy with custom logic",
        )
        assert "custom logic" in spec.description


# ═══════════════════════════════════════════════════════════════════════════
# Helper function tests
# ═══════════════════════════════════════════════════════════════════════════

class TestHelpers:
    def test_sharpe_empty(self):
        assert _sharpe(np.array([])) == 0.0

    def test_sharpe_single(self):
        assert _sharpe(np.array([100])) == 0.0

    def test_sharpe_positive(self):
        pnls = np.array([100, 150, 120, 80, 200, 90, 130])
        s = _sharpe(pnls)
        assert s > 0
        # Mean is positive, so Sharpe should be positive
        assert isinstance(s, float)

    def test_sharpe_all_same(self):
        # Zero std → zero Sharpe
        pnls = np.array([100, 100, 100, 100])
        assert _sharpe(pnls) == 0.0

    def test_sharpe_negative(self):
        pnls = np.array([-100, -50, -200, -30])
        assert _sharpe(pnls) < 0

    def test_cagr_positive(self):
        c = _cagr(50_000, 100_000, 5.0)
        # 50% total return over 5 years ≈ 8.4% CAGR
        assert 0.08 < c < 0.09

    def test_cagr_zero_years(self):
        assert _cagr(10000, 100000, 0) == -1.0

    def test_cagr_total_loss(self):
        assert _cagr(-100_000, 100_000, 5) == -1.0

    def test_cagr_100pct(self):
        c = _cagr(100_000, 100_000, 1.0)
        assert abs(c - 1.0) < 0.01  # 100% return in 1 year = 100% CAGR

    def test_max_dd_flat(self):
        eq = np.array([100, 100, 100, 100])
        assert _max_dd(eq) == 0.0

    def test_max_dd_simple(self):
        eq = np.array([100, 110, 90, 105])
        dd = _max_dd(eq)
        # Peak 110, trough 90 → DD = 20/110 = 18.2%
        assert abs(dd - 0.1818) < 0.01

    def test_max_dd_empty(self):
        assert _max_dd(np.array([])) == 0.0

    def test_spy_correlation_empty(self):
        assert _spy_correlation({}, pd.DataFrame()) == 0.0

    def test_spy_correlation_few_points(self):
        spy = pd.DataFrame({"Close": [100, 101, 102]},
                            index=pd.date_range("2020-01-01", periods=3))
        assert _spy_correlation({"2020-01-01": 10}, spy) == 0.0  # <5 points


# ═══════════════════════════════════════════════════════════════════════════
# Config builder tests
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigBuilder:
    def test_default_config_structure(self):
        spec = ExperimentSpec(
            experiment_id="EXP-X", name="Test",
            strategy_type="credit_spread", ticker="SPY",
        )
        config = build_default_config(spec)
        assert "strategy" in config
        assert "risk" in config
        assert "backtest" in config
        assert config["backtest"]["starting_capital"] == 100_000

    def test_params_override(self):
        spec = ExperimentSpec(
            experiment_id="EXP-X", name="Test",
            strategy_type="credit_spread", ticker="SPY",
            params={"spread_width": 10.0, "otm_pct": 0.03, "target_dte": 45},
        )
        config = build_default_config(spec)
        assert config["strategy"]["spread_width"] == 10.0
        assert config["strategy"]["otm_pct"] == 0.03
        assert config["strategy"]["target_dte"] == 45

    def test_config_overrides(self):
        spec = ExperimentSpec(
            experiment_id="EXP-X", name="Test",
            strategy_type="credit_spread", ticker="SPY",
            config_overrides={"backtest": {"commission_per_contract": 0.0}},
        )
        config = build_default_config(spec)
        assert config["backtest"]["commission_per_contract"] == 0.0

    def test_capital_propagation(self):
        spec = ExperimentSpec(
            experiment_id="EXP-X", name="Test",
            strategy_type="credit_spread", ticker="SPY",
            capital=500_000,
        )
        config = build_default_config(spec)
        assert config["backtest"]["starting_capital"] == 500_000


# ═══════════════════════════════════════════════════════════════════════════
# Walk-forward validation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestWalkForward:
    def _make_trades(self, years_pnl: dict) -> list:
        """Create fake trades with given PnLs per year."""
        trades = []
        for yr, pnls in years_pnl.items():
            for i, pnl in enumerate(pnls):
                trades.append({
                    "entry_date": f"{yr}-{(i % 12) + 1:02d}-15",
                    "exit_date": f"{yr}-{(i % 12) + 1:02d}-20",
                    "pnl": pnl,
                })
        return trades

    def test_empty_trades(self):
        is_sh, oos_sh, wf, windows = walk_forward_validate([])
        assert is_sh == 0.0
        assert oos_sh == 0.0
        assert windows == []

    def test_basic_walk_forward(self):
        trades = self._make_trades({
            2020: [100, 50, -30, 80, 120, 40],
            2021: [90, -20, 110, 60, 70, 30],
            2022: [150, 80, -10, 200, 130, 60],
            2023: [120, 90, -40, 180, 100, 50],
        })
        is_sh, oos_sh, wf, windows = walk_forward_validate(trades, oos_start_year=2022)
        # IS = 2020-2021 (positive), OOS = 2022-2023 (positive)
        assert is_sh > 0
        assert oos_sh > 0
        assert len(windows) >= 2  # at least 2 rolling windows

    def test_oos_negative(self):
        trades = self._make_trades({
            2020: [100, 150, 80, 120, 90, 110],
            2021: [90, 130, 70, 140, 100, 85],
            2022: [-200, -300, -100, -250, -180, -150],
        })
        is_sh, oos_sh, wf, windows = walk_forward_validate(trades, oos_start_year=2022)
        assert is_sh > 0
        assert oos_sh < 0

    def test_windows_have_correct_periods(self):
        trades = self._make_trades({
            2020: [100, 50, 80],
            2021: [90, 120, 60],
            2022: [110, 70, 130],
        })
        _, _, _, windows = walk_forward_validate(trades)
        periods = [(w.is_period, w.oos_period) for w in windows]
        assert ("2020", "2021") in periods
        assert ("2021", "2022") in periods


# ═══════════════════════════════════════════════════════════════════════════
# North Star evaluation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestNorthStar:
    def _make_result(self, **kwargs) -> ExperimentResult:
        spec = ExperimentSpec(
            experiment_id="EXP-TEST", name="Test",
            strategy_type="credit_spread", ticker="SPY",
        )
        result = ExperimentResult(spec=spec)
        for k, v in kwargs.items():
            setattr(result, k, v)
        return result

    def test_all_pass_tier1(self):
        result = self._make_result(
            cagr=0.80, sharpe=5.0, max_dd=0.10, profitable_years=6,
            total_years=6, n_trades=100, spy_corr=0.05,
            oos_sharpe=3.0, win_rate=0.85,
        )
        evaluate_north_star(result)
        assert result.tier == 1
        assert result.north_star_passed == result.north_star_total
        assert "TIER 1" in result.verdict

    def test_some_fail_tier2(self):
        result = self._make_result(
            cagr=0.20, sharpe=2.0, max_dd=0.08, profitable_years=5,
            total_years=6, n_trades=50, spy_corr=0.10,
            oos_sharpe=1.5, win_rate=0.60,
        )
        evaluate_north_star(result)
        assert result.tier == 2
        assert result.north_star_passed < result.north_star_total

    def test_mostly_fail_tier3(self):
        result = self._make_result(
            cagr=0.05, sharpe=0.8, max_dd=0.15, profitable_years=4,
            total_years=6, n_trades=30, spy_corr=0.20,
            oos_sharpe=0.3, win_rate=0.55,
        )
        evaluate_north_star(result)
        assert result.tier in (3, 4)

    def test_dead_tier4(self):
        result = self._make_result(
            cagr=-0.50, sharpe=-1.0, max_dd=0.80, profitable_years=1,
            total_years=6, n_trades=5, spy_corr=0.90,
            oos_sharpe=-2.0, win_rate=0.30,
        )
        evaluate_north_star(result)
        assert result.tier == 4
        assert "TIER 4" in result.verdict

    def test_north_star_check_count(self):
        result = self._make_result(
            cagr=0.50, sharpe=3.0, max_dd=0.15, profitable_years=5,
            total_years=6, n_trades=50, spy_corr=0.10,
            oos_sharpe=1.0, win_rate=0.65,
        )
        evaluate_north_star(result)
        assert result.north_star_total == 8  # 8 checks

    def test_dd_higher_is_worse(self):
        """Max DD check should fail when DD is too high."""
        result = self._make_result(max_dd=0.50)
        evaluate_north_star(result)
        dd_check = next(c for c in result.north_star_checks if c.name == "Max DD")
        assert not dd_check.passed  # 50% DD > 30% threshold

    def test_dd_lower_passes(self):
        result = self._make_result(max_dd=0.10)
        evaluate_north_star(result)
        dd_check = next(c for c in result.north_star_checks if c.name == "Max DD")
        assert dd_check.passed  # 10% DD < 30% threshold


# ═══════════════════════════════════════════════════════════════════════════
# Capacity estimation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCapacity:
    def test_spy_large_capacity(self):
        cap = estimate_capacity("SPY", avg_contracts=5)
        assert "B" in cap or "M" in cap  # SPY should be large

    def test_gld_smaller(self):
        cap_spy = estimate_capacity("SPY", avg_contracts=5)
        cap_gld = estimate_capacity("GLD", avg_contracts=5)
        # SPY should have more capacity than GLD
        # Parse the numbers
        def _parse(s):
            s = s.replace("$", "").replace(",", "")
            if s.endswith("B"):
                return float(s[:-1]) * 1e9
            elif s.endswith("M"):
                return float(s[:-1]) * 1e6
            elif s.endswith("K"):
                return float(s[:-1]) * 1e3
            return float(s)
        assert _parse(cap_spy) > _parse(cap_gld)

    def test_unknown_ticker(self):
        cap = estimate_capacity("UNKNOWN_TICKER", avg_contracts=5)
        # Should use default ADV of 5000
        assert "$" in cap


# ═══════════════════════════════════════════════════════════════════════════
# ExperimentRunner tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExperimentRunner:
    def test_custom_runner(self):
        """Test running with a custom backtest function."""
        def my_runner(spec):
            return {
                "n_trades": 50,
                "total_pnl": 25000,
                "cagr": 0.20,
                "sharpe": 2.5,
                "max_dd": 0.08,
                "win_rate": 0.75,
                "profit_factor": 2.1,
                "trades": [
                    {"entry_date": f"2020-{m:02d}-15", "exit_date": f"2020-{m:02d}-20",
                     "pnl": 500} for m in range(1, 13)
                ] + [
                    {"entry_date": f"2021-{m:02d}-15", "exit_date": f"2021-{m:02d}-20",
                     "pnl": 400} for m in range(1, 13)
                ] + [
                    {"entry_date": f"2022-{m:02d}-15", "exit_date": f"2022-{m:02d}-20",
                     "pnl": 600} for m in range(1, 13)
                ] + [
                    {"entry_date": f"2023-{m:02d}-15", "exit_date": f"2023-{m:02d}-20",
                     "pnl": 300} for m in range(1, 7)
                ],
                "yearly": {
                    2020: {"n": 12, "pnl": 6000, "wr": 0.83, "sharpe": 2.0},
                    2021: {"n": 12, "pnl": 4800, "wr": 0.75, "sharpe": 1.8},
                    2022: {"n": 12, "pnl": 7200, "wr": 0.92, "sharpe": 3.0},
                    2023: {"n": 6, "pnl": 1800, "wr": 0.67, "sharpe": 1.2},
                },
            }

        spec = ExperimentSpec(
            experiment_id="EXP-TEST-CUSTOM",
            name="Custom Test",
            strategy_type="custom",
            ticker="SPY",
            custom_runner=my_runner,
        )

        runner = ExperimentRunner()
        # Mock spy_df to avoid network calls
        runner._spy_df = pd.DataFrame(
            {"Close": np.random.normal(400, 10, 1500)},
            index=pd.date_range("2019-06-01", periods=1500),
        )

        result = runner.run(spec)

        assert result.n_trades == 50
        assert result.total_pnl == 25000
        assert result.cagr == 0.20
        assert result.sharpe == 2.5
        assert result.max_dd == 0.08
        assert result.win_rate == 0.75
        assert result.tier in (1, 2, 3, 4)
        assert result.north_star_total == 8
        assert result.run_time_seconds > 0
        assert result.timestamp != ""
        assert len(result.wf_windows) > 0  # WF should have run
        assert result.oos_sharpe != 0  # should have OOS sharpe from WF
        assert result.estimated_capacity != "unknown"
        assert result.errors == []

    def test_custom_runner_with_errors(self):
        """Test that errors are caught and reported."""
        def bad_runner(spec):
            raise ValueError("Intentional test error")

        spec = ExperimentSpec(
            experiment_id="EXP-BAD",
            name="Bad Test",
            strategy_type="custom",
            ticker="SPY",
            custom_runner=bad_runner,
        )

        runner = ExperimentRunner()
        runner._spy_df = pd.DataFrame(
            {"Close": [100] * 100},
            index=pd.date_range("2020-01-01", periods=100),
        )

        result = runner.run(spec)
        assert len(result.errors) > 0
        assert "Intentional test error" in result.errors[0]
        assert "ERROR" in result.verdict

    def test_no_validation(self):
        """Test with validation='none'."""
        def simple_runner(spec):
            return {
                "n_trades": 10,
                "total_pnl": 5000,
                "cagr": 0.05,
                "sharpe": 1.0,
                "max_dd": 0.05,
                "win_rate": 0.70,
                "trades": [
                    {"entry_date": "2020-06-15", "exit_date": "2020-06-20", "pnl": 500}
                    for _ in range(10)
                ],
                "yearly": {2020: {"n": 10, "pnl": 5000, "wr": 0.70, "sharpe": 1.0}},
            }

        spec = ExperimentSpec(
            experiment_id="EXP-NOVAL",
            name="No Validation",
            strategy_type="custom",
            ticker="SPY",
            custom_runner=simple_runner,
            validation="none",
        )

        runner = ExperimentRunner()
        runner._spy_df = pd.DataFrame(
            {"Close": [100] * 100},
            index=pd.date_range("2020-01-01", periods=100),
        )

        result = runner.run(spec)
        assert result.is_sharpe == 0  # no WF
        assert result.oos_sharpe == 0
        assert result.wf_windows == []


# ═══════════════════════════════════════════════════════════════════════════
# Report generation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestReporting:
    def _make_result(self) -> ExperimentResult:
        spec = ExperimentSpec(
            experiment_id="EXP-RPT", name="Report Test",
            strategy_type="credit_spread", ticker="SPY",
        )
        result = ExperimentResult(
            spec=spec, n_trades=50, total_pnl=15000,
            cagr=0.15, sharpe=2.5, max_dd=0.08, win_rate=0.72,
            spy_corr=0.05, oos_sharpe=2.0, is_sharpe=1.5,
            wf_ratio=1.33, estimated_capacity="$2B",
            profitable_years=5, total_years=6,
            yearly={2020: {"n": 10, "pnl": 3000, "wr": 0.8, "sharpe": 2.0}},
            timestamp=datetime.utcnow().isoformat(),
        )
        evaluate_north_star(result)
        return result

    def test_html_report(self, tmp_path):
        result = self._make_result()
        runner = ExperimentRunner()
        path = runner.generate_report(result, tmp_path / "test_report.html")
        assert path.exists()
        html = path.read_text()
        assert "EXP-RPT" in html
        assert "Report Test" in html
        assert "North Star" in html
        assert "TIER" in html
        assert "Walk-Forward" in html

    def test_json_report(self, tmp_path):
        result = self._make_result()
        runner = ExperimentRunner()
        path = runner.save_json(result, tmp_path / "test.json")
        assert path.exists()
        import json
        data = json.loads(path.read_text())
        assert data["experiment_id"] == "EXP-RPT"
        assert data["n_trades"] == 50
        assert data["tier"] in (1, 2, 3, 4)
        assert "verdict" in data

    def test_registry_update(self, tmp_path):
        result = self._make_result()
        registry = tmp_path / "REGISTRY.md"
        registry.write_text("# Test Registry\n\nSome content.\n")

        runner = ExperimentRunner()
        runner.update_registry(result, registry)

        content = registry.read_text()
        assert "EXP-RPT" in content
        assert "Report Test" in content

    def test_registry_no_duplicate(self, tmp_path):
        result = self._make_result()
        registry = tmp_path / "REGISTRY.md"
        registry.write_text("# Test Registry\nEXP-RPT already here\n")

        runner = ExperimentRunner()
        runner.update_registry(result, registry)

        # Should not add duplicate
        content = registry.read_text()
        assert content.count("EXP-RPT") == 1  # only the original mention


# ═══════════════════════════════════════════════════════════════════════════
# North Star thresholds sanity
# ═══════════════════════════════════════════════════════════════════════════

class TestNorthStarThresholds:
    def test_thresholds_exist(self):
        assert "min_cagr" in NORTH_STAR
        assert "min_sharpe" in NORTH_STAR
        assert "max_dd" in NORTH_STAR
        assert "min_trades" in NORTH_STAR

    def test_thresholds_reasonable(self):
        assert 0.0 < NORTH_STAR["min_cagr"] < 2.0
        assert NORTH_STAR["min_sharpe"] > 0
        assert 0.0 < NORTH_STAR["max_dd"] < 1.0
        assert NORTH_STAR["min_trades"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# ExperimentSpec new fields
# ═══════════════════════════════════════════════════════════════════════════

class TestExperimentSpecNewFields:
    def test_hypothesis_field(self):
        spec = ExperimentSpec(
            experiment_id="EXP-X", name="Test",
            strategy_type="custom", ticker="SPY",
            hypothesis="VRP is positive on average and decays predictably",
        )
        assert "VRP" in spec.hypothesis

    def test_success_criteria_field(self):
        spec = ExperimentSpec(
            experiment_id="EXP-X", name="Test",
            strategy_type="custom", ticker="SPY",
            success_criteria={"min_oos_sharpe": 1.0, "max_dd": 0.10},
        )
        assert spec.success_criteria["min_oos_sharpe"] == 1.0
        assert spec.success_criteria["max_dd"] == 0.10

    def test_defaults_empty(self):
        spec = ExperimentSpec(
            experiment_id="EXP-X", name="Test",
            strategy_type="custom", ticker="SPY",
        )
        assert spec.hypothesis == ""
        assert spec.success_criteria == {}


# ═══════════════════════════════════════════════════════════════════════════
# Success criteria tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSuccessCriteria:
    def _make_result(self, **kwargs):
        spec = ExperimentSpec(
            experiment_id="EXP-SC", name="Test",
            strategy_type="custom", ticker="SPY",
        )
        result = ExperimentResult(spec=spec, verdict="TIER 2")
        for k, v in kwargs.items():
            setattr(result, k, v)
        return result

    def test_all_criteria_pass(self):
        result = self._make_result(sharpe=3.0, oos_sharpe=2.0, cagr=0.50)
        _check_success_criteria({"min_sharpe": 2.0, "min_oos_sharpe": 1.0}, result)
        # Should not add failure message
        assert "0/" not in result.verdict

    def test_criteria_fail(self):
        result = self._make_result(sharpe=0.5, cagr=0.02)
        _check_success_criteria({"min_sharpe": 2.0, "min_cagr": 0.50}, result)
        assert "Custom criteria" in result.verdict

    def test_dd_criteria(self):
        result = self._make_result(max_dd=0.05)
        _check_success_criteria({"max_dd": 0.10}, result)
        assert "0/" not in result.verdict  # passes

    def test_empty_criteria(self):
        result = self._make_result()
        _check_success_criteria({}, result)
        assert result.verdict == "TIER 2"  # unchanged


# ═══════════════════════════════════════════════════════════════════════════
# JSON registry tests
# ═══════════════════════════════════════════════════════════════════════════

class TestJsonRegistry:
    def _make_result(self):
        spec = ExperimentSpec(
            experiment_id="EXP-REG-TEST", name="Registry Test",
            strategy_type="custom", ticker="SPY",
            hypothesis="Test hypothesis",
        )
        return ExperimentResult(
            spec=spec, n_trades=30, total_pnl=5000, cagr=0.15,
            sharpe=2.5, max_dd=0.05, win_rate=0.70,
            spy_corr=0.10, oos_sharpe=1.8, tier=2,
            verdict="TIER 2", timestamp="2026-04-05T12:00:00",
            estimated_capacity="$100M",
        )

    def test_create_new_registry(self, tmp_path):
        result = self._make_result()
        path = tmp_path / "registry.json"
        update_json_registry(result, path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert "EXP-REG-TEST" in data["experiments"]
        exp = data["experiments"]["EXP-REG-TEST"]
        assert exp["name"] == "Registry Test"
        assert exp["metrics"]["sharpe"] == 2.5
        assert exp["tier"] == 2
        assert exp["hypothesis"] == "Test hypothesis"

    def test_update_existing_registry(self, tmp_path):
        path = tmp_path / "registry.json"
        existing = {"schema_version": "3.0", "experiments": {
            "EXP-OLD": {"id": "EXP-OLD", "name": "Old"}
        }}
        path.write_text(json.dumps(existing))

        result = self._make_result()
        update_json_registry(result, path)

        data = json.loads(path.read_text())
        assert "EXP-OLD" in data["experiments"]
        assert "EXP-REG-TEST" in data["experiments"]

    def test_overwrite_same_id(self, tmp_path):
        path = tmp_path / "registry.json"
        result = self._make_result()
        update_json_registry(result, path)
        # Update again with new sharpe
        result.sharpe = 5.0
        update_json_registry(result, path)
        data = json.loads(path.read_text())
        assert data["experiments"]["EXP-REG-TEST"]["metrics"]["sharpe"] == 5.0


# ═══════════════════════════════════════════════════════════════════════════
# Batch runner tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBatchRunner:
    def _make_runner_spec(self, exp_id, pnl):
        def runner(spec):
            return {
                "n_trades": 20, "total_pnl": pnl, "cagr": pnl / 100000,
                "sharpe": pnl / 5000, "max_dd": 0.05, "win_rate": 0.70,
                "trades": [{"entry_date": f"2022-{m:02d}-15",
                             "exit_date": f"2022-{m:02d}-20", "pnl": pnl / 12}
                           for m in range(1, 13)],
                "yearly": {2022: {"n": 12, "pnl": pnl, "wr": 0.70, "sharpe": pnl / 5000}},
            }
        return ExperimentSpec(
            experiment_id=exp_id, name=f"Test {exp_id}",
            strategy_type="custom", ticker="SPY",
            custom_runner=runner, validation="none",
        )

    def test_batch_runs_all(self, tmp_path):
        specs = [
            self._make_runner_spec("EXP-A", 10000),
            self._make_runner_spec("EXP-B", 5000),
            self._make_runner_spec("EXP-C", 20000),
        ]
        batch = BatchRunner()
        batch.runner._spy_df = pd.DataFrame(
            {"Close": np.random.normal(400, 10, 1000)},
            index=pd.date_range("2020-01-01", periods=1000),
        )
        results = batch.run_batch(specs, output_dir=tmp_path, rank_by="sharpe")
        assert len(results) == 3
        # Should be sorted by sharpe (highest first)
        assert results[0].sharpe >= results[1].sharpe >= results[2].sharpe

    def test_batch_generates_summary(self, tmp_path):
        specs = [self._make_runner_spec("EXP-X", 8000)]
        batch = BatchRunner()
        batch.runner._spy_df = pd.DataFrame(
            {"Close": [400] * 500},
            index=pd.date_range("2020-01-01", periods=500),
        )
        batch.run_batch(specs, output_dir=tmp_path)
        summary = tmp_path / "batch_summary.html"
        assert summary.exists()
        html = summary.read_text()
        assert "EXP-X" in html


# ═══════════════════════════════════════════════════════════════════════════
# Parameter sweep tests
# ═══════════════════════════════════════════════════════════════════════════

class TestParameterSweep:
    def test_sweep_returns_results(self):
        def custom(spec):
            w = spec.params.get("spread_width", 5)
            return {
                "n_trades": 20, "total_pnl": w * 100,
                "cagr": w * 0.01, "sharpe": w * 0.5,
                "max_dd": 0.05, "win_rate": 0.70,
                "trades": [{"entry_date": "2022-06-15", "exit_date": "2022-06-20",
                            "pnl": w * 10}],
                "yearly": {2022: {"n": 1, "pnl": w * 100, "wr": 1.0, "sharpe": w}},
            }

        base = ExperimentSpec(
            experiment_id="EXP-SWEEP", name="Sweep Test",
            strategy_type="custom", ticker="SPY",
            custom_runner=custom, validation="none",
        )
        sweep = ParameterSweep()
        sweep.runner._spy_df = pd.DataFrame(
            {"Close": [400] * 500},
            index=pd.date_range("2020-01-01", periods=500),
        )
        results = sweep.sweep(base, {"spread_width": [3, 5, 10]}, rank_by="sharpe")

        assert len(results) == 3
        # Width 10 should have highest sharpe (w * 0.5)
        assert results[0].params["spread_width"] == 10

    def test_sweep_grid_combinations(self):
        call_count = [0]

        def custom(spec):
            call_count[0] += 1
            return {
                "n_trades": 10, "total_pnl": 1000, "cagr": 0.01,
                "sharpe": 1.0, "max_dd": 0.02, "win_rate": 0.60,
                "trades": [{"entry_date": "2022-06-15", "exit_date": "2022-06-20", "pnl": 100}],
                "yearly": {},
            }

        base = ExperimentSpec(
            experiment_id="EXP-GRID", name="Grid Test",
            strategy_type="custom", ticker="SPY",
            custom_runner=custom, validation="none",
        )
        sweep = ParameterSweep()
        sweep.runner._spy_df = pd.DataFrame(
            {"Close": [400] * 200},
            index=pd.date_range("2020-01-01", periods=200),
        )
        results = sweep.sweep(base, {"width": [3, 5], "dte": [14, 30]})
        assert len(results) == 4  # 2 × 2 grid
        assert call_count[0] == 4

    def test_sweep_result_dataclass(self):
        sr = SweepResult(params={"a": 1}, sharpe=2.5, cagr=0.15)
        assert sr.params == {"a": 1}
        assert sr.sharpe == 2.5
        assert sr.tier == 4  # default
