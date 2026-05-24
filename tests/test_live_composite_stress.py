"""Tests for compass/live_composite_stress.py.

The headline guarantee these tests defend:

  compass.live_composite_stress.build_composite_stress
      === the EXP-3303b reference formula (inlined below as
          ``_reference_build_composite_stress``).

The reference is a verbatim copy of
``compass/exp3303_regime_transition_dd.py::build_composite_stress`` at
the time of writing — copied here, not imported, so that
(a) the test does not depend on a research-only module that may be
absent from production deployments, and
(b) any drift between the live module and the reference is loud (a
test diff, not a silent import).

If you edit the live formula you MUST also edit the reference below
and confirm the diff is intentional.

Note: the fabricated frames here are TEST inputs, not production data
— Rule Zero (no fabricated prices in live code paths) is unaffected.
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from compass import live_composite_stress as live
from shared.exceptions import DataFetchError


# ---------------------------------------------------------------------------
# Reference formula — verbatim copy of EXP-3303b's build_composite_stress.
# DO NOT IMPORT from compass.exp3303_regime_transition_dd here — that module
# is a research script (imports yfinance, pulls fixtures) and is not part of
# the production deployment.
# ---------------------------------------------------------------------------

_REF_ZSCORE_WINDOW = 63


def _reference_build_composite_stress(features: pd.DataFrame) -> pd.DataFrame:
    """Reference impl from compass/exp3303_regime_transition_dd.py."""
    f = features.copy()
    f["term_spread"] = f["vix3m"] - f["vix"]
    for col, neg in [("term_spread", True), ("vvix", False), ("skew", False)]:
        roll = f[col].rolling(_REF_ZSCORE_WINDOW, min_periods=_REF_ZSCORE_WINDOW)
        z = (f[col] - roll.mean()) / roll.std(ddof=1)
        f[f"{col}_z"] = z if not neg else -z
    f["composite_stress"] = (
        f["term_spread_z"] + f["vvix_z"] + f["skew_z"]
    ) / math.sqrt(3.0)
    return f


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Redirect the on-disk cache into a per-test temp dir so tests don't
    pollute each other or the real compass/cache directory."""
    monkeypatch.setattr(
        live, "CACHE_PATH", tmp_path / "cache" / "live_composite_stress.pkl",
    )
    live._set_cache_for_test(None)
    yield
    live._set_cache_for_test(None)


