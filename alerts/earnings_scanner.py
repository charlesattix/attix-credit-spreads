"""
Earnings volatility play scanner.

Scans for upcoming earnings within the entry window (1-3 days before),
validates IV rank and historical stay-in-range, then builds price-based
iron condors at 1.2x expected move to profit from post-earnings IV crush.

Key difference from IronCondorScanner: uses price-based strike placement
(current_price +/- 1.2 * expected_move) rather than delta-based wings.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from alerts.earnings_config import (
    EARNINGS_LOOKAHEAD_DAYS,
    build_earnings_config,
)
from shared.earnings_calendar import EarningsCalendar
from strategy import OptionsAnalyzer

logger = logging.getLogger(__name__)


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


class EarningsScanner:
    """Earnings volatility play scanner with price-based condor construction.

    Builds iron condors at 1.2x expected move for tickers with upcoming
    earnings, gated by IV rank >= 60 and historical stay-in-range >= 65%.
    """

    def __init__(self, base_config: dict, data_cache=None):
        self._base_config = base_config
        self._config = build_earnings_config(base_config)
        self._data_cache = data_cache
        self._earnings_cfg = self._config["strategy"]["earnings"]

        # OptionsAnalyzer for chain fetching — no CreditSpreadStrategy
        self._options_analyzer = OptionsAnalyzer(self._config, data_cache=data_cache)
        self._earnings_calendar = EarningsCalendar(data_cache=data_cache)

        logger.info(
            "EarningsScanner initialized (%d tickers, %d-day lookahead)",
            len(self._config["tickers"]),
            EARNINGS_LOOKAHEAD_DAYS,
        )

    # ------------------------------------------------------------------
    # Entry window gate
    # ------------------------------------------------------------------

    def is_in_entry_window(
        self, ticker: str, now_et: Optional[datetime] = None
    ) -> bool:
        """Return True if the ticker has earnings in 1-3 days.

        Args:
            ticker: Stock ticker symbol.
            now_et: Optional datetime for testing.

        Returns:
            True if earnings are within the entry window.
        """
        earnings_date = self._earnings_calendar.get_next_earnings(ticker)
        if earnings_date is None:
            return False

        now = now_et or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        days_until = (earnings_date - now).days
        return (
            self._earnings_cfg["min_entry_days_before"]
            <= days_until
            <= self._earnings_cfg["max_entry_days_before"]
        )

    # ------------------------------------------------------------------
    # Main scan
    # ------------------------------------------------------------------

    def scan(self, now_et: Optional[datetime] = None) -> List[Dict]:
        """Run the earnings scan pipeline.

        1. Get lookahead calendar for all tickers.
        2. Filter to entry window (1-3 days before earnings).
        3. Scan each qualifying ticker.
        4. Annotate with alert_source="earnings_play".

        Returns:
            List of opportunity dicts for earnings iron condors.
        """
        logger.info("Earnings scan: checking lookahead calendar")

        # Get tickers with upcoming earnings
        calendar = self._earnings_calendar.get_lookahead_calendar(
            self._config["tickers"],
            days_ahead=EARNINGS_LOOKAHEAD_DAYS,
        )

        if not calendar:
            logger.info("Earnings scan: no upcoming earnings in lookahead window")
            return []

        results: List[Dict] = []
        for entry in calendar:
            ticker = entry["ticker"]
            earnings_date = entry["earnings_date"]
            days_until = entry["days_until"]

            # Gate: entry window (1-3 days before)
            if not (
                self._earnings_cfg["min_entry_days_before"]
                <= days_until
                <= self._earnings_cfg["max_entry_days_before"]
            ):
                continue

            try:
                opp = self._scan_ticker(ticker, earnings_date)
                if opp is not None:
                    opp["alert_source"] = "earnings_play"
                    results.append(opp)
            except Exception as e:
                logger.error(
                    f"Earnings scan failed for {ticker}: {e}", exc_info=True
                )

        logger.info(f"Earnings scan complete: {len(results)} opportunities")
        return results

    # ------------------------------------------------------------------
    # Per-ticker scan
    # ------------------------------------------------------------------

    def _scan_ticker(
        self, ticker: str, earnings_date: datetime
    ) -> Optional[Dict]:
        """Scan a single ticker for an earnings condor opportunity.

        Steps:
        1. Fetch price data + options chain
        2. Gate: IV Rank >= 60
        3. Gate: historical stay-in-range >= 65%
        4. Calculate expected move from ATM straddle
        5. Build iron condor at 1.2x expected move
        6. Score and return
        """
        # Fetch price data (Polygon-backed DataCache; required)
        price_data = self._data_cache.get_history(ticker, period="1y")

        if price_data is None or (hasattr(price_data, "empty") and price_data.empty):
            logger.warning(f"No price data for {ticker}")
            return None

        current_price = float(price_data["Close"].iloc[-1])

        # Fetch options chain
        options_chain = self._options_analyzer.get_options_chain(ticker)
        if options_chain is None or (
            hasattr(options_chain, "empty") and options_chain.empty
        ):
            logger.warning(f"No options chain for {ticker}")
            return None

        # Gate: IV Rank >= 60
        current_iv = self._options_analyzer.get_current_iv(options_chain)
        iv_data = self._options_analyzer.calculate_iv_rank(ticker, current_iv)
        iv_rank = iv_data.get("iv_rank", 0)

        if iv_rank < self._config["strategy"]["min_iv_rank"]:
            logger.info(f"{ticker}: IV rank {iv_rank:.1f} < 60, skipping")
            return None

        # Gate: historical stay-in-range >= 65%
        hist = self._earnings_calendar.calculate_historical_stay_in_range(ticker)
        stay_in_range = hist.get("stay_in_range_pct", 0)
        total_quarters = hist.get("total_quarters", 0)

        if total_quarters < self._earnings_cfg["min_historical_quarters"]:
            logger.info(
                f"{ticker}: only {total_quarters} quarters of data, "
                f"need {self._earnings_cfg['min_historical_quarters']}"
            )
            return None

        if stay_in_range < self._earnings_cfg["min_stay_in_range_pct"]:
            logger.info(
                f"{ticker}: stay-in-range {stay_in_range:.1f}% < 65%, skipping"
            )
            return None

        # Calculate expected move from ATM straddle
        expected_move = self._earnings_calendar.calculate_expected_move(
            options_chain, current_price
        )
        if expected_move is None or expected_move <= 0:
            logger.info(f"{ticker}: could not calculate expected move")
            return None

        # Build iron condor at 1.2x expected move
        opp = self._build_earnings_condor(
            ticker=ticker,
            chain=options_chain,
            price=current_price,
            expected_move=expected_move,
            earnings_date=earnings_date,
            iv_rank=iv_rank,
            stay_in_range_pct=stay_in_range,
        )

        if opp is not None:
            opp["score"] = self._score_earnings_opportunity(
                iv_rank=iv_rank,
                stay_in_range=stay_in_range,
                credit=opp.get("credit", 0),
                width=opp.get("spread_width", 5),
            )

        return opp

    # ------------------------------------------------------------------
    # Price-based condor builder
    # ------------------------------------------------------------------

    def _build_earnings_condor(
        self,
        ticker: str,
        chain,
        price: float,
        expected_move: float,
        earnings_date: datetime,
        iv_rank: float,
        stay_in_range_pct: float,
    ) -> Optional[Dict]:
        """Build an iron condor at 1.2x expected move using price-based strikes.

        Strike placement:
        - short_put  = price - 1.2 * expected_move (snap to nearest strike)
        - long_put   = short_put - width
        - short_call = price + 1.2 * expected_move (snap to nearest strike)
        - long_call  = short_call + width
        """
        multiplier = self._earnings_cfg["expected_move_multiplier"]
        width = self._earnings_cfg["spread_width"]
        min_dte = self._config["strategy"]["min_dte"]
        max_dte = self._config["strategy"]["max_dte"]

        # Target strikes
        target_short_put = price - multiplier * expected_move
        target_short_call = price + multiplier * expected_move

        # Filter chain to post-earnings expiration
        if "expiration" in chain.columns:
            now = datetime.now()
            filtered = chain.copy()
            filtered["_dte"] = (pd.to_datetime(filtered["expiration"]) - now).dt.days
            filtered = filtered[
                (filtered["_dte"] >= min_dte) & (filtered["_dte"] <= max_dte)
            ]
            if hasattr(filtered, "empty") and filtered.empty:
                logger.info(f"{ticker}: no expirations in {min_dte}-{max_dte} DTE")
                return None
        else:
            filtered = chain

        # Get puts and calls
        puts = filtered[filtered["type"] == "put"] if "type" in filtered.columns else filtered
        calls = filtered[filtered["type"] == "call"] if "type" in filtered.columns else filtered

        if (hasattr(puts, "empty") and puts.empty) or (
            hasattr(calls, "empty") and calls.empty
        ):
            return None

        # Snap short put to nearest available strike
        puts_copy = puts.copy()
        puts_copy["_dist"] = (puts_copy["strike"] - target_short_put).abs()
        short_put_row = puts_copy.loc[puts_copy["_dist"].idxmin()]
        short_put_strike = float(short_put_row["strike"])

        # Long put = short_put - width
        long_put_strike = short_put_strike - width
        long_put_options = puts_copy[puts_copy["strike"] == long_put_strike]
        if hasattr(long_put_options, "empty") and long_put_options.empty:
            # Find nearest strike <= long_put_strike
            below = puts_copy[puts_copy["strike"] <= long_put_strike]
            if hasattr(below, "empty") and below.empty:
                return None
            long_put_options = below.loc[[below["strike"].idxmax()]]
            long_put_strike = float(long_put_options.iloc[0]["strike"])

        long_put_row = long_put_options.iloc[0]

        # Snap short call to nearest available strike
        calls_copy = calls.copy()
        calls_copy["_dist"] = (calls_copy["strike"] - target_short_call).abs()
        short_call_row = calls_copy.loc[calls_copy["_dist"].idxmin()]
        short_call_strike = float(short_call_row["strike"])

        # Long call = short_call + width
        long_call_strike = short_call_strike + width
        long_call_options = calls_copy[calls_copy["strike"] == long_call_strike]
        if hasattr(long_call_options, "empty") and long_call_options.empty:
            above = calls_copy[calls_copy["strike"] >= long_call_strike]
            if hasattr(above, "empty") and above.empty:
                return None
            long_call_options = above.loc[[above["strike"].idxmin()]]
            long_call_strike = float(long_call_options.iloc[0]["strike"])

        long_call_row = long_call_options.iloc[0]

        # Compute mid-prices for credit calculation
        short_put_mid = (
            float(short_put_row.get("bid", 0)) + float(short_put_row.get("ask", 0))
        ) / 2
        long_put_mid = (
            float(long_put_row.get("bid", 0)) + float(long_put_row.get("ask", 0))
        ) / 2
        short_call_mid = (
            float(short_call_row.get("bid", 0)) + float(short_call_row.get("ask", 0))
        ) / 2
        long_call_mid = (
            float(long_call_row.get("bid", 0)) + float(long_call_row.get("ask", 0))
        ) / 2

        # Combined credit = (sell puts + sell calls) - (buy puts + buy calls)
        put_credit = short_put_mid - long_put_mid
        call_credit = short_call_mid - long_call_mid
        total_credit = round(put_credit + call_credit, 2)

        if total_credit <= 0:
            return None

        # Max loss = width - credit (per side, take the wider)
        put_width = short_put_strike - long_put_strike
        call_width = long_call_strike - short_call_strike
        actual_width = max(put_width, call_width)
        max_loss = round(actual_width - total_credit, 2)

        if max_loss <= 0:
            return None

        expiration = str(short_put_row.get("expiration", ""))
        dte = int(short_put_row.get("_dte", 5)) if "_dte" in short_put_row.index else 5

        return {
            "ticker": ticker,
            "type": "earnings_iron_condor",
            "direction": "neutral",
            # Put side
            "short_strike": short_put_strike,
            "long_strike": long_put_strike,
            # Call side
            "call_short_strike": short_call_strike,
            "call_long_strike": long_call_strike,
            "spread_width": actual_width,
            "credit": total_credit,
            "total_credit": total_credit,
            "max_loss": max_loss,
            "profit_target": round(total_credit * self._earnings_cfg["profit_target_pct"], 2),
            "stop_loss": round(total_credit * self._earnings_cfg["stop_loss_multiplier"], 2),
            "current_price": price,
            "expiration": expiration,
            "dte": dte,
            "expected_move": expected_move,
            "earnings_date": earnings_date.isoformat() if hasattr(earnings_date, "isoformat") else str(earnings_date),
            "stay_in_range_pct": stay_in_range_pct,
            "iv_rank": iv_rank,
            "alert_source": "earnings_play",
            "risk_pct": self._earnings_cfg["max_risk_pct"],
            "management_instructions": (
                "Earnings iron condor. Close morning after earnings to capture IV crush, "
                "or at 50% profit / 2x credit stop loss."
            ),
        }

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_earnings_opportunity(
        iv_rank: float,
        stay_in_range: float,
        credit: float,
        width: float,
    ) -> float:
        """Score an earnings opportunity on a 0-100 scale.

        Components:
        - IV Rank: 35 pts (linear 60 -> 100)
        - Stay-in-range: 35 pts (linear 65 -> 100)
        - Credit quality: 30 pts (credit/width ratio)
        """
        # IV Rank: 35 pts (60=0, 100=35)
        iv_score = max(0, min(35, (iv_rank - 60) / 40 * 35))

        # Stay-in-range: 35 pts (65=0, 100=35)
        range_score = max(0, min(35, (stay_in_range - 65) / 35 * 35))

        # Credit quality: 30 pts (credit/width, higher is better)
        if width > 0:
            credit_ratio = credit / width
            credit_score = max(0, min(30, credit_ratio * 30 / 0.5))  # 50% of width = 30 pts
        else:
            credit_score = 0

        total = round(iv_score + range_score + credit_score, 1)
        return total
