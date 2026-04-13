"""
SENTINEL — Telegram alert dispatcher with severity levels.

Severity tiers:
  CRITICAL  — immediate action required (orphan funds, dead API on active exp,
               duplicate accounts)
  WARNING   — investigate soon (stale experiment, directional conflict,
               expiry clustering)
  INFO      — status/informational (equity snapshot, all-clear)

The daily report is a single Telegram HTML message summarising all
active experiments, orphan accounts, portfolio exposure, and any issues.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from sentinel.monitor import ExperimentHealth
    from sentinel.portfolio import PortfolioRisk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Severity model
# ---------------------------------------------------------------------------


class Severity(Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class SentinelAlert:
    severity: Severity
    experiment_id: Optional[str]
    message: str
    category: str  # orphan | ghost | stale | duplicate | api | portfolio


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def _collect_alerts(
    monitor_results: List["ExperimentHealth"],
    portfolio: "PortfolioRisk",
) -> List[SentinelAlert]:
    alerts: List[SentinelAlert] = []

    for h in monitor_results:
        exp = h.exp_id

        if h.is_orphan:
            alerts.append(SentinelAlert(
                Severity.CRITICAL, exp,
                f"ORPHAN — {exp}: retired account {h.account_id} holds ${h.equity:,.0f}",
                "orphan",
            ))

        if h.is_ghost:
            detail = f" ({h.api_error})" if h.api_error else ""
            alerts.append(SentinelAlert(
                Severity.CRITICAL, exp,
                f"GHOST — {exp}: active in registry but Alpaca unreachable{detail}",
                "ghost",
            ))

        if h.is_stale:
            age = (
                f"{h.last_order_age_days}d" if h.last_order_age_days is not None else "unknown"
            )
            alerts.append(SentinelAlert(
                Severity.WARNING, exp,
                f"STALE — {exp}: no trades in {age} (last order: {h.last_order_at or 'never'})",
                "stale",
            ))

        if h.is_duplicate:
            alerts.append(SentinelAlert(
                Severity.CRITICAL, exp,
                f"DUPLICATE ACCOUNT — {exp} shares account {h.account_id} with another experiment",
                "duplicate",
            ))

        # API error on active experiment that isn't already a ghost
        if (
            h.api_error
            and not h.is_ghost
            and h.registry_status == "paper_trading"
        ):
            alerts.append(SentinelAlert(
                Severity.WARNING, exp,
                f"API ERROR — {exp}: {h.api_error}",
                "api",
            ))

    # Portfolio-level signals
    if portfolio:
        for conflict in portfolio.directional_conflicts:
            alerts.append(SentinelAlert(
                Severity.WARNING, None,
                f"DIRECTION CONFLICT — {conflict}",
                "portfolio",
            ))

        for expiry, count in portfolio.expiration_clusters:
            if count >= 4:
                sev = Severity.WARNING
            else:
                continue  # only flag clusters of 4+ positions
            alerts.append(SentinelAlert(
                sev, None,
                f"EXPIRY CLUSTER — {count} positions expiring {expiry}",
                "portfolio",
            ))

        for ticker in portfolio.concentrated_tickers:
            te = portfolio.tickers.get(ticker)
            n = len(te.experiments) if te else "?"
            alerts.append(SentinelAlert(
                Severity.WARNING, None,
                f"CONCENTRATION — {ticker}: {n} experiments all trading same ticker",
                "portfolio",
            ))

    return alerts


def build_daily_report(
    monitor_results: List["ExperimentHealth"],
    portfolio: Optional["PortfolioRisk"],
) -> str:
    """
    Assemble the full daily health report as an HTML-formatted Telegram message.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    alerts = _collect_alerts(monitor_results, portfolio) if portfolio else _collect_alerts(monitor_results, None)
    criticals = [a for a in alerts if a.severity == Severity.CRITICAL]
    warnings  = [a for a in alerts if a.severity == Severity.WARNING]

    if criticals:
        header = f"🚨 <b>SENTINEL DAILY — {len(criticals)} CRITICAL / {len(warnings)} WARNING</b>"
    elif warnings:
        header = f"⚠️ <b>SENTINEL DAILY — {len(warnings)} WARNING</b>"
    else:
        header = "✅ <b>SENTINEL DAILY — All Clear</b>"

    lines = [header, f"<i>{now_str}</i>", ""]

    # --- Active experiments ---
    active = [h for h in monitor_results if h.registry_status == "paper_trading"]
    if active:
        lines.append("<b>Active Experiments</b>")
        for h in active:
            ok = h.api_ok and not h.is_ghost and not h.is_stale
            icon = "🟢" if ok else "🔴"
            eq = f"${h.equity:,.0f}" if h.equity is not None else "N/A"
            pos = f"{h.open_positions}p" if h.api_ok else "—"
            age = f" · last trade {h.last_order_age_days}d ago" if h.last_order_age_days is not None else ""
            lines.append(f"  {icon} {h.exp_id}: {eq} | {pos}{age}")
        lines.append("")

    # --- Orphaned retired accounts ---
    orphans = [h for h in monitor_results if h.is_orphan]
    if orphans:
        lines.append("<b>Orphaned Accounts ⚠️</b>")
        for h in orphans:
            lines.append(f"  💰 {h.exp_id} ({h.account_id}): ${h.equity:,.0f} idle")
        lines.append("")

    # --- Portfolio exposure ---
    if portfolio and portfolio.tickers:
        lines.append("<b>Portfolio Exposure</b>")
        for ticker, te in sorted(portfolio.tickers.items()):
            parts = []
            if te.bull_count:
                parts.append(f"{te.bull_count}↑")
            if te.bear_count:
                parts.append(f"{te.bear_count}↓")
            if te.ic_count:
                parts.append(f"{te.ic_count}🦅")
            if te.other_count:
                parts.append(f"{te.other_count}~")
            dir_str = " ".join(parts) if parts else "?"
            lines.append(
                f"  {ticker}: {te.total_contracts} contracts "
                f"[{dir_str}] · {len(te.experiments)} exp"
            )
        if portfolio.db_errors:
            lines.append(f"  <i>(⚠ {len(portfolio.db_errors)} DB(s) unreadable)</i>")
        lines.append("")

    # --- Issues section ---
    if alerts:
        lines.append("<b>Issues</b>")
        for a in criticals:
            lines.append(f"  🔴 {a.message}")
        for a in warnings:
            lines.append(f"  ⚠️  {a.message}")
    else:
        lines.append("<i>No issues detected.</i>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def send_daily_report(text: str) -> bool:
    """Send the daily report via shared Telegram. Returns True on success."""
    try:
        from shared.telegram_alerts import send_message
        return send_message(text)
    except Exception as e:
        logger.error("SENTINEL: Telegram send failed: %s", e)
        return False
