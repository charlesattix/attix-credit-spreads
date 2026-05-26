"""Thin REST wrapper for the Unusual Whales API.

Used by ``shared.earnings_calendar`` as the forward-earnings backend after
the yfinance → UW migration (D2). Only the endpoints required for the
earnings-volatility scanner are exposed here.

Reference: https://unusualwhales.com/skill.md

Required environment:
    UW_API_TOKEN  — UUID token issued by Unusual Whales.

Every request carries both:
    Authorization: Bearer <token>
    UW-CLIENT-API-ID: 100001
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import List, Optional

import requests

from shared.exceptions import DataFetchError

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.unusualwhales.com"
_REQUEST_TIMEOUT = 30
_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1, 2, 4)
_CLIENT_API_ID = "100001"


class UWClient:
    """REST client for Unusual Whales earnings endpoints.

    Holds a thread-safe in-memory TTL cache keyed by ``(path, sorted params)``
    so repeated calls within the same scan cycle don't hammer the API. The
    default TTL (24h) matches the previous yfinance-backed ``EarningsCalendar``
    cache.
    """

    def __init__(
        self,
        api_token: Optional[str] = None,
        cache_ttl_seconds: int = 86400,
        session: Optional[requests.Session] = None,
    ):
        self._token = api_token or os.getenv("UW_API_TOKEN", "")
        if not self._token:
            logger.warning("UW_API_TOKEN not set; UW requests will fail")
        self._session = session or requests.Session()
        self._cache: dict = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl = cache_ttl_seconds

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "UW-CLIENT-API-ID": _CLIENT_API_ID,
            "Accept": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> List[dict]:
        """GET ``path`` and return the ``data`` array from the JSON body.

        Retries on 429 / 5xx (1s / 2s / 4s backoff). Raises
        :class:`DataFetchError` on permanent failure.
        """
        if not self._token:
            raise DataFetchError("UW_API_TOKEN not configured")

        params = {k: v for k, v in (params or {}).items() if v is not None}
        cache_key = (path, tuple(sorted(params.items())))

        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry is not None:
                data, ts = entry
                if time.time() - ts < self._cache_ttl:
                    return data

        url = f"{_BASE_URL}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._session.get(
                    url,
                    headers=self._headers(),
                    params=params or None,
                    timeout=_REQUEST_TIMEOUT,
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    data = payload.get("data", []) or []
                    with self._cache_lock:
                        self._cache[cache_key] = (data, time.time())
                    return data
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    logger.warning(
                        "UW %s on %s (attempt %d/%d) — retrying",
                        resp.status_code, path, attempt + 1, _MAX_RETRIES,
                    )
                    if attempt < _MAX_RETRIES - 1:
                        time.sleep(_BACKOFF_SECONDS[attempt])
                        continue
                raise DataFetchError(
                    f"UW HTTP {resp.status_code} for {path}: {resp.text[:200]}"
                )
            except requests.RequestException as e:
                last_exc = e
                logger.warning(
                    "UW request error for %s (attempt %d/%d): %s",
                    path, attempt + 1, _MAX_RETRIES, e,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise DataFetchError(
                    f"UW request failed for {path} after {_MAX_RETRIES} attempts: {e}"
                ) from e

        raise DataFetchError(
            f"UW request failed for {path} after {_MAX_RETRIES} attempts: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_earnings_history(self, ticker: str) -> List[dict]:
        """``GET /api/stock/{ticker}/earnings`` — past + upcoming earnings.

        Returns the ``data`` array. Item shape is determined by the UW API
        (typically includes a report date and pre-computed expected-move
        fields). Callers should treat individual fields defensively.
        """
        return self._get(f"/api/stock/{ticker.upper()}/earnings")

    def get_earnings_premarket(self, date: Optional[str] = None) -> List[dict]:
        """``GET /api/earnings/premarket?date=YYYY-MM-DD``.

        Omit ``date`` for today's premarket earnings releases.
        """
        return self._get("/api/earnings/premarket", params={"date": date})

    def get_earnings_afterhours(self, date: Optional[str] = None) -> List[dict]:
        """``GET /api/earnings/afterhours?date=YYYY-MM-DD``.

        Omit ``date`` for today's after-hours earnings releases.
        """
        return self._get("/api/earnings/afterhours", params={"date": date})

    def get_options_chain(self, ticker: str) -> "pd.DataFrame":
        """``GET /api/stock/{ticker}/option-contracts`` — full options chain.

        Returns a DataFrame compatible with the ``OptionsAnalyzer`` pipeline:
        columns include ``option_type``, ``strike``, ``expiration``, ``bid``,
        ``ask``, ``iv``, ``volume``, ``open_interest``, ``delta``, ``gamma``,
        ``theta``, ``vega``.
        """
        import re
        import pandas as pd

        raw = self._get(f"/api/stock/{ticker.upper()}/option-contracts")
        if not raw:
            return pd.DataFrame()

        rows = []
        for c in raw:
            sym = c.get("option_symbol", "")
            # Parse OCC symbol: e.g. SPY260620C00600000
            m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d{8})$", sym)
            if not m:
                continue
            _, date_str, cp, strike_raw = m.groups()
            exp_date = f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}"
            strike = int(strike_raw) / 1000.0

            bid = float(c.get("nbbo_bid", 0) or 0)
            ask = float(c.get("nbbo_ask", 0) or 0)
            iv = float(c.get("implied_volatility", 0) or 0)

            rows.append({
                "option_type": "call" if cp == "C" else "put",
                "strike": strike,
                "expiration": exp_date,
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2 if (bid + ask) > 0 else float(c.get("last_price", 0) or 0),
                "last": float(c.get("last_price", 0) or 0),
                "iv": iv,
                "volume": int(c.get("volume", 0) or 0),
                "open_interest": int(c.get("open_interest", 0) or 0),
                "delta": 0.0,   # UW doesn't provide Greeks in this endpoint
                "gamma": 0.0,
                "theta": 0.0,
                "vega": 0.0,
                "symbol": sym,
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        logger.info("UW: got %d options for %s", len(df), ticker)
        return df

    def clear_cache(self) -> None:
        """Clear the in-memory response cache (used by tests)."""
        with self._cache_lock:
            self._cache.clear()
