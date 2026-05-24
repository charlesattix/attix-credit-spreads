"""
Earnings calendar, expected move calculator, and historical stay-in-range analysis.

Backed by the Unusual Whales API via :mod:`shared.uw_client` (D2 migration).
Polygon is used for the 5y price history needed by the historical
stay-in-range calculation. ``yfinance`` is no longer used on this path.

Public surface preserved for existing callers (``alerts.earnings_scanner``).
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from shared.uw_client import UWClient

logger = logging.getLogger(__name__)

# ETFs and indices that don't have earnings
_NO_EARNINGS_TICKERS = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV",
    "SMH", "ARKK", "TLT", "GLD", "SLV", "VIX", "^VIX",
})

# Fields that may carry the report date in a UW earnings item, in priority order.
_DATE_FIELDS = ("report_date", "earnings_date", "date", "expected_date")


def _parse_date(raw) -> Optional[datetime]:
    """Best-effort parse of a UW date field into a UTC-aware datetime."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw:
        return None
    s = raw.strip()
    # Date-only "YYYY-MM-DD"
    try:
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # ISO datetime, possibly with "Z"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _extract_date(item: dict) -> Optional[datetime]:
    for k in _DATE_FIELDS:
        if k in item:
            parsed = _parse_date(item[k])
            if parsed is not None:
                return parsed
    return None


