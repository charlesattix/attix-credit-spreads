"""Tests for compass/live_composite_stress.py.

The live formula is pinned to an *inlined* reference implementation in
this file. Do NOT import the formula from any other module — keeping
the reference local guarantees that future drift in any backtest module
cannot silently break the live signal, and the test never depends on
files that may not be tracked in git.
"""
from __future__ import annotations

import math
import os
import pickle
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from compass import live_composite_stress as lcs
from compass.live_composite_stress import (
    _CacheEntry,
    _ZSCORE_WINDOW,
    build_composite_stress,
    get_current_composite_stress,
)


# ---------------------------------------------------------------------------
# Inlined reference formula — pinned to EXP-3303 backtest semantics.
# ---------------------------------------------------------------------------

_REF_ZSCORE_WINDOW = 63


def _reference_build_composite_stress(features: pd.DataFrame) -> pd.DataFrame:
    f = features.copy()
    f["term_spread"] = f["vix3m"] - f["vix"]
    for col, invert_sign in (("term_spread", True), ("vvix", False), ("skew", False)):
        roll = f[col].rolling(_REF_ZSCORE_WINDOW, min_periods=_REF_ZSCORE_WINDOW)
        z = (f[col] - roll.mean()) / roll.std(ddof=1)
        f[f"{col}_z"] = -z if invert_sign else z
    f["composite_stress"] = (
        f["term_spread_z"] + f["vvix_z"] + f["skew_z"]
    ) / math.sqrt(3.0)
    return f


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _synthetic_features(n: int = 200, seed: int = 0) -> pd.DataFrame:
    """Build a deterministic DataFrame of fake VIX/VIX3M/VVIX/SKEW closes."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    vix = 15 + np.abs(rng.normal(0, 3, n)).cumsum() / 10
    vix3m = vix + rng.normal(2, 0.5, n)  # usually above VIX
    vvix = 100 + rng.normal(0, 10, n).cumsum() / 5
    skew = 130 + rng.normal(0, 5, n)
    return pd.DataFrame(
        {"vix": vix, "vix3m": vix3m, "vvix": vvix, "skew": skew}, index=dates
    )


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """Redirect the disk cache to a temp dir for the duration of the test."""
    cache_file = tmp_path / "live_composite_stress.pkl"
    monkeypatch.setattr(lcs, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(lcs, "_CACHE_FILE", cache_file)
    yield cache_file


@pytest.fixture(autouse=True)
def reset_override():
    """Always clear the test data-cache override between tests."""
    lcs._set_cache_for_test(None)
    yield
    lcs._set_cache_for_test(None)


# ---------------------------------------------------------------------------
# Formula tests — pin live to inlined reference.
# ---------------------------------------------------------------------------


class TestFormulaPinning:
    def test_formula_matches_inlined_reference(self):
        features = _synthetic_features(n=200, seed=42)
        live = build_composite_stress(features)
        ref = _reference_build_composite_stress(features)
        pd.testing.assert_series_equal(
            live["composite_stress"], ref["composite_stress"], check_names=False,
        )

    def test_zscore_window_matches_reference(self):
        assert _ZSCORE_WINDOW == _REF_ZSCORE_WINDOW

    def test_term_spread_z_is_sign_inverted(self):
        """When term spread *widens*, term_spread_z should *decrease*."""
        features = _synthetic_features(n=200, seed=1)
        # Force widening term spread on the last day.
        features.iloc[-1, features.columns.get_loc("vix3m")] += 20
        out = build_composite_stress(features)
        # Widening = more negative z (lower stress contribution).
        assert out["term_spread_z"].iloc[-1] < out["term_spread_z"].iloc[-2]

    def test_warmup_returns_nan(self):
        features = _synthetic_features(n=_ZSCORE_WINDOW - 1, seed=7)
        out = build_composite_stress(features)
        assert out["composite_stress"].isna().all()

    def test_composite_is_finite_after_warmup(self):
        features = _synthetic_features(n=_ZSCORE_WINDOW + 30, seed=13)
        out = build_composite_stress(features)
        warm = out["composite_stress"].dropna()
        assert len(warm) > 0
        assert np.isfinite(warm).all()


# ---------------------------------------------------------------------------
# Live-pipeline tests — exercise get_current_composite_stress with a
# mocked DataCache. No network.
# ---------------------------------------------------------------------------


def _mock_cache_for(features: pd.DataFrame) -> MagicMock:
    """Return a MagicMock DataCache whose get_history serves Close from
    the appropriate column of *features* keyed by ticker."""
    col_for_ticker = {
        "^VIX": "vix",
        "^VIX3M": "vix3m",
        "^VVIX": "vvix",
        "^SKEW": "skew",
    }

    def get_history(ticker, period="1y"):
        col = col_for_ticker.get(ticker)
        if col is None:
            return pd.DataFrame()
        return pd.DataFrame({"Close": features[col].values}, index=features.index)

    cache = MagicMock()
    cache.get_history.side_effect = get_history
    return cache


class TestLivePipeline:
    def test_returns_float_when_data_available(self, isolated_cache):
        features = _synthetic_features(n=200, seed=99)
        lcs._set_cache_for_test(_mock_cache_for(features))
        value = get_current_composite_stress()
        assert value is not None
        assert isinstance(value, float)
        assert np.isfinite(value)

    def test_live_value_matches_reference(self, isolated_cache):
        features = _synthetic_features(n=200, seed=77)
        lcs._set_cache_for_test(_mock_cache_for(features))
        live_value = get_current_composite_stress()
        ref = _reference_build_composite_stress(features)
        expected = float(ref["composite_stress"].dropna().iloc[-1])
        assert live_value == pytest.approx(expected, rel=1e-9, abs=1e-12)

    def test_returns_none_when_any_input_missing(self, isolated_cache):
        # Mock cache that returns empty for ^SKEW.
        features = _synthetic_features(n=200, seed=11)
        cache = MagicMock()

        def get_history(ticker, period="1y"):
            if ticker == "^SKEW":
                return pd.DataFrame()
            col = {"^VIX": "vix", "^VIX3M": "vix3m", "^VVIX": "vvix"}[ticker]
            return pd.DataFrame({"Close": features[col].values}, index=features.index)

        cache.get_history.side_effect = get_history
        lcs._set_cache_for_test(cache)
        assert get_current_composite_stress() is None

    def test_returns_none_during_warmup(self, isolated_cache):
        features = _synthetic_features(n=_ZSCORE_WINDOW - 5, seed=3)
        lcs._set_cache_for_test(_mock_cache_for(features))
        assert get_current_composite_stress() is None

    def test_returns_none_when_fetch_raises(self, isolated_cache):
        cache = MagicMock()
        cache.get_history.side_effect = RuntimeError("polygon down")
        lcs._set_cache_for_test(cache)
        assert get_current_composite_stress() is None


# ---------------------------------------------------------------------------
# Disk-cache tests.
# ---------------------------------------------------------------------------


class TestDiskCache:
    def test_cache_hit_skips_recomputation(self, isolated_cache):
        # Pre-populate cache with today's date.
        today = datetime.now(timezone.utc).date().isoformat()
        with open(isolated_cache, "wb") as fh:
            pickle.dump(_CacheEntry(date=today, value=1.234), fh)

        # No DataCache attached — if we hit the network we'd crash.
        lcs._set_cache_for_test(None)

        # Should read from disk and return 1.234 without calling DataCache.
        value = get_current_composite_stress()
        assert value == 1.234

    def test_cache_stale_triggers_recompute(self, isolated_cache):
        # Pre-populate cache with an old date.
        with open(isolated_cache, "wb") as fh:
            pickle.dump(_CacheEntry(date="2024-01-01", value=9.99), fh)

        features = _synthetic_features(n=200, seed=42)
        lcs._set_cache_for_test(_mock_cache_for(features))
        value = get_current_composite_stress()
        # Recomputed value should differ from the stale cached 9.99.
        assert value is not None
        assert value != 9.99

    def test_cache_persists_none_value(self, isolated_cache):
        # If live compute returns None, we still write a cache entry so
        # we don't thrash the network on the next call within the same UTC
        # day.
        cache = MagicMock()
        cache.get_history.return_value = pd.DataFrame()
        lcs._set_cache_for_test(cache)
        assert get_current_composite_stress() is None
        assert isolated_cache.exists()
        with open(isolated_cache, "rb") as fh:
            entry = pickle.load(fh)
        assert entry.value is None
