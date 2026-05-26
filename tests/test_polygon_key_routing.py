"""P0-2: verify Polygon API key routing for index vs non-index tickers.

`I:`-prefixed tickers (I:VIX, I:VIX3M, I:VVIX, I:SKEW) must use
POLYGON_INDICES_API_KEY; everything else must use POLYGON_API_KEY. The
stocks plan returns 403 NOT_AUTHORIZED for index aggregates, so a
wrong-key request silently falls back to yfinance with stale data.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from shared.polygon_client import _pick_key


class TestPickKey:
    def test_index_ticker_uses_indices_key(self, monkeypatch):
        monkeypatch.setenv("POLYGON_API_KEY", "STOCKS_KEY")
        monkeypatch.setenv("POLYGON_INDICES_API_KEY", "INDICES_KEY")
        for sym in ("I:VIX", "I:VIX3M", "I:VVIX", "I:SKEW", "i:vix"):
            assert _pick_key(sym) == "INDICES_KEY", f"expected indices key for {sym}"

    def test_stock_ticker_uses_stocks_key(self, monkeypatch):
        monkeypatch.setenv("POLYGON_API_KEY", "STOCKS_KEY")
        monkeypatch.setenv("POLYGON_INDICES_API_KEY", "INDICES_KEY")
        for sym in ("SPY", "QQQ", "TLT", "AAPL", "XLF"):
            assert _pick_key(sym) == "STOCKS_KEY", f"expected stocks key for {sym}"

    def test_missing_indices_key_returns_empty(self, monkeypatch):
        monkeypatch.setenv("POLYGON_API_KEY", "STOCKS_KEY")
        monkeypatch.delenv("POLYGON_INDICES_API_KEY", raising=False)
        assert _pick_key("I:VIX") == ""
        assert _pick_key("SPY") == "STOCKS_KEY"

    def test_missing_stocks_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        monkeypatch.setenv("POLYGON_INDICES_API_KEY", "INDICES_KEY")
        assert _pick_key("SPY") == ""
        assert _pick_key("I:VIX") == "INDICES_KEY"


class TestDataProvidersRouting:
    """End-to-end check: data_providers._polygon_get_historical must send
    the indices key for I:VIX and the stocks key for SPY."""

    def test_polygon_historical_routes_indices_key(self, monkeypatch):
        monkeypatch.setenv("POLYGON_API_KEY", "STOCKS_KEY")
        monkeypatch.setenv("POLYGON_INDICES_API_KEY", "INDICES_KEY")

        from scheduler import data_providers

        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            captured["apiKey"] = params.get("apiKey") if params else None
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.json = MagicMock(return_value={"results": []})
            return mock

        with patch("requests.get", side_effect=fake_get):
            data_providers._polygon_get_historical("I:VIX", days=5)
            assert captured["apiKey"] == "INDICES_KEY"
            assert "I:VIX" in captured["url"]

            data_providers._polygon_get_historical("SPY", days=5)
            assert captured["apiKey"] == "STOCKS_KEY"
            assert "SPY" in captured["url"]

    def test_get_vix_values_uses_indices_key(self, monkeypatch):
        monkeypatch.setenv("POLYGON_API_KEY", "STOCKS_KEY")
        monkeypatch.setenv("POLYGON_INDICES_API_KEY", "INDICES_KEY")

        from scheduler import data_providers

        seen_keys = []

        def fake_get(url, params=None, timeout=None):
            seen_keys.append(params.get("apiKey") if params else None)
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.json = MagicMock(return_value={"results": [{"c": 18.5}]})
            return mock

        with patch("requests.get", side_effect=fake_get):
            data_providers.get_vix_values()

        # Both I:VIX and I:VIX3M must use the indices key
        assert seen_keys, "no Polygon request was made"
        assert all(k == "INDICES_KEY" for k in seen_keys), (
            f"expected all VIX requests to use INDICES_KEY, got {seen_keys}"
        )
