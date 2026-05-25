"""Shared backtest OHLCV loader (Polygon + SQLite indices bootstrap).

Replaces the legacy ``_yf_download_safe`` / ``_yf_history_safe`` curl-based
Yahoo helpers in ``backtest.backtester``. Routes:

* stocks/ETFs (SPY, TLT, QQQ, IWM, XLK, etc.) — Polygon stock aggregates
* index tickers (^VIX, ^VIX3M, ^GSPC, ^DJI, ^IXIC) — Polygon index aggregates
  for dates ≥ 2023-02-14; the ``historical_indices`` SQLite table populated
  by ``scripts/bootstrap_indices_history.py`` for dates before that boundary.

Return shape matches what ``yfinance.download(...)`` produced so existing
callers (the MultiIndex-flatten shim, ``Close.dropna()`` chains, etc.) keep
working as inert no-ops.

Public surface::

    load_market_history(ticker, start, end) -> pd.DataFrame

Architectural rule (BACKTEST_MIGRATION_PROPOSAL.md §4.3): all Polygon HTTP
goes through ``shared.polygon_client.PolygonClient``. No parallel client.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from shared.data_cache import _INDEX_TICKER_MAP
from shared.exceptions import DataFetchError
from shared.polygon_client import PolygonClient

logger = logging.getLogger(__name__)


def _polygon_to_dataframe(results: list) -> pd.DataFrame:
    """Convert Polygon aggregate results to a yfinance-shaped DataFrame.

    Output columns: Open, High, Low, Close, Volume.
    Index: timezone-naive DatetimeIndex (date-only), sorted ascending.
    """
    if not results:
        return pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume"],
            index=pd.DatetimeIndex([], name="Date"),
        )

    df = pd.DataFrame(results)
    if "v" not in df.columns:
        df["v"] = 0
    df["Date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(None).dt.normalize()
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]
    df = df.set_index("Date").sort_index()
    return df

# First date Polygon provides for I:VIX / I:VIX3M / I:SPX daily aggregates.
# Strictly before this boundary we read from SQLite; on/after we read Polygon.
_POLYGON_INDICES_START = date(2023, 2, 14)

# SQLite bootstrap populated by scripts/bootstrap_indices_history.py.
_BOOTSTRAP_DB = Path(__file__).resolve().parent.parent / "data" / "historical_indices.sqlite"

# Module-level client (lazy) — single PolygonClient instance shared by all
# callers in this process, so HTTP sessions/connection pools are reused.
_client: Optional[PolygonClient] = None


def _get_client() -> PolygonClient:
    global _client
    if _client is None:
        _client = PolygonClient()
    return _client


# Allow tests to inject a fake/mock client.
def _set_client(client: Optional[PolygonClient]) -> None:
    """Test hook: replace the module-level PolygonClient. ``None`` resets."""
    global _client
    _client = client
    _cached_load.cache_clear()


_INDEX_PREFIX = "I:"
_EMPTY_DF = pd.DataFrame(
    columns=["Open", "High", "Low", "Close", "Volume"],
    index=pd.DatetimeIndex([], name="Date"),
)


def _to_date(value: Union[str, datetime, date]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # ISO string — accept either "YYYY-MM-DD" or full datetime
    return datetime.fromisoformat(str(value)[:10]).date()


def _normalize(ticker: str) -> str:
    """Map yfinance-style symbols (``^VIX``) to Polygon canonical (``I:VIX``)."""
    if ticker in _INDEX_TICKER_MAP:
        return _INDEX_TICKER_MAP[ticker]
    upper = ticker.upper()
    if upper in _INDEX_TICKER_MAP:
        return _INDEX_TICKER_MAP[upper]
    return ticker


def _load_sqlite_indices(
    polygon_ticker: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Return the slice of ``historical_indices`` covering [start, end] (inclusive)."""
    if not _BOOTSTRAP_DB.exists():
        logger.warning(
            "historical_indices.sqlite missing at %s — index pre-2023 data unavailable",
            _BOOTSTRAP_DB,
        )
        return _EMPTY_DF.copy()

    conn = sqlite3.connect(_BOOTSTRAP_DB)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM historical_indices "
            "WHERE ticker = ? AND date >= ? AND date <= ? "
            "ORDER BY date ASC",
            (polygon_ticker, start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return _EMPTY_DF.copy()

    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df


def _load_polygon(
    polygon_ticker: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Pull daily aggregates from Polygon via the shared PolygonClient."""
    client = _get_client()
    try:
        results = client.aggregates(
            ticker=polygon_ticker,
            multiplier=1,
            timespan="day",
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        )
    except DataFetchError:
        raise
    except Exception as e:  # defensive — client should already wrap
        raise DataFetchError(
            f"Polygon aggregates failed for {polygon_ticker} ({start}..{end}): {e}"
        ) from e
    return _polygon_to_dataframe(results)


def _is_index(polygon_ticker: str) -> bool:
    return polygon_ticker.upper().startswith(_INDEX_PREFIX)


def load_market_history(
    ticker: str,
    start: Union[str, datetime, date],
    end: Union[str, datetime, date],
) -> pd.DataFrame:
    """Return daily OHLCV bars in yfinance-shaped format.

    Columns: ``['Open', 'High', 'Low', 'Close', 'Volume']``
    Index:   tz-naive ``DatetimeIndex`` (date-only), sorted ascending.

    The returned slice is inclusive on both ends. Empty DataFrame on no data
    (matches the legacy ``_yf_download_safe`` "no rows" contract — callers
    already gate on ``.empty``). Raises ``DataFetchError`` on transport failure.

    Symbol normalization (``^VIX``→``I:VIX`` etc.) is applied via
    ``shared.data_cache._INDEX_TICKER_MAP``.

    For index tickers, rows strictly before 2023-02-14 are served from the
    ``historical_indices`` SQLite table (populated by
    ``scripts/bootstrap_indices_history.py``); rows on/after are served from
    Polygon. The two sources are concatenated and de-duplicated by date
    (Polygon takes precedence on the seam if both return the same date).
    """
    start_d = _to_date(start)
    end_d = _to_date(end)
    if end_d < start_d:
        return _EMPTY_DF.copy()

    polygon_ticker = _normalize(ticker)
    return _cached_load(polygon_ticker, start_d.isoformat(), end_d.isoformat()).copy()


@lru_cache(maxsize=128)
def _cached_load(polygon_ticker: str, start_iso: str, end_iso: str) -> pd.DataFrame:
    """Process-local cache keyed on the normalized request.

    The optimizer hits this hundreds of times per grid search with identical
    arguments; without the cache the API gets hammered. ``load_market_history``
    .copy()'s the cached frame so callers can mutate freely.
    """
    start_d = date.fromisoformat(start_iso)
    end_d = date.fromisoformat(end_iso)

    if _is_index(polygon_ticker):
        return _load_indices_hybrid(polygon_ticker, start_d, end_d)
    return _load_stock(polygon_ticker, start_d, end_d)


def _load_stock(polygon_ticker: str, start_d: date, end_d: date) -> pd.DataFrame:
    df = _load_polygon(polygon_ticker, start_d, end_d)
    return df


def _nyse_trading_days(start_d: date, end_d: date) -> Optional[pd.DatetimeIndex]:
    """Return the set of NYSE trading days in [start_d, end_d] from SPY.

    SPY's Polygon daily aggregates ARE the NYSE calendar by construction —
    SPY only prints on trading days. Using SPY as the calendar source means
    we don't carry a static holiday list (Juneteenth, Carter day of mourning,
    future holidays all handled automatically).

    Returns ``None`` if SPY data is unavailable for the range (caller should
    fall back to unfiltered data rather than dropping everything).
    """
    try:
        spy = _load_polygon("SPY", start_d, end_d)
    except DataFetchError as exc:
        logger.warning("NYSE calendar fetch (SPY) failed for %s..%s: %s",
                       start_d, end_d, exc)
        return None
    if spy.empty:
        return None
    return spy.index


def _load_indices_hybrid(polygon_ticker: str, start_d: date, end_d: date) -> pd.DataFrame:
    """Concatenate SQLite (pre-2023-02-14) and Polygon (2023-02-14+) slices.

    Polygon publishes I:VIX / I:VIX3M / I:SPX values on some US market
    holidays (Juneteenth, July 4, Labor Day, Thanksgiving, MLK Day, etc.)
    where Yahoo and the equity calendar do not. Those extra bars break
    joins against SPY/TLT-driven backtests. We filter the Polygon slice
    to the SPY trading calendar so the resulting index matches what
    backtester loops actually iterate. SQLite was sourced from Yahoo so
    it already follows the NYSE calendar — no filtering needed there.
    """
    sqlite_frame = _EMPTY_DF.copy()
    polygon_frame = _EMPTY_DF.copy()

    if start_d < _POLYGON_INDICES_START:
        # SQLite covers up to and including 2023-02-13
        from datetime import timedelta
        sqlite_end = min(end_d, _POLYGON_INDICES_START - timedelta(days=1))
        sqlite_frame = _load_sqlite_indices(polygon_ticker, start_d, sqlite_end)

    if end_d >= _POLYGON_INDICES_START:
        poly_start = max(start_d, _POLYGON_INDICES_START)
        polygon_frame = _load_polygon(polygon_ticker, poly_start, end_d)
        if not polygon_frame.empty:
            calendar = _nyse_trading_days(poly_start, end_d)
            if calendar is not None:
                polygon_frame = polygon_frame.reindex(
                    polygon_frame.index.intersection(calendar)
                )

    if sqlite_frame.empty:
        return polygon_frame
    if polygon_frame.empty:
        return sqlite_frame

    # Concatenate. If both sources somehow report the same date, Polygon wins.
    combined = pd.concat([sqlite_frame, polygon_frame])
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    return combined
