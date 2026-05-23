"""
scheduler/data_providers.py — Market data with Polygon->Alpaca->yfinance fallback chain.

Hierarchy per data type:
  ETF prices:  Polygon -> Alpaca data API -> yfinance -> stale cache
  VIX indices: Polygon (I:VIX*) -> yfinance -> stale cache -> conservative block

Every fallback logs DATA_FALLBACK at WARNING level.
On success, logs DATA_SOURCE: ticker=X source=L1_polygon.

Self-contained: no dependencies on the credit-spreads repo strategy/ module.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from shared.polygon_client import _pick_key

LOG = logging.getLogger("scheduler.data_providers")

# ── ETF tickers ─────────────────────────────────────────────────────────────
ETF_TICKERS = ["SPY", "QQQ", "XLF", "XLI", "GLD", "SLV"]

# ── VIX index mapping ────────────────────────────────────────────────────────
# Polygon uses "I:VIX" prefix for indices; yfinance uses "^VIX"
POLYGON_INDEX_MAP = {
    "VIX":   "I:VIX",
    "VIX3M": "I:VIX3M",
    "VVIX":  "I:VVIX",
}
YFINANCE_INDEX_MAP = {
    "VIX":   "^VIX",
    "VIX3M": "^VIX3M",
    "VVIX":  "^VVIX",
}

# ── Cache path (under /data volume) ──────────────────────────────────────────
_DATA_DIR   = Path(os.environ.get("COMPASS_DATA_DIR", "/data"))
_PRICE_CACHE = _DATA_DIR / "market_data_cache.json"
_CACHE_LOCK  = threading.Lock()


# ════════════════════════════════════════════════════════════════════════════
# Cache helpers
# ════════════════════════════════════════════════════════════════════════════

def _load_cache() -> dict:
    with _CACHE_LOCK:
        if _PRICE_CACHE.exists():
            try:
                return json.loads(_PRICE_CACHE.read_text())
            except Exception:
                return {}
        return {}


def _save_cache(cache: dict) -> None:
    with _CACHE_LOCK:
        _PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _PRICE_CACHE.write_text(json.dumps(cache, default=str))


def _cache_age_hours(cache: dict, key: str) -> float:
    """Return hours since a cache key was last written (inf if missing)."""
    ts_str = cache.get(f"{key}_ts")
    if not ts_str:
        return float("inf")
    try:
        ts = datetime.fromisoformat(ts_str)
        return (datetime.utcnow() - ts).total_seconds() / 3600
    except Exception:
        return float("inf")


# ════════════════════════════════════════════════════════════════════════════
# Level 1: Polygon
# ════════════════════════════════════════════════════════════════════════════

def _polygon_get_historical(ticker: str, days: int) -> Optional[pd.Series]:
    """Fetch daily close series from Polygon /v2/aggs. Returns pd.Series or None.

    Routes the API key via :func:`shared.polygon_client._pick_key` so
    ``I:`` index tickers use ``POLYGON_INDICES_API_KEY``.
    """
    import requests
    api_key = _pick_key(ticker)
    if not api_key:
        LOG.warning(
            "DATA_FALLBACK: ticker=%s level=L1_polygon reason='no api key for ticker'",
            ticker,
        )
        return None
    end   = datetime.utcnow()
    start = end - timedelta(days=days)
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
        f"/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": api_key}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None
        df = pd.DataFrame(results)
        df["date"] = pd.to_datetime(df["t"], unit="ms").dt.normalize()
        df = df.set_index("date")
        return df["c"].rename(ticker)
    except Exception as e:
        LOG.warning("DATA_FALLBACK: ticker=%s level=L1_polygon reason='%s'", ticker, e)
        return None


def _polygon_fetch_all(
    etf_tickers: list, index_map: dict, days: int
) -> Tuple[Dict[str, pd.Series], Dict[str, pd.Series]]:
    """Fetch ETFs + VIX indices from Polygon. Returns (etf_dict, index_dict).

    Per-ticker key routing happens inside :func:`_polygon_get_historical`.
    """
    etfs: Dict[str, pd.Series] = {}
    for t in etf_tickers:
        series = _polygon_get_historical(t, days)
        if series is not None and len(series) > 0:
            etfs[t] = series
            LOG.info("DATA_SOURCE: ticker=%s source=L1_polygon rows=%d", t, len(series))

    indices: Dict[str, pd.Series] = {}
    for key, poly_sym in index_map.items():
        series = _polygon_get_historical(poly_sym, days)
        if series is not None and len(series) > 0:
            # rename from "I:VIX" -> "VIX"
            indices[key] = series.rename(key)
            LOG.info("DATA_SOURCE: ticker=%s source=L1_polygon rows=%d", key, len(series))
        else:
            LOG.warning(
                "DATA_FALLBACK: ticker=%s level=L1_polygon reason='empty or missing'", key
            )

    return etfs, indices


# ════════════════════════════════════════════════════════════════════════════
# Level 2: Alpaca data API (ETFs only — no VIX)
# ════════════════════════════════════════════════════════════════════════════

def _alpaca_get_historical(
    tickers: list, days: int, api_key: str, api_secret: str
) -> Dict[str, pd.Series]:
    """Fetch ETF closes from Alpaca StockHistoricalDataClient."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)
        end   = datetime.utcnow()
        start = end - timedelta(days=days)

        req = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            adjustment="all",
        )
        bars = client.get_stock_bars(req)
        result: Dict[str, pd.Series] = {}
        for ticker in tickers:
            try:
                df = bars[ticker].df
                if df is not None and len(df) > 0:
                    series = df["close"].rename(ticker)
                    result[ticker] = series
                    LOG.info(
                        "DATA_SOURCE: ticker=%s source=L2_alpaca rows=%d", ticker, len(series)
                    )
            except Exception:
                pass
        return result
    except Exception as e:
        LOG.warning("DATA_FALLBACK: ticker=ETFs level=L2_alpaca reason='%s'", e)
        return {}


