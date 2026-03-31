"""
Adapter layer between strategy system types (Signal, Position) and paper
trader dict-based types.

Allows paper_trader.py to consume signals from strategies/*.py without
rewriting its safety infrastructure.
"""

import logging
from datetime import datetime, timezone
from typing import Dict

from strategies.base import (
    LegType,
    Position,
    Signal,
    TradeDirection,
    TradeLeg,
)

logger = logging.getLogger(__name__)


def signal_to_opportunity(signal: Signal, current_price: float) -> Dict:
    """Convert a strategy Signal → opportunity dict for paper_trader.execute_signals().

    Maps Signal fields to the dict keys paper_trader expects.
    """
    # Determine spread type string from legs
    leg_types = {leg.leg_type for leg in signal.legs}

    is_condor = (
        LegType.SHORT_PUT in leg_types
        and LegType.SHORT_CALL in leg_types
    )

    is_straddle_strangle = (
        (LegType.LONG_CALL in leg_types and LegType.LONG_PUT in leg_types
         and LegType.SHORT_CALL not in leg_types)  # long straddle/strangle
        or (LegType.SHORT_CALL in leg_types and LegType.SHORT_PUT in leg_types
            and LegType.LONG_CALL not in leg_types)  # short straddle/strangle
    )

    if is_condor and not is_straddle_strangle:
        spread_type = "iron_condor"
    elif is_straddle_strangle:
        spread_type = signal.metadata.get("spread_type", "short_straddle")
    elif LegType.SHORT_PUT in leg_types:
        spread_type = "bull_put_spread"
    elif LegType.SHORT_CALL in leg_types:
        spread_type = "bear_call_spread"
    elif LegType.LONG_PUT in leg_types and LegType.SHORT_PUT not in leg_types:
        spread_type = "protective_put"
    else:
        spread_type = signal.metadata.get("spread_type", "credit_spread")

    # Extract strikes
    short_strike = 0.0
    long_strike = 0.0
    call_strike = 0.0
    put_strike = 0.0

    if is_straddle_strangle:
        # Both legs face same direction — store as call_strike / put_strike
        for leg in signal.legs:
            if "call" in leg.leg_type.value:
                call_strike = leg.strike
            elif "put" in leg.leg_type.value:
                put_strike = leg.strike
    elif spread_type == "protective_put":
        # Single long put — no short leg
        for leg in signal.legs:
            if leg.leg_type == LegType.LONG_PUT:
                long_strike = leg.strike
    elif is_condor:
        # For iron condors, put side is the "primary" short/long
        for leg in signal.legs:
            if leg.leg_type == LegType.SHORT_PUT:
                short_strike = leg.strike
            elif leg.leg_type == LegType.LONG_PUT:
                long_strike = leg.strike
    else:
        for leg in signal.legs:
            if "short" in leg.leg_type.value:
                short_strike = leg.strike
            elif "long" in leg.leg_type.value:
                long_strike = leg.strike

    # Compute spread_width: width of the short/long strike pair.
    # For straddle/strangle and protective_put there is no defined spread width;
    # fall back to metadata or 0 so alert_generator never receives a missing key.
    if is_straddle_strangle or spread_type == "protective_put":
        _spread_width = float(signal.metadata.get("spread_width", 0))
    else:
        _spread_width = abs(short_strike - long_strike)

    opp: Dict = {
        "ticker": signal.ticker,
        "type": spread_type,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "spread_width": _spread_width,
        "expiration": signal.expiration.strftime("%Y-%m-%d") if signal.expiration else "",
        "credit": signal.net_credit,
        "max_loss": signal.max_loss,
        "dte": signal.dte,
        "score": signal.score,
        "pop": signal.metadata.get("pop", 0),
        "short_delta": signal.metadata.get("short_delta", 0),
        "current_price": current_price,
        # Per-trade exit params — carried through to paper trader
        "profit_target_pct": signal.profit_target_pct,
        "stop_loss_pct": signal.stop_loss_pct,
        # Strategy name for routing to correct strategy instance
        "strategy_name": signal.strategy_name,
    }

    # Iron condor call-side fields
    if is_condor and not is_straddle_strangle:
        for leg in signal.legs:
            if leg.leg_type == LegType.SHORT_CALL:
                opp["call_short_strike"] = leg.strike
            elif leg.leg_type == LegType.LONG_CALL:
                opp["call_long_strike"] = leg.strike

        opp["put_credit"] = signal.metadata.get("put_credit", 0)
        opp["call_credit"] = signal.metadata.get("call_credit", 0)

    # Straddle/strangle fields
    if is_straddle_strangle:
        opp["call_strike"] = call_strike
        opp["put_strike"] = put_strike
        is_long = (LegType.LONG_CALL in leg_types)
        opp["is_debit"] = is_long
        if is_long:
            # Debit position — credit field carries negative net_credit
            opp["credit"] = signal.net_credit  # negative for debit

    # IV rank if available in metadata
    if "iv_rank" in signal.metadata:
        opp["iv_rank"] = signal.metadata["iv_rank"]

    return opp


