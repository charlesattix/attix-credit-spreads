"""Tests for compass.live.vrp_data (PR-A — VRP live multi-symbol data + IV chains).

The data providers are mocked (Polygon for chains, DataCache for bars/VIX) — no
network. Mirrors tests/test_data_cache.py conventions.
"""
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from compass.live.vrp_data import (
    DEFAULT_DTE_RANGE,
    VRP_HEDGE_SYMBOLS,
    VRP_OPTION_SYMBOLS,
    VRPDataFeed,
    VRPSnapshot,
    _period_for,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(periods=600, seed=7, start=400.0):
    np.random.seed(seed)
    dates = pd.date_range("2024-01-02", periods=periods, freq="B")
    close = start + np.cumsum(np.random.randn(periods) * 2)
    return pd.DataFrame({
        "Open":   close - 0.5,
        "High":   close + 1.0,
        "Low":    close - 1.0,
        "Close":  close,
        "Volume": np.random.randint(1_000_000, 5_000_000, periods),
    }, index=dates)


def _make_chain(n=6, expiration="2026-07-17"):
    exp = pd.Timestamp(expiration)
    strikes = np.linspace(380, 420, n)
    return pd.DataFrame({
        "contract_symbol": [f"SPY260717P00{int(k)}000" for k in strikes],
        "strike": strikes,
        "type": ["put"] * n,
        "bid": np.linspace(1.0, 3.0, n),
        "ask": np.linspace(1.1, 3.1, n),
        "last": np.linspace(1.05, 3.05, n),
        "volume": np.random.randint(10, 500, n),
        "open_interest": np.random.randint(100, 5000, n),
        "iv": np.linspace(0.15, 0.25, n),
        "delta": np.linspace(-0.1, -0.4, n),
        "raw_delta": np.linspace(-0.1, -0.4, n),
        "gamma": np.full(n, 0.01),
        "theta": np.full(n, -0.05),
        "vega": np.full(n, 0.12),
        "mid": np.linspace(1.05, 3.05, n),
        "expiration": [exp] * n,
        "itm": [False] * n,
    })


def _feed(*, polygon=None, data_cache=None, cycle_ttl=300.0):
    return VRPDataFeed(polygon=polygon, data_cache=data_cache, cycle_ttl=cycle_ttl)


def _mock_polygon(chain=None):
    p = MagicMock()
    p.get_full_chain.return_value = _make_chain() if chain is None else chain
    return p


def _mock_cache(bars=None, vix_last=18.5):
    c = MagicMock()

    def _get_history(ticker, period="1y"):
        if ticker.upper() == "^VIX":
            idx = pd.date_range("2026-05-20", periods=5, freq="B")
            return pd.DataFrame({"Close": np.linspace(17.0, vix_last, 5)}, index=idx)
        return _make_bars() if bars is None else bars

    c.get_history.side_effect = _get_history
    return c


# ---------------------------------------------------------------------------
# Constants / universe
# ---------------------------------------------------------------------------

class TestUniverse:
    def test_option_symbols(self):
        assert VRP_OPTION_SYMBOLS == ["SPY", "QQQ", "XLF", "XLI", "GLD", "SLV"]

    def test_hedge_universe_is_13_etfs(self):
        assert len(VRP_HEDGE_SYMBOLS) == 13
        assert "TLT" in VRP_HEDGE_SYMBOLS and "UUP" in VRP_HEDGE_SYMBOLS

    def test_default_dte_range(self):
        assert DEFAULT_DTE_RANGE == (25, 50)

    @pytest.mark.parametrize("lookback,period", [
        (3, "5d"), (5, "5d"), (20, "1mo"), (63, "3mo"),
        (100, "6mo"), (252, "1y"), (300, "2y"), (5000, "2y"),
    ])
    def test_period_ladder(self, lookback, period):
        assert _period_for(lookback) == period


# ---------------------------------------------------------------------------
# get_bars
# ---------------------------------------------------------------------------

class TestGetBars:
    def test_returns_tail_lookback_rows(self):
        feed = _feed(data_cache=_mock_cache())
        df = feed.get_bars("SPY", lookback=30)
        assert len(df) == 30
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]

    def test_maps_lookback_to_period(self):
        cache = _mock_cache()
        feed = _feed(data_cache=cache)
        feed.get_bars("QQQ", lookback=63)
        cache.get_history.assert_called_with("QQQ", period="3mo")

    def test_empty_on_provider_failure(self):
        cache = MagicMock()
        cache.get_history.side_effect = RuntimeError("polygon down")
        feed = _feed(data_cache=cache)
        df = feed.get_bars("XLF", lookback=10)
        assert df.empty  # graceful, no raise

    def test_empty_on_no_data(self):
        cache = MagicMock()
        cache.get_history.return_value = pd.DataFrame()
        feed = _feed(data_cache=cache)
        assert feed.get_bars("GLD").empty


# ---------------------------------------------------------------------------
# get_iv_chain
# ---------------------------------------------------------------------------