# ════════════════════════════════════════════════════════════════════════════
# Level 3: yfinance (ETFs + VIX indices — last resort before cache)
# ════════════════════════════════════════════════════════════════════════════

def _yfinance_get_historical(
    yf_symbols: list, key_map: dict, days: int
) -> Dict[str, pd.Series]:
    """
    Fetch closes from yfinance for given symbols.
    key_map: {canonical_key: yf_symbol}, e.g. {"VIX": "^VIX", "SPY": "SPY"}
    Returns {canonical_key: pd.Series}.

    This is a LAST RESORT. Always logs DATA_FALLBACK at WARNING.
    """
    import yfinance as yf
    end   = datetime.utcnow() + timedelta(days=1)
    start = datetime.utcnow() - timedelta(days=days)

    symbols = list(yf_symbols)
    result: Dict[str, pd.Series] = {}
    try:
        df = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=False,
        )
        if isinstance(df.columns, pd.MultiIndex):
            closes = df["Close"]
        else:
            closes = df[["Close"]] if "Close" in df.columns else df

        for canonical, yf_sym in key_map.items():
            col = yf_sym if yf_sym in closes.columns else (
                yf_sym.lstrip("^") if yf_sym.lstrip("^") in closes.columns else None
            )
            if col is None and len(symbols) == 1:
                col = closes.columns[0] if len(closes.columns) > 0 else None
            if col is not None:
                s = closes[col].dropna().rename(canonical)
                if len(s) > 0:
                    result[canonical] = s
                    LOG.warning(
                        "DATA_FALLBACK: ticker=%s level=L3_yfinance "
                        "reason='primary sources failed'", canonical
                    )
    except Exception as e:
        LOG.warning("DATA_FALLBACK: ticker=yfinance_batch level=L3 reason='%s'", e)

    return result


# ════════════════════════════════════════════════════════════════════════════
# Level 4: Stale cache
# ════════════════════════════════════════════════════════════════════════════

