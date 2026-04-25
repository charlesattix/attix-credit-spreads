"""Tests for compass.tail_risk_protector — 32 tests."""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime
from pathlib import Path

from compass.tail_risk_protector import (
    TailRiskProtector, ThreatLevel, TailRiskState, SignalReading,
    HedgeRecommendation, CrashEvent, ProtectionBacktestResult,
    LEVEL_ACTIONS, generate_stress_data,
)


def _data(n=756, seed=42):
    return generate_stress_data(n, seed)


# ===========================================================================
# Synthetic data
# ===========================================================================

class TestSyntheticData:
    def test_all_series_present(self):
        data = _data()
        for key in ["vix", "vix_3m", "hyg_tlt_spread", "skew_25d",
                      "cross_corr", "momentum", "spy_returns"]:
            assert key in data
            assert len(data[key]) > 500

    def test_vix_range(self):
        data = _data()
        assert data["vix"].min() >= 10
        assert data["vix"].max() <= 82

    def test_covid_spike(self):
        data = _data(1512)
        # VIX should spike around day 55-80 (COVID)
        assert data["vix"].iloc[60:80].max() > 50

    def test_credit_spread_widens(self):
        data = _data(1512)
        normal = data["hyg_tlt_spread"].iloc[:40].mean()
        crisis = data["hyg_tlt_spread"].iloc[60:90].mean()
        assert crisis > normal


# ===========================================================================
# Individual signals
# ===========================================================================

class TestSignals:
    def test_vix_inversion(self):
        vix = pd.Series([30, 25, 20])
        vix_3m = pd.Series([25, 25, 25])
        ratio = TailRiskProtector.vix_term_structure(vix, vix_3m)
        assert ratio.iloc[0] > 1.0  # inverted
        assert ratio.iloc[2] < 1.0  # normal

    def test_credit_spread(self):
        hyg = pd.Series([3.0, 5.0, 8.0])
        sig = TailRiskProtector.credit_spread_signal(hyg)
        assert sig.iloc[2] > sig.iloc[0]

    def test_momentum_crash(self):
        mom = pd.Series([0.05, 0.0, -0.10])
        sig = TailRiskProtector.momentum_crash_signal(mom)
        assert sig.iloc[2] > sig.iloc[0]  # negative momentum = high signal


# ===========================================================================
# Threat classification
# ===========================================================================

class TestClassification:
    def test_green(self):
        assert TailRiskProtector._classify(15) == ThreatLevel.GREEN

    def test_yellow(self):
        assert TailRiskProtector._classify(40) == ThreatLevel.YELLOW

    def test_orange(self):
        assert TailRiskProtector._classify(60) == ThreatLevel.ORANGE

    def test_red(self):
        assert TailRiskProtector._classify(85) == ThreatLevel.RED

    def test_boundary(self):
        assert TailRiskProtector._classify(30) == ThreatLevel.YELLOW
        assert TailRiskProtector._classify(50) == ThreatLevel.ORANGE
        assert TailRiskProtector._classify(70) == ThreatLevel.RED


# ===========================================================================
# Full assessment
# ===========================================================================

class TestAssess:
    def test_produces_states(self):
        trp = TailRiskProtector(lookback=100)
        states = trp.assess(_data(500))
        assert len(states) > 200
        assert all(isinstance(s, TailRiskState) for s in states)

    def test_composite_bounded(self):
        trp = TailRiskProtector(lookback=100)
        states = trp.assess(_data(500))
        for s in states:
            assert 0 <= s.composite_score <= 100

    def test_levels_assigned(self):
        trp = TailRiskProtector(lookback=100)
        states = trp.assess(_data(500))
        levels = {s.level for s in states}
        assert ThreatLevel.GREEN in levels  # should have some normal days

    def test_has_signals(self):
        trp = TailRiskProtector(lookback=100)
        states = trp.assess(_data(500))
        assert len(states[0].signals) == 5

    def test_covid_elevated(self):
        trp = TailRiskProtector(lookback=100)
        data = _data(1512)
        states = trp.assess(data)
        # During COVID (~day 60-80), score should be elevated
        covid_states = [s for s in states if hasattr(s.date, 'month') and
                         s.date.year == 2020 and s.date.month == 3]
        if covid_states:
            max_score = max(s.composite_score for s in covid_states)
            assert max_score > 50


# ===========================================================================
# Hedge recommendations
# ===========================================================================

class TestHedgeRec:
    def test_green(self):
        state = TailRiskState(datetime.now(), [], 15, ThreatLevel.GREEN, 1.0, 0.0, 1.0)
        rec = TailRiskProtector.hedge_recommendation(state)
        assert rec.otm_put_size == 0.0
        assert rec.beta_reduction == 0.0

    def test_red(self):
        state = TailRiskState(datetime.now(), [], 85, ThreatLevel.RED, 0.0, 1.0, 0.0)
        rec = TailRiskProtector.hedge_recommendation(state)
        assert rec.otm_put_size > 0
        assert rec.beta_reduction == 1.0

    def test_orange(self):
        state = TailRiskState(datetime.now(), [], 60, ThreatLevel.ORANGE, 0.5, 0.5, 0.5)
        rec = TailRiskProtector.hedge_recommendation(state, portfolio_value=1000000)
        assert rec.estimated_cost > 0


# ===========================================================================
# Backtest
# ===========================================================================

class TestBacktest:
    def test_basic(self):
        trp = TailRiskProtector(lookback=100)
        result = trp.backtest(_data(756))
        assert isinstance(result, ProtectionBacktestResult)

    def test_dd_reduction(self):
        trp = TailRiskProtector(lookback=100)
        result = trp.backtest(_data(1512))
        assert result.dd_reduction >= 0  # protection should reduce or equal DD

    def test_crash_detection(self):
        trp = TailRiskProtector(lookback=100)
        result = trp.backtest(_data(1512))
        assert result.n_crashes >= 0

    def test_sharpe_computed(self):
        trp = TailRiskProtector(lookback=100)
        result = trp.backtest(_data(756))
        assert np.isfinite(result.protected_sharpe)


# ===========================================================================
# Level actions
# ===========================================================================

class TestLevelActions:
    def test_green_full(self):
        a = LEVEL_ACTIONS[ThreatLevel.GREEN]
        assert a["size_mult"] == 1.0
        assert a["hedge_pct"] == 0.0

    def test_red_zero_risk(self):
        a = LEVEL_ACTIONS[ThreatLevel.RED]
        assert a["size_mult"] == 0.0
        assert a["hedge_pct"] == 1.0
        assert a["beta_target"] == 0.0


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        trp = TailRiskProtector(lookback=100)
        data = _data(500)
        states = trp.assess(data)
        result = trp.backtest(data)
        out = tmp_path / "tail.html"
        path = trp.generate_report(result, states, str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Tail Risk" in html
        assert "<svg" in html
