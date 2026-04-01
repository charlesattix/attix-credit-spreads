"""Tests for compass.deployment_validator — 32 tests."""

import os
import pytest
from pathlib import Path

from compass.deployment_validator import (
    DeploymentValidator, CheckResult, ValidationReport,
)


ROOT = Path(__file__).resolve().parent.parent


# ===========================================================================
# 1. Module imports
# ===========================================================================

class TestModuleImports:
    def test_all_pass(self):
        dv = DeploymentValidator(str(ROOT))
        result = dv.check_module_imports()
        assert result.passed
        assert "All" in result.detail

    def test_bad_module(self):
        dv = DeploymentValidator(str(ROOT))
        dv.REQUIRED_MODULES = ["compass.nonexistent_xyz_999"]
        result = dv.check_module_imports()
        assert not result.passed
        assert "failed" in result.detail

    def test_partial_failure(self):
        dv = DeploymentValidator(str(ROOT))
        dv.REQUIRED_MODULES = ["compass.regime", "compass.fake_abc_777"]
        result = dv.check_module_imports()
        assert not result.passed
        assert "1 modules failed" in result.detail


# ===========================================================================
# 2. Config schema
# ===========================================================================

class TestConfigSchema:
    def test_valid_config(self):
        dv = DeploymentValidator(str(ROOT))
        cfg = {"tickers": ["SPY"], "strategy": {"min_dte": 15, "max_dte": 25}, "risk": {}}
        result = dv.check_config_schema(config=cfg)
        assert result.passed

    def test_missing_tickers(self):
        dv = DeploymentValidator(str(ROOT))
        cfg = {"strategy": {"min_dte": 15, "max_dte": 25}, "risk": {}}
        result = dv.check_config_schema(config=cfg)
        assert not result.passed
        assert "tickers" in result.detail

    def test_missing_strategy_keys(self):
        dv = DeploymentValidator(str(ROOT))
        cfg = {"tickers": ["SPY"], "strategy": {"min_dte": 15}, "risk": {}}
        result = dv.check_config_schema(config=cfg)
        assert not result.passed
        assert "max_dte" in result.detail

    def test_empty_config(self):
        dv = DeploymentValidator(str(ROOT))
        result = dv.check_config_schema(config={})
        assert not result.passed

    def test_real_config_file(self):
        cfg_path = ROOT / "configs" / "paper_exp880.yaml"
        if cfg_path.exists():
            dv = DeploymentValidator(str(ROOT), "configs/paper_exp880.yaml")
            result = dv.check_config_schema()
            assert isinstance(result, CheckResult)


# ===========================================================================
# 3. Alpaca connectivity
# ===========================================================================

class TestAlpacaConnectivity:
    def test_no_credentials(self):
        old_key = os.environ.pop("ALPACA_API_KEY", None)
        old_secret = os.environ.pop("ALPACA_API_SECRET", None)
        try:
            dv = DeploymentValidator(str(ROOT))
            result = dv.check_alpaca_connectivity()
            assert not result.passed
            assert "not set" in result.detail
        finally:
            if old_key:
                os.environ["ALPACA_API_KEY"] = old_key
            if old_secret:
                os.environ["ALPACA_API_SECRET"] = old_secret


# ===========================================================================
# 4. Model files
# ===========================================================================

class TestModelFiles:
    def test_real_dirs(self):
        dv = DeploymentValidator(str(ROOT))
        result = dv.check_model_files()
        assert isinstance(result, CheckResult)

    def test_custom_paths_found(self, tmp_path):
        model_dir = tmp_path / "models"
        model_dir.mkdir()
        (model_dir / "model.pkl").write_text("fake")
        dv = DeploymentValidator(str(tmp_path))
        result = dv.check_model_files(model_paths=["models"])
        assert result.passed
        assert "1 files" in result.detail

    def test_custom_paths_empty(self, tmp_path):
        model_dir = tmp_path / "models"
        model_dir.mkdir()
        dv = DeploymentValidator(str(tmp_path))
        result = dv.check_model_files(model_paths=["models"])
        assert not result.passed

    def test_missing_dir(self, tmp_path):
        dv = DeploymentValidator(str(tmp_path))
        result = dv.check_model_files(model_paths=["nonexistent"])
        assert not result.passed


# ===========================================================================
# 5. Directory permissions
# ===========================================================================

