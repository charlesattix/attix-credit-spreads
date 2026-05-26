"""
portfolio_risk.py — Portfolio-level drawdown circuit breaker.

Fetches combined equity from all 6 Alpaca paper accounts, tracks a
high-water mark (HWM), and exposes a multi-level circuit breaker that
blocks or sizes-down new entries when combined drawdown is too deep.

Usage::

    from shared.portfolio_risk import PortfolioRiskMonitor, CircuitBreakerLevel

    monitor = PortfolioRiskMonitor()        # no API calls in __init__
    status  = monitor.check()               # fetches live equity, updates HWM
    allowed, reason = monitor.allow_entry('EXP-400')

Circuit breaker levels:
    NORMAL    — drawdown > -8 %  : full sizing
    YELLOW    — drawdown <= -8 % : reduce all sizing by 50 %
    RED       — drawdown <= -10 %: pause new entries
    HARD_STOP — drawdown <= -12 %: flatten immediately (paper: LOG ONLY)
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Tuple

from dotenv import dotenv_values

from experiments.manager import get_manager
from shared.constants import DATA_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Circuit breaker thresholds (drawdown = negative percentage from HWM)
# ---------------------------------------------------------------------------

_YELLOW_THRESHOLD    = -8.0   # %
_RED_THRESHOLD       = -10.0  # %
_HARD_STOP_THRESHOLD = -12.0  # %

_CACHE_TTL_SECS = 60  # seconds before re-fetching equity

DB_FILENAME = "portfolio_risk.db"


# ---------------------------------------------------------------------------
# Enums / dataclasses
# ---------------------------------------------------------------------------

class CircuitBreakerLevel(Enum):
    NORMAL    = "normal"
    YELLOW    = "yellow"
    RED       = "red"
    HARD_STOP = "hard_stop"


@dataclass
class PortfolioStatus:
    level: CircuitBreakerLevel
    combined_equity: float
    hwm: float
    drawdown_pct: float              # negative = loss from HWM
    per_account: Dict[str, float]    # {exp_id: equity}
    updated_at: datetime
    action_required: Optional[str]   # None | "reduce_50pct" | "pause_entries" | "flatten_all"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_db(path: str) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hwm_state (
            id INTEGER PRIMARY KEY,
            hwm_equity REAL NOT NULL,
            hwm_date TEXT NOT NULL,
            current_equity REAL,
            current_drawdown_pct REAL,
            cb_level TEXT DEFAULT 'normal',
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at TEXT DEFAULT (datetime('now')),
            combined_equity REAL NOT NULL,
            hwm_equity REAL NOT NULL,
            drawdown_pct REAL NOT NULL,
            cb_level TEXT NOT NULL,
            per_account_json TEXT,
            triggered_action TEXT
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class PortfolioRiskMonitor:
    """Portfolio-level drawdown circuit breaker across all Alpaca accounts.

    Thread-safe.  Constructor does NOT make any API calls — safe to
    instantiate at module import time as a singleton.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        project_root: Optional[str] = None,
        cache_ttl_secs: float = _CACHE_TTL_SECS,
    ) -> None:
        self._root = Path(project_root or PROJECT_ROOT)
        self._db_path = db_path or str(Path(DATA_DIR) / DB_FILENAME)
        self._cache_ttl = cache_ttl_secs
        self._lock = threading.Lock()

        # In-memory cache
        self._last_status: Optional[PortfolioStatus] = None
        self._last_check_ts: float = 0.0

        # Open DB connection and ensure schema exists
        self._conn = _get_db(self._db_path)
        _init_db(self._conn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self) -> PortfolioStatus:
        """Fetch live equity, update HWM, return current PortfolioStatus.

        Results are cached for ``cache_ttl_secs`` seconds to avoid
        hammering Alpaca on every scanner tick.

        Fail-open: if Alpaca is unreachable, returns the last cached status
        (or a synthetic NORMAL status if no cache exists yet).
        """
        import time as _time

        now = _time.monotonic()
        if (self._last_status is not None and
                now - self._last_check_ts < self._cache_ttl):
            return self._last_status

        try:
            per_account = self._fetch_all_equity()
        except Exception as exc:
            logger.warning(
                "PortfolioRiskMonitor: equity fetch failed (%s) — "
                "returning cached/fail-open status", exc,
            )
            return self._fail_open_status()

        combined = sum(per_account.values())
        if combined <= 0:
            logger.warning(
                "PortfolioRiskMonitor: combined equity=%.2f — cannot compute "
                "drawdown; returning fail-open status", combined,
            )
            return self._fail_open_status()

        with self._lock:
            status = self._compute_and_persist(combined, per_account)

        self._last_status = status
        self._last_check_ts = now

        # Auto-trigger hard stop if we just crossed that threshold
        if status.level == CircuitBreakerLevel.HARD_STOP:
            self.execute_hard_stop()

        return status

    def allow_entry(self, experiment_id: str) -> Tuple[bool, Optional[str]]:
        """Return (allowed, reason) for a new entry on *experiment_id*.

        Returns (True, None) for NORMAL and YELLOW (with sizing reduction
        handled separately by the caller).
        Returns (False, reason_str) for RED and HARD_STOP.
        Fail-open: if last status unavailable, returns (True, None).
        """
        status = self.check()
        level = status.level

        if level == CircuitBreakerLevel.NORMAL:
            return True, None
        if level == CircuitBreakerLevel.YELLOW:
            return True, None  # entries allowed; caller must apply 50% sizing
        if level == CircuitBreakerLevel.RED:
            reason = (
                f"CB_RED: entries paused (drawdown={status.drawdown_pct:.2f}% "
                f"<= {_RED_THRESHOLD}%)"
            )
            return False, reason
        if level == CircuitBreakerLevel.HARD_STOP:
            reason = (
                f"CB_HARD_STOP: all entries blocked (drawdown={status.drawdown_pct:.2f}% "
                f"<= {_HARD_STOP_THRESHOLD}%)"
            )
            return False, reason

        # Should never reach here
        return True, None

    def execute_hard_stop(self) -> None:
        """Handle HARD_STOP event.

        Paper mode: LOG ONLY — does not submit real close orders.
        Sends a Telegram alert.
        """
        try:
            total_positions = self._count_open_positions()
        except Exception:
            total_positions = -1  # unknown

        accounts = {e['id']: e.get('env_file') for e in get_manager().live() if e.get('env_file')}
        msg = (
            f"HARD_STOP TRIGGERED: would flatten {total_positions} option "
            f"position(s) across {len(accounts)} accounts. "
            f"drawdown <= {_HARD_STOP_THRESHOLD}%. "
            f"Paper mode — no orders submitted. Manual review required."
        )
        logger.critical("PortfolioRiskMonitor: %s", msg)

        try:
            from shared.telegram_alerts import send_message
            send_message(
                f"<b>PORTFOLIO HARD STOP</b>\n"
                f"Drawdown exceeded {abs(_HARD_STOP_THRESHOLD)}%.\n"
                f"Would flatten {total_positions} position(s).\n"
                f"<i>Paper mode — no orders submitted.</i>",
                parse_mode="HTML",
            )
        except Exception as tg_err:
            logger.warning(
                "PortfolioRiskMonitor: Telegram hard-stop alert failed: %s", tg_err
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_all_equity(self) -> Dict[str, float]:
        """Fetch equity from each Alpaca account.  Returns {exp_id: equity}."""
        from alpaca.trading.client import TradingClient

        accounts = {e['id']: e.get('env_file') for e in get_manager().live() if e.get('env_file')}
        results: Dict[str, float] = {}
        for exp_id, env_file in accounts.items():
            env_path = self._root / env_file
            try:
                creds = dotenv_values(str(env_path))
                api_key = creds.get("ALPACA_API_KEY") or ""
                api_secret = creds.get("ALPACA_API_SECRET") or ""
                if not api_key or not api_secret:
                    logger.warning(
                        "PortfolioRiskMonitor: missing credentials for %s in %s",
                        exp_id, env_path,
                    )
                    continue
                client = TradingClient(api_key, api_secret, paper=True)
                acct = client.get_account()
                equity = float(acct.equity)
                results[exp_id] = equity
                logger.debug(
                    "PortfolioRiskMonitor: %s equity=%.2f", exp_id, equity
                )
            except Exception as exc:
                logger.warning(
                    "PortfolioRiskMonitor: failed to fetch equity for %s: %s",
                    exp_id, exc,
                )
        return results

    def _load_hwm(self) -> Optional[float]:
        """Load persisted HWM from DB.  Returns None if no row exists."""
        row = self._conn.execute(
            "SELECT hwm_equity FROM hwm_state WHERE id = 1"
        ).fetchone()
        return float(row["hwm_equity"]) if row else None

    def _compute_and_persist(
        self,
        combined: float,
        per_account: Dict[str, float],
    ) -> PortfolioStatus:
        """Compute drawdown, update HWM if needed, persist snapshot, return status."""
        now_utc = datetime.now(timezone.utc)
        now_str = now_utc.isoformat()

        stored_hwm = self._load_hwm()
        hwm = max(stored_hwm or combined, combined)

        # Persist updated HWM
        drawdown_pct = (combined - hwm) / hwm * 100.0  # negative = loss

        level = _level_from_drawdown(drawdown_pct)
        action = _action_from_level(level)

        if stored_hwm is None:
            # First run — insert row
            self._conn.execute(
                """
                INSERT INTO hwm_state
                    (id, hwm_equity, hwm_date, current_equity,
                     current_drawdown_pct, cb_level, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?)
                """,
                (hwm, now_str, combined, drawdown_pct, level.value, now_str),
            )
        else:
            self._conn.execute(
                """
                UPDATE hwm_state SET
                    hwm_equity = ?,
                    hwm_date = CASE WHEN ? > hwm_equity THEN ? ELSE hwm_date END,
                    current_equity = ?,
                    current_drawdown_pct = ?,
                    cb_level = ?,
                    updated_at = ?
                WHERE id = 1
                """,
                (hwm, combined, now_str, combined, drawdown_pct, level.value, now_str),
            )

        # Audit snapshot
        self._conn.execute(
            """
            INSERT INTO equity_snapshots
                (snapshot_at, combined_equity, hwm_equity, drawdown_pct,
                 cb_level, per_account_json, triggered_action)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_str, combined, hwm, drawdown_pct, level.value,
                json.dumps(per_account), action,
            ),
        )
        self._conn.commit()

        logger.info(
            "PortfolioRiskMonitor: combined=%.2f hwm=%.2f dd=%.2f%% level=%s",
            combined, hwm, drawdown_pct, level.value,
        )

        return PortfolioStatus(
            level=level,
            combined_equity=combined,
            hwm=hwm,
            drawdown_pct=drawdown_pct,
            per_account=per_account,
            updated_at=now_utc,
            action_required=action,
        )

    def _fail_open_status(self) -> PortfolioStatus:
        """Return last cached status (NORMAL if none cached)."""
        if self._last_status is not None:
            return self._last_status
        return PortfolioStatus(
            level=CircuitBreakerLevel.NORMAL,
            combined_equity=0.0,
            hwm=0.0,
            drawdown_pct=0.0,
            per_account={},
            updated_at=datetime.now(timezone.utc),
            action_required=None,
        )

    def _count_open_positions(self) -> int:
        """Approximate total open option positions across all accounts."""
        from alpaca.trading.client import TradingClient

        accounts = {e['id']: e.get('env_file') for e in get_manager().live() if e.get('env_file')}
        total = 0
        for exp_id, env_file in accounts.items():
            env_path = self._root / env_file
            try:
                creds = dotenv_values(str(env_path))
                api_key = creds.get("ALPACA_API_KEY") or ""
                api_secret = creds.get("ALPACA_API_SECRET") or ""
                if not api_key or not api_secret:
                    continue
                client = TradingClient(api_key, api_secret, paper=True)
                positions = client.get_all_positions()
                # Count only option positions (OCC symbols are > 6 chars)
                option_positions = [
                    p for p in positions
                    if hasattr(p, "symbol") and len(str(p.symbol)) > 6
                ]
                total += len(option_positions)
            except Exception as exc:
                logger.debug(
                    "PortfolioRiskMonitor: could not count positions for %s: %s",
                    exp_id, exc,
                )
        return total


# ---------------------------------------------------------------------------
# Module-level singleton (one per process)
# ---------------------------------------------------------------------------

_monitor_instance: Optional[PortfolioRiskMonitor] = None
_monitor_lock = threading.Lock()


def get_monitor() -> PortfolioRiskMonitor:
    """Return the process-wide singleton PortfolioRiskMonitor."""
    global _monitor_instance
    if _monitor_instance is None:
        with _monitor_lock:
            if _monitor_instance is None:
                _monitor_instance = PortfolioRiskMonitor()
    return _monitor_instance


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def _level_from_drawdown(drawdown_pct: float) -> CircuitBreakerLevel:
    """Map a drawdown percentage (negative = loss) to a CB level."""
    if drawdown_pct <= _HARD_STOP_THRESHOLD:
        return CircuitBreakerLevel.HARD_STOP
    if drawdown_pct <= _RED_THRESHOLD:
        return CircuitBreakerLevel.RED
    if drawdown_pct <= _YELLOW_THRESHOLD:
        return CircuitBreakerLevel.YELLOW
    return CircuitBreakerLevel.NORMAL


def _action_from_level(level: CircuitBreakerLevel) -> Optional[str]:
    return {
        CircuitBreakerLevel.NORMAL:    None,
        CircuitBreakerLevel.YELLOW:    "reduce_50pct",
        CircuitBreakerLevel.RED:       "pause_entries",
        CircuitBreakerLevel.HARD_STOP: "flatten_all",
    }[level]
