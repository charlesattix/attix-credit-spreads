"""Tests for compass/vix_ladder.py — VIX exposure ladder (EXP-2820)."""

import numpy as np
import pandas as pd
import pytest

from compass.vix_ladder import EXP2820_DEFAULT_LADDER, VIXLadder


class TestVIXLadderInit:
    def test_default_ladder(self):
        ladder = VIXLadder()
        assert len(ladder.breakpoints) == len(EXP2820_DEFAULT_LADDER)
        assert ladder.causal is True

    def test_custom_ladder(self):
        bps = [(15, 1.0), (25, 0.5), (35, 0.0)]
        ladder = VIXLadder(breakpoints=bps)
        assert len(ladder.breakpoints) == 3

    def test_non_ascending_vix_raises(self):
        with pytest.raises(ValueError, match="ascending"):
            VIXLadder(breakpoints=[(30, 1.0), (20, 0.5)])

    def test_invalid_exposure_bounds_raises(self):
        with pytest.raises(ValueError, match="invalid exposure"):
            VIXLadder(min_exposure=0.5, max_exposure=0.3)

    def test_non_causal(self):
        ladder = VIXLadder(causal=False)
        assert ladder.causal is False


class TestExposureAt:
    def test_below_first_breakpoint(self):
        ladder = VIXLadder()
        assert ladder.exposure_at(10.0) == 1.0

    def test_at_first_breakpoint(self):
        ladder = VIXLadder()
        assert ladder.exposure_at(20.0) == 1.0

    def test_at_known_breakpoint(self):
        ladder = VIXLadder()
        assert ladder.exposure_at(30.0) == 0.75
        assert ladder.exposure_at(40.0) == 0.50

    def test_interpolation_between_breakpoints(self):
        ladder = VIXLadder()
        # Between VIX 20 (1.0) and 25 (0.90) → midpoint at 22.5 = 0.95
        e = ladder.exposure_at(22.5)
        assert abs(e - 0.95) < 0.01

    def test_above_last_breakpoint(self):
        ladder = VIXLadder()
        # Default ladder's last breakpoint is (1e9, 0.00); VIX=100 interpolates
        # between (70, 0.15) and (1e9, 0.00) — effectively ~0.15
        e = ladder.exposure_at(100.0)
        assert e < 0.20  # close to 0.15, well below full exposure

    def test_nan_returns_max_exposure(self):
        ladder = VIXLadder()
        assert ladder.exposure_at(float("nan")) == 1.0

    def test_none_returns_max_exposure(self):
        ladder = VIXLadder()
        assert ladder.exposure_at(None) == 1.0

    def test_custom_ladder_interpolation(self):
        ladder = VIXLadder(breakpoints=[(10, 1.0), (30, 0.0)])
        assert ladder.exposure_at(20.0) == 0.5
        assert ladder.exposure_at(10.0) == 1.0
        assert ladder.exposure_at(30.0) == 0.0


class TestApply:
    def test_causal_shift(self):
        ladder = VIXLadder(causal=True)
        vix = pd.Series([15.0, 22.0, 35.0, 50.0, 15.0])
        result = ladder.apply(vix)
        # First element should be max_exposure (no prior VIX)
        assert result.iloc[0] == 1.0
        # Second element uses first VIX (15.0 → 1.0)
        assert result.iloc[1] == 1.0
        # Last element uses prior VIX (50.0 → 0.35)
        assert result.iloc[-1] == 0.35

    def test_non_causal_no_shift(self):
        ladder = VIXLadder(causal=False)
        vix = pd.Series([20.0, 40.0])
        result = ladder.apply(vix)
        assert result.iloc[0] == 1.0
        assert result.iloc[1] == 0.50

    def test_returns_series_for_series_input(self):
        ladder = VIXLadder(causal=False)
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        vix = pd.Series([18, 22, 28, 35, 45], index=idx)
        result = ladder.apply(vix)
        assert isinstance(result, pd.Series)
        assert list(result.index) == list(idx)

    def test_returns_ndarray_for_array_input(self):
        ladder = VIXLadder(causal=False)
        result = ladder.apply(np.array([18.0, 30.0, 50.0]))
        assert isinstance(result, np.ndarray)
        assert len(result) == 3

    def test_nan_handling(self):
        ladder = VIXLadder(causal=False)
        vix = pd.Series([20.0, float("nan"), 40.0])
        result = ladder.apply(vix)
        assert result.iloc[1] == 1.0  # NaN → max_exposure

    def test_all_values_in_bounds(self):
        ladder = VIXLadder(causal=False)
        rng = np.random.RandomState(42)
        vix = pd.Series(rng.uniform(10, 80, 500))
        result = ladder.apply(vix)
        assert result.min() >= 0.0
        assert result.max() <= 1.0


class TestDescribe:
    def test_describe_returns_dict(self):
        ladder = VIXLadder()
        d = ladder.describe()
        assert "breakpoints" in d
        assert d["causal"] is True
        assert d["source"] == "EXP-2820 flash crash protection winner"


class TestRepr:
    def test_repr_format(self):
        ladder = VIXLadder()
        r = repr(ladder)
        assert "VIXLadder" in r
        assert "causal=True" in r
