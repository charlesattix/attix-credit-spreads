"""
Gamma/lotto play scanner.

Scans for cheap OTM options ($0.10-$0.50) the day before major economic events
(FOMC, CPI, Jobs, GDP, PPI) to capture asymmetric upside from big moves.

Key difference from all prior scanners: produces single-leg debit plays
(buying one naked OTM call or put), not spreads.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from alerts.gamma_config import SCAN_HOURS, build_gamma_config
from shared.data_cache import DataCache
from shared.economic_calendar import EconomicCalendar
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


class GammaScanner:
    """Scanner finding cheap OTM options before economic events."""

    def __init__(self, base_config: dict, data_cache=None):
        self._base_config = base_config
        self._config = build_gamma_config(base_config)
        self._data_cache = data_cache
        self._gamma_cfg = self._config["strategy"]["gamma"]

        self._options_analyzer = OptionsAnalyzer(self._config, data_cache=data_cache)
        self._calendar = EconomicCalendar()

        logger.info(
            "GammaScanner initialized (%d tickers, $%.2f-$%.2f price range)",
            len(self._config["tickers"]),
            self._gamma_cfg["price_min"],
            self._gamma_cfg["price_max"],
        )

    # ------------------------------------------------------------------
    # Market hours gate
    # ------------------------------------------------------------------

    @staticmethod
    def is_market_hours(now_et: Optional[datetime] = None) -> bool:
        """Return True if within 9:35-15:30 ET on a weekday."""
        et = _now_et(now_et)
        if et.weekday() >= 5:  # Saturday / Sunday
            return False
        t = et.time()
        return SCAN_HOURS[0] <= t <= SCAN_HOURS[1]

    # ------------------------------------------------------------------
    # Main scan
    # ------------------------------------------------------------------

    def scan(self, now_et: Optional[datetime] = None) -> List[Dict]:
        """Run the gamma lotto scan pipeline.

        1. Gate market hours.
        2. Check is_event_tomorrow().
        3. For each ticker, call _scan_ticker().
        4. Annotate with alert_source="gamma_lotto".

        Returns:
            List of single-leg opportunity dicts.
        """
        et = _now_et(now_et)

        if not self.is_market_hours(et):
            logger.info("Gamma scan: outside market hours, skipping")
            return []

        if not self._calendar.is_event_tomorrow(et):
            logger.info("Gamma scan: no economic event tomorrow, skipping")
            return []

        # Get tomorrow's event(s) for context
        events = self._calendar.get_upcoming_events(days_ahead=2, reference_date=et)
        if not events:
            return []

        event = events[0]  # Use the nearest event
        logger.info(
            "Gamma scan: event tomorrow — %s (%s)",
            event["description"],
            event["event_type"],
        )

        results: List[Dict] = []
        for ticker in self._config["tickers"]:
            try:
                opps = self._scan_ticker(ticker, event)
                for opp in opps:
                    opp["alert_source"] = "gamma_lotto"
                results.extend(opps)
            except Exception as e:
                logger.error(f"Gamma scan failed for {ticker}: {e}", exc_info=True)

        logger.info(f"Gamma scan complete: {len(results)} opportunities")
        return results

    # ------------------------------------------------------------------
    # Per-ticker scan
    # ------------------------------------------------------------------

    def _scan_ticker(self, ticker: str, event: Dict) -> List[Dict]:
        """Scan a single ticker for cheap OTM gamma plays.

        Steps:
        1. Fetch price data + options chain
        2. Filter to 0DTE/1DTE
        3. Calculate expected move from HV (20-day stdev * price)
        4. Find OTM calls and puts in price/OTM range
        5. Calculate sigma payoffs
        6. Score and return
        """
        # Fetch price data (Polygon via DataCache; no yfinance fallback)
        cache = self._data_cache or DataCache()
        price_data = cache.get_history(ticker, period="1y")

        if price_data is None or (hasattr(price_data, "empty") and price_data.empty):
            logger.warning(f"No price data for {ticker}")
            return []

        current_price = float(price_data["Close"].iloc[-1])

        # Calculate expected move from 20-day historical volatility
        try:
            closes = price_data["Close"]
            if hasattr(closes, "pct_change"):
                returns = closes.pct_change().dropna()
                if len(returns) >= 20:
                    daily_std = float(returns.tail(20).std())
                else:
                    daily_std = float(returns.std()) if len(returns) > 1 else 0.01
            else:
                daily_std = 0.01
        except Exception:
            daily_std = 0.01

        expected_move = daily_std * current_price

        if expected_move <= 0:
            return []

        # Fetch options chain
        options_chain = self._options_analyzer.get_options_chain(ticker)
        if options_chain is None or (
            hasattr(options_chain, "empty") and options_chain.empty
        ):
            logger.warning(f"No options chain for {ticker}")
            return []

        # Find cheap OTM options
        calls = self._find_cheap_otm_options(
            options_chain, current_price, "call",
        )
        puts = self._find_cheap_otm_options(
            options_chain, current_price, "put",
        )

        # Build opportunity dicts with sigma payoffs and scoring
        results: List[Dict] = []
        for opt in calls + puts:
            sigma_payoffs = self._calculate_sigma_payoffs(
                strike=opt["strike"],
                price=current_price,
                expected_move=expected_move,
                debit=opt["mid_price"],
                option_type=opt["option_type"],
            )
            score = self._score_gamma_opportunity(
                debit=opt["mid_price"],
                sigma_payoffs=sigma_payoffs,
                event=event,
            )
            opp = {
                "ticker": ticker,
                "type": f"gamma_lotto_{opt['option_type']}",
                "option_type": opt["option_type"],
                "strike": opt["strike"],
                "debit": opt["mid_price"],
                "current_price": current_price,
                "expected_move": expected_move,
                "expiration": str(opt.get("expiration", "")),
                "dte": opt.get("dte", 0),
                "otm_pct": opt["otm_pct"],
                "score": score,
                "sigma_payoffs": sigma_payoffs,
                "event_type": event["event_type"],
                "event_description": event["description"],
                "risk_pct": self._gamma_cfg["max_risk_pct"],
                "stop_loss": 0.0,
                "profit_target": opt["mid_price"] * self._gamma_cfg["trailing_stop_activation"],
                "management_instructions": (
                    f"LOTTO \u2014 0.5% max risk. "
                    f"Trailing stop: activates at {self._gamma_cfg['trailing_stop_activation']:.0f}x entry, "
                    f"trails at {self._gamma_cfg['trailing_stop_level']:.0f}x entry. "
                    f"Let expire worthless if no move."
                ),
            }
            results.append(opp)

        return results

    # ------------------------------------------------------------------
    # OTM option finder
    # ------------------------------------------------------------------

    def _find_cheap_otm_options(
        self,
        chain,
        current_price: float,
        option_type: str,
    ) -> List[Dict]:
        """Filter options chain for cheap OTM options within price/OTM range."""
        price_min = self._gamma_cfg["price_min"]
        price_max = self._gamma_cfg["price_max"]
        min_otm = self._gamma_cfg["min_otm_pct"]
        max_otm = self._gamma_cfg["max_otm_pct"]
        min_dte = self._config["strategy"]["min_dte"]
        max_dte = self._config["strategy"]["max_dte"]

        results: List[Dict] = []

        try:
            import pandas as pd

            # Filter by type
            if "type" in chain.columns:
                filtered = chain[chain["type"] == option_type].copy()
            else:
                filtered = chain.copy()

            # Filter by DTE
            if "expiration" in filtered.columns:
                now = datetime.now()
                filtered["_dte"] = (pd.to_datetime(filtered["expiration"]) - now).dt.days
                filtered = filtered[
                    (filtered["_dte"] >= min_dte) & (filtered["_dte"] <= max_dte)
                ]

            if hasattr(filtered, "empty") and filtered.empty:
                return []

            for _, row in filtered.iterrows():
                strike = float(row.get("strike", 0))
                bid = float(row.get("bid", 0))
                ask = float(row.get("ask", 0))
                mid = (bid + ask) / 2 if (bid + ask) > 0 else 0

                if mid <= 0:
                    continue

                # Check price range
                if mid < price_min or mid > price_max:
                    continue

                # Calculate OTM percentage
                if option_type == "call":
                    otm_pct = (strike - current_price) / current_price
                else:
                    otm_pct = (current_price - strike) / current_price

                # Must be OTM
                if otm_pct < min_otm or otm_pct > max_otm:
                    continue

                results.append({
                    "strike": strike,
                    "option_type": option_type,
                    "mid_price": round(mid, 2),
                    "bid": bid,
                    "ask": ask,
                    "otm_pct": round(otm_pct, 4),
                    "expiration": str(row.get("expiration", "")),
                    "dte": int(row.get("_dte", 0)) if "_dte" in row.index else 0,
                })

        except Exception as e:
            logger.error(f"Error finding cheap OTM options: {e}", exc_info=True)

        return results

    # ------------------------------------------------------------------
    # Sigma payoff calculator
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_sigma_payoffs(
        strike: float,
        price: float,
        expected_move: float,
        debit: float,
        option_type: str,
    ) -> Dict:
        """Calculate payoffs at 1/2/3 sigma moves.

        Calls: payoff at N*sigma = max(0, (price + N*expected_move) - strike) - debit
        Puts:  payoff at N*sigma = max(0, strike - (price - N*expected_move)) - debit
        """
        payoffs = {}
        for n in [1, 2, 3]:
            if option_type == "call":
                intrinsic = max(0, (price + n * expected_move) - strike)
            else:
                intrinsic = max(0, strike - (price - n * expected_move))

            payoff = intrinsic - debit
            return_pct = (payoff / debit * 100) if debit > 0 else 0

            payoffs[f"sigma_{n}_payoff"] = round(payoff, 2)
            payoffs[f"sigma_{n}_return_pct"] = round(return_pct, 1)

        return payoffs

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_gamma_opportunity(
        debit: float,
        sigma_payoffs: Dict,
        event: Dict,
    ) -> float:
        """Score a gamma opportunity on a 0-100 scale.

        Components:
        - Payoff ratio at 2sigma: 40 pts (linear based on return %)
        - Option cheapness: 30 pts ($0.10=30, $0.50=0, linear)
        - Event importance: 30 pts (scaled by EVENT_IMPORTANCE)
        """
        # Payoff ratio at 2sigma: 40 pts
        sigma_2_return = sigma_payoffs.get("sigma_2_return_pct", 0)
        # Cap at 2000% return for max score
        payoff_score = min(40, max(0, sigma_2_return / 2000 * 40))

        # Option cheapness: 30 pts ($0.10 = 30, $0.50 = 0)
        cheapness_score = max(0, min(30, (0.50 - debit) / 0.40 * 30))

        # Event importance: 30 pts
        importance = event.get("importance", 0.5)
        event_score = importance * 30

        total = round(payoff_score + cheapness_score + event_score, 1)
        return min(100, total)
