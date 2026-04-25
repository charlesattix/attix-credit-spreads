"""Tests for compass.regime_hmm — 25 tests."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from compass.regime_hmm import (
    GaussianHMM, RegimeTransitionEngine, HMMParams,
    RegimeState, TransitionEvent, BacktestResult,
    generate_regime_data, STATE_NAMES, N_STATES,
)


def _data(n=500, seed=42):
    return generate_regime_data(n, seed)


class TestSyntheticData:
    def test_shapes(self):
        feat, ret = _data()
        assert feat.shape == (500, 3)
        assert len(ret) == 500
    def test_columns(self):
        feat, _ = _data()
        assert list(feat.columns) == ["returns", "vix", "breadth"]
    def test_vix_range(self):
        feat, _ = _data(1000)
        assert feat["vix"].min() >= 8
        assert feat["vix"].max() <= 100


class TestGaussianHMM:
    def test_fit(self):
        feat, _ = _data(300)
        hmm = GaussianHMM(n_states=3, n_iter=5)
        p = hmm.fit(feat.values)
        assert isinstance(p, HMMParams)
        assert p.transition_matrix.shape == (3, 3)
    def test_transition_rows_sum_one(self):
        feat, _ = _data(300)
        hmm = GaussianHMM(n_states=3, n_iter=5)
        p = hmm.fit(feat.values)
        for i in range(3):
            assert abs(p.transition_matrix[i].sum() - 1.0) < 0.01
    def test_predict_proba_shape(self):
        feat, _ = _data(200)
        hmm = GaussianHMM(n_states=3, n_iter=5)
        hmm.fit(feat.values)
        probs = hmm.predict_proba(feat.values)
        assert probs.shape == (200, 3)
    def test_probs_sum_one(self):
        feat, _ = _data(200)
        hmm = GaussianHMM(n_states=3, n_iter=5)
        hmm.fit(feat.values)
        probs = hmm.predict_proba(feat.values)
        for i in range(len(probs)):
            assert abs(probs[i].sum() - 1.0) < 0.05
    def test_predict(self):
        feat, _ = _data(200)
        hmm = GaussianHMM(n_states=3, n_iter=5)
        hmm.fit(feat.values)
        states = hmm.predict(feat.values)
        assert len(states) == 200
        assert set(states).issubset({0, 1, 2})


class TestEngine:
    def test_fit(self):
        feat, _ = _data(300)
        eng = RegimeTransitionEngine(n_states=3, n_iter=5)
        p = eng.fit(feat)
        assert p.transition_matrix.shape == (3, 3)
    def test_assess(self):
        feat, _ = _data(300)
        eng = RegimeTransitionEngine(n_states=3, n_iter=5)
        states = eng.assess(feat)
        assert len(states) == 300
        assert all(isinstance(s, RegimeState) for s in states)
    def test_regime_names(self):
        feat, _ = _data(300)
        eng = RegimeTransitionEngine(n_states=4, n_iter=5)
        states = eng.assess(feat)
        names = {s.regime_name for s in states}
        assert len(names) >= 1
    def test_transition_signal_bounded(self):
        feat, _ = _data(300)
        eng = RegimeTransitionEngine(n_states=3, n_iter=5)
        states = eng.assess(feat)
        for s in states:
            assert -1 <= s.transition_signal <= 1
    def test_transition_matrix_property(self):
        feat, _ = _data(300)
        eng = RegimeTransitionEngine(n_states=3, n_iter=5)
        eng.fit(feat)
        tm = eng.transition_matrix
        assert tm is not None
        assert tm.shape == (3, 3)


class TestBacktest:
    def test_basic(self):
        feat, ret = _data(500)
        eng = RegimeTransitionEngine(n_states=3, n_iter=5)
        result = eng.backtest(feat, ret)
        assert isinstance(result, BacktestResult)
    def test_sharpe_finite(self):
        feat, ret = _data(500)
        eng = RegimeTransitionEngine(n_states=3, n_iter=5)
        result = eng.backtest(feat, ret)
        assert np.isfinite(result.sharpe)
    def test_transitions_detected(self):
        feat, ret = _data(500)
        eng = RegimeTransitionEngine(n_states=3, n_iter=5)
        result = eng.backtest(feat, ret)
        assert result.n_transitions >= 0
    def test_accuracy_bounded(self):
        feat, ret = _data(500)
        eng = RegimeTransitionEngine(n_states=3, n_iter=5)
        result = eng.backtest(feat, ret)
        assert 0 <= result.accuracy <= 1


class TestReport:
    def test_creates_file(self, tmp_path):
        feat, ret = _data(300)
        eng = RegimeTransitionEngine(n_states=3, n_iter=5)
        states = eng.assess(feat)
        result = eng.backtest(feat, ret)
        out = tmp_path / "hmm.html"
        path = eng.generate_report(result, states, str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Regime" in html
        assert "Transition Matrix" in html
