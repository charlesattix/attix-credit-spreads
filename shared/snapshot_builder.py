"""
Build a MarketSnapshot from the data already fetched in main.py's
_analyze_ticker() — bridges OLD data sources to NEW strategy interface.

Also handles regime injection (Task 1.11) and economic calendar wiring (Task 1.6).
"""

import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from strategies.base import MarketSnapshot
from strategies.pricing import calculate_rsi
from shared.constants import get_risk_free_rate

logger = logging.getLogger(__name__)


def build_live_market_snapshot(
    ticker: str,
    price_data: pd.DataFrame,
    current_price: float,
    iv_data: Dict,
    technical_signals: Dict,
    regime: Optional[str] = None,
    vix_data: Optional[pd.DataFrame] = None,
    upcoming_events: Optional[List[Dict]] = None,
    recent_events: Optional[List[Dict]] = None,
) -> MarketSnapshot:
    """Convert per-ticker data from _analyze_ticker() into a MarketSnapshot.

    Args:
        ticker: Ticker symbol being analyzed.
        price_data: OHLCV DataFrame for the ticker.
        current_price: Latest close price.
        iv_data: Dict with iv_rank, iv_percentile, current_iv.
        technical_signals: Dict from TechnicalAnalyzer.analyze().
        regime: Current market regime string (e.g. 'bull', 'bear', 'neutral').
        vix_data: Optional VIX price DataFrame.
        upcoming_events: Economic events within lookahead window.
        recent_events: Economic events that recently occurred.

    Returns:
        MarketSnapshot ready for strategy.generate_signals().
    """
    now = datetime.now(timezone.utc)

    # Handle MultiIndex columns from yfinance
    if isinstance(price_data.columns, pd.MultiIndex):
        price_data = price_data.copy()
        price_data.columns = price_data.columns.get_level_values(0)

    # VIX — None means unavailable, strategies must handle it
    vix_val = None
    vix_history = None
    if vix_data is not None and not vix_data.empty:
        vix_df = vix_data
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df = vix_df.copy()
            vix_df.columns = vix_df.columns.get_level_values(0)
        vix_close = vix_df["Close"].dropna()
        if not vix_close.empty:
            vix_val = float(vix_close.iloc[-1])
            vix_history = vix_close
    if vix_val is None:
        logger.warning("VIX data unavailable for %s snapshot — vix=None", ticker)

    # Realized vol: ATR(20) / Close * sqrt(252)
    realized_vol_val = 0.25
    if all(col in price_data.columns for col in ("High", "Low", "Close")):
        high = price_data["High"]
        low = price_data["Low"]
        close = price_data["Close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr20 = tr.rolling(20, min_periods=5).mean()
        if not atr20.empty and close.iloc[-1] > 0:
            rv = float(atr20.iloc[-1]) / float(close.iloc[-1]) * math.sqrt(252)
            realized_vol_val = max(0.10, min(1.00, rv))

    # RSI
    closes_list = price_data["Close"].tolist()
    rsi_val = calculate_rsi(closes_list, period=14)

    # IV rank from iv_data
    iv_rank_val = iv_data.get("iv_rank", 25.0)

    # Normalize regime string (strategies expect lowercase)
    if regime:
        regime = regime.lower()
        # Map combo detector outputs to strategy regime names
        regime_map = {"bull": "bull", "bear": "bear", "neutral": "low_vol"}
        regime = regime_map.get(regime, regime)

    return MarketSnapshot(
        date=now,
        price_data={ticker: price_data},
        prices={ticker: current_price},
        vix=vix_val,
        vix_history=vix_history,
        iv_rank={ticker: iv_rank_val},
        realized_vol={ticker: realized_vol_val},
        rsi={ticker: rsi_val},
        upcoming_events=upcoming_events or [],
        recent_events=recent_events or [],
        risk_free_rate=get_risk_free_rate(now),
        regime=regime,
    )


def reprice_signals_from_chain(signals, options_chain, slippage: float = 0.05):
    """Re-price Signal objects using real options chain bid/ask data.

    When real chain data is available, overwrite BS-derived credit/max_loss
    with market prices for more accurate live trading.

    Args:
        signals: List of Signal objects from generate_signals().
        options_chain: DataFrame with columns: strike, option_type, bid, ask, expiration.
        slippage: Per-spread slippage deduction in dollars (default 0.05).

    Returns:
        Updated list of Signal objects (modified in place).
    """
    if options_chain is None or options_chain.empty:
        return signals

    for signal in signals:
        if not signal.legs or len(signal.legs) < 2:
            continue

        try:
            exp_str = signal.expiration.strftime("%Y-%m-%d") if signal.expiration else None
            if not exp_str:
                continue

            # Try to find matching chain data for this expiration
            chain_exp = options_chain[
                options_chain["expiration"].astype(str).str[:10] == exp_str
            ]
            if chain_exp.empty:
                continue

            # Re-price each leg from chain mid-price
            total_credit = 0.0
            all_priced = True
            for leg in signal.legs:
                opt_type = "put" if "put" in leg.leg_type.value else "call"
                matches = chain_exp[
                    (chain_exp["strike"] == leg.strike)
                    & (chain_exp["option_type"].str.lower() == opt_type)
                ]
                if matches.empty:
                    all_priced = False
                    break

                row = matches.iloc[0]
                bid = float(row.get("bid", 0))
                ask = float(row.get("ask", 0))
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0

                if mid <= 0:
                    all_priced = False
                    break

                # Short legs: we receive credit; Long legs: we pay
                if "short" in leg.leg_type.value:
                    total_credit += mid
                else:
                    total_credit -= mid

            if all_priced and total_credit > 0:
                spread_width = abs(signal.legs[0].strike - signal.legs[1].strike)
                signal.net_credit = round(total_credit - slippage, 4)
                signal.max_loss = round(spread_width - signal.net_credit, 4)
                signal.max_profit = signal.net_credit

        except Exception as e:
            logger.debug("reprice_signals_from_chain: skip signal %s: %s", signal.ticker, e)

    return signals