class TestGetIVChain:
    def test_passes_dte_range_to_polygon(self):
        poly = _mock_polygon()
        feed = _feed(polygon=poly)
        feed.get_iv_chain("SPY", dte_range=(30, 45))
        poly.get_full_chain.assert_called_with("SPY", min_dte=30, max_dte=45)

    def test_default_dte_range(self):
        poly = _mock_polygon()
        feed = _feed(polygon=poly)
        feed.get_iv_chain("QQQ")
        poly.get_full_chain.assert_called_with("QQQ", min_dte=25, max_dte=50)

    def test_chain_has_iv_and_greeks(self):
        feed = _feed(polygon=_mock_polygon())
        df = feed.get_iv_chain("SPY")
        for col in ("iv", "delta", "gamma", "theta", "vega"):
            assert col in df.columns

    def test_empty_on_provider_failure(self):
        poly = MagicMock()
        poly.get_full_chain.side_effect = RuntimeError("429 rate limited")
        feed = _feed(polygon=poly)
        assert feed.get_iv_chain("SLV").empty  # graceful

    def test_empty_chain_passthrough(self):
        feed = _feed(polygon=_mock_polygon(chain=pd.DataFrame()))
        assert feed.get_iv_chain("XLI").empty


# ---------------------------------------------------------------------------
# get_vix_realtime
# ---------------------------------------------------------------------------

class TestGetVix:
    def test_returns_latest_close(self):
        feed = _feed(data_cache=_mock_cache(vix_last=22.3))
        assert feed.get_vix_realtime() == pytest.approx(22.3)

    def test_none_on_failure(self):
        cache = MagicMock()
        cache.get_history.side_effect = RuntimeError("no key")
        feed = _feed(data_cache=cache)
        assert feed.get_vix_realtime() is None

    def test_none_on_empty(self):
        cache = MagicMock()
        cache.get_history.return_value = pd.DataFrame()
        feed = _feed(data_cache=cache)
        assert feed.get_vix_realtime() is None


# ---------------------------------------------------------------------------
# Per-cycle caching (rate-limit protection)
# ---------------------------------------------------------------------------

class TestCycleCache:
    def test_chain_fetched_once_per_cycle(self):
        poly = _mock_polygon()
        feed = _feed(polygon=poly)
        feed.get_iv_chain("SPY")
        feed.get_iv_chain("SPY")
        feed.get_iv_chain("SPY")
        assert poly.get_full_chain.call_count == 1

    def test_bars_fetched_once_per_cycle(self):
        cache = _mock_cache()
        feed = _feed(data_cache=cache)
        feed.get_bars("SPY", lookback=30)
        feed.get_bars("SPY", lookback=30)
        assert cache.get_history.call_count == 1

    def test_reset_cycle_forces_refetch(self):
        poly = _mock_polygon()
        feed = _feed(polygon=poly)
        feed.get_iv_chain("SPY")
        feed.reset_cycle()
        feed.get_iv_chain("SPY")
        assert poly.get_full_chain.call_count == 2

    def test_ttl_expiry_refetches(self):
        poly = _mock_polygon()
        feed = _feed(polygon=poly, cycle_ttl=0.0)  # everything immediately stale
        feed.get_iv_chain("SPY")
        feed.get_iv_chain("SPY")
        assert poly.get_full_chain.call_count == 2

    def test_distinct_lookbacks_cached_separately(self):
        cache = _mock_cache()
        feed = _feed(data_cache=cache)
        feed.get_bars("SPY", lookback=30)
        feed.get_bars("SPY", lookback=60)
        assert cache.get_history.call_count == 2

    def test_returned_frame_is_copy(self):
        feed = _feed(polygon=_mock_polygon())
        a = feed.get_iv_chain("SPY")
        a.loc[0, "iv"] = -999.0
        b = feed.get_iv_chain("SPY")
        assert b.loc[0, "iv"] != -999.0


# ---------------------------------------------------------------------------
# snapshot — coherent cross-section + graceful degradation
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_full_snapshot(self):
        feed = _feed(polygon=_mock_polygon(), data_cache=_mock_cache(vix_last=19.0))
        snap = feed.snapshot(option_symbols=["SPY", "QQQ"])
        assert isinstance(snap, VRPSnapshot)
        assert set(snap.chains) == {"SPY", "QQQ"}
        assert snap.vix == pytest.approx(19.0)
        assert snap.degraded == []
        assert snap.spot["SPY"] > 0
        assert snap.symbols == ["QQQ", "SPY"]

    def test_degrades_failing_symbol_without_crashing(self):
        poly = MagicMock()

        def _chain(ticker, min_dte, max_dte):
            if ticker == "XLF":
                raise RuntimeError("no chain for XLF")
            return _make_chain()

        poly.get_full_chain.side_effect = _chain
        feed = _feed(polygon=poly, data_cache=_mock_cache())
        snap = feed.snapshot(option_symbols=["SPY", "XLF", "QQQ"])
        assert "XLF" in snap.degraded
        assert set(snap.chains) == {"SPY", "QQQ"}  # survivors still present

    def test_defaults_to_full_option_universe(self):
        feed = _feed(polygon=_mock_polygon(), data_cache=_mock_cache())
        snap = feed.snapshot()
        assert set(snap.chains) == set(VRP_OPTION_SYMBOLS)

    def test_snapshot_reuses_cycle_cache(self):
        poly = _mock_polygon()
        feed = _feed(polygon=poly, data_cache=_mock_cache())
        feed.get_iv_chain("SPY")          # warm cache
        feed.snapshot(option_symbols=["SPY"])  # should reuse, not refetch
        assert poly.get_full_chain.call_count == 1
