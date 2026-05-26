"""Thin wrapper around Polygon.io REST aggregates endpoint.

Routes index tickers (``I:``-prefixed) to ``POLYGON_INDICES_API_KEY``; all
other tickers (stocks, ETFs) to ``POLYGON_API_KEY``. Used by
``shared.data_cache`` as the OHLCV history backend on the live trade-decision
path.
"""
from __future__ import annotations

import logging
import os
import time
from typing import List, Optional

import requests

from shared.exceptions import DataFetchError

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.polygon.io"
_REQUEST_TIMEOUT = 30
_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1, 2, 4)


def _pick_key(ticker: str) -> str:
    """Return the Polygon API key for ``ticker``.

    Index tickers (``I:`` prefix, e.g. ``I:VIX``, ``I:VVIX``, ``I:SKEW``)
    are not authorized by Polygon's stocks plan; route them to
    ``POLYGON_INDICES_API_KEY``. Everything else uses ``POLYGON_API_KEY``.
    """
    if ticker.upper().startswith("I:"):
        return os.getenv("POLYGON_INDICES_API_KEY", "")
    return os.getenv("POLYGON_API_KEY", "")


class PolygonClient:
    """REST client for Polygon aggregates."""

    def __init__(
        self,
        stocks_api_key: Optional[str] = None,
        indices_api_key: Optional[str] = None,
    ):
        self._stocks_key = stocks_api_key or os.getenv("POLYGON_API_KEY", "")
        self._indices_key = indices_api_key or os.getenv("POLYGON_INDICES_API_KEY", "")
        if not self._stocks_key:
            logger.warning("POLYGON_API_KEY not set; stock aggregates will fail")
        if not self._indices_key:
            logger.warning("POLYGON_INDICES_API_KEY not set; index aggregates will fail")
        self._session = requests.Session()

    def _api_key_for(self, ticker: str) -> str:
        if ticker.upper().startswith("I:"):
            return self._indices_key
        return self._stocks_key

    def aggregates(
        self,
        ticker: str,
        multiplier: int,
        timespan: str,
        from_date: str,
        to_date: str,
    ) -> List[dict]:
        """Fetch aggregate bars.

        Returns a list of result dicts from Polygon (keys: ``t``, ``o``,
        ``h``, ``l``, ``c``, ``v``, ...). Empty list if no data. Raises
        :class:`DataFetchError` on permanent failure (after retries).
        """
        api_key = self._api_key_for(ticker)
        if not api_key:
            raise DataFetchError(
                f"No API key configured for {ticker} (stocks={'set' if self._stocks_key else 'unset'}, "
                f"indices={'set' if self._indices_key else 'unset'})"
            )

        url = (
            f"{_BASE_URL}/v2/aggs/ticker/{ticker}/range/"
            f"{multiplier}/{timespan}/{from_date}/{to_date}"
        )
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}

        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._session.get(url, params=params, timeout=_REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("results", []) or []
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    logger.warning(
                        "Polygon %s for %s (attempt %d/%d) — retrying",
                        resp.status_code, ticker, attempt + 1, _MAX_RETRIES,
                    )
                    if attempt < _MAX_RETRIES - 1:
                        time.sleep(_BACKOFF_SECONDS[attempt])
                        continue
                raise DataFetchError(
                    f"Polygon HTTP {resp.status_code} for {ticker}: {resp.text[:200]}"
                )
            except requests.RequestException as e:
                last_exc = e
                logger.warning(
                    "Polygon request error for %s (attempt %d/%d): %s",
                    ticker, attempt + 1, _MAX_RETRIES, e,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise DataFetchError(
                    f"Polygon request failed for {ticker} after {_MAX_RETRIES} attempts: {e}"
                ) from e

        raise DataFetchError(
            f"Polygon request failed for {ticker} after {_MAX_RETRIES} attempts: {last_exc}"
        )