class EarningsCalendar:
    """Earnings date fetcher with caching and historical analysis (UW-backed)."""

    def __init__(self, data_cache=None, uw_client: Optional[UWClient] = None):
        self._data_cache = data_cache
        self._uw = uw_client or UWClient()
        # Simple earnings cache: ticker -> (datetime|None, fetched_at)
        self._earnings_cache: Dict[str, tuple] = {}
        self._cache_ttl_hours = 24

    # ------------------------------------------------------------------
    # Internal: fetch & parse UW history for a ticker
    # ------------------------------------------------------------------

    def _fetch_history(self, ticker: str) -> List[dict]:
        try:
            return self._uw.get_earnings_history(ticker)
        except Exception as e:
            logger.warning(f"UW earnings history failed for {ticker}: {e}")
            return []

    # ------------------------------------------------------------------
    # Public: next earnings date
    # ------------------------------------------------------------------

    def get_next_earnings(self, ticker: str) -> Optional[datetime]:
        """Get the next upcoming earnings date for a ticker (or None).

        Returns None for ETFs/indices and for tickers with no upcoming
        scheduled earnings in the UW response.
        """
        if ticker in _NO_EARNINGS_TICKERS:
            return None

        if ticker in self._earnings_cache:
            cached_date, fetched_at = self._earnings_cache[ticker]
            age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
            if age_hours < self._cache_ttl_hours:
                return cached_date

        items = self._fetch_history(ticker)
        now = datetime.now(timezone.utc)
        future_dates: List[datetime] = []
        for item in items:
            dt = _extract_date(item)
            if dt is not None and dt >= now:
                future_dates.append(dt)

        next_date = min(future_dates) if future_dates else None
        self._earnings_cache[ticker] = (next_date, datetime.now(timezone.utc))
        return next_date

    # ------------------------------------------------------------------
    # Public: lookahead calendar across many tickers
    # ------------------------------------------------------------------

    def get_lookahead_calendar(
        self, tickers: List[str], days_ahead: int = 14
    ) -> List[Dict]:
        """Get upcoming earnings within a lookahead window.

        Returns a list of dicts sorted by days_until ascending:
        [{"ticker": str, "earnings_date": datetime, "days_until": int}, ...]
        """
        now = datetime.now(timezone.utc)
        results = []

        for ticker in tickers:
            earnings_date = self.get_next_earnings(ticker)
            if earnings_date is None:
                continue

            days_until = (earnings_date - now).days
            if 0 <= days_until <= days_ahead:
                results.append({
                    "ticker": ticker,
                    "earnings_date": earnings_date,
                    "days_until": days_until,
                })

        results.sort(key=lambda x: x["days_until"])
        return results

    # ------------------------------------------------------------------
    # Public: historical earnings dates
    # ------------------------------------------------------------------

    def get_historical_earnings_dates(
        self, ticker: str, num_quarters: int = 8
    ) -> List[datetime]:
        """Get past earnings dates for a ticker (most recent first)."""
        if ticker in _NO_EARNINGS_TICKERS:
            return []

        items = self._fetch_history(ticker)
        now = datetime.now(timezone.utc)
        past_dates: List[datetime] = []
        for item in items:
            dt = _extract_date(item)
            if dt is not None and dt < now:
                past_dates.append(dt)

        past_dates.sort(reverse=True)
        return past_dates[:num_quarters]

    # ------------------------------------------------------------------
    # Public: expected move from ATM straddle
    # ------------------------------------------------------------------

    def calculate_expected_move(
        self, options_chain, current_price: float
    ) -> Optional[float]:
        """Calculate expected move from ATM straddle mid-prices.

        Finds the ATM call and ATM put (closest strikes to ``current_price``),
        computes their mid-prices, and returns the sum as the implied
        expected move in dollars.

        Note: UW's earnings endpoints do not expose a pre-computed expected
        move (verified against the live API 2026-05-22). The ATM-straddle
        derivation has no yfinance dependency, so it is retained here.

        Args:
            options_chain: DataFrame with columns: strike, type, bid, ask
            current_price: Current underlying price

        Returns:
            Expected move in dollars, or ``None`` if calculation fails.
        """
        try:
            if options_chain is None or (
                hasattr(options_chain, "empty") and options_chain.empty
            ):
                return None

            calls = options_chain[options_chain["type"] == "call"] if "type" in options_chain.columns else None
            puts = options_chain[options_chain["type"] == "put"] if "type" in options_chain.columns else None

            if calls is None or puts is None:
                return None
            if hasattr(calls, "empty") and calls.empty:
                return None
            if hasattr(puts, "empty") and puts.empty:
                return None

            calls = calls.copy()
            calls["_dist"] = (calls["strike"] - current_price).abs()
            atm_call = calls.loc[calls["_dist"].idxmin()]
            call_mid = (float(atm_call.get("bid", 0)) + float(atm_call.get("ask", 0))) / 2

            puts = puts.copy()
            puts["_dist"] = (puts["strike"] - current_price).abs()
            atm_put = puts.loc[puts["_dist"].idxmin()]
            put_mid = (float(atm_put.get("bid", 0)) + float(atm_put.get("ask", 0))) / 2

            if call_mid <= 0 or put_mid <= 0:
                return None

            return round(call_mid + put_mid, 2)

        except Exception as e:
            logger.warning(f"Expected move calculation failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Public: historical stay-in-range
    # ------------------------------------------------------------------

    def calculate_historical_stay_in_range(
        self, ticker: str, num_quarters: int = 8
    ) -> Dict:
        """Analyze historical earnings moves vs HV-approximated expected move.

        For each historical earnings date:
        - Get pre-earnings close and post-earnings close
        - Compute actual move percentage
        - Approximate expected move from 30-day historical volatility

        5-year daily price history is fetched directly from Polygon (the
        live ``DataCache`` is bounded to 1y, so we go to ``PolygonClient``
        for this one read-only call).
        """
        default = {
            "stay_in_range_pct": 0.0,
            "avg_move_pct": 0.0,
            "total_quarters": 0,
            "quarters_in_range": 0,
        }

        try:
            hist_dates = self.get_historical_earnings_dates(ticker, num_quarters)
            if not hist_dates:
                return default

            closes = self._fetch_5y_closes(ticker)
            if closes is None or closes.empty:
                return default

            total = 0
            in_range = 0
            move_pcts: List[float] = []

            for earnings_dt in hist_dates:
                try:
                    earnings_date = earnings_dt.date()

                    prior_closes = closes[closes.index.date < earnings_date]
                    if len(prior_closes) < 30:
                        continue
                    pre_close = float(prior_closes.iloc[-1])

                    post_closes = closes[closes.index.date > earnings_date]
                    if post_closes.empty:
                        continue
                    post_close = float(post_closes.iloc[0])

                    actual_move_pct = abs(post_close - pre_close) / pre_close * 100

                    recent = prior_closes.iloc[-30:]
                    returns = recent.pct_change().dropna()
                    if len(returns) < 5:
                        continue
                    daily_vol = float(returns.std())
                    expected_move_pct = daily_vol * 100

                    total += 1
                    move_pcts.append(actual_move_pct)
                    if actual_move_pct <= expected_move_pct * 1.2:
                        in_range += 1

                except Exception:
                    continue

            if total == 0:
                return default

            return {
                "stay_in_range_pct": round(in_range / total * 100, 1),
                "avg_move_pct": round(sum(move_pcts) / len(move_pcts), 2),
                "total_quarters": total,
                "quarters_in_range": in_range,
            }

        except Exception as e:
            logger.warning(f"Historical stay-in-range calculation failed for {ticker}: {e}")
            return default

    # ------------------------------------------------------------------
    # Internal: 5y daily closes via Polygon
    # ------------------------------------------------------------------

    def _fetch_5y_closes(self, ticker: str):
        """Fetch ~5y of daily closes for ``ticker`` from Polygon.

        Returns a ``pd.Series`` of closes indexed by date (timezone-naive),
        or ``None`` on failure.
        """
        try:
            from datetime import timedelta

            import pandas as pd

            from shared.polygon_client import PolygonClient

            client = PolygonClient()
            today = datetime.now(timezone.utc).date()
            from_date = (today - timedelta(days=365 * 5 + 30)).isoformat()
            to_date = today.isoformat()
            results = client.aggregates(
                ticker=ticker.upper(),
                multiplier=1,
                timespan="day",
                from_date=from_date,
                to_date=to_date,
            )
            if not results:
                return None
            df = pd.DataFrame(results)
            df["Date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(None).dt.normalize()
            df = df.set_index("Date").sort_index()
            return df["c"].rename("Close")
        except Exception as e:
            logger.warning(f"Polygon 5y close fetch failed for {ticker}: {e}")
            return None
