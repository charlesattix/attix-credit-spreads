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

from .env_helpers import getenv_or_default

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
        # `not val` skips empty-string keys (the empty-string footgun): a
        # present-but-blank ALPACA_API_KEY_EXP* must NOT be treated as configured.
        if not var.startswith("ALPACA_API_KEY_EXP") or not (val and val.strip()):
            continue
        suffix = var[len("ALPACA_API_KEY_"):]          # "EXP400"
        secret = getenv_or_default(f"ALPACA_API_SECRET_{suffix}", "")
        if secret:
            keys[suffix] = (val, secret)
        else:
            # Key present but its secret is missing/blank — a half-configured
            # credential that would silently disable this experiment's live data.
            logger.warning(
                "[alpaca_live] %s has an API key but ALPACA_API_SECRET_%s is "
                "missing or empty — skipping (no live data for this experiment)",
                suffix, suffix,
            )
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
# Opening-fill enrichment
# ---------------------------------------------------------------------------

def _attach_opened_at(positions: list, orders: list) -> None:
    """
    Enrich each open position dict in-place with an ``opened_at`` field: the
    ISO-8601 UTC timestamp of the order that opened that leg.

    A long position is opened by a ``buy``; a short by a ``sell``. We scan the
    already-fetched orders list — including the individual legs of multi-leg
    (MLEG) option orders — for filled fills matching the leg's symbol and
    opening side, and use the most recent ``filled_at`` among them.

    Always sets the key (to ``None`` when no matching fill is found) so the
    renderer can rely on its presence.
    """
    # {(symbol, side): latest_filled_at_iso}
    fills: dict[tuple[str, str], str] = {}

    def _record(symbol, side, filled_at) -> None:
        if not symbol or not side or not filled_at:
            return
        key = (symbol, str(side).lower())
        prev = fills.get(key)
        # filled_at values are same-format UTC ISO strings → lexical max == latest
        if prev is None or filled_at > prev:
            fills[key] = filled_at

    for o in orders or []:
        if not isinstance(o, dict):
            continue
        if o.get("status") == "filled":
            _record(o.get("symbol"), o.get("side"), o.get("filled_at"))
        # Legs of multi-leg option orders carry their own symbol/side/fill time.
        for leg in o.get("legs") or []:
            if isinstance(leg, dict) and leg.get("status") == "filled":
                _record(
                    leg.get("symbol"),
                    leg.get("side"),
                    leg.get("filled_at") or o.get("filled_at"),
                )

    for p in positions or []:
        if not isinstance(p, dict):
            continue
        opening_side = "buy" if p.get("side") == "long" else "sell"
        p["opened_at"] = fills.get((p.get("symbol"), opening_side))


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

    # --- Enrich positions with opening-fill timestamps (non-fatal) -----------
    try:
        _attach_opened_at(result["positions"], result["orders"])
    except Exception as exc:
        logger.warning("[alpaca_live] %s opened_at enrich error: %s", normalized_id, exc)

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
        logger.warning(
            "[alpaca_live] no ALPACA_API_KEY_EXP* env vars found (or all empty) — "
            "no live Alpaca data will be available"
        )
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
                    # fetch_live_data() never raises — it returns a dict with the
                    # "error" field populated. Surface that here so the exp_id and
                    # the underlying error are visible at the aggregation layer.
                    if data.get("error"):
                        logger.warning(
                            "[alpaca_live] %s fetch returned error: %s",
                            norm, data["error"],
                        )
                except Exception as exc:
                    logger.error("[alpaca_live] %s fetch failed: %s", norm, exc)

    return results
