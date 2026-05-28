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


def _env_flag(name: str) -> bool:
    """Parse a boolean env flag (default False)."""
    return os.environ.get(name, "false").strip().lower() in ("1", "true", "yes", "on")


class DataCache:
    """In-memory TTL cache for Polygon daily-bar history.

    Each ticker is fetched once for the full ``_FETCH_CALENDAR_DAYS``
    window and cached. Callers requesting shorter periods get a slice
    of the cached frame; no extra network calls.
    """

    def __init__(self, ttl_seconds: int = 900, api_key: Optional[str] = None,
                 shared_cache=None):
        self._cache: dict = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._api_key = api_key or os.environ.get("POLYGON_API_KEY", "")
        self._indices_api_key = os.environ.get("POLYGON_INDICES_API_KEY", "")
        self._provider = None  # built lazily on first use
        self._indices_provider = None  # separate provider for index tickers

        # --- Phase 1: cross-process shared SQLite bar cache (feature-flagged) ---
        # OFF by default — when disabled, get_history() behaves EXACTLY as before
        # (direct Polygon fetch into the per-process in-memory cache). Phase 2
        # flips USE_SHARED_CACHE per-experiment.
        self._use_shared = _env_flag("USE_SHARED_CACHE")
        self._shared_cache = shared_cache          # injectable (tests / DI)
        self._shared_init_failed = False
        self._refreshing: set = set()              # in-process single-flight
        self._refresh_lock = threading.Lock()
        # Bounded wait for a peer subprocess's in-flight fetch before a loser
        # falls back to a direct fetch (cross-process single-flight).
        self._shared_wait_secs = float(os.environ.get("SHARED_CACHE_WAIT_SECS", "5"))

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

        # Shared SQLite cache (Phase 1). Returns a full-window frame on hit, or
        # None to signal "fall back to a direct fetch" (flag off, miss, or any
        # cache error). Never raises for a cache problem — only a genuine
        # provider failure (raised by _fetch_full) propagates.
        data = None
        if self._use_shared:
            data = self._try_shared(ticker, polygon_ticker)
        if data is None:
            data = self._fetch_full(ticker, polygon_ticker)

        with self._lock:
            self._cache[key] = (data, time.time())
        return self._slice_to_period(data, period).copy()

    # ------------------------------------------------------------------
    # Direct provider fetch (the pre-Phase-1 behaviour, extracted)
    # ------------------------------------------------------------------

    def _fetch_full(self, ticker: str, polygon_ticker: str) -> pd.DataFrame:
        """Fetch the full ``_FETCH_CALENDAR_DAYS`` window directly from Polygon."""
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
        return data

    # ------------------------------------------------------------------
    # Shared-cache path (stale-while-revalidate + graceful fallback)
    # ------------------------------------------------------------------

    def _get_shared_cache(self):
        """Lazily build the SharedBarCache; None if it can't be created."""
        if self._shared_cache is not None:
            return self._shared_cache
        if self._shared_init_failed:
            return None
        try:
            from shared.shared_bar_cache import SharedBarCache
            self._shared_cache = SharedBarCache(fresh_ttl=self._ttl)
        except Exception as exc:
            logger.warning("Shared cache unavailable (%s) — using direct fetch", exc)
            self._shared_init_failed = True
            return None
        return self._shared_cache

    def _try_shared(self, ticker: str, polygon_ticker: str) -> Optional[pd.DataFrame]:
        """Serve from the shared cache with stale-while-revalidate semantics.

        Returns a full-window DataFrame, or None to tell the caller to fall back
        to a direct fetch. A genuine provider failure (on a MISS refetch) is
        allowed to propagate so behaviour matches the non-shared path.
        """
        from shared.shared_bar_cache import Freshness, SharedCacheError

        sc = self._get_shared_cache()
        if sc is None:
            return None

        try:
            res = sc.get_bars(ticker)
        except SharedCacheError as exc:
            logger.warning("Shared cache read failed for %s (%s) — direct fetch", ticker, exc)
            return None

        if res.status == Freshness.FRESH:
            metrics.inc("shared_cache_fresh")
            return res.df

        if res.status == Freshness.STALE:
            metrics.inc("shared_cache_stale")
            self._schedule_refresh(ticker, polygon_ticker)
            return res.df  # serve stale immediately; background thread revalidates

        # MISS — coordinate a single fetch across all subprocesses.
        metrics.inc("shared_cache_miss")
        return self._coordinated_fetch(ticker, polygon_ticker, sc)

    def _coordinated_fetch(self, ticker: str, polygon_ticker: str, sc):
        """Fetch a MISSing ticker under a cross-process single-flight lock.

        Only the lock winner calls Polygon and writes through. Losers wait a
        bounded time for the winner's result, then fall back to a direct fetch
        rather than block the scan.
        """
        from shared.shared_bar_cache import Freshness, SharedCacheError

        lock_key = ticker.upper()
        try:
            acquired = sc.try_acquire_fetch_lock(lock_key, ttl=30.0)
        except SharedCacheError as exc:
            logger.warning("Lock acquire failed for %s (%s) — direct fetch", ticker, exc)
            acquired = True  # lock table broken → just fetch (degrade gracefully)

        if acquired:
            try:
                # Double-check: a prior holder may have populated it between our
                # MISS read and winning the lock — avoids a redundant fetch.
                try:
                    res = sc.get_bars(ticker)
                    if res.status == Freshness.FRESH and res.df is not None:
                        return res.df
                except SharedCacheError:
                    pass
                data = self._fetch_full(ticker, polygon_ticker)
                try:
                    sc.put_bars(ticker, data)
                except SharedCacheError as exc:
                    logger.warning("Shared cache write failed for %s (%s)", ticker, exc)
                return data
            finally:
                try:
                    sc.release_fetch_lock(lock_key)
                except SharedCacheError:
                    pass

        # Another process is fetching — wait briefly for its write-through.
        deadline = time.monotonic() + self._shared_wait_secs
        while time.monotonic() < deadline:
            time.sleep(0.25)
            try:
                res = sc.get_bars(ticker)
            except SharedCacheError:
                break
            if res.status in (Freshness.FRESH, Freshness.STALE) and res.df is not None:
                metrics.inc("shared_cache_wait_hit")
                return res.df

        # Timed out waiting — fetch directly rather than stall the scan.
        metrics.inc("shared_cache_wait_timeout")
        return self._fetch_full(ticker, polygon_ticker)

    def _schedule_refresh(self, ticker: str, polygon_ticker: str) -> None:
        """Kick a background revalidation for a stale ticker (single-flight).

        In-process dedup uses a case-normalised key; cross-process dedup uses
        the shared advisory lock so only one subprocess refreshes.
        """
        norm = ticker.upper()
        with self._refresh_lock:
            if norm in self._refreshing:
                return
            self._refreshing.add(norm)

        def _run():
            from shared.shared_bar_cache import Freshness, SharedCacheError
            holds_lock = False
            sc = None
            try:
                sc = self._get_shared_cache()
                if sc is None:
                    return
                # Cross-process single-flight: only one subprocess refreshes.
                try:
                    holds_lock = sc.try_acquire_fetch_lock(norm, ttl=30.0)
                except SharedCacheError:
                    holds_lock = False
                if not holds_lock:
                    return  # another process owns the refresh
                # Re-check: it may have just been refreshed to FRESH.
                try:
                    if sc.get_bars(ticker).status == Freshness.FRESH:
                        return
                except SharedCacheError:
                    pass
                data = self._fetch_full(ticker, polygon_ticker)
                try:
                    sc.put_bars(ticker, data)
                except SharedCacheError as exc:
                    logger.warning("Background cache write failed for %s (%s)", ticker, exc)
                with self._lock:
                    self._cache[norm] = (data, time.time())
                logger.info("Shared cache background-refreshed %s", ticker)
            except Exception as exc:
                logger.warning("Background refresh failed for %s: %s", ticker, exc)
            finally:
                if holds_lock and sc is not None:
                    try:
                        sc.release_fetch_lock(norm)
                    except SharedCacheError:
                        pass
                with self._refresh_lock:
                    self._refreshing.discard(norm)

        threading.Thread(target=_run, name=f"bar-refresh-{norm}", daemon=True).start()

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
