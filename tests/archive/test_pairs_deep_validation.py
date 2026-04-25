"""Tests for compass.pairs_deep_validation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from compass.pairs_deep_validation import (
    CAPITAL,
    CORR_THRESHOLDS,
    LOOKBACK_WINDOWS,
    PAIRS,
    SPREAD_WIDTHS,
    PairConfig,
    ValidationResult,
    WFWindow,
    _sharpe,
    _stats,
)


class TestPairConfig:
    def test_default_label(self):
        c = PairConfig("TLT", "SPY")
        assert "TLT-SPY" in c.label
        assert "lb30" in c.label

    def test_custom_label(self):
        c = PairConfig("TLT", "SPY", label="custom")
        assert c.label == "custom"

    def test_defaults(self):
        c = PairConfig("TLT", "SPY")
        assert c.lookback == 30
        assert c.corr_threshold == 0.0
        assert c.spread_width == 5.0
        assert c.otm_pct == 0.93
        assert c.min_spacing == 14


class TestSharpe:
    def test_empty(self):
        assert _sharpe([]) == 0.0

    def test_single(self):
        assert _sharpe([100]) == 0.0

    def test_positive(self):
        pnls = [50, 60, 40, 55, 45]
        s = _sharpe(pnls)
        assert s > 0

    def test_negative(self):
        pnls = [-50, -60, -40, -55, -45]
        s = _sharpe(pnls)
        assert s < 0

    def test_zero_std(self):
        pnls = [50, 50, 50]
        s = _sharpe(pnls)
        assert s == 0  # zero std → zero sharpe


class TestStats:
    def test_empty_trades(self):
        s = _stats([])
        assert s["n"] == 0
        assert s["pnl"] == 0

    def test_all_winners(self):
        trades = [{"pnl": 100}, {"pnl": 200}, {"pnl": 50}]
        s = _stats(trades)
        assert s["n"] == 3
        assert s["pnl"] == 350
        assert s["wr"] == 1.0
        assert s["sharpe"] > 0

    def test_mixed(self):
        trades = [{"pnl": 100}, {"pnl": -50}, {"pnl": 200}]
        s = _stats(trades)
        assert s["n"] == 3
        assert s["wr"] == pytest.approx(0.667, abs=0.01)
        assert s["dd"] > 0

    def test_max_dd_computed(self):
        trades = [{"pnl": 100}, {"pnl": -500}, {"pnl": 200}]
        s = _stats(trades)
        assert s["dd"] > 0


class TestDataIntegrity:
    def test_pairs_list_not_empty(self):
        assert len(PAIRS) >= 5

    def test_original_pair_included(self):
        assert ("TLT", "SPY") in PAIRS

    def test_lookback_windows(self):
        assert 30 in LOOKBACK_WINDOWS
        assert all(lb > 0 for lb in LOOKBACK_WINDOWS)

    def test_corr_thresholds(self):
        assert 0.0 in CORR_THRESHOLDS

    def test_spread_widths(self):
        assert 5.0 in SPREAD_WIDTHS

    def test_capital_positive(self):
        assert CAPITAL > 0


class TestValidationResult:
    def _make_result(self):
        return ValidationResult(
            wf_windows=[
                WFWindow(2020, 2020, 2021, 2.5, 4, 174, 1.0),
                WFWindow(2020, 2021, 2022, 0.7, 5, 330, 0.8),
                WFWindow(2020, 2022, 2023, 7.5, 8, 186, 1.0),
            ],
            wf_avg_oos_sharpe=3.57,
            wf_consistency=1.0,
            param_results=[
                {"param": "lookback", "value": 30, "full_sharpe": 2.9, "oos_sharpe": 6.3,
                 "n_trades": 32, "oos_n": 23, "pnl": 1293},
            ],
            baseline_sharpe=2.896,
            sensitivity_range=(1.5, 7.1),
            pair_results={"TLT-SPY": {"n": 32, "pnl": 1293, "sharpe": 2.9, "oos_sharpe": 6.3}},
            best_pair="TLT-SPY",
            regime_results={"bull": {"n": 21, "sharpe": 7.56}},
            bias_flags=["TINY_PNL: $1,293 on $100,000"],
            overall_verdict="CONFIRMED",
            all_trades=[{"entry_date": "2023-01-15", "exit_date": "2023-02-15", "pnl": 50}],
            config=PairConfig("TLT", "SPY"),
        )

    def test_verdict_values(self):
        r = self._make_result()
        assert r.overall_verdict in ("CONFIRMED", "CAUTION", "OVERFIT")

    def test_wf_consistency_range(self):
        r = self._make_result()
        assert 0 <= r.wf_consistency <= 1

    def test_bias_flags_list(self):
        r = self._make_result()
        assert isinstance(r.bias_flags, list)


class TestReportGeneration:
    def _make_result(self):
        return ValidationResult(
            wf_windows=[WFWindow(2020, 2022, 2023, 5.0, 8, 200, 1.0)],
            wf_avg_oos_sharpe=5.0,
            wf_consistency=1.0,
            param_results=[{"param": "lookback", "value": 30, "full_sharpe": 2.9,
                           "oos_sharpe": 6.3, "n_trades": 32, "oos_n": 23, "pnl": 1293}],
            baseline_sharpe=2.9,
            sensitivity_range=(1.5, 7.1),
            pair_results={"TLT-SPY": {"n": 32, "pnl": 1293, "sharpe": 2.9, "oos_sharpe": 6.3, "wr": 0.97, "dd": 0.003, "oos_n": 23, "oos_pnl": 789}},
            best_pair="TLT-SPY",
            regime_results={"bull": {"n": 21, "sharpe": 7.56, "pnl": 609, "wr": 1.0, "dd": 0}},
            bias_flags=[],
            overall_verdict="CONFIRMED",
            all_trades=[{"pnl": 50}],
            config=PairConfig("TLT", "SPY"),
        )

    def test_html_report(self):
        from compass.pairs_deep_validation import PairsDeepValidator
        val = PairsDeepValidator()
        r = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            val.generate_report(r, path)
            assert path.exists()
            content = path.read_text()
            assert "<!DOCTYPE html>" in content
            assert "CONFIRMED" in content
            assert "Walk-Forward" in content
            assert "Parameter Sensitivity" in content
            assert "Multi-Pair" in content
            assert "Regime" in content
            assert "Bias" in content

    def test_json_summary(self):
        from compass.pairs_deep_validation import PairsDeepValidator
        val = PairsDeepValidator()
        r = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "summary.json"
            val.save_summary(r, path)
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["verdict"] == "CONFIRMED"
            assert "walk_forward" in data
            assert "bias_flags" in data
