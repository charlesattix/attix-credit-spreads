"""
Tests for compass/experiment_launcher.py — Config validation and experiment launcher.

Covers:
  - Schema validation (missing fields, type errors, range violations, cross-field)
  - Pre-flight checks (training data, models, features, risk gates, hedge params)
  - Dry-run simulation (all stages, risk gate blocking)
  - PreflightReport properties (all_passed, n_errors, n_warnings)
  - HTML launch report (sections, badges, content)
  - ExperimentLauncher integration (preflight, custom checks)
  - Edge cases (empty config, partial config)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from compass.experiment_launcher import (
    CheckResult,
    DryRunResult,
    ExperimentLauncher,
    PreflightReport,
    ValidationError,
    _check_feature_pipeline,
    _check_hedge_params,
    _check_risk_gates,
    _check_training_data,
    simulate_dry_run,
    validate_config,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def valid_config():
    """A fully valid experiment config."""
    return {
        "strategy": {
            "min_dte": 30,
            "max_dte": 45,
            "min_delta": 0.20,
            "max_delta": 0.30,
            "spread_width": 5,
        },
        "risk": {
            "max_risk_per_trade": 5.0,
            "max_positions": 6,
            "profit_target": 50,
            "stop_loss_pct_of_width": 75,
            "max_portfolio_risk_pct": 40,
        },
        "backtest": {
            "starting_capital": 100_000,
            "commission_per_contract": 0.65,
        },
    }


@pytest.fixture
def base_dir(tmp_path):
    """Temp dir mimicking project structure with training data and features."""
    compass_dir = tmp_path / "compass"
    compass_dir.mkdir()
    # Training data
    (compass_dir / "training_data_combined.csv").write_text("col1,col2\n1,2\n")
    # Feature pipeline
    (compass_dir / "feature_pipeline.py").write_text("# stub\n")
    # Model file
    (compass_dir / "signal_model.joblib").write_text("stub")
    return tmp_path


@pytest.fixture
def launcher(valid_config, base_dir):
    """Ready-to-use ExperimentLauncher."""
    return ExperimentLauncher(
        config=valid_config,
        experiment_id="EXP-TEST",
        base_dir=base_dir,
        backtest_metrics={"sharpe": 1.5, "max_dd_pct": 12.0, "win_rate": 65.0},
    )


# ── Validation tests ─────────────────────────────────────────────────────────

class TestValidation:
    def test_valid_config_no_errors(self, valid_config):
        errors = validate_config(valid_config)
        assert len(errors) == 0

    def test_missing_top_level_section(self, valid_config):
        del valid_config["risk"]
        errors = validate_config(valid_config)
        assert any(e.field == "risk" for e in errors)

    def test_missing_all_sections(self):
        errors = validate_config({})
        assert len(errors) >= 3  # strategy, risk, backtest

    def test_missing_strategy_field(self, valid_config):
        del valid_config["strategy"]["min_dte"]
        errors = validate_config(valid_config)
        assert any(e.field == "min_dte" for e in errors)

    def test_wrong_type(self, valid_config):
        valid_config["strategy"]["min_dte"] = "thirty"
        errors = validate_config(valid_config)
        assert any("type" in e.message.lower() for e in errors)

    def test_below_minimum(self, valid_config):
        valid_config["strategy"]["min_delta"] = -0.5
        errors = validate_config(valid_config)
        assert any("below minimum" in e.message for e in errors)

    def test_above_maximum(self, valid_config):
        valid_config["strategy"]["max_delta"] = 5.0
        errors = validate_config(valid_config)
        assert any("above maximum" in e.message for e in errors)

    def test_cross_field_min_dte_gte_max_dte(self, valid_config):
        valid_config["strategy"]["min_dte"] = 50
        valid_config["strategy"]["max_dte"] = 30
        errors = validate_config(valid_config)
        assert any("min_dte" in e.field and "max_dte" in e.field for e in errors)

    def test_cross_field_min_delta_gte_max_delta(self, valid_config):
        valid_config["strategy"]["min_delta"] = 0.40
        valid_config["strategy"]["max_delta"] = 0.20
        errors = validate_config(valid_config)
        assert any("min_delta" in e.field for e in errors)

    def test_risk_field_missing(self, valid_config):
        del valid_config["risk"]["max_positions"]
        errors = validate_config(valid_config)
        assert any(e.section == "risk" and e.field == "max_positions" for e in errors)

    def test_backtest_capital_too_low(self, valid_config):
        valid_config["backtest"]["starting_capital"] = 100
        errors = validate_config(valid_config)
        assert any("below minimum" in e.message for e in errors)

    def test_validation_error_dataclass(self):
        ve = ValidationError(section="test", field="f", message="bad")
        assert ve.severity == "error"


# ── Pre-flight check tests ────────────────────────────────────────────────────

class TestPreflightChecks:
    def test_training_data_found(self, valid_config, base_dir):
        result = _check_training_data(valid_config, base_dir)
        assert result.passed
        assert "training_data" in result.details

    def test_training_data_missing(self, valid_config, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        (empty_dir / "compass").mkdir()
        result = _check_training_data(valid_config, empty_dir)
        assert not result.passed

    def test_feature_pipeline_found(self, valid_config, base_dir):
        result = _check_feature_pipeline(valid_config, base_dir)
        assert result.passed

    def test_feature_pipeline_missing(self, valid_config, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        (empty_dir / "compass").mkdir()
        result = _check_feature_pipeline(valid_config, empty_dir)
        assert not result.passed

    def test_risk_gates_configured(self, valid_config, base_dir):
        result = _check_risk_gates(valid_config, base_dir)
        assert result.passed

    def test_risk_gates_missing_config(self, base_dir):
        result = _check_risk_gates({}, base_dir)
        assert not result.passed

    def test_risk_gates_missing_critical_field(self, base_dir):
        config = {"risk": {"profit_target": 50}}
        result = _check_risk_gates(config, base_dir)
        assert not result.passed

    def test_hedge_params_configured(self, valid_config, base_dir):
        result = _check_hedge_params(valid_config, base_dir)
        assert result.passed

    def test_hedge_params_missing_stop(self, base_dir):
        config = {"risk": {"profit_target": 50}}
        result = _check_hedge_params(config, base_dir)
        assert not result.passed
        assert "stop_loss" in result.message

    def test_check_result_dataclass(self):
        cr = CheckResult(name="test", passed=True, message="ok")
        assert cr.passed
        assert cr.details == ""


# ── Dry-run tests ─────────────────────────────────────────────────────────────

class TestDryRun:
    def test_returns_dry_run_result(self, valid_config, base_dir):
        result = simulate_dry_run(valid_config, base_dir)
        assert isinstance(result, DryRunResult)

    def test_all_stages_present(self, valid_config, base_dir):
        result = simulate_dry_run(valid_config, base_dir)
        stages = {s.stage for s in result.steps}
        assert "load_config" in stages
        assert "build_features" in stages
        assert "run_model" in stages
        assert "check_sizing" in stages
        assert "risk_gates" in stages
        assert "summary" in stages

    def test_would_trade_with_valid_config(self, valid_config, base_dir):
        result = simulate_dry_run(valid_config, base_dir)
        assert result.would_trade

    def test_blocked_by_excessive_risk(self, valid_config, base_dir):
        valid_config["risk"]["max_risk_per_trade"] = 10.0  # > 5% MASTERPLAN limit
        result = simulate_dry_run(valid_config, base_dir)
        assert not result.would_trade
        assert result.risk_gate_result == "block"

    def test_estimated_contracts_positive(self, valid_config, base_dir):
        result = simulate_dry_run(valid_config, base_dir)
        assert result.estimated_contracts >= 1

    def test_estimated_risk_dollars(self, valid_config, base_dir):
        result = simulate_dry_run(valid_config, base_dir)
        assert result.estimated_risk_dollars == 100_000 * 0.05  # 5% of 100k

    def test_no_model_warns(self, valid_config, tmp_path):
        empty = tmp_path / "nomodels"
        empty.mkdir()
        compass = empty / "compass"
        compass.mkdir()
        (compass / "feature_pipeline.py").write_text("# stub")
        result = simulate_dry_run(valid_config, empty)
        model_step = next(s for s in result.steps if s.stage == "run_model")
        assert model_step.status == "warn"


# ── PreflightReport tests ────────────────────────────────────────────────────

class TestPreflightReport:
    def test_all_passed_with_valid(self, launcher):
        report = launcher.preflight()
        assert isinstance(report, PreflightReport)
        assert report.n_errors == 0

    def test_not_passed_with_invalid(self, base_dir):
        bad_config = {"strategy": {"min_dte": "bad"}}
        launcher = ExperimentLauncher(bad_config, base_dir=base_dir)
        report = launcher.preflight()
        assert not report.all_passed
        assert report.n_errors > 0

    def test_n_checks_total(self, launcher):
        report = launcher.preflight()
        assert report.n_checks_total == 5  # 5 default checks

    def test_experiment_id_in_report(self, launcher):
        report = launcher.preflight()
        assert report.experiment_id == "EXP-TEST"

    def test_config_summary_populated(self, launcher):
        report = launcher.preflight()
        assert "strategy" in report.config_summary
        assert "risk" in report.config_summary

    def test_dry_run_included(self, launcher):
        report = launcher.preflight(include_dry_run=True)
        assert report.dry_run is not None

    def test_dry_run_excluded(self, launcher):
        report = launcher.preflight(include_dry_run=False)
        assert report.dry_run is None

    def test_estimated_performance(self, launcher):
        report = launcher.preflight()
        assert report.estimated_performance["sharpe"] == 1.5


# ── HTML report tests ─────────────────────────────────────────────────────────

class TestHTMLReport:
    def test_generates_valid_html(self, launcher):
        report = launcher.preflight()
        html = launcher.generate_html(report)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_contains_experiment_id(self, launcher):
        report = launcher.preflight()
        html = launcher.generate_html(report)
        assert "EXP-TEST" in html

    def test_contains_status_badge(self, launcher):
        report = launcher.preflight()
        html = launcher.generate_html(report)
        assert "READY" in html

    def test_contains_validation_section(self, launcher):
        report = launcher.preflight()
        html = launcher.generate_html(report)
        assert "Validation" in html

    def test_contains_preflight_checks(self, launcher):
        report = launcher.preflight()
        html = launcher.generate_html(report)
        assert "Pre-Flight Checks" in html

    def test_contains_dry_run(self, launcher):
        report = launcher.preflight()
        html = launcher.generate_html(report)
        assert "Dry Run" in html
        assert "Would Trade" in html

    def test_contains_config_summary(self, launcher):
        report = launcher.preflight()
        html = launcher.generate_html(report)
        assert "Config Summary" in html

    def test_contains_estimated_performance(self, launcher):
        report = launcher.preflight()
        html = launcher.generate_html(report)
        assert "Estimated Performance" in html
        assert "Sharpe" in html

    def test_blocked_status_badge(self, base_dir):
        bad_config = {}
        launcher = ExperimentLauncher(bad_config, base_dir=base_dir)
        report = launcher.preflight()
        html = launcher.generate_html(report)
        assert "NOT READY" in html


# ── File output tests ─────────────────────────────────────────────────────────

class TestFileOutput:
    def test_writes_html_file(self, launcher):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "launch.html")
            report = launcher.generate_report_file(output_path=path)
            assert os.path.isfile(path)
            assert isinstance(report, PreflightReport)
            with open(path) as f:
                assert "<!DOCTYPE html>" in f.read()

    def test_creates_output_directory(self, launcher):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sub", "dir", "report.html")
            launcher.generate_report_file(output_path=path)
            assert os.path.isfile(path)


# ── Custom checks tests ──────────────────────────────────────────────────────

class TestCustomChecks:
    def test_custom_check_function(self, valid_config, base_dir):
        def _custom_check(config, bd):
            return CheckResult(name="custom", passed=True, message="Custom OK")

        launcher = ExperimentLauncher(
            valid_config, base_dir=base_dir, checks=[_custom_check]
        )
        report = launcher.preflight(include_dry_run=False)
        assert report.n_checks_total == 1
        assert report.checks[0].name == "custom"

    def test_empty_checks_list(self, valid_config, base_dir):
        launcher = ExperimentLauncher(
            valid_config, base_dir=base_dir, checks=[]
        )
        report = launcher.preflight(include_dry_run=False)
        assert report.n_checks_total == 0
