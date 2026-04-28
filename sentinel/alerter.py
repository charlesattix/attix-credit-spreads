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

import atexit
import logging
import logging.handlers
import os
import queue as _queue
import threading
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


# ---------------------------------------------------------------------------
# Alert promotion: route ERROR/CRITICAL log records into sentinel.alerts_log
# ---------------------------------------------------------------------------

# Logger-name allowlist. A record is only promoted to alerts_log if its
# logger name starts with one of these prefixes.
_ALLOWLIST_PREFIXES = (
    "execution.",
    "shared.",
    "strategy.",
    "strategies.",
    "sentinel.",
    "ml.",
    "compass.",
)

# Exact-match exclusions inside the allowlist.  We MUST NOT promote logs
# from sentinel.history (would recurse via record_alert) or from this
# module itself (recursion via send_daily_report failure paths).
_EXCLUDE_EXACT = frozenset({"sentinel.history", "sentinel.alerter"})

# Max DB-stored message length (matches dedup key window in record_alert).
_MAX_MSG_LEN = 280


class SentinelAlertLogHandler(logging.Handler):
    """
    Logging handler that promotes ERROR+ records into sentinel alerts_log.

    Severity mapping:
      logging.ERROR    → 'warning'
      logging.CRITICAL → 'critical'

    Records below ERROR, records from disallowed modules, and records from
    the recursion-prone modules (sentinel.history, sentinel.alerter) are
    silently dropped.

    The DB writer (SentinelDB.record_alert) is invoked synchronously here,
    but install_log_handler() wraps this handler in a QueueListener so the
    actual emit() happens on a background thread.
    """

    def __init__(
        self,
        db: Optional["object"] = None,
        level: int = logging.ERROR,
    ) -> None:
        super().__init__(level=level)
        self._db = db

    def _resolve_db(self):
        if self._db is None:
            from sentinel.history import SentinelDB
            self._db = SentinelDB()
        return self._db

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.ERROR:
                return
            name = record.name or ""
            if name in _EXCLUDE_EXACT:
                return
            if not any(name.startswith(p) for p in _ALLOWLIST_PREFIXES):
                return

            severity = "critical" if record.levelno >= logging.CRITICAL else "warning"
            try:
                text = record.getMessage()
            except Exception:
                text = str(record.msg)

            message = f"{name}: {text}"
            if len(message) > _MAX_MSG_LEN:
                message = message[:_MAX_MSG_LEN]

            experiment_id = os.environ.get("EXPERIMENT_ID") or None

            self._resolve_db().record_alert(
                severity, message, experiment_id=experiment_id,
            )
        except Exception:
            # Logging must never raise.
            self.handleError(record)


# Module-level state for install_log_handler / _uninstall_log_handler.
_install_lock = threading.Lock()
_listener: Optional[logging.handlers.QueueListener] = None
_queue_handler: Optional[logging.handlers.QueueHandler] = None
_atexit_registered = False


def install_log_handler(experiment_id: Optional[str] = None) -> None:
    """
    Install a QueueHandler on the root logger that asynchronously promotes
    ERROR+ records into the sentinel alerts_log via SentinelAlertLogHandler.

    Safe to call multiple times in the same process — second and subsequent
    calls are no-ops.  Each scanner process should call this once at start.

    If *experiment_id* is provided AND EXPERIMENT_ID is not already set in
    the environment, it is published so the handler can attribute alerts.
    """
    global _listener, _queue_handler, _atexit_registered

    with _install_lock:
        if _queue_handler is not None:
            return

        if experiment_id:
            os.environ.setdefault("EXPERIMENT_ID", experiment_id)

        q: _queue.Queue = _queue.Queue(-1)
        qh = logging.handlers.QueueHandler(q)
        qh.setLevel(logging.ERROR)

        target = SentinelAlertLogHandler()
        target.setLevel(logging.ERROR)

        listener = logging.handlers.QueueListener(
            q, target, respect_handler_level=True
        )
        listener.start()

        root = logging.getLogger()
        root.addHandler(qh)

        _queue_handler = qh
        _listener = listener

        if not _atexit_registered:
            atexit.register(_uninstall_log_handler)
            _atexit_registered = True


def _uninstall_log_handler() -> None:
    """Tear down the QueueHandler/QueueListener installed by install_log_handler.

    Used by tests; also registered via atexit so the listener thread stops
    cleanly on interpreter shutdown.
    """
    global _listener, _queue_handler

    with _install_lock:
        qh = _queue_handler
        listener = _listener
        _queue_handler = None
        _listener = None

    if qh is not None:
        try:
            logging.getLogger().removeHandler(qh)
        except Exception:
            pass
    if listener is not None:
        try:
            listener.stop()
        except Exception:
            pass
