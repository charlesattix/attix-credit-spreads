"""Tests for compass.correlation_alpha — cross-asset correlation trading."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compass.correlation_alpha import (
    CorrelationAlphaBacktest,
    CorrelationAlphaResult,
    CorrelationTracker,
    PairCorrelation,
    PairSignal,
    PairTrade,
    SignalGenerator,
    generate_pair_data,
)


def _data(n: int = 500, seed: int = 42) -> pd.DataFrame:
    return generate_pair_data(n, seed)


# ── CorrelationTracker ──────────────────────────────────────────────────────
class TestCorrelationTracker:
    def test_compute_returns_list(self):
        df = _data(200)
        t = CorrelationTracker(lookback=40)
        pcs = t.compute(df, 100)
        assert isinstance(pcs, list)
        assert len(pcs) > 0

    def test_correlation_bounded(self):
        df = _data(300)
        t = CorrelationTracker(lookback=40)
        for pc in t.compute(df, 150):
            assert -1.0 <= pc.current_corr <= 1.0

    def test_zscore_computed(self):
        df = _data(300)
        t = CorrelationTracker(lookback=40)
        pcs = t.compute(df, 200)
        for pc in pcs:
            assert isinstance(pc.zscore, float)

    def test_regime_valid(self):
        df = _data(300)
        t = CorrelationTracker(lookback=40)
        for pc in t.compute(df, 200):
            assert pc.regime in ("high", "normal", "breakdown", "divergence")

    def test_percentile_bounded(self):
        df = _data(300)
        t = CorrelationTracker(lookback=40)
        for pc in t.compute(df, 200):
            assert 0 <= pc.percentile <= 100

    def test_spy_qqq_positive_corr(self):
        df = _data(300)
        t = CorrelationTracker(pairs=[("SPY", "QQQ")], lookback=40)
        pcs = t.compute(df, 150)
        if pcs:
            assert pcs[0].current_corr > 0  # should be highly correlated

    def test_spy_tlt_lower_corr(self):
        df = _data(300)
        t = CorrelationTracker(pairs=[("SPY", "QQQ"), ("SPY", "TLT")], lookback=40)
        pcs = t.compute(df, 150)
        if len(pcs) == 2:
            spy_qqq = next(p for p in pcs if p.asset_b == "QQQ")
            spy_tlt = next(p for p in pcs if p.asset_b == "TLT")
            assert spy_qqq.current_corr > spy_tlt.current_corr

    def test_too_early_returns_none(self):
        df = _data(100)
        t = CorrelationTracker(lookback=60)
        pcs = t.compute(df, 30)
        assert len(pcs) == 0

    def test_missing_column_skipped(self):
        df = _data(200)[["SPY", "QQQ"]]
        t = CorrelationTracker(pairs=[("SPY", "QQQ"), ("SPY", "MISSING")], lookback=40)
        pcs = t.compute(df, 100)
        assert len(pcs) == 1


# ── SignalGenerator ─────────────────────────────────────────────────────────
class TestSignalGenerator:
    def _pc(self, zscore: float = 0.0, regime: str = "normal") -> PairCorrelation:
        return PairCorrelation("SPY", "QQQ", 0.5, 0.8, 0.1, zscore, regime, 50.0)

    def test_no_signal_normal(self):
        sg = SignalGenerator()
        sig = sg.generate(self._pc(zscore=-0.5), "2024-01-01")
        assert sig.action == "none"

    def test_enter_on_breakdown(self):
        sg = SignalGenerator(entry_zscore=-2.0)
        sig = sg.generate(self._pc(zscore=-2.5, regime="breakdown"), "2024-01-01")
        assert sig.action == "enter_long_spread"
        assert sig.confidence > 0

    def test_exit_on_recovery(self):
        sg = SignalGenerator(exit_zscore=-0.5)
        sig = sg.generate(self._pc(zscore=0.0), "2024-01-01", in_trade=True)
        assert sig.action == "exit"

    def test_hold_during_trade(self):
        sg = SignalGenerator(exit_zscore=-0.5)
        sig = sg.generate(self._pc(zscore=-1.5), "2024-01-01", in_trade=True)
        assert sig.action == "none"

    def test_direction_overweights_laggard(self):
        sg = SignalGenerator(entry_zscore=-2.0)
        sig = sg.generate(self._pc(zscore=-2.5, regime="breakdown"), "d", False, 0.05, -0.03)
        assert sig.direction == "b"  # b lagged, overweight b

    def test_confidence_scales_with_zscore(self):
        sg = SignalGenerator(entry_zscore=-2.0)
        mild = sg.generate(self._pc(zscore=-2.1, regime="breakdown"), "d")
        strong = sg.generate(self._pc(zscore=-3.5, regime="breakdown"), "d")
        assert strong.confidence >= mild.confidence


# ── Backtest ────────────────────────────────────────────────────────────────
class TestBacktest:
    def test_returns_result(self):
        r = CorrelationAlphaBacktest(lookback=40).run(_data(300))
        assert isinstance(r, CorrelationAlphaResult)

    def test_pair_histories_populated(self):
        r = CorrelationAlphaBacktest(lookback=40).run(_data(300))
        assert len(r.pair_histories) > 0

    def test_signals_generated(self):
        r = CorrelationAlphaBacktest(lookback=40).run(_data(500))
        assert len(r.signals) > 0

    def test_win_rate_bounded(self):
        r = CorrelationAlphaBacktest(lookback=40, entry_zscore=-1.5).run(_data(500))
        assert 0 <= r.win_rate_pct <= 100

    def test_max_dd_nonneg(self):
        r = CorrelationAlphaBacktest(lookback=40).run(_data(500))
        assert r.max_dd_pct >= 0

    def test_ending_capital_positive(self):
        r = CorrelationAlphaBacktest(lookback=40).run(_data(300))
        assert r.ending_capital > 0

    def test_generated_at(self):
        r = CorrelationAlphaBacktest(lookback=40).run(_data(200))
        assert len(r.generated_at) > 0

    def test_too_short(self):
        r = CorrelationAlphaBacktest(lookback=60).run(_data(50))
        assert r.total_trades == 0

    def test_trade_has_fields(self):
        r = CorrelationAlphaBacktest(lookback=40, entry_zscore=-1.5).run(_data(800))
        if r.trades:
            t = r.trades[0]
            assert t.hold_days > 0
            assert t.exit_reason in ("convergence", "max_hold", "end_of_data")
            assert t.direction != ""

    def test_lower_threshold_more_trades(self):
        df = _data(500)
        strict = CorrelationAlphaBacktest(lookback=40, entry_zscore=-3.0).run(df)
        loose = CorrelationAlphaBacktest(lookback=40, entry_zscore=-1.0).run(df)
        assert loose.total_trades >= strict.total_trades

    def test_max_hold_enforced(self):
        r = CorrelationAlphaBacktest(lookback=40, max_hold=10, entry_zscore=-1.0).run(_data(500))
        for t in r.trades:
            assert t.hold_days <= 11  # +1 tolerance for boundary

    def test_multiple_pairs_traded(self):
        r = CorrelationAlphaBacktest(lookback=40, entry_zscore=-1.0).run(_data(800))
        pairs = {(t.asset_a, t.asset_b) for t in r.trades}
        # Should trade at least one pair
        assert len(pairs) >= 1


# ── Synthetic data ──────────────────────────────────────────────────────────
class TestSyntheticData:
    def test_shape(self):
        df = generate_pair_data(100)
        assert df.shape == (100, 4)
        assert set(df.columns) == {"SPY", "QQQ", "IWM", "TLT"}

    def test_deterministic(self):
        a = generate_pair_data(50, seed=99)
        b = generate_pair_data(50, seed=99)
        pd.testing.assert_frame_equal(a, b)

    def test_has_breakdown_periods(self):
        df = generate_pair_data(600)
        # Around index 200, QQQ decouples
        corr_before = np.corrcoef(df["SPY"].iloc[150:190], df["QQQ"].iloc[150:190])[0, 1]
        corr_during = np.corrcoef(df["SPY"].iloc[200:220], df["QQQ"].iloc[200:220])[0, 1]
        assert corr_during < corr_before


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_pair_correlation(self):
        pc = PairCorrelation("SPY", "QQQ", 0.85, 0.90, 0.05, -1.0, "normal", 30.0)
        assert pc.current_corr == 0.85

    def test_pair_signal(self):
        ps = PairSignal("d", "SPY", "QQQ", "enter_long_spread", -2.5, 0.3, "b", 0.8)
        assert ps.action == "enter_long_spread"

    def test_pair_trade(self):
        pt = PairTrade("d1", "d2", "SPY", "QQQ", -2.5, -0.3, 0.3, 0.7, 15, 500, "long_b_short_a", "convergence")
        assert pt.pnl == 500

    def test_result_defaults(self):
        r = CorrelationAlphaResult()
        assert r.trades == []
        assert r.total_trades == 0