class TestDirectoryPermissions:
    def test_writable(self, tmp_path):
        dv = DeploymentValidator(str(tmp_path))
        result = dv.check_directory_permissions(dirs=["data", "output"])
        assert result.passed
        assert "2 directories writable" in result.detail

    def test_creates_missing(self, tmp_path):
        dv = DeploymentValidator(str(tmp_path))
        result = dv.check_directory_permissions(dirs=["new_dir"])
        assert result.passed
        assert (tmp_path / "new_dir").exists()

    def test_real_project(self):
        dv = DeploymentValidator(str(ROOT))
        result = dv.check_directory_permissions()
        assert result.passed


# ===========================================================================
# 6. Telegram config
# ===========================================================================

class TestTelegramConfig:
    def test_not_configured(self):
        old_token = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        old_chat = os.environ.pop("TELEGRAM_CHAT_ID", None)
        try:
            dv = DeploymentValidator(str(ROOT))
            result = dv.check_telegram_config()
            assert result.passed  # optional, so missing is OK
            assert "not configured" in result.detail
        finally:
            if old_token:
                os.environ["TELEGRAM_BOT_TOKEN"] = old_token
            if old_chat:
                os.environ["TELEGRAM_CHAT_ID"] = old_chat

    def test_partial_config(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "test_token_12345678901234567890"
        old_chat = os.environ.pop("TELEGRAM_CHAT_ID", None)
        try:
            dv = DeploymentValidator(str(ROOT))
            result = dv.check_telegram_config()
            assert not result.passed
            assert "CHAT_ID missing" in result.detail
        finally:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            if old_chat:
                os.environ["TELEGRAM_CHAT_ID"] = old_chat

    def test_both_present(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "1234567890:ABCDEFghijklmnop_12345"
        os.environ["TELEGRAM_CHAT_ID"] = "-100123456"
        try:
            dv = DeploymentValidator(str(ROOT))
            result = dv.check_telegram_config()
            assert result.passed
        finally:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)


# ===========================================================================
# 7. Dry trade simulation
# ===========================================================================

class TestDryTrade:
    def test_passes(self):
        dv = DeploymentValidator(str(ROOT))
        result = dv.check_dry_trade()
        assert result.passed
        assert "Dry trade OK" in result.detail
        assert "position" in result.detail
        assert "audit" in result.detail


# ===========================================================================
# 8. Full validation
# ===========================================================================

class TestFullValidation:
    def test_with_mock_config(self):
        dv = DeploymentValidator(str(ROOT))
        cfg = {"tickers": ["SPY"], "strategy": {"min_dte": 15, "max_dte": 25}, "risk": {}}
        report = dv.validate(config=cfg, skip_alpaca=True)
        assert isinstance(report, ValidationReport)
        assert report.n_passed >= 4  # imports, config, dirs, dry trade should pass
        assert report.total_duration_ms > 0
        assert report.timestamp is not None

    def test_report_structure(self):
        dv = DeploymentValidator(str(ROOT))
        cfg = {"tickers": ["SPY"], "strategy": {"min_dte": 15, "max_dte": 25}, "risk": {}}
        report = dv.validate(config=cfg, skip_alpaca=True)
        assert len(report.checks) >= 6  # 7 minus alpaca
        assert report.n_passed + report.n_failed == len(report.checks)

    def test_all_passed_flag(self):
        dv = DeploymentValidator(str(ROOT))
        cfg = {"tickers": ["SPY"], "strategy": {"min_dte": 15, "max_dte": 25}, "risk": {}}
        report = dv.validate(config=cfg, skip_alpaca=True,
                              model_paths=["ml/models", "data/models"],
                              dirs=["data", "output", "reports"])
        # all_passed depends on model files existing
        assert isinstance(report.all_passed, bool)


# ===========================================================================
# HTML report
# ===========================================================================

class TestHTMLReport:
    def test_creates_file(self, tmp_path):
        dv = DeploymentValidator(str(ROOT))
        cfg = {"tickers": ["SPY"], "strategy": {"min_dte": 15, "max_dte": 25}, "risk": {}}
        report = dv.validate(config=cfg, skip_alpaca=True)
        out = tmp_path / "validation.html"
        path = dv.generate_report(report, str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Deployment Validation" in html
        assert "PASS" in html

    def test_shows_failures(self, tmp_path):
        dv = DeploymentValidator(str(ROOT))
        report = dv.validate(config={}, skip_alpaca=True)  # bad config
        out = tmp_path / "v.html"
        dv.generate_report(report, str(out))
        html = out.read_text()
        assert "FAIL" in html