def trade_dict_to_position(trade: Dict) -> Position:
    """Convert a paper trader trade dict → Position for strategy.manage_position().

    Builds TradeLeg objects from the trade dict's strike/expiration fields.
    """
    spread_type = (trade.get("type") or "").lower()
    ticker = trade.get("ticker", "")

    # Parse expiration
    exp_str = str(trade.get("expiration", ""))
    exp_str = exp_str.split(" ")[0] if " " in exp_str else exp_str
    try:
        expiration = datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            expiration = datetime.fromisoformat(exp_str)
            if expiration.tzinfo is None:
                expiration = expiration.replace(tzinfo=timezone.utc)
        except ValueError:
            expiration = datetime.now(timezone.utc)

    short_strike = trade.get("short_strike", 0)
    long_strike = trade.get("long_strike", 0)
    contracts = trade.get("contracts", 1)
    credit = trade.get("credit", 0)

    # Parse entry date (needed by all branches)
    entry_date = None
    entry_str = trade.get("entry_date")
    if entry_str:
        try:
            entry_date = datetime.fromisoformat(str(entry_str))
            if entry_date.tzinfo is None:
                entry_date = entry_date.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    legs = []

    if "straddle" in spread_type or "strangle" in spread_type:
        # Straddle/strangle: 2 legs, both same direction
        call_strike = trade.get("call_strike", 0)
        put_strike = trade.get("put_strike", 0)
        is_long = spread_type.startswith("long_")

        if is_long:
            legs = [
                TradeLeg(LegType.LONG_CALL, call_strike, expiration),
                TradeLeg(LegType.LONG_PUT, put_strike, expiration),
            ]
            direction = TradeDirection.LONG
        else:
            legs = [
                TradeLeg(LegType.SHORT_CALL, call_strike, expiration),
                TradeLeg(LegType.SHORT_PUT, put_strike, expiration),
            ]
            direction = TradeDirection.SHORT

        # max_loss: debit paid (long) or credit * 3 (short, matches strategy)
        if is_long:
            max_loss = abs(credit)
        else:
            max_loss = trade.get("max_loss_per_spread", credit * 3)

        return Position(
            id=trade.get("id", ""),
            strategy_name=trade.get("strategy_name", ""),
            ticker=ticker,
            direction=direction,
            legs=legs,
            contracts=contracts,
            entry_date=entry_date,
            net_credit=credit,
            max_loss_per_unit=max_loss,
            max_profit_per_unit=abs(credit) if is_long else credit,
            profit_target_pct=trade.get("profit_target_pct", 0.50),
            stop_loss_pct=trade.get("stop_loss_pct", 0.50),
        )

    if "condor" in spread_type:
        # Iron condor: 4 legs
        call_short = trade.get("call_short_strike", 0)
        call_long = trade.get("call_long_strike", 0)

        legs = [
            TradeLeg(LegType.SHORT_PUT, short_strike, expiration),
            TradeLeg(LegType.LONG_PUT, long_strike, expiration),
            TradeLeg(LegType.SHORT_CALL, call_short, expiration),
            TradeLeg(LegType.LONG_CALL, call_long, expiration),
        ]
        direction = TradeDirection.NEUTRAL
    elif "protective" in spread_type:
        # Protective put (tail hedge): single long put
        legs = [
            TradeLeg(LegType.LONG_PUT, long_strike, expiration),
        ]
        direction = TradeDirection.LONG
    elif "call" in spread_type:
        # Bear call spread
        legs = [
            TradeLeg(LegType.SHORT_CALL, short_strike, expiration),
            TradeLeg(LegType.LONG_CALL, long_strike, expiration),
        ]
        direction = TradeDirection.SHORT
    else:
        # Bull put spread (default)
        legs = [
            TradeLeg(LegType.SHORT_PUT, short_strike, expiration),
            TradeLeg(LegType.LONG_PUT, long_strike, expiration),
        ]
        direction = TradeDirection.SHORT

    spread_width = abs(short_strike - long_strike)
    max_loss = trade.get("max_loss_per_spread", spread_width - credit)

    return Position(
        id=trade.get("id", ""),
        strategy_name=trade.get("strategy_name", ""),
        ticker=ticker,
        direction=direction,
        legs=legs,
        contracts=contracts,
        entry_date=entry_date,
        net_credit=credit,
        max_loss_per_unit=max_loss,
        max_profit_per_unit=credit,
        profit_target_pct=trade.get("profit_target_pct", 0.50),
        stop_loss_pct=trade.get("stop_loss_pct", 2.5),
    )