def _use_cached(
    cache: dict,
    missing_tickers: list,
    max_age_hours_etf: float = 48.0,
    max_age_hours_vix: float = 24.0,
) -> Tuple[Dict[str, float], list]:
    """
    Return last-known closes for missing tickers from cache.
    Returns (recovered: dict, still_missing: list).
    Logs DATA_FALLBACK for each cached value used.
    """
    recovered: Dict[str, float] = {}
    still_missing: List[str] = []
    for ticker in missing_tickers:
        cached_close = cache.get(f"{ticker}_close")
        if cached_close is not None:
            age = _cache_age_hours(cache, ticker)
            max_age = max_age_hours_vix if ticker in ("VIX", "VIX3M", "VVIX") else max_age_hours_etf
            if age <= max_age:
                recovered[ticker] = float(cached_close)
                LOG.warning(
                    "DATA_FALLBACK: ticker=%s level=L4_stale_cache "
                    "age_hours=%.1f max_allowed=%.1f", ticker, age, max_age
                )
            else:
                LOG.error(
                    "DATA_FALLBACK: ticker=%s level=L4_stale_cache EXPIRED "
                    "age_hours=%.1f > max %.1f — cannot use", ticker, age, max_age
                )
                still_missing.append(ticker)
        else:
            still_missing.append(ticker)
    return recovered, still_missing


# ════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════

def fetch_market_data(
    as_of: date,
    days: int = 180,
) -> Tuple[Dict[str, pd.Series], List[str]]:
    """
    Fetch market data with full fallback chain.

    Returns:
        (data_dict, alerts)
        data_dict: {ticker: pd.Series of closes} for all 9 tickers
        alerts: list of alert strings for tickers that fell to L3/L4 or failed

    Tickers in data_dict:
        ETFs:    SPY, QQQ, XLF, XLI, GLD, SLV
        Indices: VIX, VIX3M, VVIX
    """
    polygon_stocks_key  = os.environ.get("POLYGON_API_KEY", "")
    polygon_indices_key = os.environ.get("POLYGON_INDICES_API_KEY", "")
    # Generic ALPACA_API_KEY retired 2026-05-23 (P0-4). The L2 Alpaca data
    # fallback below is therefore disabled — we go Polygon → yfinance → cache.

    data: Dict[str, pd.Series] = {}
    alerts: List[str] = []
    cache = _load_cache()

    # ── Level 1: Polygon (all tickers; per-ticker key routing) ──────────
    if polygon_stocks_key or polygon_indices_key:
        if not polygon_stocks_key:
            LOG.warning("POLYGON_API_KEY not set — ETF fetch will fail")
        if not polygon_indices_key:
            LOG.warning("POLYGON_INDICES_API_KEY not set — VIX index fetch will fail")
        etfs_poly, indices_poly = _polygon_fetch_all(
            ETF_TICKERS, POLYGON_INDEX_MAP, days
        )
        data.update(etfs_poly)
        data.update(indices_poly)
    else:
        LOG.warning("POLYGON_API_KEY / POLYGON_INDICES_API_KEY not set — skipping L1")

    # ── Level 2: Alpaca data API — DISABLED (P0-4 cleanup, 2026-05-23) ───
    # The L2 Alpaca historical fallback relied on the generic ALPACA_API_KEY,
    # which is dead (Alpaca returns 401). L1 Polygon + L3 yfinance + L4 cache
    # provide adequate coverage; per-experiment keys are not used for shared
    # ETF data (identity-neutral fetch).

    # ── Level 3: yfinance (for anything still missing) ────────────────
    all_tickers = ETF_TICKERS + list(YFINANCE_INDEX_MAP.keys())
    missing_all = [t for t in all_tickers if t not in data]
    if missing_all:
        yf_map = {}
        for t in missing_all:
            if t in YFINANCE_INDEX_MAP:
                yf_map[t] = YFINANCE_INDEX_MAP[t]
            else:
                yf_map[t] = t  # ETF ticker == yfinance symbol
        yf_syms = list(yf_map.values())
        recovered_yf = _yfinance_get_historical(yf_syms, yf_map, days)
        data.update(recovered_yf)
        for t in missing_all:
            if t in recovered_yf:
                alerts.append(f"DATA_FALLBACK L3_yfinance: {t} — Polygon/Alpaca failed")

    # ── Level 4: Stale cache (last resort) ───────────────────────────────
    still_missing_final = [t for t in all_tickers if t not in data]
    if still_missing_final:
        cached_closes, unfixable = _use_cached(cache, still_missing_final)
        for t, close in cached_closes.items():
            # Reconstruct minimal series from cached scalar
            data[t] = pd.Series([close], name=t)
            alerts.append(f"DATA_FALLBACK L4_cache: {t}={close:.2f} (stale)")

        for t in unfixable:
            alerts.append(f"DATA_FAILURE: {t} unavailable from all sources")

    # ── Update cache with fresh data ──────────────────────────────────────
    now_str = datetime.utcnow().isoformat()
    for t, series in data.items():
        if series is not None and len(series) > 0:
            cache[f"{t}_close"] = float(series.iloc[-1])
            cache[f"{t}_ts"]    = now_str
    _save_cache(cache)

    return data, alerts


