"""
alpaca_live.py — Live Alpaca API data for the dashboard.

Reads per-experiment keys from env vars:
  ALPACA_API_KEY_EXP400 / ALPACA_API_SECRET_EXP400
  ALPACA_API_KEY_EXP401 / ALPACA_API_SECRET_EXP401
  ... etc.

Fetches account equity, open positions, and recent orders directly from
Alpaca paper-trading REST API. Results are cached 60s to avoid rate limits.

Graceful degradation: any error returns None / empty list so callers can
fall back to local DB / pushed-data.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ALPACA_BASE = "https://paper-api.alpaca.markets"
CACHE_TTL   = 60.0  # seconds

# {normalized_id: (timestamp, data_dict)}
_cache: dict[str, tuple[float, dict]] = {}


# ---------------------------------------------------------------------------
# Key discovery
# ---------------------------------------------------------------------------

def discover_experiment_keys() -> dict[str, tuple[str, str]]:
    """
    Scan environment for ALPACA_API_KEY_EXP* vars.

    Returns {normalized_id: (api_key, api_secret)}
    e.g. {"EXP400": ("PKXXX", "secretXXX"), "EXP401": ...}
    """
    keys: dict[str, tuple[str, str]] = {}
    for var, val in os.environ.items():
        if not var.startswith("ALPACA_API_KEY_EXP") or not val:
            continue
        suffix = var[len("ALPACA_API_KEY_"):]          # "EXP400"
        secret = os.environ.get(f"ALPACA_API_SECRET_{suffix}", "")
        if secret:
            keys[suffix] = (val, secret)
    return keys


def _normalize(exp_id: str) -> str:
    """EXP-400 → EXP400"""
    return exp_id.upper().replace("-", "")


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(api_key: str, api_secret: str, path: str, params: dict | None = None):
    """Single Alpaca REST GET. Raises httpx.HTTPStatusError on bad status."""
    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }
    resp = httpx.get(
        f"{ALPACA_BASE}{path}",
        headers=headers,
        params=params or {},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Per-experiment fetch
# ---------------------------------------------------------------------------

def fetch_live_data(normalized_id: str, api_key: str, api_secret: str) -> dict:
    """
    Fetch account + positions + orders for one experiment.

    Returns a dict matching the shape html.py expects under s["alpaca"]:
      equity, buying_power, cash, unrealized_pl, day_pl,
      positions (list), orders (list), error, fetched_at
    """
    result: dict = {
        "equity":       None,
        "buying_power": None,
        "cash":         None,
        "unrealized_pl": None,
        "day_pl":       None,
        "positions":    [],
        "orders":       [],
        "error":        None,
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
    }

    # --- Account (required; abort on failure) --------------------------------
    try:
        acct = _get(api_key, api_secret, "/v2/account")
        result["equity"]        = float(acct.get("equity")                  or 0)
        result["buying_power"]  = float(acct.get("buying_power")            or 0)
        result["cash"]          = float(acct.get("cash")                    or 0)
        result["unrealized_pl"] = float(acct.get("unrealized_pl")           or 0)
        result["day_pl"]        = float(acct.get("unrealized_intraday_pl")  or 0)
    except Exception as exc:
        result["error"] = f"account: {exc}"
        logger.warning("[alpaca_live] %s account error: %s", normalized_id, exc)
        return result

    # --- Positions (non-fatal) -----------------------------------------------
    try:
        positions = _get(api_key, api_secret, "/v2/positions")
        result["positions"] = positions if isinstance(positions, list) else []
    except Exception as exc:
        logger.warning("[alpaca_live] %s positions error: %s", normalized_id, exc)

    # --- Orders last 30 days (non-fatal) -------------------------------------
    try:
        after = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        orders = _get(api_key, api_secret, "/v2/orders", {
            "status":    "all",
            "after":     after,
            "limit":     500,
            "direction": "desc",
        })
        result["orders"] = orders if isinstance(orders, list) else []
    except Exception as exc:
        logger.warning("[alpaca_live] %s orders error: %s", normalized_id, exc)

    return result


# ---------------------------------------------------------------------------
# Public API — with caching
# ---------------------------------------------------------------------------

def get_live_alpaca(exp_id: str) -> Optional[dict]:
    """
    Live Alpaca data for one experiment (60s cache).
    exp_id may be "EXP-400" or "EXP400".
    Returns None if no keys configured or on error.
    """
    norm = _normalize(exp_id)
    keys = discover_experiment_keys()
    creds = keys.get(norm)
    if not creds:
        return None

    cached = _cache.get(norm)
    if cached and (time.time() - cached[0]) < CACHE_TTL:
        return cached[1]

    api_key, api_secret = creds
    data = fetch_live_data(norm, api_key, api_secret)
    _cache[norm] = (time.time(), data)
    return data


def get_all_live_alpaca() -> dict[str, dict]:
    """
    Fetch live Alpaca data for ALL configured experiments in parallel.

    Returns {normalized_id: alpaca_dict}  e.g. {"EXP400": {...}, "EXP401": {...}}
    Only includes experiments that have keys configured in env.
    """
    all_keys = discover_experiment_keys()
    if not all_keys:
        return {}

    results: dict[str, dict] = {}
    uncached: dict[str, tuple[str, str]] = {}

    for norm, creds in all_keys.items():
        cached = _cache.get(norm)
        if cached and (time.time() - cached[0]) < CACHE_TTL:
            results[norm] = cached[1]
        else:
            uncached[norm] = creds

    if uncached:
        with ThreadPoolExecutor(max_workers=min(len(uncached), 8)) as pool:
            futures = {
                pool.submit(fetch_live_data, norm, key, secret): norm
                for norm, (key, secret) in uncached.items()
            }
            for future in as_completed(futures):
                norm = futures[future]
                try:
                    data = future.result()
                    _cache[norm] = (time.time(), data)
                    results[norm] = data
                except Exception as exc:
                    logger.error("[alpaca_live] %s fetch failed: %s", norm, exc)

    return results
