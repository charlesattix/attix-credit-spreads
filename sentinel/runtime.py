"""
SENTINEL Gates 7, 8 & 9 — Runtime health monitors.

Gate 7 — Orphan / Unmanaged Position Detector
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Compares Alpaca /v2/positions vs DB open trades every scan.
Detects orphans (Alpaca-only) and ghosts (DB-only).
  - Orphan persists ≥3 scans → CRITICAL
  - ≥5 simultaneous orphans → HALT experiment
  - Ghost → CRITICAL (external close not captured)

Gate 8 — Live-vs-Backtest Runtime Drift Tracker
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Monitors each active experiment's rolling 30-trade window and compares
against backtest baselines stored in ``sentinel_state.json`` under the
``backtest_baseline`` key.

Tracked metrics:
  win_rate, avg_loss, peak_drawdown_pct

Alert thresholds:
  Metric       | WARNING        | CRITICAL       | HALT
  -------------|----------------|----------------|-------------
  win_rate     | -10 pp         | -15 pp         | -20 pp
  avg_loss     | 1.5x baseline  | 2.0x baseline  | 3.0x baseline
  drawdown     | 80% MC worst   | 100% MC worst  | 110% MC worst

Gate 9 — Position Lifecycle Monitor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tracks every position from open→close and enforces time-bounded lifecycle.
Flags positions stuck in intermediate states:

  Status              | WARNING     | CRITICAL
  --------------------|-------------|----------
  pending_open        | 30 min      | 2 hrs
  pending_close       | 30 min      | 2 hrs
  needs_investigation | immediately | 4 hrs
  open (no mgmt)      | 24 hrs      | —

Usage
-----
  from sentinel.runtime import orphan_gate, check_runtime_drift

  orphan_gate("EXP-400")                        # Gate 7
  drift_alerts = check_runtime_drift("EXP-400") # Gate 8

Post-scan unified entry point::

  from sentinel.runtime import post_scan_check
  post_scan_check("EXP-400", db_path, config)
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Rolling window size
WINDOW_SIZE = 30

# Minimum trades before any alert fires
MIN_TRADES = 10

# Low-confidence zone — severity is downgraded by one tier
LOW_CONFIDENCE_THRESHOLD = 20

# Gate 7 thresholds
_ORPHAN_CONSECUTIVE_ESCALATION = 3   # orphan persists ≥3 scans → CRITICAL
_ORPHAN_SIMULTANEOUS_HALT = 5        # ≥5 orphans at once → HALT
_ORPHAN_COUNT_KEY = "sentinel_orphan_counts"  # scanner_state key prefix




# ---------------------------------------------------------------------------
# Alert thresholds
# ---------------------------------------------------------------------------

# win_rate: delta in percentage points below baseline
_WR_WARN    = 10.0   # -10pp
_WR_CRIT    = 15.0   # -15pp
_WR_HALT    = 20.0   # -20pp

# avg_loss: multiplier over baseline average loss
_AL_WARN    = 1.5
_AL_CRIT    = 2.0
_AL_HALT    = 3.0

# drawdown: fraction of MC worst-case drawdown
_DD_WARN    = 0.80   # 80% of MC worst
_DD_CRIT    = 1.00   # 100% of MC worst
_DD_HALT    = 1.10   # 110% of MC worst


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RuntimeMetrics:
    """Rolling window metrics for one experiment."""
    exp_id: str
    window_size: int = 0          # actual number of trades in window
    total_closed: int = 0         # total closed trades in DB

    win_rate: Optional[float] = None        # percentage 0-100
    avg_pnl: Optional[float] = None         # mean PnL (all trades)
    avg_loss: Optional[float] = None        # mean |PnL| of losers
    avg_win: Optional[float] = None         # mean PnL of winners
    wins: int = 0
    losses: int = 0

    peak_equity: Optional[float] = None
    trough_equity: Optional[float] = None
    current_equity: Optional[float] = None
    peak_drawdown_pct: Optional[float] = None   # as positive percentage


@dataclass
class DriftAlert:
    """A single drift alert for one metric on one experiment."""
    exp_id: str
    metric: str              # "win_rate" | "avg_loss" | "drawdown"
    severity: str            # "warning" | "critical" | "halt"
    message: str
    live_value: float
    baseline_value: float
    low_confidence: bool = False   # True when 10 <= trades < 20


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _resolve_db_path(exp_id: str) -> Optional[Path]:
    """Resolve the trades DB path for an experiment via sentinel_state.json."""
    try:
        from sentinel.state import load_state
        state = load_state()
        exp = state.get("experiments", {}).get(exp_id, {})
        paper_config = exp.get("paper_config")
        if paper_config:
            cfg_path = _PROJECT_ROOT / paper_config
            if cfg_path.exists():
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f)
                db_path = cfg.get("db_path")
                if db_path:
                    resolved = _PROJECT_ROOT / db_path
                    if resolved.exists():
                        return resolved
    except Exception as e:
        logger.debug("runtime: failed to resolve DB for %s: %s", exp_id, e)
    return None


def _get_baseline(exp_id: str) -> Optional[Dict[str, float]]:
    """Retrieve backtest baseline for *exp_id* from the experiment registry."""
    from experiments.manager import get_manager
    return get_manager().baseline(exp_id)


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def compute_metrics(exp_id: str, window: int = WINDOW_SIZE) -> Optional[RuntimeMetrics]:
    """
    Compute rolling-window trade metrics from the experiment's trades DB.

    Returns None if the DB is unavailable or has no closed trades.
    """
    db_path = _resolve_db_path(exp_id)
    if not db_path:
        logger.warning("runtime: no DB found for %s — skipping", exp_id)
        return None

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        # Total closed trades
        total_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM trades WHERE status LIKE 'closed%'"
        ).fetchone()
        total_closed = total_row["cnt"] if total_row else 0

        if total_closed == 0:
            return RuntimeMetrics(exp_id=exp_id, window_size=0, total_closed=0)

        # Rolling window: last N closed trades by exit_date
        rows = conn.execute(
            """
            SELECT pnl, exit_date FROM trades
            WHERE status LIKE 'closed%' AND pnl IS NOT NULL
            ORDER BY exit_date DESC
            LIMIT ?
            """,
            (window,),
        ).fetchall()

        if not rows:
            return RuntimeMetrics(exp_id=exp_id, window_size=0, total_closed=total_closed)

        pnls = [float(r["pnl"]) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        metrics = RuntimeMetrics(
            exp_id=exp_id,
            window_size=len(pnls),
            total_closed=total_closed,
            win_rate=round(len(wins) / len(pnls) * 100, 1) if pnls else None,
            avg_pnl=round(sum(pnls) / len(pnls), 2) if pnls else None,
            avg_loss=round(sum(abs(p) for p in losses) / len(losses), 2) if losses else 0.0,
            avg_win=round(sum(wins) / len(wins), 2) if wins else 0.0,
            wins=len(wins),
            losses=len(losses),
        )

        # Peak-to-trough drawdown from equity curve
        # Reconstruct equity from the scanner_state table (peak_equity) or
        # from the config's account_size + cumulative PnL
        try:
            peak_row = conn.execute(
                "SELECT value FROM scanner_state WHERE key = 'peak_equity'"
            ).fetchone()
            if peak_row:
                metrics.peak_equity = float(peak_row["value"])

            # Get current equity from config account_size + total PnL
            total_pnl_row = conn.execute(
                "SELECT SUM(pnl) AS total FROM trades WHERE status LIKE 'closed%' AND pnl IS NOT NULL"
            ).fetchone()
            total_pnl = float(total_pnl_row["total"]) if total_pnl_row and total_pnl_row["total"] else 0.0

            # Read account_size from config
            _cfg_rel = _get_paper_config_path(exp_id)
            cfg_path = _PROJECT_ROOT / _cfg_rel if _cfg_rel else None
            if cfg_path and cfg_path.exists():
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f)
                account_size = float(cfg.get("risk", {}).get("account_size", 100000))
            else:
                account_size = 100000.0

            metrics.current_equity = account_size + total_pnl

            if metrics.peak_equity and metrics.current_equity:
                dd = (metrics.peak_equity - metrics.current_equity) / metrics.peak_equity * 100
                metrics.peak_drawdown_pct = round(max(dd, 0.0), 2)
            elif metrics.current_equity < account_size:
                # No peak_equity in scanner_state — use account_size as peak
                metrics.peak_equity = account_size
                dd = (account_size - metrics.current_equity) / account_size * 100
                metrics.peak_drawdown_pct = round(max(dd, 0.0), 2)

        except Exception as e:
            logger.debug("runtime: equity calculation failed for %s: %s", exp_id, e)

        return metrics

    finally:
        conn.close()


def _get_paper_config_path(exp_id: str) -> Optional[str]:
    """Get paper_config relative path from sentinel_state.json. Returns None if not found."""
    try:
        from sentinel.state import load_state
        state = load_state()
        return state.get("experiments", {}).get(exp_id, {}).get("paper_config")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------


def _classify_severity(
    raw_severity: str,
    window_size: int,
) -> str:
    """
    Downgrade severity by one tier when window is in the low-confidence zone
    (10-19 trades).  Below MIN_TRADES, return empty string (suppress alert).
    """
    if window_size < MIN_TRADES:
        return ""  # suppress

    if window_size < LOW_CONFIDENCE_THRESHOLD:
        # Downgrade by one tier
        downgrades = {"halt": "critical", "critical": "warning", "warning": "info"}
        return downgrades.get(raw_severity, raw_severity)

    return raw_severity


def detect_drift(
    metrics: RuntimeMetrics,
    baseline: Dict[str, float],
) -> List[DriftAlert]:
    """
    Compare live metrics against backtest baseline and return any alerts.

    Returns an empty list when everything is within tolerance.
    """
    alerts: List[DriftAlert] = []
    exp_id = metrics.exp_id
    n = metrics.window_size
    low_conf = MIN_TRADES <= n < LOW_CONFIDENCE_THRESHOLD

    # --- Win rate drift ---
    if metrics.win_rate is not None and "win_rate" in baseline:
        bl_wr = baseline["win_rate"]
        delta = bl_wr - metrics.win_rate  # positive = live is worse

        if delta >= _WR_HALT:
            raw = "halt"
        elif delta >= _WR_CRIT:
            raw = "critical"
        elif delta >= _WR_WARN:
            raw = "warning"
        else:
            raw = ""

        if raw:
            sev = _classify_severity(raw, n)
            if sev and sev != "info":
                alerts.append(DriftAlert(
                    exp_id=exp_id,
                    metric="win_rate",
                    severity=sev,
                    message=(
                        f"Win rate drift: live {metrics.win_rate:.1f}% vs "
                        f"baseline {bl_wr:.1f}% (Δ {-delta:+.1f}pp, "
                        f"{n} trades{'*' if low_conf else ''})"
                    ),
                    live_value=metrics.win_rate,
                    baseline_value=bl_wr,
                    low_confidence=low_conf,
                ))

    # --- Average loss drift ---
    if metrics.avg_loss is not None and metrics.avg_loss > 0 and "avg_loss" in baseline:
        bl_al = baseline["avg_loss"]
        ratio = metrics.avg_loss / bl_al if bl_al > 0 else 0.0

        if ratio >= _AL_HALT:
            raw = "halt"
        elif ratio >= _AL_CRIT:
            raw = "critical"
        elif ratio >= _AL_WARN:
            raw = "warning"
        else:
            raw = ""

        if raw:
            sev = _classify_severity(raw, n)
            if sev and sev != "info":
                alerts.append(DriftAlert(
                    exp_id=exp_id,
                    metric="avg_loss",
                    severity=sev,
                    message=(
                        f"Avg loss drift: live ${metrics.avg_loss:,.0f} vs "
                        f"baseline ${bl_al:,.0f} ({ratio:.1f}x, "
                        f"{metrics.losses} losers in {n} trades{'*' if low_conf else ''})"
                    ),
                    live_value=metrics.avg_loss,
                    baseline_value=bl_al,
                    low_confidence=low_conf,
                ))

    # --- Drawdown drift ---
    if metrics.peak_drawdown_pct is not None and "mc_worst_dd_pct" in baseline:
        bl_dd = baseline["mc_worst_dd_pct"]  # e.g. 41.5 means -41.5%
        if bl_dd > 0:
            dd_ratio = metrics.peak_drawdown_pct / bl_dd

            if dd_ratio >= _DD_HALT:
                raw = "halt"
            elif dd_ratio >= _DD_CRIT:
                raw = "critical"
            elif dd_ratio >= _DD_WARN:
                raw = "warning"
            else:
                raw = ""

            if raw:
                sev = _classify_severity(raw, n)
                if sev and sev != "info":
                    alerts.append(DriftAlert(
                        exp_id=exp_id,
                        metric="drawdown",
                        severity=sev,
                        message=(
                            f"Drawdown drift: live -{metrics.peak_drawdown_pct:.1f}% vs "
                            f"MC worst -{bl_dd:.1f}% ({dd_ratio:.0%} of limit"
                            f"{'*' if low_conf else ''})"
                        ),
                        live_value=metrics.peak_drawdown_pct,
                        baseline_value=bl_dd,
                        low_confidence=low_conf,
                    ))

    return alerts


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def check_runtime_drift(
    exp_id: str,
    record_snapshot: bool = True,
) -> List[DriftAlert]:
    """
    Full Gate 8 check for one experiment.

    1. Compute rolling 30-trade metrics from trades DB
    2. Compare against backtest baseline
    3. Optionally record snapshot to sentinel.db
    4. Return list of drift alerts (empty = healthy)

    If *record_snapshot* is True (default), writes the rolling metrics
    to the experiment_snapshots table in sentinel.db so the daily report
    and HTML dashboards can display trend data.
    """
    baseline = _get_baseline(exp_id)
    if not baseline:
        logger.info("runtime: no baseline for %s — skipping drift check", exp_id)
        return []

    metrics = compute_metrics(exp_id)
    if not metrics or metrics.window_size == 0:
        logger.info("runtime: no closed trades for %s — skipping", exp_id)
        return []

    # Detect drift
    alerts = detect_drift(metrics, baseline)

    # Record snapshot
    if record_snapshot:
        try:
            from sentinel.history import SentinelDB
            db = SentinelDB()
            db.record_snapshot(
                exp_id,
                equity=metrics.current_equity,
                open_positions=0,  # runtime check doesn't know open positions
                total_trades=metrics.total_closed,
                win_rate=metrics.win_rate,
                notes=(
                    f"Gate8 rolling-{metrics.window_size}: "
                    f"WR={metrics.win_rate:.1f}% "
                    f"avgPnL=${metrics.avg_pnl:,.0f} "
                    f"avgLoss=${metrics.avg_loss:,.0f} "
                    f"DD={metrics.peak_drawdown_pct:.1f}%"
                    if metrics.win_rate is not None and metrics.avg_pnl is not None
                    and metrics.avg_loss is not None and metrics.peak_drawdown_pct is not None
                    else f"Gate8 rolling-{metrics.window_size}"
                ),
            )
        except Exception as e:
            logger.warning("runtime: snapshot write failed for %s: %s", exp_id, e)

    # Log alerts
    for alert in alerts:
        log_fn = logger.critical if alert.severity == "halt" else (
            logger.warning if alert.severity == "critical" else logger.info
        )
        log_fn("GATE8 %s [%s] %s: %s", exp_id, alert.severity.upper(), alert.metric, alert.message)

    return alerts


def check_all_runtime_drift(
    record_snapshot: bool = True,
    halt_on_breach: bool = False,
) -> Dict[str, List[DriftAlert]]:
    """
    Run Gate 8 for all active experiments.

    Returns a dict mapping experiment ID to its list of drift alerts.
    If *halt_on_breach* is True, experiments with halt-severity alerts
    are halted in sentinel_state.json.
    """
    try:
        from sentinel.state import load_state, set_halt
        state = load_state()
    except Exception as e:
        logger.error("runtime: cannot load sentinel_state.json: %s", e)
        return {}

    active_ids = [
        eid for eid, exp in state.get("experiments", {}).items()
        if exp.get("status") == "active"
    ]

    all_alerts: Dict[str, List[DriftAlert]] = {}

    for exp_id in sorted(active_ids):
        alerts = check_runtime_drift(exp_id, record_snapshot=record_snapshot)
        all_alerts[exp_id] = alerts

        # Enforce halt if requested
        if halt_on_breach and alerts:
            halt_alerts = [a for a in alerts if a.severity == "halt"]
            if halt_alerts:
                reason = "; ".join(a.message for a in halt_alerts)
                try:
                    set_halt(
                        exp_id,
                        f"Gate8 runtime drift halt: {reason[:200]}",
                        halted_by="runtime.py:G8",
                        halt_evidence={
                            "gate_id": "G8",
                            "metric_name": "runtime_drift",
                            "stored_value": "baseline",
                            "current_value": reason[:200],
                            "threshold": "halt_severity",
                        },
                    )
                    logger.critical(
                        "GATE8: HALTED %s — %d metric(s) breached halt threshold",
                        exp_id, len(halt_alerts),
                    )
                    # Record halt alert in sentinel.db
                    from sentinel.history import SentinelDB
                    db = SentinelDB()
                    db.record_alert(
                        "critical",
                        f"Gate8 HALT: {reason[:200]}",
                        experiment_id=exp_id,
                    )
                except Exception as e:
                    logger.error("runtime: failed to halt %s: %s", exp_id, e)

    return all_alerts


# ---------------------------------------------------------------------------
# Pretty-print for CLI / daily report integration
# ---------------------------------------------------------------------------


def format_drift_report(
    all_alerts: Dict[str, List[DriftAlert]],
    all_metrics: Optional[Dict[str, RuntimeMetrics]] = None,
) -> str:
    """
    Format Gate 8 results as a human-readable text block.

    Suitable for inclusion in the daily Telegram message or CLI output.
    """
    lines: List[str] = []
    lines.append("<b>Gate 8 — Runtime Drift</b>")

    if not all_alerts:
        lines.append("  <i>No active experiments to check.</i>")
        return "\n".join(lines)

    any_alerts = False
    for exp_id in sorted(all_alerts):
        alerts = all_alerts[exp_id]
        m = all_metrics.get(exp_id) if all_metrics else None

        if not alerts:
            # Clean — show summary metrics if available
            if m and m.win_rate is not None:
                avg_loss_str = f"${m.avg_loss:,.0f}" if m.avg_loss is not None else "N/A"
                dd_str = f"{m.peak_drawdown_pct:.1f}%" if m.peak_drawdown_pct is not None else "N/A"
                lines.append(
                    f"  ✅ {exp_id}: WR={m.win_rate:.0f}% "
                    f"avgL={avg_loss_str} DD={dd_str} "
                    f"({m.window_size}t)"
                )
            else:
                lines.append(f"  ✅ {exp_id}: within tolerance")
            continue

        any_alerts = True
        for a in alerts:
            icon = {"halt": "🛑", "critical": "🔴", "warning": "⚠️"}.get(a.severity, "❓")
            conf = " <i>(low-confidence)</i>" if a.low_confidence else ""
            lines.append(f"  {icon} {exp_id}: {a.message}{conf}")

    if not any_alerts:
        lines.append("  <i>All experiments within baseline tolerance.</i>")

    return "\n".join(lines)


# ===========================================================================
# Gate 9 — Position Lifecycle Monitor
# ===========================================================================

# Time-in-state thresholds (in minutes)
_LIFECYCLE_THRESHOLDS: Dict[str, Dict[str, Optional[float]]] = {
    "pending_open":        {"warning": 30,   "critical": 120},
    "pending_close":       {"warning": 30,   "critical": 120},
    "needs_investigation": {"warning": 0,    "critical": 240},   # warn immediately
    "open_no_management":  {"warning": 1440, "critical": None},  # 24h warn, no auto-crit
}


@dataclass
class StuckPosition:
    """A position that has been in an intermediate state too long."""
    trade_id: str
    exp_id: str
    status: str
    ticker: str
    short_strike: Optional[float]
    expiration: Optional[str]
    minutes_in_state: float
    severity: str            # "warning" | "critical"
    message: str


@dataclass
class LifecycleResult:
    """Gate 9 check result for one experiment."""
    exp_id: str
    total_open: int = 0
    total_pending: int = 0
    stuck: List[StuckPosition] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.stuck and not self.errors

    @property
    def has_critical(self) -> bool:
        return any(s.severity == "critical" for s in self.stuck)


def _minutes_since(dt_str: Optional[str], now: datetime) -> Optional[float]:
    """Parse an ISO timestamp and return minutes elapsed since then."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        return delta.total_seconds() / 60.0
    except (ValueError, TypeError):
        return None


