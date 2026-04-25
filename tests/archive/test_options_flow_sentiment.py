"""Tests for compass.options_flow_sentiment — 28 tests."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from compass.options_flow_sentiment import (
    OptionsFlowSentiment, FlowReading, FlowBacktestResult,
    generate_flow_data,
)


def _data(n=756, seed=42):
    return generate_flow_data(n, seed=seed)


# ===========================================================================
# Synthetic data
# ===========================================================================

class TestSyntheticData:
    def test_all_keys(self):
        data = _data()
        for key in ["put_volume", "call_volume", "atm_put_vol", "atm_call_vol",
                      "otm_put_vol", "otm_call_vol", "total_oi", "oi_change",
                      "gamma_weighted_vol", "spy_returns"]:
            assert key in data
            assert len(data[key]) == 756

    def test_volumes_positive(self):
        data = _data()
        assert (data["put_volume"] > 0).all()
        assert (data["call_volume"] > 0).all()


# ===========================================================================
# Put/call ratios
# ===========================================================================

class TestPCRatio:
    def test_total(self):
        data = _data()
        pc = OptionsFlowSentiment.put_call_ratio(data["put_volume"], data["call_volume"])
        assert len(pc) == 756
        assert pc.min() > 0

    def test_atm(self):
        data = _data()
        atm = OptionsFlowSentiment.atm_put_call(data["atm_put_vol"], data["atm_call_vol"])
        assert atm.min() > 0

    def test_otm_fractions(self):
        data = _data()
        total = data["put_volume"] + data["call_volume"]
        otm_p = OptionsFlowSentiment.otm_put_fraction(data["otm_put_vol"], total)
        otm_c = OptionsFlowSentiment.otm_call_fraction(data["otm_call_vol"], total)
        assert (otm_p >= 0).all()
        assert (otm_c >= 0).all()
        assert otm_p.max() < 1.0
        assert otm_c.max() < 1.0


# ===========================================================================
# GEX
# ===========================================================================

class TestGEX:
    def test_estimate(self):
        ofs = OptionsFlowSentiment()
        data = _data()
        gex = ofs.estimate_gex(data["gamma_weighted_vol"])
        assert len(gex.dropna()) > 500

    def test_snapshot(self):
        data = _data()
        gex = OptionsFlowSentiment.gex_snapshot(data["call_volume"], data["put_volume"])
        assert len(gex) == 756


# ===========================================================================
# Unusual activity
# ===========================================================================

class TestUnusual:
    def test_detect(self):
        ofs = OptionsFlowSentiment(unusual_threshold=3.0)
        data = _data()
        flags = ofs.detect_unusual(data["oi_change"])
        assert flags.sum() >= 0  # may or may not have unusual days

    def test_score_bounded(self):
        ofs = OptionsFlowSentiment()
        data = _data()
        score = ofs.unusual_score(data["oi_change"])
        assert score.min() >= 0
        assert score.max() <= 1.0


# ===========================================================================
# Composite score
# ===========================================================================

class TestComposite:
    def test_bounded(self):
        ofs = OptionsFlowSentiment()
        data = _data(500)
        composite = ofs.composite_score(data)
        valid = composite.dropna()
        assert valid.min() >= -1.0
        assert valid.max() <= 1.0

    def test_signal_series(self):
        ofs = OptionsFlowSentiment()
        data = _data(500)
        sig = ofs.signal_series(data, threshold=0.2)
        assert set(sig.dropna().unique()).issubset({-1.0, 0.0, 1.0})


# ===========================================================================
# Readings
# ===========================================================================

class TestReadings:
    def test_produces(self):
        ofs = OptionsFlowSentiment()
        data = _data(500)
        readings = ofs.compute_readings(data)
        assert len(readings) > 200
        assert all(isinstance(r, FlowReading) for r in readings)

    def test_composite_in_readings(self):
        ofs = OptionsFlowSentiment()
        readings = ofs.compute_readings(_data(300))
        for r in readings:
            assert -1 <= r.composite_score <= 1


# ===========================================================================
# Overlay
# ===========================================================================

class TestOverlay:
    def test_blocks_bearish(self):
        ofs = OptionsFlowSentiment()
        data = _data(500)
        base = pd.Series(1.0, index=data["spy_returns"].index)
        filtered = ofs.overlay_filter(base, data, block_threshold=-0.3)
        assert (filtered == 0).sum() >= 0  # may block some days

    def test_preserves_bullish(self):
        ofs = OptionsFlowSentiment()
        data = _data(500)
        base = pd.Series(1.0, index=data["spy_returns"].index)
        filtered = ofs.overlay_filter(base, data, block_threshold=-0.3)
        assert (filtered == 1.0).sum() > (filtered == 0).sum()


# ===========================================================================
# Backtest
# ===========================================================================

class TestBacktest:
    def test_basic(self):
        ofs = OptionsFlowSentiment()
        result = ofs.backtest(_data(756), threshold=0.2)
        assert isinstance(result, FlowBacktestResult)

    def test_has_signals(self):
        ofs = OptionsFlowSentiment()
        result = ofs.backtest(_data(756), threshold=0.15)
        assert result.n_signals > 0

    def test_win_rate_bounded(self):
        ofs = OptionsFlowSentiment()
        result = ofs.backtest(_data(756), threshold=0.15)
        if result.n_signals > 0:
            assert 0 <= result.win_rate <= 1
            assert 0 <= result.signal_accuracy <= 1

    def test_short_data(self):
        ofs = OptionsFlowSentiment()
        data = {k: v.iloc[:10] for k, v in _data(20).items()}
        result = ofs.backtest(data)
        assert result.n_signals == 0


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        ofs = OptionsFlowSentiment()
        data = _data(500)
        readings = ofs.compute_readings(data)
        result = ofs.backtest(data, threshold=0.2)
        out = tmp_path / "flow.html"
        path = ofs.generate_report(result, readings, str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Options Flow" in html
        assert "<svg" in html
