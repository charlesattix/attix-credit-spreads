"""
Iron condor scanner with day-of-week entry gates.

Wraps the existing ``CreditSpreadStrategy`` + ``OptionsAnalyzer`` with an
iron condor config overlay and only fires on Monday/Tuesday (weekly entry).
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from alerts.iron_condor_config import ENTRY_DAYS, build_iron_condor_config
from shared.data_cache import DataCache
from strategy import CreditSpreadStrategy, OptionsAnalyzer, TechnicalAnalyzer

logger = logging.getLogger(__name__)


def _now_et(now_et: Optional[datetime] = None) -> datetime:
    """Return *now_et* or the current time in US/Eastern.

    If a timezone-aware datetime is supplied, it is converted to ET.
    If timezone-naive, it is assumed to already be ET.
    """
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


class IronCondorScanner:
    """Day-of-week-gated iron condor scanner for high-IV environments.

    Uses the same ``CreditSpreadStrategy`` as the main system, but with
    an iron condor config overlay (4-10 DTE, 16-delta wings, IV rank >= 50).
    Scans are gated to Monday/Tuesday entry days.
    """

    def __init__(self, base_config: dict, data_cache=None):
        self._base_config = base_config
        self._config = build_iron_condor_config(base_config)
        self._data_cache = data_cache

        # Instantiate separate strategy/analysis components for iron condors
        self._strategy = CreditSpreadStrategy(self._config)
        self._options_analyzer = OptionsAnalyzer(self._config, data_cache=data_cache)
        self._technical_analyzer = TechnicalAnalyzer(self._config)

        logger.info(
            "IronCondorScanner initialized (%s, 4-10 DTE)",
            ", ".join(self._config["tickers"]),
        )

    # ------------------------------------------------------------------
    # Timing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_entry_day(now_et: Optional[datetime] = None) -> bool:
        """Return True if the current ET day is an entry day (Mon/Tue)."""
        return _now_et(now_et).weekday() in ENTRY_DAYS

    # ------------------------------------------------------------------
    # Main scan
    # ------------------------------------------------------------------

    def scan(self, now_et: Optional[datetime] = None) -> List[Dict]:
        """Run the iron condor scan pipeline.

        1. Gate: return [] if not an entry day (Mon/Tue).
        2. For each ticker: fetch data, run analysis.
        3. Filter to iron_condor type only.
        4. Annotate results with iron_condor metadata.

        Returns:
            List of opportunity dicts (same shape as ``SpreadOpportunity``).
        """
        if not self.is_entry_day(now_et):
            return []

        logger.info("Iron condor scan: entry day active")

        results: List[Dict] = []
        for ticker in self._config["tickers"]:
            try:
                opps = self._scan_ticker(ticker)
                for opp in opps:
                    opp["alert_source"] = "iron_condor"
                results.extend(opps)
            except Exception as e:
                logger.error(f"Iron condor scan failed for {ticker}: {e}", exc_info=True)

        logger.info(f"Iron condor scan complete: {len(results)} opportunities")
        return results

    # ------------------------------------------------------------------
    # Per-ticker scan
    # ------------------------------------------------------------------

    def _scan_ticker(self, ticker: str) -> List[Dict]:
        """Scan a single ticker for iron condor opportunities."""
        # Fetch price data (Polygon via DataCache; no yfinance fallback)
        cache = self._data_cache or DataCache()
        price_data = cache.get_history(ticker, period="1y")

        if price_data is None or (hasattr(price_data, "empty") and price_data.empty):
            logger.warning(f"No price data for {ticker}")
            return []

        current_price = float(price_data["Close"].iloc[-1])

        # Fetch options chain
        options_chain = self._options_analyzer.get_options_chain(ticker)
        if options_chain is None or (hasattr(options_chain, "empty") and options_chain.empty):
            logger.warning(f"No options chain for {ticker}")
            return []

        # Technical analysis
        technical_signals = self._technical_analyzer.analyze(ticker, price_data)

        # IV analysis — early exit if IV rank too low (req 3.1)
        current_iv = self._options_analyzer.get_current_iv(options_chain)
        iv_data = self._options_analyzer.calculate_iv_rank(ticker, current_iv)

        iv_rank = iv_data.get("iv_rank", 0)
        if iv_rank < self._config["strategy"]["min_iv_rank"]:
            logger.info(f"{ticker}: IV rank {iv_rank:.1f} < 50, skipping")
            return []

        # Evaluate spreads (strategy returns iron condors when enabled)
        opportunities = self._strategy.evaluate_spread_opportunity(
            ticker=ticker,
            option_chain=options_chain,
            technical_signals=technical_signals,
            iv_data=iv_data,
            current_price=current_price,
        )

        # Filter to iron_condor only
        condors = [
            opp for opp in opportunities
            if opp.get("type") == "iron_condor"
        ]

        # Annotate each opportunity
        for opp in condors:
            opp["alert_source"] = "iron_condor"

        return condors
