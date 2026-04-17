"""
Portfolio-level drawdown circuit breaker.

Aggregates equity across ALL experiments via Alpaca accounts,
tracks drawdown from peak (HWM), and enforces three tiers:

  CAUTION  at -8%  → reduce position sizes by 50%
  PAUSE    at -10% → no new trades allowed
  HALT     at -12% → flatten all positions + require manual reset

State is persisted in ``circuit_breaker_state.json`` so it survives
process restarts.  Each scanner calls ``check()`` before opening
new positions.

Usage::

    from shared.portfolio_circuit_breaker import PortfolioCircuitBreaker

    pcb = PortfolioCircuitBreaker()
    result = pcb.check()       # fetches equity, updates state
    if result["level"] == "HALT":
        sys.exit(1)
    sizing_mult = result["sizing_multiplier"]   # 1.0 or 0.5
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Level constants ──────────────────────────────────────────────────────────

LEVEL_NORMAL  = "NORMAL"
LEVEL_CAUTION = "CAUTION"
LEVEL_PAUSE   = "PAUSE"
LEVEL_HALT    = "HALT"

_LEVELS_ORDERED = [LEVEL_NORMAL, LEVEL_CAUTION, LEVEL_PAUSE, LEVEL_HALT]

# ── Default thresholds (as negative fractions) ───────────────────────────────

DEFAULT_CAUTION_PCT = -0.08   # -8%
DEFAULT_PAUSE_PCT   = -0.10   # -10%
DEFAULT_HALT_PCT    = -0.12   # -12%
DEFAULT_RECOVERY_PCT = -0.06  # recover to NORMAL when DD > -6%

DEFAULT_STATE_FILE = _PROJECT_ROOT / "circuit_breaker_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PortfolioCircuitBreaker:
    """Portfolio-level drawdown circuit breaker across all experiments."""

    def __init__(
        self,
        state_file: Optional[str | Path] = None,
        caution_pct: float = DEFAULT_CAUTION_PCT,
        pause_pct: float = DEFAULT_PAUSE_PCT,
        halt_pct: float = DEFAULT_HALT_PCT,
        recovery_pct: float = DEFAULT_RECOVERY_PCT,
        equity_fetcher: Optional[Callable[[], float]] = None,
        alert_fn: Optional[Callable[[str], bool]] = None,
    ):
        """
        Args:
            state_file:     Path to JSON state file.
            caution_pct:    DD threshold for CAUTION (default -0.08).
            pause_pct:      DD threshold for PAUSE (default -0.10).
            halt_pct:       DD threshold for HALT (default -0.12).
            recovery_pct:   DD above which CAUTION/PAUSE recover to NORMAL.
            equity_fetcher: Callable returning total portfolio equity.
                            Default uses ``_fetch_all_alpaca_equity()``.
            alert_fn:       Callable(message) → bool for notifications.
                            Default uses ``shared.telegram_alerts.send_message``.
        """
        self.state_file = Path(state_file or DEFAULT_STATE_FILE)
        self.caution_pct = caution_pct
        self.pause_pct = pause_pct
        self.halt_pct = halt_pct
        self.recovery_pct = recovery_pct
        self._equity_fetcher = equity_fetcher or _fetch_all_alpaca_equity
        self._alert_fn = alert_fn or _default_alert

        # Load persisted state
        self._state = self._load_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, equity_override: Optional[float] = None) -> Dict[str, Any]:
        """Fetch equity, update drawdown state, return action dict.

        Args:
            equity_override: Use this value instead of fetching from Alpaca.
                             Useful for tests or when equity is already known.

        Returns:
            Dict with keys:
                level:              NORMAL | CAUTION | PAUSE | HALT
                sizing_multiplier:  1.0 (normal), 0.5 (caution), 0.0 (pause/halt)
                entry_allowed:      bool
                drawdown_pct:       float (e.g. -0.085)
                hwm:                float
                equity:             float
                action:             str describing what happened
                message:            human-readable summary
        """
        # 1. Get current equity
        if equity_override is not None:
            equity = equity_override
        else:
            try:
                equity = self._equity_fetcher()
            except Exception as exc:
                logger.error("Portfolio CB: equity fetch failed: %s", exc)
                # Fail-open: return current state without changing levels
                return self._make_result(
                    action="fetch_failed",
                    message=f"Equity fetch failed: {exc}",
                )

        if equity <= 0:
            logger.warning("Portfolio CB: equity=%.2f ≤ 0, skipping", equity)
            return self._make_result(action="invalid_equity", message="Invalid equity")

        # 2. Update HWM
        hwm = self._state.get("hwm", 0.0)
        if equity > hwm:
            self._state["hwm"] = equity
            self._state["hwm_date"] = _now_iso()
            hwm = equity

        self._state["last_equity"] = equity
        self._state["last_check"] = _now_iso()

        # 3. Compute drawdown
        dd = (equity - hwm) / hwm if hwm > 0 else 0.0

        # 4. Evaluate level transitions
        prev_level = self._state.get("level", LEVEL_NORMAL)
        new_level = self._evaluate_level(dd, prev_level)

        # 5. Handle transition
        action = "none"
        message = f"DD={dd:.1%} level={new_level}"

        if new_level != prev_level:
            action = f"transition_{prev_level}_to_{new_level}".lower()
            message = self._transition_message(prev_level, new_level, dd, equity, hwm)
            self._state["level"] = new_level
            self._state["last_transition"] = _now_iso()
            self._state["transition_history"] = self._state.get("transition_history", [])
            self._state["transition_history"].append({
                "from": prev_level,
                "to": new_level,
                "dd_pct": round(dd * 100, 2),
                "equity": round(equity, 2),
                "hwm": round(hwm, 2),
                "at": _now_iso(),
            })
            # Keep last 50 transitions
            self._state["transition_history"] = self._state["transition_history"][-50:]
            # Alert on level changes
            self._send_alert(message)
            logger.warning("Portfolio CB: %s", message)
        else:
            self._state["level"] = new_level

        self._state["drawdown_pct"] = round(dd, 6)

        # 6. Persist
        self._save_state()

        return self._make_result(action=action, message=message)

    def is_entry_allowed(self) -> bool:
        """Return True if new trade entries are permitted."""
        return self._state.get("level", LEVEL_NORMAL) in (LEVEL_NORMAL, LEVEL_CAUTION)

    def get_sizing_multiplier(self) -> float:
        """Return position sizing multiplier: 1.0 normal, 0.5 caution, 0.0 otherwise."""
        level = self._state.get("level", LEVEL_NORMAL)
        if level == LEVEL_NORMAL:
            return 1.0
        elif level == LEVEL_CAUTION:
            return 0.5
        return 0.0

    def get_status(self) -> Dict[str, Any]:
        """Return full status dict for dashboards."""
        return {
            "level": self._state.get("level", LEVEL_NORMAL),
            "hwm": self._state.get("hwm", 0.0),
            "hwm_date": self._state.get("hwm_date"),
            "last_equity": self._state.get("last_equity", 0.0),
            "drawdown_pct": self._state.get("drawdown_pct", 0.0),
            "last_check": self._state.get("last_check"),
            "last_transition": self._state.get("last_transition"),
            "entry_allowed": self.is_entry_allowed(),
            "sizing_multiplier": self.get_sizing_multiplier(),
            "thresholds": {
                "caution": self.caution_pct,
                "pause": self.pause_pct,
                "halt": self.halt_pct,
                "recovery": self.recovery_pct,
            },
        }

    def reset(self, reason: str = "manual") -> None:
        """Reset from HALT back to NORMAL. Requires intentional operator action."""
        prev = self._state.get("level", LEVEL_NORMAL)
        self._state["level"] = LEVEL_NORMAL
        self._state["last_transition"] = _now_iso()
        self._state["transition_history"] = self._state.get("transition_history", [])
        self._state["transition_history"].append({
            "from": prev,
            "to": LEVEL_NORMAL,
            "reason": f"manual_reset: {reason}",
            "at": _now_iso(),
        })
        self._save_state()
        msg = f"Portfolio CB RESET from {prev} → NORMAL (reason: {reason})"
        self._send_alert(msg)
        logger.warning(msg)

    def set_hwm(self, hwm: float) -> None:
        """Manually set the high-water mark (for initial setup)."""
        self._state["hwm"] = hwm
        self._state["hwm_date"] = _now_iso()
        self._save_state()

    # ── Level evaluation ──────────────────────────────────────────────────────

    def _evaluate_level(self, dd: float, current_level: str) -> str:
        """Determine the appropriate level based on drawdown.

        Rules:
        - Levels only escalate (NORMAL→CAUTION→PAUSE→HALT), never skip.
        - HALT never auto-recovers (requires manual reset).
        - CAUTION/PAUSE recover to NORMAL when DD > recovery_pct.
        """
        if current_level == LEVEL_HALT:
            return LEVEL_HALT  # no auto-recovery

        # Check for escalation
        if dd <= self.halt_pct:
            return LEVEL_HALT
        if dd <= self.pause_pct:
            # Can escalate from NORMAL or CAUTION, or stay at PAUSE
            if _level_rank(current_level) <= _level_rank(LEVEL_PAUSE):
                return LEVEL_PAUSE
            return current_level
        if dd <= self.caution_pct:
            if _level_rank(current_level) <= _level_rank(LEVEL_CAUTION):
                return LEVEL_CAUTION
            return current_level

        # DD is better than caution threshold — check recovery
        if current_level in (LEVEL_CAUTION, LEVEL_PAUSE):
            if dd > self.recovery_pct:
                return LEVEL_NORMAL
            return current_level

        return LEVEL_NORMAL

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_result(self, action: str, message: str) -> Dict[str, Any]:
        level = self._state.get("level", LEVEL_NORMAL)
        return {
            "level": level,
            "sizing_multiplier": self.get_sizing_multiplier(),
            "entry_allowed": self.is_entry_allowed(),
            "drawdown_pct": self._state.get("drawdown_pct", 0.0),
            "hwm": self._state.get("hwm", 0.0),
            "equity": self._state.get("last_equity", 0.0),
            "action": action,
            "message": message,
        }

    def _transition_message(
        self, prev: str, new: str, dd: float, equity: float, hwm: float,
    ) -> str:
        arrow = f"{prev} → {new}"
        parts = [f"Portfolio CB: {arrow} | DD={dd:.1%} | equity=${equity:,.0f} | HWM=${hwm:,.0f}"]
        if new == LEVEL_CAUTION:
            parts.append("Position sizes reduced to 50%.")
        elif new == LEVEL_PAUSE:
            parts.append("New entries BLOCKED.")
        elif new == LEVEL_HALT:
            parts.append("HARD STOP — flatten all positions. Manual reset required.")
        elif new == LEVEL_NORMAL and prev != LEVEL_NORMAL:
            parts.append("Recovered — full sizing restored.")
        return " ".join(parts)

    def _send_alert(self, message: str) -> None:
        try:
            self._alert_fn(message)
        except Exception as exc:
            logger.debug("Portfolio CB: alert send failed (non-fatal): %s", exc)

    # ── State persistence ─────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Portfolio CB: failed to load state: %s", exc)
        return {
            "level": LEVEL_NORMAL,
            "hwm": 0.0,
            "hwm_date": None,
            "last_equity": 0.0,
            "drawdown_pct": 0.0,
            "last_check": None,
            "last_transition": None,
            "transition_history": [],
        }

    def _save_state(self) -> None:
        """Atomic write: write to temp file, then rename."""
        try:
            data = json.dumps(self._state, indent=2, default=str)
            # Write to a temp file in the same directory, then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.state_file.parent),
                prefix=".cb_state_",
                suffix=".tmp",
            )
            try:
                os.write(fd, data.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp_path, str(self.state_file))
        except OSError as exc:
            logger.error("Portfolio CB: failed to save state: %s", exc)


# ── Level ranking helper ─────────────────────────────────────────────────────

def _level_rank(level: str) -> int:
    """Return numeric rank: NORMAL=0, CAUTION=1, PAUSE=2, HALT=3."""
    try:
        return _LEVELS_ORDERED.index(level)
    except ValueError:
        return 0


# ── Default equity fetcher ───────────────────────────────────────────────────

def _fetch_all_alpaca_equity() -> float:
    """Sum equity across all experiment Alpaca accounts.

    Uses ``shared.credentials.get_all_portfolios()`` + ``check_portfolio()``
    to discover and query all accounts.
    """
    from shared.credentials import get_all_portfolios, check_portfolio

    portfolios = get_all_portfolios()
    if not portfolios:
        raise RuntimeError("No .env.exp* files found — cannot fetch equity")

    total = 0.0
    ok_count = 0
    errors: List[str] = []

    for p in portfolios:
        result = check_portfolio(p["env_file"])
        if result["ok"]:
            total += result["equity"]
            ok_count += 1
        else:
            errors.append(f"{p['experiment']}: {result['error']}")

    if ok_count == 0:
        raise RuntimeError(f"All equity fetches failed: {'; '.join(errors)}")

    if errors:
        logger.warning(
            "Portfolio CB: %d/%d accounts failed: %s",
            len(errors), len(portfolios), "; ".join(errors),
        )

    return total


# ── Default alert function ───────────────────────────────────────────────────

def _default_alert(message: str) -> bool:
    """Send Telegram alert via shared.telegram_alerts."""
    try:
        from shared.telegram_alerts import send_message
        return send_message(f"🛡️ <b>CIRCUIT BREAKER</b>\n\n{message}", parse_mode="HTML")
    except Exception as exc:
        logger.debug("Portfolio CB: telegram alert failed: %s", exc)
        return False
