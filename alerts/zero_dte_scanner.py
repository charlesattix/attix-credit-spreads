"""
0DTE/1DTE credit spread scanner with intraday timing gates.

Wraps the existing ``CreditSpreadStrategy`` + ``OptionsAnalyzer`` with a
0DTE config overlay and only fires during predefined entry windows (ET).
"""

import logging
from datetime import datetime, time
from typing import Dict, List, Optional

from alerts.zero_dte_config import SPX_PROPERTIES, build_zero_dte_config
from shared.data_cache import DataCache
from strategy import CreditSpreadStrategy, OptionsAnalyzer, TechnicalAnalyzer

logger = logging.getLogger(__name__)

# Entry windows in US/Eastern time (hour, minute)
_ENTRY_WINDOWS = {
    "post_open": (time(9, 35), time(10, 0)),
    "midday": (time(11, 0), time(12, 0)),
    "afternoon": (time(14, 0), time(14, 30)),
}


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


class ZeroDTEScanner:
    """Timing-gated 0DTE/1DTE credit spread scanner for SPY and SPX.

    Uses the same ``CreditSpreadStrategy`` as the main system, but with
    a 0DTE config overlay (min_dte=0, tighter deltas, $5 wide spreads).
    Scans are gated to specific intraday windows when 0DTE setups are
    most reliable.
    """

    def __init__(self, base_config: dict, data_cache=None):
        self._base_config = base_config
        self._config = build_zero_dte_config(base_config)
        self._data_cache = data_cache

        # Instantiate separate strategy/analysis components for 0DTE
        self._strategy = CreditSpreadStrategy(self._config)
        self._options_analyzer = OptionsAnalyzer(self._config, data_cache=data_cache)
        self._technical_analyzer = TechnicalAnalyzer(self._config)

        logger.info("ZeroDTEScanner initialized (SPY + SPX, 0-1 DTE)")

    # ------------------------------------------------------------------
    # Timing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_in_entry_window(now_et: Optional[datetime] = None) -> bool:
        """Return True if the current ET time falls in any entry window."""
        t = _now_et(now_et).time()
        for start, end in _ENTRY_WINDOWS.values():
            if start <= t < end:
                return True
        return False

    @staticmethod
    def active_window_name(now_et: Optional[datetime] = None) -> str:
        """Return the name of the active entry window, or ``'none'``."""
        t = _now_et(now_et).time()
        for name, (start, end) in _ENTRY_WINDOWS.items():
            if start <= t < end:
                return name
        return "none"

    # ------------------------------------------------------------------
    # Main scan
    # ------------------------------------------------------------------

    def scan(self, now_et: Optional[datetime] = None) -> List[Dict]:
        """Run the 0DTE scan pipeline.

        1. Gate: return [] if outside an entry window.
        2. For each ticker (SPY, SPX): fetch data, run analysis.
        3. Annotate results with 0DTE metadata.

        Returns:
            List of opportunity dicts (same shape as ``SpreadOpportunity``).
        """
        if not self.is_in_entry_window(now_et):
            return []

        window = self.active_window_name(now_et)
        logger.info(f"0DTE scan: entry window '{window}' active")

        results: List[Dict] = []
        for ticker in self._config["tickers"]:
            try:
                opps = self._scan_ticker(ticker, window)
                results.extend(opps)
            except Exception as e:
                logger.error(f"0DTE scan failed for {ticker}: {e}", exc_info=True)

        logger.info(f"0DTE scan complete: {len(results)} opportunities")
        return results

    # ------------------------------------------------------------------
    # Per-ticker scan
    # ------------------------------------------------------------------

    def _scan_ticker(self, ticker: str, window: str) -> List[Dict]:
        """Scan a single ticker for 0DTE opportunities."""
        # SPX needs special handling — price data comes from ^GSPC
        price_ticker = ticker
        if ticker == "SPX":
            if not self._has_spx_provider():
                logger.warning(
                    "Skipping SPX: no Polygon or Tradier provider configured "
                    "(yfinance does not support SPX options)"
                )
                return []
            price_ticker = SPX_PROPERTIES["price_ticker"]  # ^GSPC

        # Fetch price data (Polygon via DataCache; no yfinance fallback)
        cache = self._data_cache or DataCache()
        price_data = cache.get_history(price_ticker, period="1y")

        if price_data is None or (hasattr(price_data, "empty") and price_data.empty):
            logger.warning(f"No price data for {price_ticker}")
            return []

        current_price = float(price_data["Close"].iloc[-1])

        # Fetch options chain (OptionsAnalyzer handles provider routing)
        options_chain = self._options_analyzer.get_options_chain(ticker)
        if options_chain is None or (hasattr(options_chain, "empty") and options_chain.empty):
            logger.warning(f"No options chain for {ticker}")
            return []

        # Technical analysis
        technical_signals = self._technical_analyzer.analyze(ticker, price_data)

        # IV analysis
        current_iv = self._options_analyzer.get_current_iv(options_chain)
        iv_data = self._options_analyzer.calculate_iv_rank(price_ticker, current_iv)

        # Evaluate spreads
        opportunities = self._strategy.evaluate_spread_opportunity(
            ticker=ticker,
            option_chain=options_chain,
            technical_signals=technical_signals,
            iv_data=iv_data,
            current_price=current_price,
        )

        # Annotate each opportunity with 0DTE metadata
        for opp in opportunities:
            opp["alert_source"] = "zero_dte"
            opp["entry_window"] = window
            if ticker == "SPX":
                opp["settlement"] = SPX_PROPERTIES["settlement"]
                opp["exercise_style"] = SPX_PROPERTIES["exercise_style"]
                opp["tax_treatment"] = SPX_PROPERTIES["tax_treatment"]
                opp["management_instructions"] = (
                    "Cash-settled, no assignment risk. "
                    "Close at 50% profit or 2x credit stop. "
                    "Section 1256: 60% long-term / 40% short-term tax treatment."
                )

        return opportunities

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _has_spx_provider(self) -> bool:
        """Return True if a provider capable of SPX options is configured."""
        return (
            self._options_analyzer.tradier is not None
            or self._options_analyzer.polygon is not None
        )