def check_position_lifecycle(
    exp_id: str,
    now: Optional[datetime] = None,
) -> LifecycleResult:
    """
    Gate 9: scan the trades DB for positions stuck in intermediate states.

    Checks:
      pending_open         — order submitted, no fill confirmation
      pending_close        — close submitted, no fill
      needs_investigation  — phantom position (Alpaca has it, DB doesn't)
      open (no management) — open trade with no updated_at change in 24h

    Returns LifecycleResult with stuck positions and their severities.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    result = LifecycleResult(exp_id=exp_id)

    db_path = _resolve_db_path(exp_id)
    if not db_path:
        result.errors.append(f"No DB found for {exp_id}")
        return result

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        # Fetch all non-closed trades
        rows = conn.execute("""
            SELECT id, status, ticker, short_strike, expiration,
                   created_at, updated_at
            FROM trades
            WHERE status NOT LIKE 'closed%%' AND status != 'failed_open'
            ORDER BY updated_at ASC
        """).fetchall()

        for row in rows:
            trade_id = row["id"]
            status = row["status"]
            ticker = row["ticker"] or "?"
            short_strike = row["short_strike"]
            expiration = row["expiration"]
            updated_at = row["updated_at"]
            created_at = row["created_at"]

            if status == "open":
                result.total_open += 1
            elif status in ("pending_open", "pending_close", "needs_investigation"):
                result.total_pending += 1

            # Determine the relevant timestamp for time-in-state
            reference_ts = updated_at or created_at
            elapsed = _minutes_since(reference_ts, now)
            if elapsed is None:
                continue

            # Check lifecycle thresholds for pending/investigation statuses
            if status in ("pending_open", "pending_close", "needs_investigation"):
                thresholds = _LIFECYCLE_THRESHOLDS.get(status, {})
                warn_mins = thresholds.get("warning", float("inf"))
                crit_mins = thresholds.get("critical")

                if crit_mins is not None and elapsed >= crit_mins:
                    severity = "critical"
                elif elapsed >= warn_mins:
                    severity = "warning"
                else:
                    continue  # within tolerance

                age_str = _format_age(elapsed)
                result.stuck.append(StuckPosition(
                    trade_id=trade_id,
                    exp_id=exp_id,
                    status=status,
                    ticker=ticker,
                    short_strike=short_strike,
                    expiration=expiration,
                    minutes_in_state=round(elapsed, 1),
                    severity=severity,
                    message=(
                        f"{status} for {age_str}: {ticker} "
                        f"strike={short_strike} exp={expiration} "
                        f"[{trade_id[:24]}]"
                    ),
                ))

            elif status == "open":
                # Check for stale open positions (no management action in 24h)
                thresholds = _LIFECYCLE_THRESHOLDS["open_no_management"]
                warn_mins = thresholds.get("warning", float("inf"))
                if warn_mins is not None and elapsed >= warn_mins:
                    age_str = _format_age(elapsed)
                    result.stuck.append(StuckPosition(
                        trade_id=trade_id,
                        exp_id=exp_id,
                        status="open_no_management",
                        ticker=ticker,
                        short_strike=short_strike,
                        expiration=expiration,
                        minutes_in_state=round(elapsed, 1),
                        severity="warning",
                        message=(
                            f"open with no management for {age_str}: {ticker} "
                            f"strike={short_strike} exp={expiration} "
                            f"[{trade_id[:24]}]"
                        ),
                    ))

    except Exception as e:
        result.errors.append(f"DB query failed: {e}")
    finally:
        conn.close()

    # Log summary
    if result.stuck:
        crits = sum(1 for s in result.stuck if s.severity == "critical")
        warns = len(result.stuck) - crits
        logger.warning(
            "GATE9 %s: %d stuck position(s) — %d critical, %d warning",
            exp_id, len(result.stuck), crits, warns,
        )
        for s in result.stuck:
            log_fn = logger.critical if s.severity == "critical" else logger.warning
            log_fn("GATE9 %s [%s] %s", exp_id, s.severity.upper(), s.message)
    else:
        logger.debug(
            "GATE9 %s: all positions healthy (%d open, %d pending)",
            exp_id, result.total_open, result.total_pending,
        )

    return result


def check_all_position_lifecycles(
    halt_on_critical: bool = False,
) -> Dict[str, LifecycleResult]:
    """
    Run Gate 9 for all active experiments.

    Returns dict mapping experiment ID → LifecycleResult.
    If *halt_on_critical*, experiments with critical-severity stuck positions
    are halted in sentinel_state.json.
    """
    try:
        from sentinel.state import load_state, set_halt
        state = load_state()
    except Exception as e:
        logger.error("lifecycle: cannot load sentinel_state.json: %s", e)
        return {}

    active_ids = [
        eid for eid, exp in state.get("experiments", {}).items()
        if exp.get("status") == "active"
    ]

    results: Dict[str, LifecycleResult] = {}
    for exp_id in sorted(active_ids):
        lc_result = check_position_lifecycle(exp_id)
        results[exp_id] = lc_result

        if halt_on_critical and lc_result.has_critical:
            stuck_msgs = [s.message for s in lc_result.stuck if s.severity == "critical"]
            reason = f"Gate9 stuck positions: {'; '.join(stuck_msgs)}"
            try:
                set_halt(
                    exp_id,
                    reason[:200],
                    halted_by="runtime.py:G9",
                    halt_evidence={
                        "gate_id": "G9",
                        "metric_name": "stuck_positions",
                        "stored_value": "0_critical",
                        "current_value": f"{len(stuck_msgs)}_critical",
                        "threshold": "0_critical",
                    },
                )
                logger.critical(
                    "GATE9: HALTED %s — %d critical stuck position(s)",
                    exp_id, len(stuck_msgs),
                )
                from sentinel.history import SentinelDB
                SentinelDB().record_alert("critical", reason[:200], experiment_id=exp_id)
            except Exception as e:
                logger.error("lifecycle: failed to halt %s: %s", exp_id, e)

    return results


def format_lifecycle_report(results: Dict[str, LifecycleResult]) -> str:
    """Format Gate 9 results as a human-readable text block for Telegram/CLI."""
    lines: List[str] = ["<b>Gate 9 — Position Lifecycle</b>"]

    if not results:
        lines.append("  <i>No active experiments to check.</i>")
        return "\n".join(lines)

    any_stuck = False
    for exp_id in sorted(results):
        r = results[exp_id]
        if not r.stuck and not r.errors:
            lines.append(
                f"  ✅ {exp_id}: {r.total_open} open, "
                f"{r.total_pending} pending — all healthy"
            )
            continue

        any_stuck = True
        for s in r.stuck:
            icon = "🛑" if s.severity == "critical" else "⚠️"
            lines.append(f"  {icon} {exp_id}: {s.message}")

        for e in r.errors:
            lines.append(f"  ❌ {exp_id}: {e}")

    if not any_stuck:
        lines.append("  <i>All positions within lifecycle bounds.</i>")

    return "\n".join(lines)


def _format_age(minutes: float) -> str:
    """Human-readable duration from minutes."""
    if minutes < 60:
        return f"{minutes:.0f}m"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:.1f}h"
    days = hours / 24
    return f"{days:.1f}d"


# ===========================================================================
# Gate 6 — Trade Sizing Validator
# ===========================================================================

# Sizing deviation thresholds (from proposal)
_SIZING_OK_PCT = 0.15        # 0-15%: OK (normal rounding)
_SIZING_WARNING_PCT = 0.35   # 15-35%: WARNING
# > 35%: CRITICAL; 0 contracts placed: HALT


@dataclass
class SizingDeviation:
    """Result of sizing check for one trade."""
    trade_id: str
    ticker: str
    expected_contracts: int
    actual_contracts: int
    deviation_pct: float
    severity: str        # "ok" | "warning" | "critical" | "halt"
    message: str


@dataclass
class SizingResult:
    """Gate 6 result for one experiment."""
    exp_id: str
    trades_checked: int = 0
    deviations: List[SizingDeviation] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(d.severity in ("critical", "halt") for d in self.deviations)


def check_trade_sizing(
    exp_id: str,
    account_equity: float,
    config: dict,
    *,
    scan_start_time: Optional[str] = None,
    db_path: Optional[str] = None,
) -> SizingResult:
    """
    Gate 6: verify actual contracts match the backtest sizing formula.

    Expected = min(floor(equity × risk_pct / (spread_width × 100)), max_contracts)

    Args:
        exp_id: Experiment identifier.
        account_equity: Current Alpaca account equity.
        config: Parsed paper config dict.
        scan_start_time: ISO timestamp — only check trades opened after this.
        db_path: Explicit DB path; resolves from state if None.
    """
    result = SizingResult(exp_id=exp_id)

    if account_equity <= 0:
        result.errors.append(f"Invalid equity: {account_equity}")
        return result

    # Extract sizing parameters from config
    risk_section = config.get("risk", {})
    risk_pct = (
        risk_section.get("max_risk_per_trade")
        or risk_section.get("risk_per_trade")
    )
    if risk_pct is None:
        result.errors.append("Missing risk_per_trade in config")
        return result
    risk_pct = float(risk_pct)
    if risk_pct > 1:
        risk_pct /= 100  # convert 8 → 0.08

    spread_width = config.get("strategy", {}).get("spread_width")
    if spread_width is None:
        result.errors.append("Missing spread_width in config")
        return result
    spread_width = float(spread_width)

    max_contracts = int(risk_section.get("max_contracts", 999))

    # Resolve DB
    resolved_path = db_path or _resolve_db_path_str(exp_id)
    if not resolved_path:
        result.errors.append(f"No DB path for {exp_id}")
        return result

    import sqlite3
    conn = sqlite3.connect(resolved_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        if scan_start_time:
            rows = conn.execute(
                "SELECT id, ticker, contracts FROM trades "
                "WHERE status = 'open' AND entry_date >= ?",
                (scan_start_time,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, ticker, contracts FROM trades WHERE status = 'open'"
            ).fetchall()

        for row in rows:
            trade_id = str(row["id"])
            ticker = row["ticker"] or "?"
            actual = int(row["contracts"] or 0)

            expected = min(
                math.floor(account_equity * risk_pct / (spread_width * 100)),
                max_contracts,
            )

            result.trades_checked += 1

            if expected <= 0:
                result.deviations.append(SizingDeviation(
                    trade_id=trade_id, ticker=ticker,
                    expected_contracts=0, actual_contracts=actual,
                    deviation_pct=float("inf"),
                    severity="halt",
                    message=(
                        f"Sizing formula returned 0 (equity=${account_equity:,.0f}, "
                        f"risk={risk_pct:.2%}, width={spread_width})"
                    ),
                ))
                continue

            deviation = abs(actual - expected) / expected

            if actual == 0:
                severity = "halt"
            elif deviation > _SIZING_WARNING_PCT:
                severity = "critical"
            elif deviation > _SIZING_OK_PCT:
                severity = "warning"
            else:
                severity = "ok"

            result.deviations.append(SizingDeviation(
                trade_id=trade_id, ticker=ticker,
                expected_contracts=expected, actual_contracts=actual,
                deviation_pct=round(deviation, 4),
                severity=severity,
                message=(
                    f"{ticker}: expected {expected}, placed {actual} "
                    f"(deviation {deviation:.0%})"
                ),
            ))
    finally:
        conn.close()

    # Log issues
    for d in result.deviations:
        if d.severity != "ok":
            log_fn = logger.critical if d.severity == "halt" else logger.warning
            log_fn("GATE6 %s [%s] %s", exp_id, d.severity.upper(), d.message)

    return result


def _resolve_db_path_str(exp_id: str) -> Optional[str]:
    """Resolve DB path as a string (for Gate 6/7 where we get db_path as arg)."""
    p = _resolve_db_path(exp_id)
    return str(p) if p else None


# ===========================================================================
# Gate 7 — Orphan / Unmanaged Position Detector
# ===========================================================================


@dataclass
class OrphanResult:
    """Gate 7 result for one experiment."""
    exp_id: str
    orphans: List[str] = field(default_factory=list)        # OCC symbols in Alpaca not in DB
    ghosts: List[Dict[str, Any]] = field(default_factory=list)  # DB records not in Alpaca
    qty_mismatches: List[Dict[str, Any]] = field(default_factory=list)  # G23: per-leg qty drift
    stale_orphans: List[Dict[str, Any]] = field(default_factory=list)   # G23: 24h+ unmanaged
    consecutive_scans: int = 0
    alerts: List[Dict[str, str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            not self.orphans
            and not self.ghosts
            and not self.qty_mismatches
            and not self.stale_orphans
        )

    @property
    def halt_required(self) -> bool:
        return len(self.orphans) >= _ORPHAN_SIMULTANEOUS_HALT


def check_orphan_positions(
    exp_id: str,
    alpaca_positions: List[Dict[str, Any]],
    *,
    db_path: Optional[str] = None,
) -> OrphanResult:
    """
    Gate 7: reconcile Alpaca positions vs DB state.

    Compares at OCC symbol level (not just ticker) for precise matching.
    Creates placeholder trades for orphans (status=unmanaged) and updates
    ghosts to status=needs_investigation with reconciliation_events audit.

    Args:
        exp_id: Experiment identifier.
        alpaca_positions: List of position dicts from Alpaca /v2/positions.
            Each must have a "symbol" key.
        db_path: Explicit DB path; resolves from state if None.
    """
    result = OrphanResult(exp_id=exp_id)

    resolved_path = db_path or _resolve_db_path_str(exp_id)
    if not resolved_path:
        result.errors.append(f"No DB path for {exp_id}")
        return result

    import sqlite3
    conn = sqlite3.connect(resolved_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        # --- Alpaca: build a sym → signed-qty map.
        #
        # G23 fix: do NOT filter by `len > 10`. Pre-fix, that filter dropped
        # 3-letter equity symbols (e.g. SPY +100 from a short-put assignment),
        # silently hiding the most common partial-assignment artifact. We now
        # consider every Alpaca symbol; for credit-spread experiments the
        # account is options-only, so any non-OCC symbol is an orphan signal.
        alpaca_qty_map: Dict[str, int] = {}
        alpaca_qty_known: set = set()  # symbols where the broker actually reported qty
        for pos in alpaca_positions:
            sym = (pos.get("symbol") or "").upper()
            if not sym:
                continue
            raw_qty = pos.get("qty") if isinstance(pos, dict) else None
            if raw_qty is None:
                # Real Alpaca always returns qty; tests/legacy callers may
                # pass symbol-only dicts. Treat absence as "qty unknown" and
                # skip qty_mismatch for this symbol.
                alpaca_qty_map[sym] = 0
            else:
                try:
                    alpaca_qty_map[sym] = int(float(raw_qty))
                    alpaca_qty_known.add(sym)
                except (TypeError, ValueError):
                    alpaca_qty_map[sym] = 0
        alpaca_symbols = set(alpaca_qty_map.keys())

        # --- DB open: per-symbol signed qty when trade_legs.qty is present;
        # symbol-only set when it isn't (legacy DBs / metadata-only rows).
        db_qty_map: Dict[str, int] = {}
        db_symbols: set = set()

        # Probe trade_legs columns so we tolerate legacy schemas that lack `qty`.
        leg_cols: set = set()
        try:
            leg_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(trade_legs)").fetchall()
            }
        except Exception:
            leg_cols = set()

        has_qty_col = "qty" in leg_cols
        select_cols = (
            "tl.occ_symbol, tl.qty, tl.leg_type, t.contracts"
            if has_qty_col else
            "tl.occ_symbol, NULL AS qty, tl.leg_type, t.contracts"
        )
        try:
            rows = conn.execute(
                f"SELECT {select_cols} "
                "FROM trade_legs tl "
                "JOIN trades t ON tl.trade_id = t.id "
                "WHERE t.status IN ('open', 'pending_open', 'pending_close') "
                "AND tl.occ_symbol IS NOT NULL"
            ).fetchall()
            for r in rows:
                sym = str(r[0]).upper()
                db_symbols.add(sym)
                # Prefer trade_legs.qty when populated; otherwise infer
                # signed qty from leg_type (short_* → -contracts).
                leg_qty = r[1]
                if leg_qty is not None:
                    db_qty_map[sym] = int(leg_qty)
                else:
                    leg_type = (r[2] or "").lower()
                    contracts = int(r[3] or 0)
                    # Only infer signed qty when contracts > 0. Legacy fixtures
                    # often leave contracts NULL/0; in that case we know the
                    # symbol is open but cannot reliably compare to broker_qty.
                    if contracts > 0:
                        if leg_type.startswith("short"):
                            db_qty_map[sym] = -contracts
                        elif leg_type.startswith("long"):
                            db_qty_map[sym] = contracts
        except Exception:
            pass  # trade_legs table may not exist at all

        # Always merge metadata symbols (covers DBs without trade_legs table
        # and catches any legs stored only in metadata JSON). Metadata-only
        # rows lack qty info, so they participate in orphan/ghost detection
        # but are excluded from qty_mismatch checks.
        try:
            meta_rows = conn.execute(
                "SELECT metadata FROM trades "
                "WHERE status IN ('open', 'pending_open', 'pending_close')"
            ).fetchall()
            for r in meta_rows:
                try:
                    meta = json.loads(r[0]) if r[0] else {}
                    for key in ("short_leg_symbol", "long_leg_symbol",
                                "call_short_symbol", "call_long_symbol",
                                "put_short_symbol", "put_long_symbol"):
                        sym = meta.get(key)
                        if sym:
                            db_symbols.add(str(sym).upper())
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception:
            pass  # metadata query is best-effort

        # --- G23: qty-mismatch detection.
        # For symbols on both sides with a known DB qty, compare values.
        # A qty mismatch is NOT an orphan and NOT a ghost — distinct alert.
        intersect = alpaca_symbols & db_symbols
        for sym in sorted(intersect):
            if sym not in db_qty_map:
                continue  # legacy/metadata-only row — no qty to compare
            if sym not in alpaca_qty_known:
                continue  # broker didn't report qty — cannot compare
            broker_qty = alpaca_qty_map.get(sym, 0)
            db_qty = db_qty_map[sym]
            if broker_qty != db_qty:
                result.qty_mismatches.append({
                    "occ_symbol": sym,
                    "broker_qty": broker_qty,
                    "db_qty": db_qty,
                })

        # --- Detect orphans and ghosts.
        # qty_mismatch symbols are excluded from both lists.
        mismatch_syms = {qm["occ_symbol"] for qm in result.qty_mismatches}
        result.orphans = sorted((alpaca_symbols - db_symbols) - mismatch_syms)
        ghost_symbols = (db_symbols - alpaca_symbols) - mismatch_syms

        # Build ghost details from trade_legs
        if ghost_symbols:
            for sym in sorted(ghost_symbols):
                try:
                    ghost_rows = conn.execute(
                        "SELECT DISTINCT t.id, t.ticker, t.status "
                        "FROM trades t "
                        "JOIN trade_legs tl ON tl.trade_id = t.id "
                        "WHERE t.status IN ('open', 'pending_open', 'pending_close') "
                        "AND UPPER(tl.occ_symbol) = ?",
                        (sym,),
                    ).fetchall()
                    for gr in ghost_rows:
                        result.ghosts.append({
                            "id": str(gr["id"]),
                            "ticker": gr["ticker"],
                            "status": gr["status"],
                            "occ_symbol": sym,
                        })
                except Exception:
                    result.ghosts.append({
                        "id": "unknown", "ticker": _ticker_from_occ(sym),
                        "status": "open", "occ_symbol": sym,
                    })

        # --- G23: stale-orphan re-alert.
        # Any unmanaged DB row older than 24h whose OCC symbol is still
        # live in the broker is critical: G7 detected it ≥1 day ago and
        # nobody resolved it. Emit a new critical alert each daily run
        # until the row is closed or superseded.
        try:
            stale_rows = conn.execute(
                "SELECT id, ticker, metadata, created_at "
                "FROM trades "
                "WHERE status = 'unmanaged' "
                "AND created_at IS NOT NULL "
                "AND created_at < datetime('now', '-24 hours')"
            ).fetchall()
            for sr in stale_rows:
                sym = ""
                try:
                    meta = json.loads(sr["metadata"]) if sr["metadata"] else {}
                    sym = str(meta.get("occ_symbol", "")).upper()
                except (json.JSONDecodeError, TypeError):
                    sym = ""
                if sym and sym in alpaca_symbols:
                    result.stale_orphans.append({
                        "id": sr["id"],
                        "occ_symbol": sym,
                        "ticker": sr["ticker"],
                        "created_at": sr["created_at"],
                    })
        except Exception:
            pass  # best-effort; legacy DBs without metadata are skipped

        # --- Consecutive scan tracking ---
        prev_count = _read_orphan_scan_count(conn, exp_id)
        if result.orphans:
            result.consecutive_scans = prev_count + 1
            _write_orphan_scan_count(conn, exp_id, result.consecutive_scans)
        else:
            if prev_count > 0:
                _write_orphan_scan_count(conn, exp_id, 0)
            result.consecutive_scans = 0

        # --- Create orphan placeholder trades (status=unmanaged) ---
        for sym in result.orphans:
            _create_orphan_record(conn, exp_id, sym)

        # --- Mark ghosts as needs_investigation + reconciliation event ---
        for ghost in result.ghosts:
            _mark_ghost_record(conn, ghost)

        # --- Build alerts ---
        if result.orphans:
            syms_str = ", ".join(result.orphans[:10])

            if len(result.orphans) >= _ORPHAN_SIMULTANEOUS_HALT:
                result.alerts.append({
                    "severity": "halt",
                    "message": (
                        f"Gate 7 HALT: {len(result.orphans)} orphan positions "
                        f"({syms_str}). Scanner blocked until resolved."
                    ),
                })
            elif result.consecutive_scans >= _ORPHAN_CONSECUTIVE_ESCALATION:
                result.alerts.append({
                    "severity": "critical",
                    "message": (
                        f"Gate 7: orphan positions unresolved for "
                        f"{result.consecutive_scans} scans ({syms_str})."
                    ),
                })
            else:
                result.alerts.append({
                    "severity": "warning",
                    "message": (
                        f"Gate 7: {len(result.orphans)} new orphan position(s) "
                        f"({syms_str}). Investigate within 24h."
                    ),
                })

        if result.ghosts:
            ids_str = ", ".join(g["id"][:24] for g in result.ghosts[:5])
            result.alerts.append({
                "severity": "critical",
                "message": (
                    f"Gate 7: {len(result.ghosts)} ghost position(s) — "
                    f"DB shows open but Alpaca has no position. "
                    f"IDs: {ids_str}."
                ),
            })

        # G23: one critical alert per qty_mismatch leg (keyed on OCC).
        # Aggregating by ticker would lose per-leg detail (cc1 review).
        for qm in result.qty_mismatches:
            result.alerts.append({
                "severity": "critical",
                "message": (
                    f"Gate 23 qty_mismatch: {qm['occ_symbol']} "
                    f"broker_qty={qm['broker_qty']} db_qty={qm['db_qty']} "
                    f"(distinct from orphan/ghost; investigate immediately)."
                ),
            })

        # G23: re-alert on stale unmanaged rows (>24h with live broker counterpart).
        for so in result.stale_orphans:
            result.alerts.append({
                "severity": "critical",
                "message": (
                    f"Gate 23 stale orphan: {so['occ_symbol']} unmanaged in DB "
                    f"since {so['created_at']} (>24h) and still live in broker. "
                    f"Run reconcile_positions or close manually."
                ),
            })

    finally:
        conn.close()

    # Log and send Telegram alerts
    for a in result.alerts:
        log_fn = logger.critical if a["severity"] == "halt" else logger.warning
        log_fn("GATE7 %s [%s] %s", exp_id, a["severity"].upper(), a["message"])

    _send_gate7_telegram_alerts(exp_id, result)

    return result


def orphan_gate(
    experiment_id: str,
    *,
    db_path: Optional[str] = None,
) -> None:
    """
    Convenience wrapper for scanner injection: fetches Alpaca positions
    and runs check_orphan_positions().  Halts on ≥5 simultaneous orphans.

    Usage (after pre_scan_check, inside scanner):

        from sentinel.runtime import orphan_gate
        orphan_gate("EXP-800")
    """
    api_key = os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID")
    api_secret = os.environ.get("ALPACA_API_SECRET") or os.environ.get("APCA_API_SECRET_KEY")

    if not api_key or not api_secret:
        logger.debug("GATE7: %s — no Alpaca creds, skipping orphan_gate", experiment_id)
        return

    paper = os.environ.get("ALPACA_PAPER", "true").lower() not in ("false", "0", "no")

    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(api_key, api_secret, paper=paper)
        positions = client.get_all_positions()
        pos_dicts = [{"symbol": str(p.symbol), "qty": str(p.qty)} for p in positions]
    except Exception as exc:
        logger.warning("GATE7: %s — Alpaca fetch failed, skipping: %s", experiment_id, exc)
        return

    result = check_orphan_positions(experiment_id, pos_dicts, db_path=db_path)

    if result.halt_required:
        try:
            from sentinel.state import set_halt
            set_halt(
                experiment_id,
                f"Gate7: {len(result.orphans)} simultaneous orphan positions",
                halted_by="runtime.py:G7",
                halt_evidence={
                    "gate_id": "G7",
                    "metric_name": "orphan_positions",
                    "stored_value": "0",
                    "current_value": str(len(result.orphans)),
                    "threshold": "0",
                },
            )
        except Exception as exc:
            logger.error("GATE7: failed to halt %s: %s", experiment_id, exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Gate 7 — DB helpers
# ---------------------------------------------------------------------------


def _create_orphan_record(conn, exp_id: str, occ_symbol: str) -> None:
    """Insert a placeholder trade for an orphan position (idempotent via INSERT OR IGNORE)."""
    trade_id = f"orphan_{occ_symbol}"
    try:
        conn.execute(
            "INSERT OR IGNORE INTO trades (id, source, ticker, strategy_type, status, "
            "metadata, created_at, updated_at) "
            "VALUES (?, 'sentinel', ?, 'unknown', 'unmanaged', ?, "
            "datetime('now'), datetime('now'))",
            (
                trade_id,
                _ticker_from_occ(occ_symbol),
                json.dumps({
                    "occ_symbol": occ_symbol,
                    "detected_by": "sentinel_gate7",
                    "experiment_id": exp_id,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.error("GATE7: failed to create orphan record for %s: %s", occ_symbol, exc)


def _mark_ghost_record(conn, ghost: Dict[str, Any]) -> None:
    """Mark a ghost trade as needs_investigation and log reconciliation event.

    Both writes happen in a single transaction to avoid partial updates.
    """
    trade_id = ghost.get("id")
    if not trade_id or trade_id == "unknown":
        return
    try:
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE trades SET status = 'needs_investigation', "
            "updated_at = datetime('now') WHERE id = ?",
            (trade_id,),
        )
        conn.execute(
            "INSERT INTO reconciliation_events "
            "(trade_id, event_type, details) VALUES (?, ?, ?)",
            (
                trade_id,
                "ghost_detected",
                json.dumps({
                    "occ_symbol": ghost.get("occ_symbol", ""),
                    "detected_by": "sentinel_gate7",
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }),
            ),
        )
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        logger.error("GATE7: failed to mark ghost %s: %s", trade_id, exc)


def _read_orphan_scan_count(conn, exp_id: str) -> int:
    """Read consecutive orphan scan count from scanner_state table."""
    try:
        row = conn.execute(
            "SELECT value FROM scanner_state WHERE key = ?",
            (f"{_ORPHAN_COUNT_KEY}_{exp_id}",),
        ).fetchone()
        return int(row["value"]) if row else 0
    except Exception:
        return 0


def _write_orphan_scan_count(conn, exp_id: str, count: int) -> None:
    """Write consecutive orphan scan count to scanner_state table."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO scanner_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (f"{_ORPHAN_COUNT_KEY}_{exp_id}", str(count),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("GATE7: failed to write orphan scan count for %s: %s", exp_id, exc)


