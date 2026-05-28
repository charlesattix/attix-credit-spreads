"""Equity-history backfill + off-hours portfolio push.

Makes the dashboard equity chart show inception→now for every experiment,
**always** — not just during market-hours Tier-2 scans:

  - ``backfill_equity_history()`` bulk-upserts the full daily curve from Alpaca's
    ``/v2/account/portfolio/history`` into the ``equity_history`` table
    (idempotent — by (exp_id, date)).
  - ``push_portfolio_snapshot()`` pushes a complete portfolio body (incl. the
    curve) to the dashboard — runnable on startup / periodically / off-hours, so
    the chart renders 24/7 and never goes blank.
  - ``refresh_and_push()`` does both; reads creds/dashboard from env by default.

Best-effort throughout: any network/DB failure is logged and swallowed so it
never disrupts the caller (e.g. the PositionMonitor loop).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from shared.database import bulk_upsert_equity_points, get_equity_history

logger = logging.getLogger(__name__)

ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"


def fetch_portfolio_history(
    api_key: str,
    api_secret: str,
    period: str = "1A",
    timeframe: str = "1D",
    base_url: str = ALPACA_PAPER_URL,
) -> List[Dict]:
    """Fetch the daily equity curve from Alpaca portfolio history.

    Returns ``[{"date","equity","profit_loss"}, ...]`` ascending, filtered to
    non-zero equity. ``period`` defaults to ``1A`` (1 year of daily points) so
    inception is covered for any account younger than a year.
    """
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
    resp = requests.get(
        f"{base_url}/v2/account/portfolio/history",
        headers=headers,
        params={"period": period, "timeframe": timeframe},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    timestamps = data.get("timestamp") or []
    equities = data.get("equity") or []
    pnls = data.get("profit_loss") or []
    out: List[Dict] = []
    for i, ts in enumerate(timestamps):
        eq = equities[i] if i < len(equities) else None
        if eq and eq > 0:
            d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            pl = pnls[i] if i < len(pnls) else 0
            out.append({"date": d, "equity": round(float(eq), 2),
                        "profit_loss": round(float(pl or 0), 2)})
    return out


def backfill_equity_history(
    exp_id: str, db_path: Optional[str], api_key: str, api_secret: str,
    period: str = "1A",
) -> int:
    """Backfill the full inception→now curve into equity_history (idempotent)."""
    try:
        points = fetch_portfolio_history(api_key, api_secret, period=period)
    except Exception as e:
        logger.warning("[equity] exp=%s action=backfill_skipped error=%s", exp_id, e)
        return 0
    if not points:
        logger.info("[equity] exp=%s action=backfill source=alpaca points=0 (empty history)", exp_id)
        return 0
    # Backfill owns PAST days only. TODAY's point is the live current equity
    # (written by the PositionMonitor cycle / push) — Alpaca's portfolio-history
    # current-day value lags at the prior close, so never let it overwrite the
    # live "today" point.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    past = [p for p in points if p.get("date") != today]
    if not past:
        logger.info("[equity] exp=%s action=backfill source=alpaca points=0 (only today, skipped)", exp_id)
        return 0
    try:
        bulk_upsert_equity_points(exp_id, past, source="alpaca_backfill", path=db_path)
    except Exception as e:
        logger.warning("[equity] exp=%s action=backfill_db_failed error=%s", exp_id, e)
        return 0
    logger.info(
        "[equity] exp=%s action=backfill source=alpaca points=%d range=%s..%s (today excluded)",
        exp_id, len(past), past[0]["date"], past[-1]["date"],
    )
    return len(past)


def push_portfolio_snapshot(
    exp_id: str, db_path: Optional[str],
    dashboard_url: str, dashboard_api_key: str,
    api_key: str, api_secret: str,
) -> bool:
    """Push a complete portfolio body (equity/positions/orders + curve) to the
    dashboard so the chart renders regardless of market state.

    Sends the same shape as the market-hours scan push, so it never clobbers the
    experiment's account/positions block.
    """
    if not (dashboard_url and dashboard_api_key and api_key and api_secret):
        return False
    base = dashboard_url if dashboard_url.startswith("http") else f"https://{dashboard_url}"
    h = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
    try:
        acct = requests.get(f"{ALPACA_PAPER_URL}/v2/account", headers=h, timeout=10).json()
        positions = requests.get(f"{ALPACA_PAPER_URL}/v2/positions", headers=h, timeout=10).json()
        orders = requests.get(f"{ALPACA_PAPER_URL}/v2/orders?status=open&limit=50", headers=h, timeout=10).json()
    except Exception as e:
        logger.warning("[equity] exp=%s action=push_skipped error=%s", exp_id, e)
        return False
    try:
        curve = get_equity_history(exp_id, limit=365, path=db_path)
    except Exception:
        curve = []
    try:
        requests.post(
            f"{base.rstrip('/')}/api/v1/experiments/{exp_id}/push-portfolio",
            json={
                "equity": float((acct or {}).get("equity") or 0),
                "cash": float((acct or {}).get("cash") or 0),
                "buying_power": float((acct or {}).get("buying_power") or 0),
                "unrealized_pl": float((acct or {}).get("unrealized_pl") or 0),
                "positions": positions if isinstance(positions, list) else [],
                "orders": orders if isinstance(orders, list) else [],
                "equity_history": curve,
            },
            headers={"X-API-Key": dashboard_api_key},
            timeout=10,
        )
    except Exception as e:
        logger.warning("[equity] exp=%s action=push_failed error=%s", exp_id, e)
        return False
    logger.info("[equity] exp=%s action=pushed points=%d", exp_id, len(curve))
    return True


def refresh_and_push(
    exp_id: str, db_path: Optional[str], *,
    api_key: Optional[str] = None, api_secret: Optional[str] = None,
    dashboard_url: Optional[str] = None, dashboard_api_key: Optional[str] = None,
    period: str = "1A",
) -> int:
    """Backfill the curve from Alpaca + push it to the dashboard, in one call.

    Creds/dashboard default to the standard env vars when not provided. Returns
    the number of points backfilled. Always attempts the push (so the chart is
    repopulated even if the backfill added nothing new).
    """
    api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
    api_secret = api_secret or os.environ.get("ALPACA_API_SECRET", "")
    dashboard_url = dashboard_url or os.environ.get("RAILWAY_SERVICE_ATTIX_DASHBOARD_URL", "") \
        or os.environ.get("RAILWAY_SERVICE_ATTIX_CREDIT_SPREADS_URL", "")
    dashboard_api_key = dashboard_api_key or os.environ.get("DASHBOARD_API_KEY", "")

    n = 0
    if api_key and api_secret:
        n = backfill_equity_history(exp_id, db_path, api_key, api_secret, period=period)
    push_portfolio_snapshot(exp_id, db_path, dashboard_url, dashboard_api_key, api_key, api_secret)
    return n
