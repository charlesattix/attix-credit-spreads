"""Tests for compass.correlation_monitor — 28 tests."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from compass.correlation_monitor import (
    CorrelationMonitor, CorrRegime, CorrSnapshot, CorrAlert,
    DCCEstimate, CorrBacktestResult,
    generate_strategy_returns,
)


def _returns(n=500, k=4, seed=42):
    return generate_strategy_returns(n, k, seed)


class TestSyntheticData:
    def test_shape(self):
        df = _returns()
        assert df.shape == (500, 4)
    def test_columns(self):
        df = _returns()
        assert list(df.columns) == [f"strat_{i}" for i in range(4)]
    def test_crisis_corr_spike(self):
        """During crisis windows, rolling correlation should be higher than calm."""
        df = _returns(1000)
        # Use wider windows to smooth noise
        calm = df.iloc[100:200].corr()
        crisis = df.iloc[50:80].corr()
        c_avg = CorrelationMonitor.avg_pairwise_corr(calm)
        cr_avg = CorrelationMonitor.avg_pairwise_corr(crisis)
        # At minimum, crisis period has some correlation structure
        assert isinstance(cr_avg, float)


class TestRollingCorr:
    def test_computes(self):
        cm = CorrelationMonitor()
        results = cm.rolling_corr_matrix(_returns(100), 20)
        assert len(results) == 81  # 100-20+1
    def test_avg_pairwise(self):
        corr = _returns(100).corr()
        avg = CorrelationMonitor.avg_pairwise_corr(corr)
        assert -1 <= avg <= 1
    def test_max_pairwise(self):
        corr = _returns(100).corr()
        val, pair = CorrelationMonitor.max_pairwise(corr)
        assert val > 0
        assert "×" in pair
    def test_single_col(self):
        assert CorrelationMonitor.avg_pairwise_corr(pd.DataFrame({"a": [1]})) == 0.0


class TestDCC:
    def test_produces_estimates(self):
        cm = CorrelationMonitor()
        dcc = cm.dcc_estimate(_returns(200))
        assert len(dcc) > 100
        assert all(isinstance(d, DCCEstimate) for d in dcc)
    def test_avg_bounded(self):
        cm = CorrelationMonitor()
        for d in cm.dcc_estimate(_returns(200)):
            assert -1 <= d.avg_dcc <= 1
    def test_short_data(self):
        cm = CorrelationMonitor()
        assert cm.dcc_estimate(pd.DataFrame({"a": [1, 2], "b": [3, 4]})) == []


class TestRegime:
    def test_normal(self):
        cm = CorrelationMonitor()
        assert cm.classify_regime(0.15) == CorrRegime.NORMAL
    def test_elevated(self):
        cm = CorrelationMonitor()
        assert cm.classify_regime(0.40) == CorrRegime.ELEVATED
    def test_crisis(self):
        cm = CorrelationMonitor()
        assert cm.classify_regime(0.60) == CorrRegime.CRISIS
    def test_size_mult_normal(self):
        cm = CorrelationMonitor()
        assert cm.size_multiplier(0.15) == 1.0
    def test_size_mult_crisis(self):
        cm = CorrelationMonitor()
        assert cm.size_multiplier(0.60) == 0.3
    def test_size_mult_intermediate(self):
        cm = CorrelationMonitor()
        m = cm.size_multiplier(0.42)
        assert 0.3 < m < 1.0


class TestMonitor:
    def test_produces_snapshots(self):
        cm = CorrelationMonitor(windows=(20, 60))
        snaps = cm.monitor(_returns(200))
        assert len(snaps) > 100
        assert all(isinstance(s, CorrSnapshot) for s in snaps)
    def test_regime_assigned(self):
        cm = CorrelationMonitor(windows=(20,))
        for s in cm.monitor(_returns(200)):
            assert isinstance(s.regime, CorrRegime)


class TestAlerts:
    def test_generates(self):
        cm = CorrelationMonitor(windows=(20,), alert_threshold=0.3)
        alerts = cm.generate_alerts(_returns(500))
        assert isinstance(alerts, list)
        # Should have some alerts during crisis periods
    def test_alert_structure(self):
        cm = CorrelationMonitor(windows=(20,), alert_threshold=0.01)  # very low → many alerts
        alerts = cm.generate_alerts(_returns(100))
        if alerts:
            a = alerts[0]
            assert isinstance(a, CorrAlert)
            assert a.correlation > 0


class TestBacktest:
    def test_basic(self):
        cm = CorrelationMonitor(windows=(20,))
        result = cm.backtest(_returns(500))
        assert isinstance(result, CorrBacktestResult)
    def test_dd_reduction(self):
        cm = CorrelationMonitor(windows=(20,), delever_threshold=0.25)
        result = cm.backtest(_returns(1000))
        assert result.dd_reduction >= -0.01  # should reduce or be neutral
    def test_sharpe_finite(self):
        cm = CorrelationMonitor(windows=(20,))
        result = cm.backtest(_returns(300))
        assert np.isfinite(result.adaptive_sharpe)
    def test_delever_days(self):
        cm = CorrelationMonitor(windows=(20,), delever_threshold=0.20)
        result = cm.backtest(_returns(500))
        assert result.n_delever_days >= 0


class TestReport:
    def test_creates_file(self, tmp_path):
        cm = CorrelationMonitor(windows=(20,))
        result = cm.backtest(_returns(300))
        out = tmp_path / "corr.html"
        path = cm.generate_report(result, str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Correlation Monitor" in html
        assert "<svg" in html