# ---------------------------------------------------------------------------
# Gate 7 — Telegram alerting
# ---------------------------------------------------------------------------


def _send_gate7_telegram_alerts(exp_id: str, result: OrphanResult) -> None:
    """Send Telegram alerts for Gate 7 findings."""
    if not result.alerts:
        return

    parts: List[str] = []
    for a in result.alerts:
        icon = {"halt": "🛑", "critical": "🔴", "warning": "⚠️"}.get(a["severity"], "❓")
        parts.append(f"{icon} {a['message']}")

    msg = (
        f"🛡️ SENTINEL — Gate 7 (Orphan Detector)\n"
        f"<b>{exp_id}</b>\n\n"
        + "\n\n".join(parts)
    )

    try:
        from shared.telegram_alerts import send_message
        send_message(msg, parse_mode="HTML")
    except ImportError:
        logger.warning("GATE7: telegram_alerts not importable — alert skipped")
    except Exception as exc:
        logger.error("GATE7: Telegram dispatch failed: %s", exc)


# ---------------------------------------------------------------------------
# Gate 7 — Helpers
# ---------------------------------------------------------------------------


def _ticker_from_occ(occ_symbol: str) -> str:
    """Extract ticker from OCC symbol (e.g. 'SPY   260418P00520000' → 'SPY')."""
    return occ_symbol[:6].strip() if len(occ_symbol) >= 6 else occ_symbol