def get_spot_price(ticker: str) -> Optional[float]:
    """
    Get a single current spot price. Uses Polygon snapshot first, then yfinance.
    Used by pre_market_check and circuit_breaker_check for quick single-ticker queries.
    """
    polygon_key = _pick_key(ticker)

    # Level 1: Polygon snapshot
    if polygon_key:
        try:
            import requests
            resp = requests.get(
                f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
                params={"apiKey": polygon_key},
                timeout=10,
            )
            resp.raise_for_status()
            price = resp.json().get("ticker", {}).get("lastTrade", {}).get("p")
            if price:
                LOG.info("DATA_SOURCE: ticker=%s source=L1_polygon_snapshot price=%.2f", ticker, price)
                return float(price)
        except Exception as e:
            LOG.warning("DATA_FALLBACK: ticker=%s level=L1_polygon_snapshot reason='%s'", ticker, e)

    # Level 2 (stocks only): yfinance fast_info
    try:
        import yfinance as yf
        price = float(yf.Ticker(ticker).fast_info["last_price"])
        LOG.warning("DATA_FALLBACK: ticker=%s level=L3_yfinance source=fast_info", ticker)
        return price
    except Exception as e:
        LOG.warning("DATA_FALLBACK: ticker=%s level=L3_yfinance reason='%s'", ticker, e)

    return None


def get_vix_values() -> Tuple[Optional[float], Optional[float]]:
    """
    Get current VIX and VIX3M. Uses Polygon daily aggs first, then yfinance.
    Returns (vix, vix3m) — either may be None if all sources fail.
    """
    vix = vix3m = None

    # Level 1: Polygon daily aggs (index tickers — routed to POLYGON_INDICES_API_KEY)
    try:
        import requests
        today_str  = datetime.utcnow().strftime("%Y-%m-%d")
        start_str  = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%d")
        for name, sym in [("VIX", "I:VIX"), ("VIX3M", "I:VIX3M")]:
            api_key = _pick_key(sym)
            if not api_key:
                LOG.warning(
                    "DATA_FALLBACK: ticker=%s level=L1_polygon reason='POLYGON_INDICES_API_KEY not set'",
                    name,
                )
                continue
            resp = requests.get(
                f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/{start_str}/{today_str}",
                params={"adjusted": "true", "sort": "desc", "limit": 1, "apiKey": api_key},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                val = float(results[0]["c"])
                if name == "VIX":
                    vix = val
                    LOG.info("DATA_SOURCE: ticker=VIX source=L1_polygon value=%.2f", vix)
                else:
                    vix3m = val
                    LOG.info("DATA_SOURCE: ticker=VIX3M source=L1_polygon value=%.2f", vix3m)
    except Exception as e:
        LOG.warning("DATA_FALLBACK: VIX/VIX3M level=L1_polygon reason='%s'", e)

    # Level 2: yfinance fallback
    if vix is None:
        try:
            import yfinance as yf
            vix = float(yf.Ticker("^VIX").fast_info["last_price"])
            LOG.warning("DATA_FALLBACK: ticker=VIX level=L3_yfinance")
        except Exception as e:
            LOG.warning("DATA_FALLBACK: ticker=VIX level=L3_yfinance reason='%s'", e)

    if vix3m is None:
        try:
            import yfinance as yf
            vix3m = float(yf.Ticker("^VIX3M").fast_info.get("last_price", vix or 0))
            LOG.warning("DATA_FALLBACK: ticker=VIX3M level=L3_yfinance")
        except Exception as e:
            LOG.warning("DATA_FALLBACK: ticker=VIX3M level=L3_yfinance reason='%s'", e)

    return vix, vix3m
