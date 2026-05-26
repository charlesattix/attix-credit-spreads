"""Tests for DataCache (Polygon-backed)."""
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from shared.data_cache import DataCache, _polygon_to_dataframe
from shared.exceptions import DataFetchError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_polygon_results(periods: int = 100, seed: int = 42, start: str = "2025-01-01"):
    """Synthesize a Polygon aggregates response (list of bar dicts)."""
    np.random.seed(seed)
    dates = pd.date_range(start, periods=periods, freq="B")
    close = 450.0 + np.cumsum(np.random.randn(periods) * 2)
    results = []
    for dt, c in zip(dates, close):
        ts_ms = int(pd.Timestamp(dt).tz_localize("UTC").timestamp() * 1000)
        results.append({
            "t": ts_ms,
            "o": float(c - 0.5),
            "h": float(c + 1.0),
            "l": float(c - 1.0),
            "c": float(c),
            "v": int(np.random.randint(1_000_000, 5_000_000)),
        })
    return results


def _mock_client(results=None):
    if results is None:
        results = _make_polygon_results()
    client = MagicMock()
    client.aggregates.return_value = results
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDataCache:

    def test_get_history_caches(self):
        """Second call should use cached data, not download again."""
        client = _mock_client()
        cache = DataCache(ttl_seconds=60, polygon_client=client)

        r1 = cache.get_history("SPY")
        r2 = cache.get_history("SPY")

        assert client.aggregates.call_count == 1
        assert len(r1) == len(r2)

    def test_get_history_returns_copy(self):
        """Each call should return a copy, not a reference to cached data."""
        client = _mock_client()
        cache = DataCache(ttl_seconds=60, polygon_client=client)

        r1 = cache.get_history("SPY")
        r2 = cache.get_history("SPY")

        r1.iloc[0, 0] = -9999
        assert r2.iloc[0, 0] != -9999

    def test_different_tickers_download_separately(self):
        """Different tickers should each trigger their own download."""
        client = _mock_client()
        cache = DataCache(ttl_seconds=60, polygon_client=client)

        cache.get_history("SPY")
        cache.get_history("QQQ")

        assert client.aggregates.call_count == 2

    def test_clear_resets_cache(self):
        client = _mock_client()
        cache = DataCache(ttl_seconds=60, polygon_client=client)

        cache.get_history("SPY")
        cache.clear()
        cache.get_history("SPY")

        assert client.aggregates.call_count == 2

    def test_dataframe_schema(self):
        """Returned DataFrame must match yfinance shape: capitalized columns,
        timezone-naive DatetimeIndex, ascending."""
        client = _mock_client(_make_polygon_results(periods=50))
        cache = DataCache(ttl_seconds=60, polygon_client=client)

        df = cache.get_history("SPY")

        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.tz is None
        assert df.index.is_monotonic_increasing
        assert len(df) == 50

    def test_period_slicing(self):
        """get_history(period='1mo') should slice to ~21 bars."""
        client = _mock_client(_make_polygon_results(periods=252))
        cache = DataCache(ttl_seconds=60, polygon_client=client)

        df_full = cache.get_history("SPY", period="1y")
        df_1mo = cache.get_history("SPY", period="1mo")

        assert len(df_full) == 252
        assert len(df_1mo) == 21
        # Caches the full series only; slice happens locally
        assert client.aggregates.call_count == 1

    def test_index_symbol_mapping(self):
        """`^VIX` should be routed to `I:VIX` on the Polygon call."""
        client = _mock_client(_make_polygon_results(periods=30))
        cache = DataCache(ttl_seconds=60, polygon_client=client)

        cache.get_history("^VIX")

        call_kwargs = client.aggregates.call_args.kwargs
        assert call_kwargs["ticker"] == "I:VIX"

    def test_get_ticker_obj_raises(self):
        """get_ticker_obj must now raise NotImplementedError."""
        cache = DataCache()
        with pytest.raises(NotImplementedError):
            cache.get_ticker_obj("SPY")

    def test_empty_response_raises(self):
        """Polygon returning 0 bars must raise DataFetchError."""
        client = _mock_client(results=[])
        cache = DataCache(ttl_seconds=60, polygon_client=client)

        with pytest.raises(DataFetchError):
            cache.get_history("SPY")

    def test_polygon_to_dataframe_empty(self):
        df = _polygon_to_dataframe([])
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert df.empty
