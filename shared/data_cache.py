"""Thread-safe TTL cache for market data backed by Polygon.io.

Historical migration: previously yfinance-backed. Polygon.io is now the
single source for index and equity daily bars used by the live pipeline.
Yahoo-style tickers (``^VIX``, ``^VIX3M`` …) are auto-translated to
Polygon index tickers (``I:VIX``, ``I:VIX3M`` …) so existing callers do
not need to change.

Options chains and corporate calendars must NOT be fetched here — use
``shared.iron_vault`` or ``strategy.polygon_provider`` directly.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import List, Optional

import pandas as pd

from shared.exceptions import DataFetchError
from shared.metrics import metrics

logger = logging.getLogger(__name__)

# Yahoo-style → Polygon index ticker translation. Anything not in this map
# is passed through unchanged (equities like SPY, TLT, …).
_INDEX_TICKER_MAP = {
    "^VIX":   "I:VIX",
    "^VIX3M": "I:VIX3M",
    "^VVIX":  "I:VVIX",
    "^SKEW":  "I:SKEW",
    "^GSPC":  "I:SPX",
    "^DJI":   "I:DJI",
    "^IXIC":  "I:NDX",
    "^RUT":   "I:RUT",
}

# Mapping of period strings to approximate trading days.
_PERIOD_DAYS = {
    "5d":  5,
    "1mo": 21,
    "3mo": 63,
    "6mo": 126,
    "1y":  252,
    "2y":  504,
}

# Calendar days we fetch from Polygon. 2 years of trading-day coverage is
# enough for any caller-requested period (longest is "2y" = 504 trading
# days ≈ 730 calendar days).
_FETCH_CALENDAR_DAYS = 760


def _to_polygon_ticker(ticker: str) -> str:
    """Translate Yahoo-style index tickers to Polygon equivalents."""
    return _INDEX_TICKER_MAP.get(ticker.upper(), ticker.upper())


class DataCache:
    """In-memory TTL cache for Polygon daily-bar history.

    Each ticker is fetched once for the full ``_FETCH_CALENDAR_DAYS``
    window and cached. Callers requesting shorter periods get a slice
    of the cached frame; no extra network calls.
    """

    def __init__(self, ttl_seconds: int = 900, api_key: Optional[str] = None):
        self._cache: dict = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._api_key = api_key or os.environ.get("POLYGON_API_KEY", "")
        self._indices_api_key = os.environ.get("POLYGON_INDICES_API_KEY", "")
        self._provider = None  # built lazily on first use
        self._indices_provider = None  # separate provider for index tickers

    # ------------------------------------------------------------------
    # Provider
    # ------------------------------------------------------------------

    def _get_provider(self, polygon_ticker: str = ""):
        """Lazily construct the PolygonProvider.

        Index tickers (``I:*``) use ``POLYGON_INDICES_API_KEY`` when
        available, falling back to the default key.
        """
        if polygon_ticker.startswith("I:") and self._indices_api_key:
            if self._indices_provider is None:
                from strategy.polygon_provider import PolygonProvider
                self._indices_provider = PolygonProvider(api_key=self._indices_api_key)
            return self._indices_provider

        if self._provider is None:
            if not self._api_key:
                raise DataFetchError(
                    "POLYGON_API_KEY is not set — DataCache cannot fetch "
                    "market data."
                )
            # Imported lazily to avoid a circular import at module load.
            from strategy.polygon_provider import PolygonProvider
            self._provider = PolygonProvider(api_key=self._api_key)
        return self._provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_history(self, ticker: str, period: str = "1y") -> pd.DataFrame:
        """Return cached daily bars for ``ticker`` over ``period``.

        ``period`` accepts the same strings as the old yfinance-backed
        cache (``"5d"``, ``"1mo"``, ``"3mo"``, ``"6mo"``, ``"1y"``,
        ``"2y"``). The returned DataFrame has the same columns and
        ``DatetimeIndex`` shape callers used to receive previously:
        ``Open``, ``High``, ``Low``, ``Close``, ``Volume``.
        """
        key = ticker.upper()

        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                data, ts = entry
                if time.time() - ts < self._ttl:
                    metrics.inc("cache_hits")
                    return self._slice_to_period(data, period).copy()

        metrics.inc("cache_misses")

        polygon_ticker = _to_polygon_ticker(ticker)
        try:
            data = self._get_provider(polygon_ticker).get_historical(
                polygon_ticker, days=_FETCH_CALENDAR_DAYS,
            )
        except DataFetchError:
            raise
        except Exception as exc:
            logger.error(
                "Failed Polygon fetch for %s (%s): %s",
                ticker, polygon_ticker, exc, exc_info=True,
            )
            raise DataFetchError(
                f"Failed to download data for {ticker}: {exc}"
            ) from exc

        if data is None or data.empty:
            raise DataFetchError(
                f"Polygon returned no data for {ticker} ({polygon_ticker})"
            )

        with self._lock:
            self._cache[key] = (data, time.time())
        return self._slice_to_period(data, period).copy()

    @staticmethod
    def _slice_to_period(data: pd.DataFrame, period: str) -> pd.DataFrame:
        """Slice the cached full-window DataFrame to the requested period."""
        days = _PERIOD_DAYS.get(period)
        if days is None or days >= len(data):
            return data
        return data.iloc[-days:]

    def pre_warm(self, tickers: List[str]) -> None:
        """Pre-populate the cache for a list of tickers.

        Errors are logged but do not propagate so that a single failed
        ticker does not prevent the rest of the cache from being warmed.
        """
        for ticker in tickers:
            try:
                self.get_history(ticker)
                logger.info("Pre-warmed cache for %s", ticker)
            except Exception as exc:
                logger.warning("Pre-warm failed for %s: %s", ticker, exc)

    def get_ticker_obj(self, ticker: str):
        """Deprecated: yfinance Ticker objects are no longer supported.

        Callers needing option chains must use ``PolygonOptionsClient``
        directly. Callers needing OHLCV history should use ``get_history``.
        """
        raise NotImplementedError(
            "get_ticker_obj is no longer supported after the Polygon migration. "
            "Use PolygonOptionsClient for option chains or get_history for OHLCV."
        )

    def clear(self) -> None:
        """Clear all cached data."""
        with self._lock:
            self._cache.clear()
