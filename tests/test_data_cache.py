"""Tests for DataCache (Polygon-backed)."""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from shared.data_cache import DataCache, _to_polygon_ticker
from shared.exceptions import DataFetchError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_price_df(periods=600, seed=42):
    np.random.seed(seed)
    dates = pd.date_range("2024-01-02", periods=periods, freq="B")
    close = 450.0 + np.cumsum(np.random.randn(periods) * 2)
    return pd.DataFrame({
        "Open":   close - 0.5,
        "High":   close + 1.0,
        "Low":    close - 1.0,
        "Close":  close,
        "Volume": np.random.randint(1_000_000, 5_000_000, periods),
    }, index=dates)


def _mock_provider(df=None):
    if df is None:
        df = _make_price_df()
    provider = MagicMock()
    provider.get_historical.return_value = df
    return provider


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTickerTranslation:
    def test_yahoo_index_maps_to_polygon(self):
        assert _to_polygon_ticker("^VIX")   == "I:VIX"
        assert _to_polygon_ticker("^VIX3M") == "I:VIX3M"
        assert _to_polygon_ticker("^VVIX")  == "I:VVIX"
        assert _to_polygon_ticker("^SKEW")  == "I:SKEW"

    def test_equity_ticker_passthrough(self):
        assert _to_polygon_ticker("SPY") == "SPY"
        assert _to_polygon_ticker("tlt") == "TLT"


class TestDataCache:

    def test_get_history_caches(self):
        cache = DataCache(ttl_seconds=60, api_key="test")
        cache._provider = _mock_provider()

        result1 = cache.get_history("SPY")
        result2 = cache.get_history("SPY")

        assert cache._provider.get_historical.call_count == 1
        assert len(result1) == len(result2)

    def test_get_history_returns_copy(self):
        cache = DataCache(ttl_seconds=60, api_key="test")
        cache._provider = _mock_provider()

        result1 = cache.get_history("SPY")
        result2 = cache.get_history("SPY")

        result1.iloc[0, 0] = -9999
        assert result2.iloc[0, 0] != -9999

    def test_different_tickers_download_separately(self):
        cache = DataCache(ttl_seconds=60, api_key="test")
        cache._provider = _mock_provider()

        cache.get_history("SPY")
        cache.get_history("QQQ")

        assert cache._provider.get_historical.call_count == 2

    def test_vix_routes_to_polygon_index_ticker(self):
        cache = DataCache(ttl_seconds=60, api_key="test")
        cache._provider = _mock_provider()

        cache.get_history("^VIX")

        called_ticker = cache._provider.get_historical.call_args[0][0]
        assert called_ticker == "I:VIX"

    def test_clear_resets_cache(self):
        cache = DataCache(ttl_seconds=60, api_key="test")
        cache._provider = _mock_provider()

        cache.get_history("SPY")
        cache.clear()
        cache.get_history("SPY")

        assert cache._provider.get_historical.call_count == 2

    def test_empty_polygon_response_raises(self):
        cache = DataCache(ttl_seconds=60, api_key="test")
        cache._provider = _mock_provider(df=pd.DataFrame())

        with pytest.raises(DataFetchError):
            cache.get_history("SPY")

    def test_missing_api_key_raises(self):
        cache = DataCache(ttl_seconds=60, api_key="")
        with pytest.raises(DataFetchError):
            cache.get_history("SPY")

    def test_period_slice(self):
        df = _make_price_df(periods=300)
        cache = DataCache(ttl_seconds=60, api_key="test")
        cache._provider = _mock_provider(df=df)

        result_5d = cache.get_history("SPY", period="5d")
        result_1mo = cache.get_history("SPY", period="1mo")

        assert len(result_5d) == 5
        assert len(result_1mo) == 21
