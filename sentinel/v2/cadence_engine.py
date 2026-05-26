"""
sentinel/v2/cadence_engine.py — Trade cadence monitoring.

Independently queries Alpaca for actual trades and compares against the
expected cadence per experiment stream. Runs OUTSIDE the scanner process —
it detects silence even when scanners are dead.

Called by: SentinelWatchdog at 17:00 ET daily.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

LOG = logging.getLogger("sentinel.v2.cadence_engine")

# US market holidays (simplified — augment as needed)
_MARKET_HOLIDAYS_2026 = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}


def is_market_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _MARKET_HOLIDAYS_2026


def market_days_between(start: date, end: date) -> int:
    count = 0
    cur = start
    while cur <= end:
        if is_market_day(cur):
            count += 1
        cur += timedelta(days=1)
    return count


@dataclass
class CadenceSpec:
    exp_id: str
    display_name: str
    frequency: Literal["weekly", "biweekly", "monthly"]
    expected_entry_weekday: int    # 0=Monday, 4=Friday
    min_trades_per_period: int
    lookback_periods: int
    zero_trade_alert_market_days: int = 14
    alpaca_env_file: str = ""      # path to .env.expXXX for credentials


@dataclass
class CadenceResult:
    exp_id: str
    spec: CadenceSpec
    total_trades_in_window: int
    missed_periods: int
    last_trade_date: date | None
    market_days_since_last_trade: int
    status: Literal["ok", "info", "warn", "critical"]
    message: str
    details: list[str] = field(default_factory=list)


# ── Stream cadence registry ──────────────────────────────────────────────────

STREAM_CADENCE: dict[str, CadenceSpec] = {
    "EXP-400": CadenceSpec(
        exp_id="EXP-400", display_name="Champion",
        frequency="weekly", expected_entry_weekday=0,
        min_trades_per_period=1, lookback_periods=2,
        zero_trade_alert_market_days=10,
        alpaca_env_file=".env.champion",
    ),
    "EXP-401": CadenceSpec(
        exp_id="EXP-401", display_name="EXP-401",
        frequency="weekly", expected_entry_weekday=0,
        min_trades_per_period=1, lookback_periods=2,
        zero_trade_alert_market_days=10,
        alpaca_env_file=".env.exp401",
    ),
    # EXP-600 (IBIT Adaptive) retired 2026-05-26 — closed by Carlos. See registry.json.
    "EXP-1220": CadenceSpec(
        exp_id="EXP-1220", display_name="EXP-1220",
        frequency="weekly", expected_entry_weekday=0,
        min_trades_per_period=1, lookback_periods=2,
        zero_trade_alert_market_days=10,
        alpaca_env_file=".env.exp1220",
    ),
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _period_starts(spec: CadenceSpec, as_of: date) -> list[date]:
    """
    Return the start dates of the last N+1 periods for the given spec.
    For 'weekly': last N Mondays. For 'biweekly': last N fortnights.
    """
    if spec.frequency == "weekly":
        days_per_period = 7
    elif spec.frequency == "biweekly":
        days_per_period = 14
    else:  # monthly
        days_per_period = 30

    periods = []
    start = as_of - timedelta(days=as_of.weekday())  # most recent Monday
    for i in range(spec.lookback_periods + 1):
        periods.append(start - timedelta(days=i * days_per_period))
    return periods


def _fetch_alpaca_orders(env_file: str, since: date) -> list[dict]:
    """
    Fetch completed orders from Alpaca for this experiment's account.
    Returns list of dicts with keys: id, symbol, filled_at, side, qty.
    Returns empty list on any error (never raises).
    """
    try:
        from dotenv import dotenv_values
        creds = dotenv_values(env_file)
        api_key    = creds.get("ALPACA_API_KEY") or os.environ.get("ALPACA_API_KEY", "")
        api_secret = creds.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_API_SECRET", "")
        if not api_key or not api_secret:
            LOG.warning("cadence_engine: no credentials for %s", env_file)
            return []

        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        client = TradingClient(api_key, api_secret, paper=True)
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=datetime.combine(since, datetime.min.time()),
            limit=100,
        )
        orders = client.get_orders(req)
        return [
            {
                "id": str(o.id),
                "symbol": str(o.symbol),
                "filled_at": o.filled_at.date() if o.filled_at else None,
                "side": str(o.side),
            }
            for o in orders
            if o.filled_at is not None
        ]
    except Exception as exc:
        LOG.error("cadence_engine: Alpaca fetch failed for %s: %s", env_file, exc)
        return []


# ── Core cadence check ───────────────────────────────────────────────────────

def check_cadence(spec: CadenceSpec, as_of: date) -> CadenceResult:
    """
    Check whether experiment spec.exp_id is trading at its expected cadence.
    Queries Alpaca directly — does not depend on local trade DBs or scanner state.
    """
    lookback_days = {
        "weekly":   spec.lookback_periods * 7 + 7,
        "biweekly": spec.lookback_periods * 14 + 14,
        "monthly":  spec.lookback_periods * 30 + 30,
    }[spec.frequency]

    since = as_of - timedelta(days=lookback_days)
    orders = _fetch_alpaca_orders(spec.alpaca_env_file, since)

    # Group trades by period
    period_starts = _period_starts(spec, as_of)
    period_starts_sorted = sorted(period_starts)

    period_trade_counts: list[int] = []
    for i, ps in enumerate(period_starts_sorted[:-1]):  # skip current incomplete period
        pe = period_starts_sorted[i + 1] - timedelta(days=1)
        count = sum(1 for o in orders if o["filled_at"] and ps <= o["filled_at"] <= pe)
        period_trade_counts.append(count)

    missed_periods = sum(1 for c in period_trade_counts if c < spec.min_trades_per_period)
    total_trades = len(orders)

    # Last trade date
    trade_dates = [o["filled_at"] for o in orders if o["filled_at"]]
    last_trade = max(trade_dates) if trade_dates else None
    mdays_since = market_days_between(last_trade + timedelta(days=1), as_of) if last_trade else 999

    details = [
        f"Period counts (oldest→newest): {period_trade_counts}",
        f"Last trade: {last_trade or 'NEVER'}",
        f"Market days since last trade: {mdays_since}",
        f"Total trades in window: {total_trades}",
    ]

    if total_trades == 0 and mdays_since >= spec.zero_trade_alert_market_days:
        status = "critical"
        msg = (
            f"{spec.exp_id}: ZERO trades in {mdays_since} market days "
            f"(threshold: {spec.zero_trade_alert_market_days}d) — system may be silent"
        )
    elif missed_periods >= 3:
        status = "critical"
        msg = f"{spec.exp_id}: {missed_periods} consecutive missed trade periods — CRITICAL"
    elif missed_periods == 2:
        status = "warn"
        msg = f"{spec.exp_id}: {missed_periods} consecutive missed trade periods — investigate"
    elif missed_periods == 1:
        status = "info"
        msg = f"{spec.exp_id}: 1 missed trade period — monitor"
    else:
        status = "ok"
        msg = f"{spec.exp_id}: cadence OK ({total_trades} trades in window)"

    return CadenceResult(
        exp_id=spec.exp_id,
        spec=spec,
        total_trades_in_window=total_trades,
        missed_periods=missed_periods,
        last_trade_date=last_trade,
        market_days_since_last_trade=mdays_since,
        status=status,
        message=msg,
        details=details,
    )


def check_all_active(
    active_exp_ids: list[str],
    as_of: date | None = None,
) -> list[CadenceResult]:
    """
    Run cadence checks for all active (non-halted) experiments.
    Skips halted experiments — no alert for intentionally stopped streams.
    """
    as_of = as_of or date.today()
    results = []
    for exp_id, spec in STREAM_CADENCE.items():
        if exp_id not in active_exp_ids:
            LOG.info("cadence_engine: skipping %s (halted or inactive)", exp_id)
            continue
        result = check_cadence(spec, as_of)
        LOG.info("cadence_engine: %s → %s: %s", exp_id, result.status, result.message)
        results.append(result)
    return results
