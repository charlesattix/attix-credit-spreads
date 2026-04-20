"""
Build a MarketSnapshot from live market data for strategy consumption.

Mirrors the portfolio_backtester._build_market_snapshot() method so that
live paper trading uses the same data shapes as backtesting.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from shared.constants import get_risk_free_rate
from shared.indicators import calculate_iv_rank
from strategies.base import MarketSnapshot
from strategies.pricing import calculate_rsi

logger = logging.getLogger(__name__)


def build_live_snapshot(
    tickers: List[str],
    data_cache,
    options_analyzer=None,
) -> MarketSnapshot:
    """Build a MarketSnapshot from live market data.

    Args:
        tickers: List of ticker symbols to include.
        data_cache: DataCache instance for fetching price history.
        options_analyzer: Optional OptionsAnalyzer for IV data.

    Returns:
        MarketSnapshot populated with live data.
    """
    now = datetime.now(timezone.utc)

    price_data: Dict[str, pd.DataFrame] = {}
    prices: Dict[str, float] = {}
    open_prices: Dict[str, float] = {}
    gaps: Dict[str, float] = {}
    iv_rank_map: Dict[str, float] = {}
    realized_vol: Dict[str, float] = {}
    rsi_map: Dict[str, float] = {}

    # Fetch VIX for IV rank calculation — None means unavailable
    vix_val = None
    vix_history: Optional[pd.Series] = None
    global_iv_rank = None
    try:
        vix_df = data_cache.get_history("^VIX", period="1y")
        if not vix_df.empty:
            if isinstance(vix_df.columns, pd.MultiIndex):
                vix_df.columns = vix_df.columns.get_level_values(0)
            vix_close = vix_df["Close"].dropna()
            if not vix_close.empty:
                vix_val = float(vix_close.iloc[-1])
                vix_history = vix_close

                # Compute global IV rank from VIX history (same as backtester)
                window = vix_close.tail(252)
                if len(window) >= 20:
                    ivr_result = calculate_iv_rank(window, vix_val)
                    global_iv_rank = ivr_result["iv_rank"]
                else:
                    global_iv_rank = 25.0
    except Exception as e:
        logger.warning("Failed to fetch VIX data: %s", e)
    if vix_val is None:
        logger.warning("VIX data unavailable — vix=None, iv_rank=None; trading should be blocked")

    for ticker in tickers:
        if ticker == "^VIX":
            continue

        try:
            df = data_cache.get_history(ticker, period="1y")
            if df.empty:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            price_data[ticker] = df
            prices[ticker] = float(df["Close"].iloc[-1])

            # Open price
            if "Open" in df.columns:
                open_prices[ticker] = float(df["Open"].iloc[-1])

            # Gap detection
            if len(df) >= 2 and "Open" in df.columns:
                today_open = float(df["Open"].iloc[-1])
                prev_close = float(df["Close"].iloc[-2])
                if prev_close > 0:
                    gap_pct = (today_open - prev_close) / prev_close
                    if abs(gap_pct) >= 0.005:  # 0.5% threshold
                        gaps[ticker] = gap_pct

            # IV rank — use global VIX-based rank (same as backtester)
            iv_rank_map[ticker] = global_iv_rank

            # Realized vol: ATR(20) / Close * sqrt(252), clipped [0.10, 1.00]
            # Same formula as portfolio_backtester._build_realized_vol_series
            if all(col in df.columns for col in ("High", "Low", "Close")):
                high = df["High"]
                low = df["Low"]
                close = df["Close"]
                prev_close_s = close.shift(1)
                tr = pd.concat(
                    [high - low, (high - prev_close_s).abs(), (low - prev_close_s).abs()],
                    axis=1,
                ).max(axis=1)
                atr20 = tr.rolling(20, min_periods=5).mean()
                if not atr20.empty and close.iloc[-1] > 0:
                    rv = float(atr20.iloc[-1]) / float(close.iloc[-1]) * math.sqrt(252)
                    rv = max(0.10, min(1.00, rv))
                    realized_vol[ticker] = rv
                else:
                    realized_vol[ticker] = 0.25
            else:
                realized_vol[ticker] = 0.25

            # RSI — 14-period (same as backtester)
            closes_list = df["Close"].tolist()
            rsi_map[ticker] = calculate_rsi(closes_list, period=14)

        except Exception as e:
            logger.warning("Failed to process %s for snapshot: %s", ticker, e)

    return MarketSnapshot(
        date=now,
        price_data=price_data,
        prices=prices,
        open_prices=open_prices,
        gaps=gaps,
        vix=vix_val,
        vix_history=vix_history,
        iv_rank=iv_rank_map,
        realized_vol=realized_vol,
        rsi=rsi_map,
        risk_free_rate=get_risk_free_rate(now),
        regime=None,
    )