def _make_features(periods: int = 300, seed: int = 7) -> pd.DataFrame:
    """A deterministic VIX/VIX3M/VVIX/SKEW panel for arithmetic tests."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=periods)
    vix = np.clip(15 + rng.normal(0, 0.4, periods).cumsum(), 8, 80)
    vix3m = vix + rng.normal(0, 0.6, periods) + 1.0
    vvix = np.clip(95 + rng.normal(0, 0.6, periods).cumsum(), 60, 200)
    skew = np.clip(135 + rng.normal(0, 0.3, periods).cumsum(), 100, 180)
    return pd.DataFrame(
        {"vix": vix, "vix3m": vix3m, "vvix": vvix, "skew": skew},
        index=dates,
    )


def _bars_from_close(close: pd.Series) -> pd.DataFrame:
    """Wrap a Close series in the OHLCV frame DataCache returns."""
    return pd.DataFrame({
        "Open":   close.values,
        "High":   close.values,
        "Low":    close.values,
        "Close":  close.values,
        "Volume": np.zeros(len(close), dtype=int),
    }, index=close.index)


def _mock_cache(feats: pd.DataFrame) -> MagicMock:
    cache = MagicMock()
    mapping = {"^VIX": "vix", "^VIX3M": "vix3m", "^VVIX": "vvix", "^SKEW": "skew"}

    def _hist(ticker, period="1y"):
        return _bars_from_close(feats[mapping[ticker]])
    cache.get_history.side_effect = _hist
    return cache


# ---------------------------------------------------------------------------
# Formula parity — the most important invariant
# ---------------------------------------------------------------------------

class TestFormulaMatchesBacktest:

    def test_full_frame_matches_backtest(self):
        feats = _make_features()
        live_out = live.build_composite_stress(feats)
        bt_out = _reference_build_composite_stress(feats)

        for col in ("term_spread", "term_spread_z", "vvix_z", "skew_z", "composite_stress"):
            assert col in live_out.columns
            assert col in bt_out.columns
            pd.testing.assert_series_equal(
                live_out[col], bt_out[col], check_names=False,
                check_exact=False, atol=1e-12, rtol=0,
            )

    def test_term_spread_sign_inverted(self):
        """High term_spread (contango) → negative term_spread_z."""
        feats = _make_features()
        out = live.build_composite_stress(feats)
        valid = out["term_spread_z"].dropna()
        spread = out["term_spread"].loc[valid.index]
        corr = float(np.corrcoef(spread.values, valid.values)[0, 1])
        assert corr < -0.95

    def test_composite_uses_sqrt3_normalisation(self):
        feats = _make_features()
        out = live.build_composite_stress(feats)
        expected = (out["term_spread_z"] + out["vvix_z"] + out["skew_z"]) / math.sqrt(3.0)
        pd.testing.assert_series_equal(
            out["composite_stress"], expected, check_names=False,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class TestGetCurrent:

    def test_returns_float_when_data_available(self):
        live._set_cache_for_test(_mock_cache(_make_features()))
        cs = live.get_current_composite_stress()
        assert cs is not None
        assert isinstance(cs, float)
        assert math.isfinite(cs)

    def test_returns_none_when_polygon_unavailable(self):
        cache = MagicMock()
        cache.get_history.side_effect = DataFetchError("no Polygon plan for ^VVIX")
        live._set_cache_for_test(cache)
        assert live.get_current_composite_stress() is None

    def test_returns_none_when_window_incomplete(self):
        live._set_cache_for_test(_mock_cache(_make_features(periods=10)))
        assert live.get_current_composite_stress() is None


# ---------------------------------------------------------------------------
# should_gate_spx_streams
# ---------------------------------------------------------------------------

class TestShouldGate:

    def test_gates_when_composite_exceeds_theta(self, monkeypatch):
        monkeypatch.setattr(live, "get_current_composite_stress", lambda: 3.0)
        assert live.should_gate_spx_streams(theta=2.5) is True

    def test_passes_when_composite_below_theta(self, monkeypatch):
        monkeypatch.setattr(live, "get_current_composite_stress", lambda: 1.0)
        assert live.should_gate_spx_streams(theta=2.5) is False

    def test_warm_up_behaviour_when_unavailable(self, monkeypatch):
        """Matches exp3303 ``apply_regime_gate``: when composite is NaN/None
        we are in warm-up — leverage stays at 1.0, i.e. NO gating. This
        preserves backtest-vs-live parity. (The composite itself still
        returns None per Rule Zero — fail-closed applies to the value, not
        to the gate decision, which intentionally mirrors the backtest.)"""
        monkeypatch.setattr(live, "get_current_composite_stress", lambda: None)
        assert live.should_gate_spx_streams(theta=2.5) is False

    def test_boundary_exclusive(self):
        """composite_stress > theta → gate. Equality does NOT gate (matches
        backtest's ``> theta`` comparison)."""
        # patch the symbol the gate reads from
        import compass.live_composite_stress as lcs
        original = lcs.get_current_composite_stress
        try:
            lcs.get_current_composite_stress = lambda: 2.5
            assert lcs.should_gate_spx_streams(theta=2.5) is False
        finally:
            lcs.get_current_composite_stress = original


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

class TestDiskCache:

    def test_round_trip(self):
        feats = _make_features()
        live._set_cache_for_test(_mock_cache(feats))
        v1 = live.get_current_composite_stress()
        assert v1 is not None
        assert live.CACHE_PATH.exists()

        # Even with a broken downstream cache, the pickle should keep
        # serving the same value for the rest of the UTC day.
        broken = MagicMock()
        broken.get_history.side_effect = RuntimeError("network down")
        live._set_cache_for_test(broken)
        v2 = live.get_current_composite_stress()
        assert v2 == pytest.approx(v1)
