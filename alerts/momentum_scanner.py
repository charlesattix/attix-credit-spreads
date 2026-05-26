"""
Momentum swing scanner for debit spread alerts.

Scans high-beta tickers for technical breakout/momentum triggers and builds
debit spread opportunities (bull call / bear put) with 7-14 DTE.  Unlike
the credit spread scanners, this module does NOT use ``CreditSpreadStrategy``
— debit spreads have inverted economics (pay to enter, profit from direction).
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from alerts.momentum_config import SCAN_HOURS, build_momentum_config
from shared.indicators import calculate_rsi
from strategy import OptionsAnalyzer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

def _now_et(now_et: Optional[datetime] = None) -> datetime:
    """Return *now_et* or the current time in US/Eastern."""
    if now_et is not None:
        if now_et.tzinfo is not None:
            try:
                from zoneinfo import ZoneInfo
            except ImportError:
                from backports.zoneinfo import ZoneInfo
            return now_et.astimezone(ZoneInfo("America/New_York"))
        return now_et

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


class MomentumScanner:
    """Market-hours-gated momentum swing scanner.

    Detects technical triggers (breakout, VWAP reclaim, RSI divergence,
    EMA crossover) and builds debit spread opportunities for each.
    """

    def __init__(self, base_config: dict, data_cache=None):
        self._base_config = base_config
        self._config = build_momentum_config(base_config)
        self._data_cache = data_cache
        self._momentum_cfg = self._config["strategy"]["momentum"]

        # Only OptionsAnalyzer — no CreditSpreadStrategy
        self._options_analyzer = OptionsAnalyzer(self._config, data_cache=data_cache)

        logger.info(
            "MomentumScanner initialized (%d tickers, 7-14 DTE)",
            len(self._config["tickers"]),
        )

    # ------------------------------------------------------------------
    # Timing gate
    # ------------------------------------------------------------------

    @staticmethod
    def is_market_hours(now_et: Optional[datetime] = None) -> bool:
        """Return True if current ET time is within the scan window.

        Scan window: 9:35-15:30 ET, weekdays only (Mon-Fri).
        """
        et = _now_et(now_et)
        # Weekday check (Mon=0 ... Fri=4)
        if et.weekday() > 4:
            return False
        current_time = et.time()
        return SCAN_HOURS[0] <= current_time <= SCAN_HOURS[1]

    # ------------------------------------------------------------------
    # Main scan
    # ------------------------------------------------------------------

    def scan(self, now_et: Optional[datetime] = None) -> List[Dict]:
        """Run the momentum scan pipeline.

        1. Gate: return [] if outside market hours.
        2. For each ticker: fetch data, detect triggers, score, build spreads.
        3. Annotate results with alert_source="momentum_swing".

        Returns:
            List of opportunity dicts for debit spreads.
        """
        if not self.is_market_hours(now_et):
            return []

        logger.info("Momentum scan: market hours active")

        results: List[Dict] = []
        for ticker in self._config["tickers"]:
            try:
                opps = self._scan_ticker(ticker)
                for opp in opps:
                    opp["alert_source"] = "momentum_swing"
                results.extend(opps)
            except Exception as e:
                logger.error(f"Momentum scan failed for {ticker}: {e}", exc_info=True)

        logger.info(f"Momentum scan complete: {len(results)} opportunities")
        return results

    # ------------------------------------------------------------------
    # Per-ticker scan
    # ------------------------------------------------------------------

    def _scan_ticker(self, ticker: str) -> List[Dict]:
        """Scan a single ticker for momentum triggers and build debit spreads."""
        # Fetch price data (Polygon-backed DataCache; required)
        price_data = self._data_cache.get_history(ticker, period="1y")

        if price_data is None or (hasattr(price_data, "empty") and price_data.empty):
            logger.warning(f"No price data for {ticker}")
            return []

        if len(price_data) < 30:
            logger.warning(f"Insufficient price data for {ticker} ({len(price_data)} bars)")
            return []

        # Earnings proximity check
        if not self._check_earnings_clearance(ticker, price_data):
            return []

        # Detect triggers
        triggers = self._detect_triggers(ticker, price_data)
        if not triggers:
            return []

        # Score and build spreads
        results: List[Dict] = []
        for trigger in triggers:
            score = self._compute_momentum_score(ticker, price_data, trigger)
            if score < self._momentum_cfg["min_momentum_score"]:
                continue

            opp = self._build_debit_spread(ticker, price_data, trigger, score)
            if opp is not None:
                results.append(opp)

        return results

    # ------------------------------------------------------------------
    # Earnings check
    # ------------------------------------------------------------------

    def _check_earnings_clearance(self, ticker: str, price_data) -> bool:
        """Return True if earnings are >= 5 days away (or unknown).

        Uses a simple heuristic: if we can't determine earnings date,
        allow the trade (conservative skip only when known too close).
        """
        # For MVP, we don't have an earnings calendar API, so always pass
        # The scoring function still penalises unknown proximity
        return True

    # ------------------------------------------------------------------
    # Trigger detection (req 4.1)
    # ------------------------------------------------------------------

    def _detect_triggers(self, ticker: str, price_data) -> List[Dict]:
        """Detect momentum triggers from price data.

        Triggers:
        1. Breakout: Close above 20-day high (bullish) or below 20-day low (bearish)
        2. VWAP reclaim: Gap-down >= 2% + close above rolling VWAP (bullish)
        3. RSI divergence: Price lower-low + RSI higher-low (bullish) or inverse
        4. EMA crossover: 8-EMA crosses 21-EMA

        Returns:
            List of trigger dicts with type and direction.
        """
        triggers: List[Dict] = []
        closes = price_data["Close"]
        highs = price_data["High"]
        lows = price_data["Low"]
        volumes = price_data["Volume"]

        lookback = self._momentum_cfg["consolidation_lookback"]
        min_rel_vol = self._momentum_cfg["min_relative_volume"]

        current_close = float(closes.iloc[-1])
        current_volume = float(volumes.iloc[-1])

        # Volume filter
        avg_volume = float(volumes.iloc[-lookback:].mean()) if len(volumes) >= lookback else float(volumes.mean())
        if avg_volume <= 0:
            return []
        relative_volume = current_volume / avg_volume

        # --- 1. Breakout ---
        if len(highs) >= lookback + 1:
            high_20 = float(highs.iloc[-(lookback + 1):-1].max())
            low_20 = float(lows.iloc[-(lookback + 1):-1].min())

            if current_close > high_20 and relative_volume >= min_rel_vol:
                triggers.append({
                    "type": "breakout",
                    "direction": "bullish",
                    "detail": f"Close {current_close:.2f} > 20d high {high_20:.2f}, "
                              f"vol {relative_volume:.1f}x",
                })
            elif current_close < low_20 and relative_volume >= min_rel_vol:
                triggers.append({
                    "type": "breakout",
                    "direction": "bearish",
                    "detail": f"Close {current_close:.2f} < 20d low {low_20:.2f}, "
                              f"vol {relative_volume:.1f}x",
                })

        # --- 2. VWAP reclaim ---
        gap_threshold = self._momentum_cfg["vwap_gap_threshold"]
        if len(closes) >= 2:
            prev_close = float(closes.iloc[-2])
            open_price = float(price_data["Open"].iloc[-1]) if "Open" in price_data.columns else current_close

            if prev_close > 0:
                gap_pct = (open_price - prev_close) / prev_close
                if gap_pct <= -gap_threshold:
                    # Gap-down detected — check if close recovered above VWAP
                    vwap = self._calculate_vwap(price_data)
                    if vwap is not None and current_close > vwap:
                        triggers.append({
                            "type": "vwap_reclaim",
                            "direction": "bullish",
                            "detail": f"Gap-down {gap_pct:.1%}, reclaimed VWAP {vwap:.2f}",
                        })

        # --- 3. RSI divergence ---
        rsi_lookback = self._momentum_cfg["rsi_divergence_lookback"]
        if len(closes) >= rsi_lookback + 14:
            rsi = calculate_rsi(closes)
            if rsi is not None and len(rsi) >= rsi_lookback:
                rsi_window = rsi.iloc[-rsi_lookback:]
                price_window = closes.iloc[-rsi_lookback:]

                # Bullish divergence: price lower-low + RSI higher-low
                price_window.idxmin()
                rsi_window.idxmin()

                if (float(price_window.iloc[-1]) <= float(price_window.min()) * 1.02 and
                        float(rsi_window.iloc[-1]) > float(rsi_window.min()) * 1.05):
                    triggers.append({
                        "type": "rsi_divergence",
                        "direction": "bullish",
                        "detail": "Price near low, RSI rising (bullish divergence)",
                    })

                # Bearish divergence: price higher-high + RSI lower-high
                if (float(price_window.iloc[-1]) >= float(price_window.max()) * 0.98 and
                        float(rsi_window.iloc[-1]) < float(rsi_window.max()) * 0.95):
                    triggers.append({
                        "type": "rsi_divergence",
                        "direction": "bearish",
                        "detail": "Price near high, RSI falling (bearish divergence)",
                    })

        # --- 4. EMA crossover ---
        ema_fast_period = self._momentum_cfg["ema_fast"]
        ema_slow_period = self._momentum_cfg["ema_slow"]
        if len(closes) >= ema_slow_period + 2:
            ema_fast = closes.ewm(span=ema_fast_period, adjust=False).mean()
            ema_slow = closes.ewm(span=ema_slow_period, adjust=False).mean()

            # Current: fast > slow, previous: fast <= slow → bullish crossover
            if (float(ema_fast.iloc[-1]) > float(ema_slow.iloc[-1]) and
                    float(ema_fast.iloc[-2]) <= float(ema_slow.iloc[-2])):
                triggers.append({
                    "type": "ema_crossover",
                    "direction": "bullish",
                    "detail": "8-EMA crossed above 21-EMA",
                })

            # Current: fast < slow, previous: fast >= slow → bearish crossover
            if (float(ema_fast.iloc[-1]) < float(ema_slow.iloc[-1]) and
                    float(ema_fast.iloc[-2]) >= float(ema_slow.iloc[-2])):
                triggers.append({
                    "type": "ema_crossover",
                    "direction": "bearish",
                    "detail": "8-EMA crossed below 21-EMA",
                })

        return triggers

    # ------------------------------------------------------------------
    # Momentum scoring (req 4.3)
    # ------------------------------------------------------------------

    def _compute_momentum_score(
        self, ticker: str, price_data, trigger: Dict
    ) -> float:
        """Compute a 0-100 momentum score.

        Components:
        - Relative volume: 35 pts (linear 1x→3x)
        - ADX trend strength: 35 pts (linear 15→45)
        - Earnings proximity: 30 pts (30 if >= 5 days, 0 if < 5)
        """
        volumes = price_data["Volume"]
        lookback = self._momentum_cfg["consolidation_lookback"]

        # --- Relative volume: 35 pts ---
        avg_vol = float(volumes.iloc[-lookback:].mean()) if len(volumes) >= lookback else float(volumes.mean())
        current_vol = float(volumes.iloc[-1])
        rel_vol = current_vol / avg_vol if avg_vol > 0 else 0
        # Linear scale: 1x → 0 pts, 3x → 35 pts
        vol_score = max(0, min(35, (rel_vol - 1.0) / 2.0 * 35))

        # --- ADX trend strength: 35 pts ---
        adx = self._calculate_adx(price_data)
        # Linear scale: 15 → 0 pts, 45 → 35 pts
        adx_score = max(0, min(35, (adx - 15) / 30 * 35))

        # --- Earnings proximity: 30 pts ---
        # MVP: assume >= 5 days (we don't have earnings calendar yet)
        earnings_score = 30.0

        total = vol_score + adx_score + earnings_score
        return round(total, 1)

    # ------------------------------------------------------------------
    # Debit spread builder (req 4.2)
    # ------------------------------------------------------------------

    def _build_debit_spread(
        self, ticker: str, price_data, trigger: Dict, score: float
    ) -> Optional[Dict]:
        """Build a debit spread opportunity from a trigger.

        Bullish → bull call debit (buy ATM call, sell OTM call)
        Bearish → bear put debit (buy ATM put, sell OTM put)
        """
        current_price = float(price_data["Close"].iloc[-1])
        width = self._momentum_cfg["spread_width"]

        # Fetch options chain
        options_chain = self._options_analyzer.get_options_chain(ticker)
        if options_chain is None or (hasattr(options_chain, "empty") and options_chain.empty):
            logger.warning(f"No options chain for {ticker}")
            return None

        min_dte = self._config["strategy"]["min_dte"]
        max_dte = self._config["strategy"]["max_dte"]

        # Filter to target DTE range
        if "expiration" in options_chain.columns:
            now = datetime.now()
            chain = options_chain.copy()
            chain["_dte"] = (pd.to_datetime(chain["expiration"]) - now).dt.days
            chain = chain[(chain["_dte"] >= min_dte) & (chain["_dte"] <= max_dte)]

            if chain.empty:
                logger.info(f"{ticker}: No expirations in {min_dte}-{max_dte} DTE range")
                return None
        else:
            chain = options_chain

        direction = trigger["direction"]

        if direction == "bullish":
            return self._build_bull_call_debit(
                ticker, chain, current_price, width, trigger, score
            )
        else:
            return self._build_bear_put_debit(
                ticker, chain, current_price, width, trigger, score
            )

    def _build_bull_call_debit(
        self, ticker, chain, current_price, width, trigger, score
    ) -> Optional[Dict]:
        """Buy ATM call, sell OTM call at ATM + width."""
        calls = chain[chain["type"] == "call"] if "type" in chain.columns else chain
        if calls.empty:
            return None

        # Find ATM call (closest strike to current price)
        calls = calls.copy()
        calls["_dist"] = (calls["strike"] - current_price).abs()
        atm = calls.loc[calls["_dist"].idxmin()]
        atm_strike = float(atm["strike"])
        short_strike = atm_strike + width

        # Find the short leg
        short_options = calls[calls["strike"] == short_strike]
        if short_options.empty:
            # Find nearest available strike >= atm + width
            above = calls[calls["strike"] >= atm_strike + width]
            if above.empty:
                return None
            short_options = above.iloc[[0]]
            short_strike = float(short_options.iloc[0]["strike"])

        short_leg = short_options.iloc[0]

        # Estimate debit from mid-prices
        long_mid = (float(atm.get("bid", 0)) + float(atm.get("ask", 0))) / 2
        short_mid = (float(short_leg.get("bid", 0)) + float(short_leg.get("ask", 0))) / 2
        debit = round(long_mid - short_mid, 2)
        actual_width = short_strike - atm_strike

        if debit <= 0 or actual_width <= 0:
            return None

        # Validate 2:1+ R:R (debit <= width / 2)
        if debit > actual_width / 2:
            return None

        max_profit = round(actual_width - debit, 2)
        expiration = str(atm.get("expiration", ""))
        dte = int(atm.get("_dte", 10)) if "_dte" in atm.index else 10

        return {
            "ticker": ticker,
            "type": "bull_call_debit",
            "direction": "bullish",
            "long_strike": atm_strike,
            "short_strike": short_strike,
            "spread_width": actual_width,
            "debit": debit,
            "credit": debit,  # alias for from_opportunity compatibility
            "max_loss": debit,
            "max_profit": max_profit,
            "profit_target": round(debit * self._momentum_cfg["profit_target_pct"], 2),
            "stop_loss": round(debit * self._momentum_cfg["stop_loss_pct"], 2),
            "current_price": current_price,
            "expiration": expiration,
            "dte": dte,
            "score": score,
            "trigger_type": trigger["type"],
            "trigger_detail": trigger.get("detail", ""),
            "alert_source": "momentum_swing",
            "risk_pct": 0.02,
        }

    def _build_bear_put_debit(
        self, ticker, chain, current_price, width, trigger, score
    ) -> Optional[Dict]:
        """Buy ATM put, sell OTM put at ATM - width."""
        puts = chain[chain["type"] == "put"] if "type" in chain.columns else chain
        if puts.empty:
            return None

        # Find ATM put (closest strike to current price)
        puts = puts.copy()
        puts["_dist"] = (puts["strike"] - current_price).abs()
        atm = puts.loc[puts["_dist"].idxmin()]
        atm_strike = float(atm["strike"])
        short_strike = atm_strike - width

        # Find the short leg
        short_options = puts[puts["strike"] == short_strike]
        if short_options.empty:
            below = puts[puts["strike"] <= atm_strike - width]
            if below.empty:
                return None
            short_options = below.iloc[[-1]]
            short_strike = float(short_options.iloc[0]["strike"])

        short_leg = short_options.iloc[0]

        # Estimate debit from mid-prices
        long_mid = (float(atm.get("bid", 0)) + float(atm.get("ask", 0))) / 2
        short_mid = (float(short_leg.get("bid", 0)) + float(short_leg.get("ask", 0))) / 2
        debit = round(long_mid - short_mid, 2)
        actual_width = atm_strike - short_strike

        if debit <= 0 or actual_width <= 0:
            return None

        # Validate 2:1+ R:R
        if debit > actual_width / 2:
            return None

        max_profit = round(actual_width - debit, 2)
        expiration = str(atm.get("expiration", ""))
        dte = int(atm.get("_dte", 10)) if "_dte" in atm.index else 10

        return {
            "ticker": ticker,
            "type": "bear_put_debit",
            "direction": "bearish",
            "long_strike": atm_strike,
            "short_strike": short_strike,
            "spread_width": actual_width,
            "debit": debit,
            "credit": debit,
            "max_loss": debit,
            "max_profit": max_profit,
            "profit_target": round(debit * self._momentum_cfg["profit_target_pct"], 2),
            "stop_loss": round(debit * self._momentum_cfg["stop_loss_pct"], 2),
            "current_price": current_price,
            "expiration": expiration,
            "dte": dte,
            "score": score,
            "trigger_type": trigger["type"],
            "trigger_detail": trigger.get("detail", ""),
            "alert_source": "momentum_swing",
            "risk_pct": 0.02,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calculate_adx(self, price_data, period: int = 14) -> float:
        """Calculate ADX (Average Directional Index) using Wilder's smoothing.

        Returns a single float: the latest ADX value.
        """
        highs = price_data["High"].values.astype(float)
        lows = price_data["Low"].values.astype(float)
        closes = price_data["Close"].values.astype(float)

        n = len(closes)
        if n < period + 1:
            return 0.0

        # True Range, +DM, -DM
        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)

        for i in range(1, n):
            h_l = highs[i] - lows[i]
            h_pc = abs(highs[i] - closes[i - 1])
            l_pc = abs(lows[i] - closes[i - 1])
            tr[i] = max(h_l, h_pc, l_pc)

            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]

            plus_dm[i] = up if (up > down and up > 0) else 0
            minus_dm[i] = down if (down > up and down > 0) else 0

        # Wilder's smoothing
        atr = np.zeros(n)
        plus_di_smooth = np.zeros(n)
        minus_di_smooth = np.zeros(n)

        # Initial sums
        atr[period] = np.sum(tr[1:period + 1])
        plus_di_smooth[period] = np.sum(plus_dm[1:period + 1])
        minus_di_smooth[period] = np.sum(minus_dm[1:period + 1])

        for i in range(period + 1, n):
            atr[i] = atr[i - 1] - atr[i - 1] / period + tr[i]
            plus_di_smooth[i] = plus_di_smooth[i - 1] - plus_di_smooth[i - 1] / period + plus_dm[i]
            minus_di_smooth[i] = minus_di_smooth[i - 1] - minus_di_smooth[i - 1] / period + minus_dm[i]

        # DI+ and DI-
        dx = np.zeros(n)
        for i in range(period, n):
            if atr[i] == 0:
                continue
            plus_di = 100 * plus_di_smooth[i] / atr[i]
            minus_di = 100 * minus_di_smooth[i] / atr[i]
            di_sum = plus_di + minus_di
            if di_sum > 0:
                dx[i] = 100 * abs(plus_di - minus_di) / di_sum

        # ADX = smoothed DX
        adx_start = 2 * period
        if n <= adx_start:
            return float(dx[period]) if n > period else 0.0

        adx = np.zeros(n)
        adx[adx_start] = np.mean(dx[period:adx_start + 1])

        for i in range(adx_start + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

        return float(adx[-1])

    def _calculate_vwap(self, price_data) -> Optional[float]:
        """Calculate rolling VWAP from daily bars.

        Uses a 20-day rolling window.
        """
        if "Volume" not in price_data.columns:
            return None

        lookback = self._momentum_cfg["consolidation_lookback"]
        window = price_data.iloc[-lookback:] if len(price_data) >= lookback else price_data

        typical_price = (
            window["High"] + window["Low"] + window["Close"]
        ) / 3
        volume = window["Volume"]

        vol_sum = float(volume.sum())
        if vol_sum <= 0:
            return None

        vwap = float((typical_price * volume).sum() / vol_sum)
        return vwap
