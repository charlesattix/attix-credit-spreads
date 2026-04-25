"""Tests for compass.iron_condor_optimizer."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from compass.iron_condor_optimizer import (
    CAPITAL,
    IC_TICKERS,
    ICConfig,
    ICResult,
    ICYearResult,
    IronCondorOptimizer,
    OptimizationResult,
    VIX_FILTER_RANGES,
    _compute_ic_result,
    _result_to_dict,
    backtest_iron_condor,
)


# ── ICConfig ─────────────────────────────────────────────────────────────


class TestICConfig:
    def test_default_label_generated(self):
        c = ICConfig(
            ticker="XLF", sizing_pct=0.05, spread_width=2,
            target_dte=35, min_entry_offset=28,
            put_otm_pct=0.07, call_otm_pct=0.05, regime_filter="none",
        )
        assert "XLF" in c.label
        assert "5%" in c.label

    def test_custom_label_preserved(self):
        c = ICConfig(
            ticker="XLF", sizing_pct=0.05, spread_width=2,
            target_dte=35, min_entry_offset=28,
            put_otm_pct=0.07, call_otm_pct=0.05, regime_filter="none",
            label="custom",
        )
        assert c.label == "custom"


# ── VIX filter ranges ────────────────────────────────────────────────────


class TestVIXFilters:
    def test_none_filter_allows_all(self):
        lo, hi = VIX_FILTER_RANGES["none"]
        assert lo == 0 and hi == 100

    def test_low_vol_filter(self):
        lo, hi = VIX_FILTER_RANGES["low_vol"]
        assert lo == 0 and hi == 20

    def test_high_vol_filter(self):
        lo, hi = VIX_FILTER_RANGES["high_vol"]
        assert lo == 20 and hi == 100

    def test_moderate_filter(self):
        lo, hi = VIX_FILTER_RANGES["moderate"]
        assert lo == 15 and hi == 30


# ── _compute_ic_result ───────────────────────────────────────────────────


class TestComputeICResult:
    def _make_config(self):
        return ICConfig(
            ticker="XLF", sizing_pct=0.05, spread_width=1,
            target_dte=35, min_entry_offset=28,
            put_otm_pct=0.07, call_otm_pct=0.05, regime_filter="none",
        )

    def test_empty_trades(self):
        r = _compute_ic_result(self._make_config(), [])
        assert r.n_trades == 0
        assert r.total_pnl == 0
        assert r.sharpe == 0

    def test_single_winning_trade(self):
        trades = [{"entry_date": "2023-01-15", "exit_date": "2023-02-15", "pnl": 500}]
        r = _compute_ic_result(self._make_config(), trades)
        assert r.n_trades == 1
        assert r.total_pnl == 500
        assert r.win_rate == 1.0

    def test_mixed_trades(self):
        trades = [
            {"entry_date": "2022-01-15", "exit_date": "2022-02-15", "pnl": 200},
            {"entry_date": "2022-03-15", "exit_date": "2022-04-15", "pnl": -100},
            {"entry_date": "2023-05-15", "exit_date": "2023-06-15", "pnl": 300},
            {"entry_date": "2024-01-15", "exit_date": "2024-02-15", "pnl": 150},
        ]
        r = _compute_ic_result(self._make_config(), trades)
        assert r.n_trades == 4
        assert r.total_pnl == 550
        assert r.win_rate == 0.75
        assert r.max_dd >= 0
        # Walk-forward: IS=2022 (2 trades), OOS=2023-2024 (2 trades)
        assert r.oos_sharpe != 0 or r.is_sharpe != 0

    def test_yearly_breakdown(self):
        trades = [
            {"entry_date": "2022-01-15", "exit_date": "2022-02-15", "pnl": 200},
            {"entry_date": "2023-05-15", "exit_date": "2023-06-15", "pnl": 300},
        ]
        r = _compute_ic_result(self._make_config(), trades)
        assert 2022 in r.yearly
        assert 2023 in r.yearly
        assert r.yearly[2022].n_trades == 1
        assert r.yearly[2023].total_pnl == 300

    def test_max_dd_computed(self):
        trades = [
            {"entry_date": "2023-01-15", "exit_date": "2023-02-15", "pnl": 500},
            {"entry_date": "2023-03-15", "exit_date": "2023-04-15", "pnl": -800},
            {"entry_date": "2023-05-15", "exit_date": "2023-06-15", "pnl": 200},
        ]
        r = _compute_ic_result(self._make_config(), trades)
        assert r.max_dd > 0  # Should have a drawdown after the loss

    def test_wf_ratio(self):
        trades = [
            {"entry_date": "2020-06-15", "exit_date": "2020-07-15", "pnl": 100},
            {"entry_date": "2021-06-15", "exit_date": "2021-07-15", "pnl": 200},
            {"entry_date": "2022-06-15", "exit_date": "2022-07-15", "pnl": -50},
            {"entry_date": "2023-06-15", "exit_date": "2023-07-15", "pnl": 150},
            {"entry_date": "2024-06-15", "exit_date": "2024-07-15", "pnl": 300},
        ]
        r = _compute_ic_result(self._make_config(), trades)
        # IS = 2020-2022, OOS = 2023-2024
        # Both should have non-zero Sharpes
        assert isinstance(r.wf_ratio, float)


# ── _result_to_dict ──────────────────────────────────────────────────────


class TestResultToDict:
    def test_serializable(self):
        config = ICConfig(
            ticker="XLF", sizing_pct=0.05, spread_width=1,
            target_dte=35, min_entry_offset=28,
            put_otm_pct=0.07, call_otm_pct=0.05, regime_filter="none",
        )
        result = ICResult(
            config=config, trades=[], n_trades=10,
            total_pnl=1000, win_rate=0.7, max_dd=0.05,
            sharpe=2.0, cagr=0.1, oos_sharpe=1.5,
            yearly={}, is_sharpe=2.5, wf_ratio=0.6,
        )
        d = _result_to_dict(result)
        # Should be JSON-serializable
        json.dumps(d)
        assert d["ticker"] == "XLF"
        assert d["n_trades"] == 10
        assert d["oos_sharpe"] == 1.5


# ── IronCondorOptimizer ─────────────────────────────────────────────────


class TestIronCondorOptimizer:
    def test_build_configs_not_empty(self):
        opt = IronCondorOptimizer()
        configs = opt._build_configs()
        assert len(configs) > 20  # Should have many configs

    def test_build_configs_includes_all_tickers(self):
        opt = IronCondorOptimizer()
        configs = opt._build_configs()
        tickers_present = {c.ticker for c in configs}
        for t in IC_TICKERS:
            assert t in tickers_present, f"Missing ticker {t}"

    def test_build_configs_includes_sizing_sweep(self):
        opt = IronCondorOptimizer()
        configs = opt._build_configs()
        xlf_sizings = {c.sizing_pct for c in configs if c.ticker == "XLF"}
        assert 0.05 in xlf_sizings
        assert 0.10 in xlf_sizings
        assert 0.20 in xlf_sizings

    def test_build_configs_includes_width_sweep(self):
        opt = IronCondorOptimizer()
        configs = opt._build_configs()
        xlf_widths = {c.spread_width for c in configs if c.ticker == "XLF"}
        assert 2 in xlf_widths
        assert 3 in xlf_widths
        assert 5 in xlf_widths

    def test_build_configs_includes_regime_sweep(self):
        opt = IronCondorOptimizer()
        configs = opt._build_configs()
        xlf_regimes = {c.regime_filter for c in configs if c.ticker == "XLF"}
        assert "low_vol" in xlf_regimes
        assert "high_vol" in xlf_regimes
        assert "moderate" in xlf_regimes


# ── Report generation ────────────────────────────────────────────────────


class TestReportGeneration:
    def _make_result(self):
        config = ICConfig(
            ticker="XLF", sizing_pct=0.05, spread_width=1,
            target_dte=35, min_entry_offset=28,
            put_otm_pct=0.07, call_otm_pct=0.05, regime_filter="none",
        )
        r = ICResult(
            config=config, trades=[{"entry_date": "2023-01-15", "exit_date": "2023-02-15", "pnl": 500}],
            n_trades=10, total_pnl=1000, win_rate=0.7, max_dd=0.05,
            sharpe=2.0, cagr=0.1, oos_sharpe=1.5,
            yearly={2023: ICYearResult(2023, 10, 1000, 0.7, 0.05, 2.0, 0.01)},
            is_sharpe=2.5, wf_ratio=0.6,
        )
        return OptimizationResult(
            configs_tested=1, results=[r],
            best_by_sharpe=r, best_by_cagr=r, best_by_calmar=r,
            ticker_summary={"XLF": {"configs": 1, "best_sharpe": 1.5, "best_cagr": 0.1, "trades": 10}},
            sizing_summary={"5%": {"configs": 1, "best_sharpe": 1.5, "best_pnl": 1000}},
        )

    def test_html_report_generation(self):
        opt = IronCondorOptimizer()
        result = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            opt.generate_report(result, path)
            assert path.exists()
            content = path.read_text()
            assert "<!DOCTYPE html>" in content
            assert "Iron Condor" in content
            assert "XLF" in content

    def test_json_summary_generation(self):
        opt = IronCondorOptimizer()
        result = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "summary.json"
            opt.save_summary(result, path)
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["configs_tested"] == 1
            assert data["best_by_oos_sharpe"]["ticker"] == "XLF"

    def test_html_contains_all_sections(self):
        opt = IronCondorOptimizer()
        result = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            opt.generate_report(result, path)
            content = path.read_text()
            assert "Ticker Comparison" in content
            assert "Position Sizing" in content
            assert "Walk-Forward" in content
            assert "All Configurations" in content


# ── Data integrity ───────────────────────────────────────────────────────


class TestDataIntegrity:
    def test_ic_tickers_list(self):
        assert len(IC_TICKERS) >= 5
        assert "SPY" in IC_TICKERS
        assert "XLF" in IC_TICKERS

    def test_capital_positive(self):
        assert CAPITAL > 0

    def test_all_regime_filters_have_ranges(self):
        from compass.iron_condor_optimizer import REGIME_FILTERS
        for rf in REGIME_FILTERS:
            assert rf in VIX_FILTER_RANGES
