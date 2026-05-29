"""Verify Polygon OPTIONS-endpoint API-key routing.

Options endpoints (/v3/snapshot/options/*, /v3/reference/options/contracts) must
use POLYGON_OPTIONS_API_KEY (falling back to POLYGON_API_KEY for backward compat);
aggregate/stock endpoints must keep using the stocks key. Mirrors
tests/test_polygon_key_routing.py but routes by ENDPOINT (options) rather than
ticker (indices). Setting the options key must never alter the stocks key.
"""
from __future__ import annotations

from strategy.polygon_provider import PolygonProvider


def _provider(monkeypatch, options_key="OPTIONS_KEY", stocks_key="STOCKS_KEY"):
    if options_key is None:
        monkeypatch.delenv("POLYGON_OPTIONS_API_KEY", raising=False)
    else:
        monkeypatch.setenv("POLYGON_OPTIONS_API_KEY", options_key)
    return PolygonProvider(api_key=stocks_key)  # __init__ reads the env at construction


class TestOptionsKeyRouting:
    def test_options_endpoints_use_options_key(self, monkeypatch):
        p = _provider(monkeypatch)
        assert p._key_for_path("/v3/snapshot/options/SPY") == "OPTIONS_KEY"
        assert p._key_for_path("/v3/snapshot/options/QQQ") == "OPTIONS_KEY"
        assert p._key_for_path("/v3/reference/options/contracts") == "OPTIONS_KEY"

    def test_pagination_url_routes_to_options_key(self, monkeypatch):
        p = _provider(monkeypatch)
        nxt = "https://api.polygon.io/v3/snapshot/options/SPY?cursor=abc123"
        assert p._key_for_path(nxt) == "OPTIONS_KEY"

    def test_aggregate_endpoints_use_stocks_key(self, monkeypatch):
        p = _provider(monkeypatch)
        assert p._key_for_path("/v2/aggs/ticker/SPY/range/1/day/2024-01-01/2024-02-01") == "STOCKS_KEY"
        assert p._key_for_path("/v3/reference/tickers/SPY") == "STOCKS_KEY"

    def test_fallback_to_stocks_when_options_key_missing(self, monkeypatch):
        # Backward compat: no POLYGON_OPTIONS_API_KEY → options endpoints fall back
        # to the stocks key (prior behavior); nothing breaks.
        p = _provider(monkeypatch, options_key=None)
        assert p._key_for_path("/v3/snapshot/options/SPY") == "STOCKS_KEY"
        assert p._key_for_path("/v2/aggs/ticker/SPY/range/1/day/x/y") == "STOCKS_KEY"

    def test_options_routing_does_not_alter_stocks_key(self, monkeypatch):
        # Sacred: introducing the options key must not change the stocks key.
        p = _provider(monkeypatch)
        assert p.api_key == "STOCKS_KEY"
        assert p.options_api_key == "OPTIONS_KEY"
