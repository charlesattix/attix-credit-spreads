"""Thread-safe TTL cache for OHLCV history (Polygon-backed).

Preserves the public surface of the previous yfinance-backed DataCache so
existing callers (alerts/, strategy/, compass/) work unchanged.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List

import pandas as pd

from shared.exceptions import DataFetchError
from shared.metrics import metrics
from shared.polygon_client import PolygonClient

logger = logging.getLogger(__name__)

# Mapping of period strings to approximate trading days
_PERIOD_DAYS = {
    '5d': 5,
    '1mo': 21,
    '3mo': 63,
    '6mo': 126,
    '1y': 252,
}

# yfinance/Yahoo index symbols → Polygon index tickers
_SYMBOL_MAP = {
    '^VIX':   'I:VIX',
    '^VIX3M': 'I:VIX3M',
    '^GSPC':  'I:SPX',
    '^DJI':   'I:DJI',
    '^IXIC':  'I:NDX',
}


def _polygon_to_dataframe(results: list) -> pd.DataFrame:
    """Convert Polygon aggregate results to a yfinance-shaped DataFrame.

    Output columns: Open, High, Low, Close, Volume.
    Index: timezone-naive DatetimeIndex (date-only), sorted ascending.
    """
    if not results:
        return pd.DataFrame(
            columns=['Open', 'High', 'Low', 'Close', 'Volume'],
            index=pd.DatetimeIndex([], name='Date'),
        )

    # Polygon 't' is epoch milliseconds (UTC, market-day timestamp)
    df = pd.DataFrame(results)
    # Index tickers may omit volume — fill with 0
    if 'v' not in df.columns:
        df['v'] = 0
    df['Date'] = pd.to_datetime(df['t'], unit='ms', utc=True).dt.tz_convert(None).dt.normalize()
    df = df.rename(columns={'o': 'Open', 'h': 'High', 'l': 'Low', 'c': 'Close', 'v': 'Volume'})
    df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
    df = df.set_index('Date').sort_index()
    return df


class DataCache:
    """Download each ticker's data once (1y period), slice to requested period."""

    def __init__(self, ttl_seconds: int = 900, polygon_client: PolygonClient = None):
        self._cache: dict = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._client = polygon_client or PolygonClient()

    def get_history(self, ticker: str, period: str = '1y') -> pd.DataFrame:
        """Get historical data, using cache if fresh.

        Always downloads the full 1y period and caches by ticker only.
        Shorter periods are sliced locally to avoid redundant downloads.
        """
        key = ticker.upper()
        cached = None
        with self._lock:
            if key in self._cache:
                data, ts = self._cache[key]
                if time.time() - ts < self._ttl:
                    logger.debug(f"Cache hit for {key}")
                    metrics.inc('cache_hits')
                    cached = data

        if cached is not None:
            return self._slice_to_period(cached, period).copy()

        metrics.inc('cache_misses')

        polygon_ticker = _SYMBOL_MAP.get(ticker, _SYMBOL_MAP.get(key, ticker))
        # Fetch ~1y of daily aggregates. Add buffer for weekends/holidays so
        # we reliably receive ~252 trading days.
        today = datetime.now(timezone.utc).date()
        from_date = (today - timedelta(days=400)).isoformat()
        to_date = today.isoformat()

        try:
            results = self._client.aggregates(
                ticker=polygon_ticker,
                multiplier=1,
                timespan='day',
                from_date=from_date,
                to_date=to_date,
            )
            data = _polygon_to_dataframe(results)
            if data.empty:
                raise DataFetchError(f"Polygon returned 0 bars for {ticker} ({polygon_ticker})")
            with self._lock:
                self._cache[key] = (data, time.time())
            return self._slice_to_period(data, period).copy()
        except DataFetchError:
            raise
        except Exception as e:
            logger.error(f"Failed to download {ticker}: {e}", exc_info=True)
            raise DataFetchError(f"Failed to download data for {ticker}: {e}") from e

    @staticmethod
    def _slice_to_period(data: pd.DataFrame, period: str) -> pd.DataFrame:
        """Slice a full-year DataFrame to the requested period."""
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
                logger.info(f"Pre-warmed cache for {ticker}")
            except Exception as e:
                logger.warning(f"Pre-warm failed for {ticker}: {e}")

    def get_ticker_obj(self, ticker: str):
        """Deprecated: yfinance Ticker objects are no longer supported.

        Callers needing option chains must use ``PolygonOptionsClient``
        directly. Callers needing OHLCV history should use ``get_history``.
        """
        raise NotImplementedError(
            "get_ticker_obj is no longer supported after the Polygon migration. "
            "Use PolygonOptionsClient for option chains or get_history for OHLCV."
        )

    def clear(self):
        """Clear all cached data."""
        with self._lock:
            self._cache.clear()
