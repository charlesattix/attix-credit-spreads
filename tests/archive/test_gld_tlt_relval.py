"""Tests for compass.gld_tlt_relval (EXP-1630)."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.gld_tlt_relval import (
    CAPITAL,
    LOOKBACK,
    MAX_CONTRACTS,
    MIN_SPACING,
    OOS_START,
    OTM_PCT,
    RISK_PER_TRADE,
    SPREAD_WIDTH_GLD,
    SPREAD_WIDTH_TLT,
    Z_ENTRY,
    Z_EXIT,
    EXP1630Result,
    GldTltRelVal,
    YearStats,
    _sharpe,
)


# ── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    def test_z_entry_threshold(self):
        assert Z_ENTRY == 1.5

    def test_z_exit_threshold(self):
        assert Z_EXIT == 0.5

    def test_lookback_period(self):
        assert LOOKBACK == 20

    def test_capital(self):
        assert CAPITAL == 100_000

    def test_min_spacing(self):
        assert MIN_SPACING == 14

    def test_spread_widths(self):
        assert SPREAD_WIDTH_GLD == 2.0
        assert SPREAD_WIDTH_TLT == 2.0

    def test_otm_pct(self):
        assert OTM_PCT == 0.95

    def test_risk_per_trade(self):
        assert RISK_PER_TRADE == 0.02

    def test_max_contracts(self):
        assert MAX_CONTRACTS == 10

    def test_oos_start(self):
        assert OOS_START == 2022


# ── Sharpe helper ────────────────────────────────────────────────────────


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

    def test_constant_returns_zero(self):
        """Zero std → zero sharpe."""
        assert _sharpe([50, 50, 50]) == 0


# ── YearStats ────────────────────────────────────────────────────────────


class TestYearStats:
    def test_creation(self):
        ys = YearStats(2023, 10, 500.0, 0.8, 0.02, 2.5, 0.005)
        assert ys.year == 2023
        assert ys.n_trades == 10
        assert ys.total_pnl == 500.0
        assert ys.win_rate == 0.8


# ── EXP1630Result ────────────────────────────────────────────────────────


class TestEXP1630Result:
    def _make_result(self, n_trades=20, pnl=2000, oos_sharpe=2.0):
        return EXP1630Result(
            trades=[{"entry_date": "2023-01-15", "exit_date": "2023-02-15", "pnl": 100}] * n_trades,
            n_trades=n_trades, total_pnl=pnl, win_rate=0.85, max_dd=0.02,
            sharpe=1.5, cagr=0.02, spy_corr=0.03, exp1220_corr=0.0,
            yearly={2023: YearStats(2023, n_trades, pnl, 0.85, 0.02, 1.5, 0.02)},
            is_sharpe=0.5, oos_sharpe=oos_sharpe, wf_ratio=4.0,
            n_long_signals=5, n_short_signals=15,
            avg_hold_days=10.0,
            data_end_gld="2024-03-15", data_end_tlt="2024-07-19",
        )

    def test_basic_fields(self):
        r = self._make_result()
        assert r.n_trades == 20
        assert r.total_pnl == 2000
        assert r.spy_corr == 0.03

    def test_walk_forward_fields(self):
        r = self._make_result()
        assert r.is_sharpe == 0.5
        assert r.oos_sharpe == 2.0
        assert r.wf_ratio == 4.0

    def test_signal_counts(self):
        r = self._make_result()
        assert r.n_long_signals == 5
        assert r.n_short_signals == 15

    def test_data_end_dates(self):
        r = self._make_result()
        assert "2024" in r.data_end_gld
        assert "2024" in r.data_end_tlt


# ── Z-score signal logic ────────────────────────────────────────────────


class TestZScoreSignal:
    """Test the z-score computation used in the strategy."""

    def test_z_score_computation(self):
        """Z-score of ratio should be (ratio - mean) / std."""
        np.random.seed(42)
        n = 100
        ratio = pd.Series(np.random.randn(n) * 0.5 + 10)
        mean = ratio.rolling(20).mean()
        std = ratio.rolling(20).std()
        z = (ratio - mean) / std
        # After warmup, z should be roughly N(0,1)
        z_valid = z.dropna()
        assert abs(z_valid.mean()) < 1.0
        assert z_valid.std() > 0.5

    def test_extreme_z_triggers_entry(self):
        """Z-scores beyond ±1.5 should trigger entries."""
        z_values = [0.0, 0.5, 1.0, 1.4, 1.6, 2.0, -1.6, -2.0]
        triggers = [abs(z) >= Z_ENTRY for z in z_values]
        assert triggers == [False, False, False, False, True, True, True, True]


# ── Trade direction logic ────────────────────────────────────────────────


class TestTradeDirection:
    def test_positive_z_short_ratio(self):
        """z > 1.5 → GLD rich → short ratio (sell GLD calls + TLT puts)."""
        z = 2.0
        assert z > Z_ENTRY
        direction = "short_ratio" if z > Z_ENTRY else "long_ratio"
        assert direction == "short_ratio"

    def test_negative_z_long_ratio(self):
        """z < -1.5 → GLD cheap → long ratio (sell GLD puts + TLT calls)."""
        z = -2.0
        assert z < -Z_ENTRY
        direction = "long_ratio" if z < -Z_ENTRY else "short_ratio"
        assert direction == "long_ratio"


# ── Position sizing ──────────────────────────────────────────────────────


class TestPositionSizing:
    def test_contract_count(self):
        """2% of $100K = $2000 risk. $2 wide spread = $200 max loss. → 10 contracts."""
        risk = CAPITAL * RISK_PER_TRADE
        max_loss_per = SPREAD_WIDTH_GLD * 100  # $200
        contracts = min(MAX_CONTRACTS, int(risk / max_loss_per))
        assert contracts == 10

    def test_max_contract_cap(self):
        assert MAX_CONTRACTS == 10


# ── Report generation ────────────────────────────────────────────────────


class TestReportGeneration:
    def _make_result(self):
        return EXP1630Result(
            trades=[{"entry_date": "2023-01-15", "exit_date": "2023-02-15", "pnl": 100,
                     "direction": "short_ratio", "z_score": 1.8, "n_legs": 2,
                     "contracts": 5, "exit_reasons": "GLD:profit_target, TLT:dte_exit",
                     "hold_days": 12, "total_credit": 0.45}],
            n_trades=1, total_pnl=100, win_rate=1.0, max_dd=0.001,
            sharpe=2.0, cagr=0.01, spy_corr=0.03, exp1220_corr=0.0,
            yearly={2023: YearStats(2023, 1, 100, 1.0, 0.001, 2.0, 0.001)},
            is_sharpe=0.5, oos_sharpe=2.0, wf_ratio=4.0,
            n_long_signals=0, n_short_signals=1,
            avg_hold_days=12.0,
            data_end_gld="2024-03-15", data_end_tlt="2024-07-19",
        )

    def test_html_report(self):
        runner = GldTltRelVal()
        result = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            runner.generate_report(result, path)
            assert path.exists()
            content = path.read_text()
            assert "<!DOCTYPE html>" in content
            assert "EXP-1630" in content
            assert "GLD/TLT" in content
            assert "Hypothesis" in content
            assert "Walk-Forward" in content
            assert "Correlation" in content

    def test_json_summary(self):
        runner = GldTltRelVal()
        result = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "summary.json"
            runner.save_summary(result, path)
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["experiment"] == "EXP-1630"
            assert data["n_trades"] == 1
            assert data["spy_corr"] == 0.03
            assert "yearly" in data

    def test_html_contains_trade_log(self):
        runner = GldTltRelVal()
        result = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            runner.generate_report(result, path)
            content = path.read_text()
            assert "Trade Log" in content
            assert "short_ratio" in content