# ===========================================================================
# post_scan_check — unified entry point for all runtime gates
# ===========================================================================


def post_scan_check(
    exp_id: str,
    db_path: str,
    config: dict,
    *,
    alpaca_positions: Optional[List[Dict]] = None,
    account_equity: Optional[float] = None,
    scan_start_time: Optional[str] = None,
    runtime_gates_enabled: bool = True,
    skip_gates: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Run all runtime gates (6–9) after a scan completes.

    Args:
        exp_id: Experiment identifier (e.g. "EXP-400").
        db_path: Path to the experiment's SQLite database.
        config: Parsed paper config dict (YAML content).
        alpaca_positions: Pre-fetched Alpaca positions (avoids extra API call).
        account_equity: Pre-fetched account equity (for Gate 6).
        scan_start_time: ISO timestamp of scan start (for Gate 6).
        runtime_gates_enabled: Master switch. False skips all gates.
        skip_gates: Optional list of gate numbers to skip (e.g. [6, 7]).

    Returns:
        Dict with per-gate results and a "halted" flag.
    """
    if not runtime_gates_enabled:
        logger.debug("SENTINEL runtime gates disabled for %s", exp_id)
        return {"gates_skipped": True}

    skip = set(skip_gates or [])
    results: Dict[str, Any] = {"halted": False}

    # Gate 6 — Trade Sizing Validator
    if 6 not in skip and account_equity is not None:
        try:
            results["gate6"] = check_trade_sizing(
                exp_id, account_equity, config,
                scan_start_time=scan_start_time,
                db_path=db_path,
            )
            if not results["gate6"].passed:
                _try_halt(exp_id, results["gate6"], "Gate 6")
                results["halted"] = True
        except Exception as e:
            logger.error("SENTINEL Gate 6 error for %s: %s", exp_id, e)
            results["gate6"] = {"error": str(e)}

    # Gate 7 — Orphan Detector
    if 7 not in skip and alpaca_positions is not None:
        try:
            results["gate7"] = check_orphan_positions(
                exp_id, alpaca_positions, db_path=db_path,
            )
            if results["gate7"].halt_required:
                _try_halt(exp_id, results["gate7"], "Gate 7")
                results["halted"] = True
        except Exception as e:
            logger.error("SENTINEL Gate 7 error for %s: %s", exp_id, e)
            results["gate7"] = {"error": str(e)}

    # Gate 8 — Drift Tracker
    if 8 not in skip:
        try:
            results["gate8"] = check_runtime_drift(exp_id, record_snapshot=True)
            halt_alerts = [a for a in results["gate8"] if a.severity == "halt"]
            if halt_alerts:
                reason = "; ".join(a.message for a in halt_alerts)
                try:
                    from sentinel.state import set_halt
                    set_halt(
                        exp_id,
                        f"Gate 8: {reason}"[:200],
                        halted_by="runtime.py:G8",
                        halt_evidence={
                            "gate_id": "G8",
                            "metric_name": "runtime_drift",
                            "stored_value": "baseline",
                            "current_value": reason[:200],
                            "threshold": "halt_severity",
                        },
                    )
                    logger.critical("SENTINEL HALT via Gate 8: %s", exp_id)
                except Exception as e:
                    logger.error("Failed to halt %s via Gate 8: %s", exp_id, e)
                results["halted"] = True
        except Exception as e:
            logger.error("SENTINEL Gate 8 error for %s: %s", exp_id, e)
            results["gate8"] = {"error": str(e)}

    # Gate 9 — Lifecycle Monitor
    if 9 not in skip:
        try:
            results["gate9"] = check_position_lifecycle(exp_id)
            if results["gate9"].has_critical:
                stuck_msgs = [s.message for s in results["gate9"].stuck if s.severity == "critical"]
                reason = f"Gate 9: {'; '.join(stuck_msgs)}"
                try:
                    from sentinel.state import set_halt
                    set_halt(
                        exp_id,
                        reason[:200],
                        halted_by="runtime.py:G9",
                        halt_evidence={
                            "gate_id": "G9",
                            "metric_name": "stuck_positions",
                            "stored_value": "0_critical",
                            "current_value": f"{len(stuck_msgs)}_critical",
                            "threshold": "0_critical",
                        },
                    )
                    logger.critical("SENTINEL HALT via Gate 9: %s", exp_id)
                except Exception as e:
                    logger.error("Failed to halt %s via Gate 9: %s", exp_id, e)
                results["halted"] = True
        except Exception as e:
            logger.error("SENTINEL Gate 9 error for %s: %s", exp_id, e)
            results["gate9"] = {"error": str(e)}

    return results


def _try_halt(exp_id: str, gate_result: Any, gate_name: str) -> None:
    """Attempt to halt an experiment based on gate result."""
    try:
        from sentinel.state import set_halt
        # Per-gate-shape evidence: capture the actual breach metric so
        # `sentinel_cli why-halted` can reconstruct it post-hoc.
        if isinstance(gate_result, SizingResult):
            halt_msgs = [d.message for d in gate_result.deviations if d.severity == "halt"]
            reason = f"{gate_name}: {'; '.join(halt_msgs)}"
            metric = "trade_sizing"
            current = "; ".join(halt_msgs)[:200] or "halt-severity deviation"
        elif isinstance(gate_result, OrphanResult):
            reason = f"{gate_name}: {len(gate_result.orphans)} orphan positions"
            metric = "orphan_positions"
            current = str(len(gate_result.orphans))
        else:
            reason = f"{gate_name}: threshold breached"
            metric = "gate_check"
            current = "threshold_breached"
        gate_id = gate_name.replace(" ", "")  # "Gate 6" → "Gate6"
        set_halt(
            exp_id,
            reason[:200],
            halted_by=f"runtime.py:_try_halt:{gate_id}",
            halt_evidence={
                "gate_id": gate_id,
                "metric_name": metric,
                "stored_value": "pass",
                "current_value": current,
                "threshold": "pass_required",
            },
        )
        logger.critical("SENTINEL HALT via %s: %s — %s", gate_name, exp_id, reason[:200])
    except Exception as e:
        logger.error("Failed to halt %s via %s: %s", exp_id, gate_name, e)


# ---------------------------------------------------------------------------
# Gate 22 — Scanner heartbeat
# ---------------------------------------------------------------------------

# Default freshness window for a scanner heartbeat during market hours.
_HEARTBEAT_THRESHOLD_MIN = 30


def _is_market_hours_et(now_utc: datetime) -> bool:
    """
    Return True if *now_utc* lands inside US equity regular trading hours
    (Mon-Fri 09:30-16:00 America/New_York).  Holidays are NOT modelled here
    — false positives on holidays are tolerable for a heartbeat gate, and
    avoiding a holiday calendar dependency keeps this gate self-contained.

    DST is handled correctly via zoneinfo when available; falls back to a
    fixed UTC-5 offset on systems without tz data.
    """
    try:
        from zoneinfo import ZoneInfo
        et = now_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: assume EDT (UTC-4) March-November, EST (UTC-5) otherwise.
        # Good enough for a sentinel gate that only gates *alerting*.
        offset_h = -4 if 3 <= now_utc.month <= 11 else -5
        et = now_utc + timedelta(hours=offset_h)

    if et.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    open_min = 9 * 60 + 30
    close_min = 16 * 60
    minutes_of_day = et.hour * 60 + et.minute
    return open_min <= minutes_of_day < close_min


def check_scanner_heartbeats(
    db: Any,
    *,
    now: Optional[datetime] = None,
    threshold_minutes: int = _HEARTBEAT_THRESHOLD_MIN,
) -> List[Dict[str, Any]]:
    """
    Gate 22 — flag scanners whose last heartbeat is older than
    *threshold_minutes* during market hours.

    Returns a list of alert dicts:
      {gate, severity, scanner_id, message, last_seen, age_minutes}

    Outside market hours this returns [] regardless of staleness; nightly
    silence is expected.
    """
    now = now or datetime.now(timezone.utc)
    if not _is_market_hours_et(now):
        return []

    cutoff = now - timedelta(minutes=threshold_minutes)
    alerts: List[Dict[str, Any]] = []

    for hb in db.get_heartbeats():
        last_seen_str = hb.get("last_seen")
        if not last_seen_str:
            continue
        try:
            from sentinel.history import _parse_ts
            last_seen = _parse_ts(last_seen_str)
        except Exception:
            last_seen = None
        if last_seen is None:
            continue
        if last_seen >= cutoff:
            continue

        age_minutes = int((now - last_seen).total_seconds() // 60)
        scanner_id = hb["scanner_id"]
        alerts.append({
            "gate": "G22",
            "severity": "warning",
            "scanner_id": scanner_id,
            "last_seen": last_seen_str,
            "age_minutes": age_minutes,
            "message": (
                f"G22 scanner heartbeat stale: {scanner_id} last seen "
                f"{age_minutes}m ago (threshold {threshold_minutes}m)"
            ),
        })

    return alerts


# ---------------------------------------------------------------------------
# Gate 24 — Stale-halt nag (market-day-aware)
# ---------------------------------------------------------------------------

# Thresholds in trading hours (NYSE regular session, Mon-Fri 09:30-16:00 ET).
# 1 market day ≈ 6.5 trading hours → warning
# 1 trading week ≈ 32.5 trading hours (5 × 6.5) → critical
_G24_WARNING_TRADING_HOURS = 6.5
_G24_CRITICAL_TRADING_HOURS = 32.5

_G24_LEGACY_RECOMMENDATION = (
    "Halts predating halted_at coverage (pre-2026-04-28) cannot be aged. "
    "Run `python scripts/sentinel_cli.py why-halted EXP-XXX` for forensic context."
)


@dataclass
class StaleHaltResult:
    """Gate 24 outcome."""
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    acknowledged: List[str] = field(default_factory=list)
    legacy_halts: List[str] = field(default_factory=list)
    legacy_recommendation: str = ""


def check_stale_halts(
    state: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> StaleHaltResult:
    """Gate 24 — flag halts that have aged past one market day / one trading week.

    Reads ``halted_at`` from each halted experiment in *state* and computes
    its age in market-trading hours (Mon-Fri 09:30-16:00 ET). Emits a warning
    at >= 6.5 trading hours and a critical at >= 32.5 trading hours.

    Suppression:
      - ``halt_acknowledged_stale=True`` → skipped (returned in ``acknowledged``)
      - missing ``halted_at`` → skipped (returned in ``legacy_halts`` with a
        recommendation to run ``sentinel_cli why-halted``)

    Active / non-halted experiments are ignored. The function is pure-read:
    no DB writes, no state.json mutation.
    """
    from shared.market_calendar import trading_hours_between

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    result = StaleHaltResult(legacy_recommendation=_G24_LEGACY_RECOMMENDATION)

    experiments = (state or {}).get("experiments", {}) or {}
    for exp_id in sorted(experiments.keys()):
        exp = experiments[exp_id]
        if not isinstance(exp, dict):
            continue
        if exp.get("status") != "halted":
            continue

        if exp.get("halt_acknowledged_stale"):
            result.acknowledged.append(exp_id)
            continue

        halted_at_iso = exp.get("halted_at")
        if not halted_at_iso:
            result.legacy_halts.append(exp_id)
            continue

        try:
            halted_at = datetime.fromisoformat(str(halted_at_iso).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            # Malformed timestamp — treat as legacy so an operator can fix it.
            result.legacy_halts.append(exp_id)
            continue
        if halted_at.tzinfo is None:
            halted_at = halted_at.replace(tzinfo=timezone.utc)

        age_h = trading_hours_between(halted_at, now)

        if age_h >= _G24_CRITICAL_TRADING_HOURS:
            severity = "critical"
        elif age_h >= _G24_WARNING_TRADING_HOURS:
            severity = "warning"
        else:
            continue

        result.alerts.append({
            "gate": "G24",
            "severity": severity,
            "experiment_id": exp_id,
            "halted_at": halted_at_iso,
            "trading_hours_age": round(age_h, 2),
            "message": (
                f"G24 stale halt: {exp_id} halted {age_h:.1f} trading hours ago "
                f"(reason: {(exp.get('halt_reason') or 'unknown')[:80]}). "
                f"Run `sentinel_cli why-halted {exp_id}` to investigate, "
                f"`sentinel_cli ack-stale {exp_id} --by ... --reason ...` to suppress, "
                f"or `sentinel_cli resume {exp_id} ...` to recover."
            ),
        })

    return result
