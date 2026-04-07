"""
Credit spread strategy — bull puts and bear calls.

Ported from backtest/backtester.py heuristic mode. Uses MA trend filter,
momentum filter, and Black-Scholes pricing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from shared.constants import DEFAULT_RISK_FREE_RATE
from strategies.base import (
    BaseStrategy,
    LegType,
    MarketSnapshot,
    ParamDef,
    PortfolioState,
    Position,
    PositionAction,
    Signal,
    TradeDirection,
    TradeLeg,
)
from strategies.pricing import (
    bs_price,
    estimate_spread_value,
    nearest_friday_expiration,
)

logger = logging.getLogger(__name__)

# Regime → direction mapping for regime_adaptive mode
DEFAULT_REGIME_DIRECTION_MAP = {
    "bull": "bull_put",
    "bear": "bear_call",
    "high_vol": "bull_put",
    "low_vol": "both",
    "crash": "skip",
}

# Position sizing multipliers per regime
REGIME_SIZE_SCALE = {
    "bull": 1.0,
    "bear": 0.8,
    "high_vol": 0.6,
    "low_vol": 1.0,
    "crash": 0.0,
}


class CreditSpreadStrategy(BaseStrategy):
    """Bull put / bear call credit spreads with MA trend filter.

    Optional EXP-1880 entry overlay
    -------------------------------
    Set ``strategy.entry_overlay = IntegratedEntryOverlay(...)`` (or build it
    via :func:`compass.exp1880_integrated_overlays.IntegratedEntryOverlay.from_config`)
    and toggle ``use_fomc_filter`` / ``use_pcr_filter`` in params to gate
    entries. Each filter is independently switchable. The overlay is queried
    once per ``generate_signals()`` call (i.e. once per scan day) and applies
    a size multiplier via ``signal.metadata['overlay_size_mult']``, which
    ``size_position()`` honours.
    """

    # EXP-1880 — optional entry overlay; default None preserves prior behaviour
    entry_overlay: object | None = None

    def _resolve_regime_direction(self, regime: str | None) -> str:
        """Map regime to trade direction, checking per-regime param overrides."""
        if regime is None:
            return "both"
        regime_lower = regime.lower()
        # Check per-regime param override (e.g. regime_dir_bull="both")
        override_key = f"regime_dir_{regime_lower}"
        override = self.params.get(override_key)
        if override is not None:
            return override
        return DEFAULT_REGIME_DIRECTION_MAP.get(regime_lower, "both")

    def _regime_size_scale(self, regime: str | None) -> float:
        """Get position size multiplier for the given regime.

        Reads per-regime overrides from params (e.g. regime_scale_bull=1.2),
        falling back to the module-level REGIME_SIZE_SCALE defaults.
        """
        if regime is None:
            return 1.0
        key = f"regime_scale_{regime.lower()}"
        return self._p(key, REGIME_SIZE_SCALE.get(regime.lower(), 1.0))

    def generate_signals(self, market_data: MarketSnapshot) -> List[Signal]:
        signals = []
        weekday = market_data.date.weekday()
        scan_day = self._p("scan_weekday", "monday")

        if scan_day == "monday" and weekday != 0:
            return []
        if scan_day == "mon_wed_fri" and weekday not in (0, 2, 4):
            return []

        direction = self._p("direction", "both")

        # Regime-adaptive: resolve direction from market regime
        regime = market_data.regime
        if direction == "regime_adaptive":
            direction = self._resolve_regime_direction(regime)
            if direction == "skip":
                return []

        for ticker, price in market_data.prices.items():
            if ticker.startswith("^"):
                continue

            df = market_data.price_data.get(ticker)
            if df is None or len(df) < 20:
                continue

            closes = df["Close"].values
            ma_period = self._p("trend_ma_period", 20)
            if len(closes) < ma_period:
                continue
            trend_ma = float(closes[-ma_period:].mean())

            # Momentum filter
            mom_filter = self._p("momentum_filter_pct", 5.0)
            lookback = min(10, len(closes) - 1)
            if lookback > 0:
                mom_pct = (price - float(closes[-lookback - 1])) / float(closes[-lookback - 1]) * 100
            else:
                mom_pct = 0.0

            iv = market_data.realized_vol.get(ticker, 0.20)

            dp = market_data.data_provider

            if direction in ("both", "bull_put") and price >= trend_ma:
                if mom_pct >= -abs(mom_filter):
                    sig = self._build_spread(
                        ticker, price, iv, market_data.date, "bull_put",
                        regime=regime, data_provider=dp,
                    )
                    if sig:
                        signals.append(sig)

            if direction in ("both", "bear_call") and price <= trend_ma:
                sig = self._build_spread(
                    ticker, price, iv, market_data.date, "bear_call",
                    regime=regime, data_provider=dp,
                )
                if sig:
                    signals.append(sig)

        # Low-credit + high-VIX-percentile entry filter.
        # Trades with net_credit < min_credit_filter AND VIX percentile >= vix_pctile_gate
        # have a disproportionate loss rate.  Skip them.
        min_credit = self._p("min_credit_filter", 0.0)
        vix_pctile_gate = self._p("vix_pctile_gate", 100)
        if min_credit > 0 and vix_pctile_gate < 100:
            vix_pctile = self._compute_vix_percentile(market_data, window=50)
            if vix_pctile is not None and vix_pctile >= vix_pctile_gate:
                signals = [s for s in signals if s.net_credit >= min_credit]

        # EXP-1880 — sentiment + put/call entry overlay (Wave-3 integration).
        # Each filter is independently switchable via params. If the overlay
        # blocks the day, drop all signals. If it allows but suggests a size
        # multiplier, stash it on every signal so size_position() picks it up.
        use_fomc = bool(self._p("use_fomc_filter", False))
        use_pcr = bool(self._p("use_pcr_filter", False))
        if (use_fomc or use_pcr) and signals and self.entry_overlay is not None:
            decision = self.entry_overlay.allow_entry(market_data.date)
            if not decision.allow:
                logger.debug(
                    "EXP-1880 overlay blocked %s on %s by %s",
                    self.name, market_data.date, decision.blocked_by,
                )
                return []
            if decision.size_mult != 1.0:
                for s in signals:
                    s.metadata["overlay_size_mult"] = decision.size_mult

        return signals

    @staticmethod
    def _compute_vix_percentile(market_data: MarketSnapshot, window: int = 50) -> float | None:
        """Compute current VIX percentile rank over trailing window days."""
        if market_data.vix_history is None or len(market_data.vix_history) < 10:
            return None
        hist = market_data.vix_history.tail(window)
        current = market_data.vix
        if current is None or len(hist) == 0:
            return None
        return float((hist < current).sum() / len(hist) * 100)

    def _build_spread(
        self, ticker: str, price: float, iv: float,
        date: datetime, spread_type: str,
        regime: str | None = None,
        data_provider=None,
    ) -> Signal | None:
        target_dte = self._p("target_dte", 35)
        min_dte = self._p("min_dte", 25)
        otm_pct = self._p("otm_pct", 0.05)
        spread_width = self._p("spread_width", 10.0)
        credit_fraction = self._p("credit_fraction", 0.35)
        slippage = 0.05

        expiration = nearest_friday_expiration(date, target_dte, min_dte)
        dte = (expiration - date).days
        T = dte / 365.0

        if spread_type == "bull_put":
            short_strike = round(price * (1 - otm_pct), 0)
            long_strike = short_strike - spread_width
            short_leg_type = LegType.SHORT_PUT
            long_leg_type = LegType.LONG_PUT
            opt_type = "P"
        else:
            short_strike = round(price * (1 + otm_pct), 0)
            long_strike = short_strike + spread_width
            short_leg_type = LegType.SHORT_CALL
            long_leg_type = LegType.LONG_CALL
            opt_type = "C"

        # Use DataProvider for real fills when available; fall back to BS
        if data_provider is not None:
            sp = data_provider.get_spread_prices(
                ticker, expiration, short_strike, long_strike, opt_type, date
            )
            if sp.valid:
                short_price = sp.short_price
                long_price = sp.long_price
                credit = sp.net_credit - sp.slippage
            else:
                return None
        else:
            short_price = bs_price(price, short_strike, T, DEFAULT_RISK_FREE_RATE, iv, opt_type)
            long_price = bs_price(price, long_strike, T, DEFAULT_RISK_FREE_RATE, iv, opt_type)
            credit = short_price - long_price

            # Fallback: use heuristic credit if BS gives unreasonable result
            min_credit = spread_width * 0.10
            if credit < min_credit:
                credit = spread_width * credit_fraction

            credit -= slippage

        if credit <= 0:
            return None

        max_loss = spread_width - credit
        if max_loss <= 0:
            return None

        legs = [
            TradeLeg(short_leg_type, short_strike, expiration, entry_price=short_price),
            TradeLeg(long_leg_type, long_strike, expiration, entry_price=long_price),
        ]

        return Signal(
            strategy_name=self.name,
            ticker=ticker,
            direction=TradeDirection.SHORT,
            legs=legs,
            net_credit=credit,
            max_loss=max_loss,
            max_profit=credit,
            profit_target_pct=self._p("profit_target_pct", 0.50),
            stop_loss_pct=self._p("stop_loss_multiplier", 2.5),
            score=50.0,
            signal_date=date,
            expiration=expiration,
            dte=dte,
            metadata={
                "spread_type": spread_type,
                "short_strike": short_strike,
                "long_strike": long_strike,
                "iv": iv,
                "regime": regime,
            },
        )

    def manage_position(
        self, position: Position, market_data: MarketSnapshot,
    ) -> PositionAction:
        # Expiration check
        if position.legs and market_data.date >= position.legs[0].expiration:
            return PositionAction.CLOSE_EXPIRY

        # Time-based exit: close trades held longer than max_hold_days.
        # Trades alive >15 days are 47% losers vs 13% for shorter holds.
        max_hold = self._p("max_hold_days", 0)
        if max_hold > 0 and position.entry_date:
            held = (market_data.date - position.entry_date).days
            if held >= max_hold:
                return PositionAction.CLOSE_TIME

        # DTE management
        manage_dte = self._p("manage_dte", 0)
        if manage_dte > 0 and position.legs:
            dte = (position.legs[0].expiration - market_data.date).days
            if dte <= manage_dte:
                return PositionAction.CLOSE_DTE

        ticker = position.ticker
        price = market_data.prices.get(ticker)
        if price is None:
            return PositionAction.HOLD

        iv = market_data.realized_vol.get(ticker, 0.20)
        spread_value = estimate_spread_value(position, price, iv, market_data.date)
        # For credit spreads, spread_value is negative (we're short).
        # Cost to close = -spread_value (what we'd pay to buy back)
        cost_to_close = -spread_value

        credit = position.net_credit

        # Profit target: spread has decayed enough
        profit_captured = credit - cost_to_close
        if profit_captured >= credit * position.profit_target_pct:
            return PositionAction.CLOSE_PROFIT

        # Stop loss: spread has moved against us
        loss = cost_to_close - credit
        if loss >= credit * position.stop_loss_pct:
            return PositionAction.CLOSE_STOP

        # Spread-width 90% safety cap — close if cost to close approaches max loss
        if len(position.legs) >= 2:
            spread_width = abs(position.legs[0].strike - position.legs[1].strike)
            if spread_width > 0 and cost_to_close >= spread_width * 0.90:
                return PositionAction.CLOSE_STOP

        return PositionAction.HOLD

    def size_position(
        self, signal: Signal, portfolio_state: PortfolioState,
    ) -> int:
        max_risk_pct = self._p("max_risk_pct", 0.02)
        risk_budget = portfolio_state.equity * max_risk_pct

        # Regime-based sizing adjustment
        regime = signal.metadata.get("regime") if signal.metadata else None
        if self._p("direction", "both") == "regime_adaptive":
            scale = self._regime_size_scale(regime)
            if scale <= 0:
                return 0
            risk_budget *= scale

        risk_per_unit = signal.max_loss * 100  # per contract (100 shares)
        if risk_per_unit <= 0:
            return 0

        # Heat cap check
        if portfolio_state.total_risk >= portfolio_state.equity * portfolio_state.max_portfolio_risk_pct:
            return 0

        contracts = max(1, int(risk_budget / risk_per_unit))

        # EXP-1880 overlay size modifier (set in generate_signals)
        overlay_mult = float(signal.metadata.get("overlay_size_mult", 1.0)) if signal.metadata else 1.0
        if overlay_mult != 1.0:
            contracts = max(1, int(round(contracts * overlay_mult)))

        return min(contracts, 10)

    @classmethod
    def get_param_space(cls) -> List[ParamDef]:
        return [
            ParamDef("direction", "choice", "both", choices=["both", "bull_put", "bear_call", "regime_adaptive"]),
            ParamDef("trend_ma_period", "int", 20, low=10, high=100, step=5),
            ParamDef("target_dte", "int", 35, low=14, high=60, step=5),
            ParamDef("min_dte", "int", 25, low=7, high=45, step=5),
            ParamDef("otm_pct", "float", 0.05, low=0.02, high=0.15, step=0.01),
            ParamDef("spread_width", "float", 10.0, low=2.0, high=20.0, step=1.0),
            ParamDef("credit_fraction", "float", 0.35, low=0.15, high=0.50, step=0.05),
            ParamDef("profit_target_pct", "float", 0.50, low=0.25, high=0.80, step=0.05),
            ParamDef("stop_loss_multiplier", "float", 2.5, low=1.5, high=4.0, step=0.25),
            ParamDef("momentum_filter_pct", "float", 5.0, low=2.0, high=10.0, step=1.0),
            ParamDef("scan_weekday", "choice", "monday", choices=["monday", "any", "mon_wed_fri"]),
            ParamDef("max_risk_pct", "float", 0.02, low=0.005, high=0.05, step=0.005),
            # Enhanced filters (from CS loser profiling analysis)
            ParamDef("max_hold_days", "int", 0, low=0, high=30, step=1,
                     description="Close trades held longer than this (0=disabled). Trades >15 days are 47% losers."),
            ParamDef("min_credit_filter", "float", 0.0, low=0.0, high=3.0, step=0.1,
                     description="Minimum net credit to accept (0=disabled). Combined with vix_pctile_gate."),
            ParamDef("vix_pctile_gate", "int", 100, low=0, high=100, step=5,
                     description="Skip low-credit trades when VIX 50d percentile >= this (100=disabled)."),
            # EXP-1880: integrated FOMC + PCR entry overlays (require entry_overlay attached)
            ParamDef("use_fomc_filter", "bool", False, description="Enable EXP-1740 FOMC hawkish + VIX-slope entry filter."),
            ParamDef("use_pcr_filter",  "bool", False, description="Enable EXP-1750 put/call ratio + VIX inversion entry filter."),
        ]
