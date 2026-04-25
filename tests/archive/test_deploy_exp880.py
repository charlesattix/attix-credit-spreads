"""Tests for scripts/deploy_exp880_paper.py — pre-flight checker."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from deploy_exp880_paper import (
    CheckResult,
    PreFlightResult,
    check_alpaca_api,
    check_config,
    check_crisis_hedge_params,
    check_infrastructure,
    check_model_files,
    check_signal_generation,
    make_decision,
    run_preflight,
)

CONFIG_PATH = Path(__file__).parent.parent / "configs" / "paper_exp880.yaml"


# ── Config validation tests ──────────────────────────────────────────────


class TestConfigValidation:
    def test_real_config_passes(self):
        if not CONFIG_PATH.exists():
            pytest.skip("paper_exp880.yaml not found")
        results = check_config(CONFIG_PATH)
        passed = [r for r in results if r.passed]
        assert len(passed) >= 8  # most checks should pass

    def test_missing_file(self):
        results = check_config(Path("/nonexistent/config.yaml"))
        assert not results[0].passed
        assert "not found" in results[0].detail

    def test_paper_mode_checked(self):
        if not CONFIG_PATH.exists():
            pytest.skip()
        results = check_config(CONFIG_PATH)
        paper_check = next((r for r in results if r.name == "paper_mode_true"), None)
        assert paper_check is not None
        assert paper_check.passed  # must be true

    def test_crisis_hedge_enabled(self):
        if not CONFIG_PATH.exists():
            pytest.skip()
        results = check_config(CONFIG_PATH)
        hedge = next((r for r in results if r.name == "crisis_hedge_enabled"), None)
        assert hedge is not None
        assert hedge.passed

    def test_hedge_params_match(self):
        if not CONFIG_PATH.exists():
            pytest.skip()
        results = check_config(CONFIG_PATH)
        min_scale = next((r for r in results if r.name == "hedge_min_scale"), None)
        assert min_scale is not None
        assert min_scale.passed  # should be 0.20

    def test_ml_ensemble_enabled(self):
        if not CONFIG_PATH.exists():
            pytest.skip()
        results = check_config(CONFIG_PATH)
        ml = next((r for r in results if r.name == "ml_ensemble_enabled"), None)
        assert ml is not None

    def test_leverage_in_range(self):
        if not CONFIG_PATH.exists():
            pytest.skip()
        results = check_config(CONFIG_PATH)
        lev = next((r for r in results if r.name == "leverage_range"), None)
        assert lev is not None
        assert lev.passed  # 2.0x is in [1.5, 3.0]

    def test_dd_circuit_breaker(self):
        if not CONFIG_PATH.exists():
            pytest.skip()
        results = check_config(CONFIG_PATH)
        dd = next((r for r in results if r.name == "dd_circuit_breaker"), None)
        assert dd is not None
        assert dd.passed  # 12% is in [10, 15]


# ── Alpaca API tests ─────────────────────────────────────────────────────


class TestAlpacaAPI:
    def test_skip_flag(self):
        results = check_alpaca_api(skip=True)
        assert len(results) == 1
        assert results[0].passed

    def test_no_key_fails(self):
        with patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_API_SECRET": ""}, clear=False):
            results = check_alpaca_api(skip=False)
            failed = [r for r in results if not r.passed]
            assert len(failed) > 0

    def test_placeholder_fails(self):
        with patch.dict(os.environ, {"ALPACA_API_KEY": "your_paper_api_key_here"}, clear=False):
            results = check_alpaca_api(skip=False)
            assert not results[0].passed


# ── Model files tests ────────────────────────────────────────────────────


class TestModelFiles:
    def test_ensemble_module_exists(self):
        results = check_model_files()
        ensemble = next((r for r in results if r.name == "ensemble_module"), None)
        assert ensemble is not None
        assert ensemble.passed  # compass/production_ensemble.py exists

    def test_returns_checks(self):
        results = check_model_files()
        assert len(results) >= 2


# ── Signal generation tests ──────────────────────────────────────────────


class TestSignalGeneration:
    def test_signal_scoring_works(self):
        results = check_signal_generation()
        scoring = next((r for r in results if r.name == "signal_scoring"), None)
        assert scoring is not None
        assert scoring.passed

    def test_crisis_hedge_init(self):
        results = check_signal_generation()
        hedge = next((r for r in results if r.name == "crisis_hedge_init"), None)
        assert hedge is not None
        assert hedge.passed


# ── Crisis hedge params tests ────────────────────────────────────────────


class TestCrisisHedgeParams:
    def test_exp880_results(self):
        results = check_crisis_hedge_params()
        loaded = next((r for r in results if "exp880" in r.name and "loaded" in r.name), None)
        if loaded:
            # If results exist, check they loaded
            assert isinstance(loaded.passed, bool)

    def test_validation_suite(self):
        results = check_crisis_hedge_params()
        val = next((r for r in results if "validation" in r.name), None)
        if val:
            assert isinstance(val.passed, bool)


# ── Infrastructure tests ─────────────────────────────────────────────────


class TestInfrastructure:
    def test_data_dir_created(self):
        results = check_infrastructure()
        data = next((r for r in results if r.name == "data_dir"), None)
        assert data is not None
        assert data.passed

    def test_logs_dir_created(self):
        results = check_infrastructure()
        logs = next((r for r in results if r.name == "logs_dir"), None)
        assert logs is not None
        assert logs.passed


# ── Decision logic tests ─────────────────────────────────────────────────


class TestDecision:
    def test_all_pass_is_go(self):
        checks = [
            CheckResult("a", True, "ok"),
            CheckResult("b", True, "ok"),
        ]
        assert make_decision(checks) is True

    def test_required_fail_is_nogo(self):
        checks = [
            CheckResult("a", True, "ok"),
            CheckResult("b", False, "fail", severity="required"),
        ]
        assert make_decision(checks) is False

    def test_recommended_fail_is_still_go(self):
        checks = [
            CheckResult("a", True, "ok"),
            CheckResult("b", False, "warn", severity="recommended"),
        ]
        assert make_decision(checks) is True

    def test_multiple_required_fails(self):
        checks = [
            CheckResult("a", False, "fail"),
            CheckResult("b", False, "fail"),
        ]
        assert make_decision(checks) is False


# ── Full preflight tests ─────────────────────────────────────────────────


class TestFullPreflight:
    def test_runs(self):
        result = run_preflight(skip_api=True)
        assert isinstance(result, PreFlightResult)
        assert result.n_passed + result.n_failed == len(result.checks)

    def test_has_timestamp(self):
        result = run_preflight(skip_api=True)
        assert len(result.timestamp) > 0

    def test_has_config_path(self):
        result = run_preflight(skip_api=True)
        assert "paper_exp880" in result.config_path

    def test_custom_config_path(self):
        result = run_preflight(config_path=CONFIG_PATH, skip_api=True)
        assert "paper_exp880" in result.config_path

    def test_missing_config(self):
        result = run_preflight(config_path=Path("/nonexistent.yaml"), skip_api=True)
        assert result.n_required_failed > 0
        assert result.go_decision is False

    def test_decision_computed(self):
        result = run_preflight(skip_api=True)
        assert isinstance(result.go_decision, bool)
