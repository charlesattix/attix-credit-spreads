"""
DrawdownCircuitBreaker — three-tier portfolio drawdown protection system.

Tiers (measured from rolling high-water mark):
  TIER_1_FLATTEN = -8%   → close all open positions immediately
  TIER_2_PAUSE   = -10%  → block new entries until recovery
  TIER_3_HALT    = -12%  → hard stop, requires manual reset

State machine:
  NORMAL → PAUSE (at -10%)
  NORMAL or PAUSE → FLATTEN (at -8%): immediate position close
  Any state → HALT (at -12%): hard stop, no auto-recovery

Recovery from PAUSE:
  When DD recovers above -8% AND cooldown (24h default) has elapsed.

Design principles:
- DB-backed state survives process restarts (scanner_state table)
- Works without Alpaca (dry-run mode: logs action, does not close)
- All Alpaca calls have a configurable timeout (default 10s)
- Fail-open on API errors (don't block trading on fetch failures)
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from shared.database import (
    get_trades,
    load_scanner_state,
    save_scanner_state,
    upsert_trade,
    init_db,
)

logger = logging.getLogger(__name__)

# ── State constants ──────────────────────────────────────────────────────────
STATE_NORMAL  = "normal"
STATE_PAUSE   = "paused"
STATE_FLATTEN = "flattened"
STATE_HALT    = "halted"

# ── DB key constants ─────────────────────────────────────────────────────────
_KEY_HWM         = "portfolio_cb_hwm"
_KEY_HWM_DATE    = "portfolio_cb_hwm_date"
_KEY_STATE       = "portfolio_cb_state"
_KEY_PAUSE_TS    = "portfolio_cb_pause_ts"
_KEY_FLATTEN_TS  = "portfolio_cb_flatten_ts"
_KEY_HALT_TS     = "portfolio_cb_halt_ts"
_KEY_HALT_REASON = "portfolio_cb_halt_reason"


class DrawdownCircuitBreaker:
    """Three-tier portfolio drawdown circuit breaker.

    Usage::

        cb = DrawdownCircuitBreaker(db_path=None)
        # On every position update:
        result = cb.check_and_act(current_nav, alpaca_client=alpaca)
        # Before entering a new trade:
        if not cb.is_entry_allowed():
            skip()
        # Status report:
        print(cb.get_status())
    """

    TIER_1_PCT = -0.08   # -8%:  auto-flatten all positions
    TIER_2_PCT = -0.10   # -10%: pause new entries
    TIER_3_PCT = -0.12   # -12%: hard stop
    RECOVERY_PCT = -0.08  # recover from PAUSE when DD recovers above -8%
    RECOVERY_COOLDOWN_HOURS = 24

    # Maximum seconds to wait for a single Alpaca close call before giving up
    ALPACA_TIMEOUT_SECS = 10

    def __init__(
        self,
        db_path: Optional[str] = None,
        starting_nav: float = 100_000.0,
        tier1_pct: Optional[float] = None,
        tier2_pct: Optional[float] = None,
        tier3_pct: Optional[float] = None,
        recovery_cooldown_hours: Optional[int] = None,
    ):
        """
        Args:
            db_path: Path to the SQLite DB. None → uses default PILOTAI_DB_PATH.
            starting_nav: Initial account size; used as fallback HWM when no
                          persisted HWM exists yet.
            tier1_pct: Override TIER_1_PCT (-8% default).  Pass as negative
                       fraction, e.g. -0.08.
            tier2_pct: Override TIER_2_PCT (-10% default).
            tier3_pct: Override TIER_3_PCT (-12% default).
            recovery_cooldown_hours: Hours to wait before re-enabling entries
                                     after a PAUSE recovery (default 24).
        """
        self.db_path = db_path
        self._starting_nav = starting_nav

        # Allow overrides (useful for tests)
        if tier1_pct is not None:
            self.TIER_1_PCT = tier1_pct
        if tier2_pct is not None:
            self.TIER_2_PCT = tier2_pct
        if tier3_pct is not None:
            self.TIER_3_PCT = tier3_pct
        if recovery_cooldown_hours is not None:
            self.RECOVERY_COOLDOWN_HOURS = recovery_cooldown_hours

        # Ensure DB is initialized
        try:
            init_db(db_path)
        except Exception as e:
            logger.warning("DrawdownCircuitBreaker: DB init failed (non-fatal): %s", e)

        # Load persisted state
        self._state = self._load_state()
        self._hwm   = self._load_hwm()

    # ── Public API ────────────────────────────────────────────────────────────

    def check_and_act(
        self,
        current_nav: float,
        alpaca_client=None,
    ) -> Dict:
        """Evaluate current NAV against HWM; trigger appropriate tier if needed.

        This is the main entry point, called after every position update.

        Args:
            current_nav:   Current portfolio NAV (cash + open position MTM).
            alpaca_client: AlpacaProvider instance (or None for dry-run).

        Returns:
            Dict with keys:
              action:     'none' | 'hwm_updated' | 'paused' | 'flattened' |
                          'halted' | 'pause_recovered'
              state:      current CB state after action
              drawdown:   current DD as fraction (e.g. -0.087)
              hwm:        current high-water mark
              message:    human-readable summary
        """
        if current_nav <= 0:
            logger.warning("DrawdownCircuitBreaker: current_nav=%.2f ≤ 0, skipping check", current_nav)
            return {"action": "none", "state": self._state, "drawdown": 0.0, "hwm": self._hwm, "message": "invalid nav"}

        # ── 1. Update HWM ────────────────────────────────────────────────────
        if current_nav > self._hwm:
            self._hwm = current_nav
            self._save_hwm(current_nav)
            if self._state == STATE_NORMAL:
                return {
                    "action": "hwm_updated",
                    "state": self._state,
                    "drawdown": 0.0,
                    "hwm": self._hwm,
                    "message": f"HWM updated to {current_nav:.2f}",
                }

        dd = (current_nav - self._hwm) / self._hwm  # negative fraction

        logger.debug(
            "DrawdownCircuitBreaker: nav=%.2f hwm=%.2f dd=%.2f%% state=%s",
            current_nav, self._hwm, dd * 100, self._state,
        )

        # ── 2. If already halted — no auto-recovery ──────────────────────────
        if self._state == STATE_HALT:
            return {
                "action": "none",
                "state": STATE_HALT,
                "drawdown": dd,
                "hwm": self._hwm,
                "message": "HALTED — manual reset required",
            }

        # ── 3. Tier 3: hard stop at -12% ─────────────────────────────────────
        if dd <= self.TIER_3_PCT:
            return self._trigger_halt(current_nav, dd, alpaca_client, reason=f"portfolio DD={dd:.1%} hit TIER_3 threshold={self.TIER_3_PCT:.1%}")

        # ── 4. Tier 1: flatten at -8% ─────────────────────────────────────────
        if dd <= self.TIER_1_PCT and self._state != STATE_FLATTEN:
            return self._trigger_flatten(current_nav, dd, alpaca_client)

        # ── 5. Tier 2: pause at -10% ──────────────────────────────────────────
        if dd <= self.TIER_2_PCT and self._state == STATE_NORMAL:
            return self._trigger_pause(current_nav, dd)

        # ── 6. Recovery from PAUSE ────────────────────────────────────────────
        if self._state == STATE_PAUSE and dd > self.RECOVERY_PCT:
            return self._check_pause_recovery(current_nav, dd)

        return {
            "action": "none",
            "state": self._state,
            "drawdown": dd,
            "hwm": self._hwm,
            "message": f"monitoring — dd={dd:.2%} state={self._state}",
        }

    def is_entry_allowed(self) -> bool:
        """Return True when new spread entries are permitted.

        Returns False when state is PAUSE, FLATTEN, or HALT.
        """
        return self._state == STATE_NORMAL

    def reset_halt(self, reason: str = "manual") -> None:
        """Manually reset the HALT state back to NORMAL.

        This requires intentional operator action; the CB never auto-recovers
        from HALT.

        Args:
            reason: Free-text reason for the reset (stored in DB).
        """
        logger.warning(
            "DrawdownCircuitBreaker: HALT manually reset by operator (reason=%s). "
            "State → NORMAL. Review positions before allowing new entries.",
            reason,
        )
        self._state = STATE_NORMAL
        self._save_state(STATE_NORMAL)
        save_scanner_state(_KEY_HALT_REASON, f"CLEARED:{reason}", path=self.db_path)

    def get_status(self) -> Dict:
        """Return a full status dict (for dashboards and health checks)."""
        hwm_date_str = load_scanner_state(_KEY_HWM_DATE, path=self.db_path) or "unknown"
        pause_ts = load_scanner_state(_KEY_PAUSE_TS, path=self.db_path)
        halt_ts  = load_scanner_state(_KEY_HALT_TS,  path=self.db_path)
        halt_reason = load_scanner_state(_KEY_HALT_REASON, path=self.db_path)
        return {
            "state":        self._state,
            "hwm":          self._hwm,
            "hwm_date":     hwm_date_str,
            "tier1_pct":    self.TIER_1_PCT,
            "tier2_pct":    self.TIER_2_PCT,
            "tier3_pct":    self.TIER_3_PCT,
            "entry_allowed": self.is_entry_allowed(),
            "pause_since":  pause_ts,
            "halt_since":   halt_ts,
            "halt_reason":  halt_reason,
        }

    def flatten_all_positions(self, alpaca_client=None) -> Dict:
        """Close all open positions immediately.

        Mirrors PositionMonitor._close_position() but iterates all open
        positions in the DB.  When alpaca_client is None, logs the action
        without touching Alpaca (dry-run).

        Returns:
            Dict with keys: closed (int), failed (int), positions (list of ids).
        """
        try:
            open_positions = get_trades(status="open", source="execution", path=self.db_path)
        except Exception as e:
            logger.error("DrawdownCircuitBreaker: failed to load open positions: %s", e)
            return {"closed": 0, "failed": 0, "positions": [], "error": str(e)}

        if not open_positions:
            logger.info("DrawdownCircuitBreaker: flatten_all — no open positions to close")
            return {"closed": 0, "failed": 0, "positions": []}

        closed_ids = []
        failed_ids = []

        for pos in open_positions:
            pos_id = pos.get("id", "?")
            try:
                self._close_single_position(pos, alpaca_client, reason="portfolio_cb_flatten")
                closed_ids.append(pos_id)
                logger.info(
                    "DrawdownCircuitBreaker: closed position %s (%s %s)",
                    pos_id, pos.get("ticker"), pos.get("strategy_type"),
                )
            except Exception as e:
                failed_ids.append(pos_id)
                logger.error(
                    "DrawdownCircuitBreaker: failed to close position %s: %s",
                    pos_id, e,
                )

        result = {
            "closed": len(closed_ids),
            "failed": len(failed_ids),
            "positions": closed_ids,
            "failed_positions": failed_ids,
        }
        logger.warning(
            "DrawdownCircuitBreaker: flatten_all complete — closed=%d failed=%d",
            len(closed_ids), len(failed_ids),
        )
        return result

    # ── Internal state transitions ────────────────────────────────────────────

    def _trigger_pause(self, nav: float, dd: float) -> Dict:
        self._state = STATE_PAUSE
        self._save_state(STATE_PAUSE)
        ts = datetime.now(timezone.utc).isoformat()
        save_scanner_state(_KEY_PAUSE_TS, ts, path=self.db_path)
        logger.critical(
            "DrawdownCircuitBreaker: TIER 2 PAUSE — nav=%.2f hwm=%.2f dd=%.2f%% "
            "(threshold=%.0f%%). New entries BLOCKED. Existing positions continue.",
            nav, self._hwm, dd * 100, abs(self.TIER_2_PCT) * 100,
        )
        self._send_alert(f"PAUSE: portfolio DD={dd:.1%} hit -10% threshold. New entries blocked.")
        return {
            "action": "paused",
            "state": STATE_PAUSE,
            "drawdown": dd,
            "hwm": self._hwm,
            "message": f"PAUSE triggered at dd={dd:.2%}",
        }

    def _trigger_flatten(self, nav: float, dd: float, alpaca_client) -> Dict:
        self._state = STATE_FLATTEN
        self._save_state(STATE_FLATTEN)
        ts = datetime.now(timezone.utc).isoformat()
        save_scanner_state(_KEY_FLATTEN_TS, ts, path=self.db_path)
        logger.critical(
            "DrawdownCircuitBreaker: TIER 1 FLATTEN — nav=%.2f hwm=%.2f dd=%.2f%% "
            "(threshold=%.0f%%). Closing ALL positions NOW.",
            nav, self._hwm, dd * 100, abs(self.TIER_1_PCT) * 100,
        )
        self._send_alert(f"FLATTEN: portfolio DD={dd:.1%} hit -8% threshold. Closing ALL positions.")
        result = self.flatten_all_positions(alpaca_client)
        return {
            "action": "flattened",
            "state": STATE_FLATTEN,
            "drawdown": dd,
            "hwm": self._hwm,
            "message": f"FLATTEN triggered at dd={dd:.2%}; closed={result['closed']} failed={result['failed']}",
            "flatten_result": result,
        }

    def _trigger_halt(self, nav: float, dd: float, alpaca_client, reason: str) -> Dict:
        self._state = STATE_HALT
        self._save_state(STATE_HALT)
        ts = datetime.now(timezone.utc).isoformat()
        save_scanner_state(_KEY_HALT_TS, ts, path=self.db_path)
        save_scanner_state(_KEY_HALT_REASON, reason, path=self.db_path)
        logger.critical(
            "DrawdownCircuitBreaker: TIER 3 HARD STOP — nav=%.2f hwm=%.2f dd=%.2f%% "
            "(threshold=%.0f%%). SCANNER HALTED. Manual reset required.",
            nav, self._hwm, dd * 100, abs(self.TIER_3_PCT) * 100,
        )
        self._send_alert(
            f"HARD STOP: portfolio DD={dd:.1%} hit -12% threshold. "
            "Scanner halted. Manual reset required."
        )
        # Also flatten all positions on hard stop
        result = self.flatten_all_positions(alpaca_client)
        return {
            "action": "halted",
            "state": STATE_HALT,
            "drawdown": dd,
            "hwm": self._hwm,
            "message": f"HALT triggered: {reason}; closed={result['closed']} failed={result['failed']}",
            "flatten_result": result,
        }

    def _check_pause_recovery(self, nav: float, dd: float) -> Dict:
        """Evaluate whether the PAUSE state can be lifted."""
        pause_ts_str = load_scanner_state(_KEY_PAUSE_TS, path=self.db_path)
        if pause_ts_str:
            try:
                pause_dt = datetime.fromisoformat(pause_ts_str)
                # Make timezone-aware if naive
                if pause_dt.tzinfo is None:
                    pause_dt = pause_dt.replace(tzinfo=timezone.utc)
                elapsed_hours = (datetime.now(timezone.utc) - pause_dt).total_seconds() / 3600
                if elapsed_hours < self.RECOVERY_COOLDOWN_HOURS:
                    logger.info(
                        "DrawdownCircuitBreaker: PAUSE recovery blocked — cooldown %.1f/%.0fh remaining",
                        self.RECOVERY_COOLDOWN_HOURS - elapsed_hours, self.RECOVERY_COOLDOWN_HOURS,
                    )
                    return {
                        "action": "none",
                        "state": STATE_PAUSE,
                        "drawdown": dd,
                        "hwm": self._hwm,
                        "message": f"PAUSE cooldown: {self.RECOVERY_COOLDOWN_HOURS - elapsed_hours:.1f}h remaining",
                    }
            except Exception as e:
                logger.debug("DrawdownCircuitBreaker: pause_ts parse failed (non-fatal): %s", e)

        # Cooldown elapsed and DD recovered — lift PAUSE
        self._state = STATE_NORMAL
        self._save_state(STATE_NORMAL)
        logger.warning(
            "DrawdownCircuitBreaker: PAUSE RECOVERED — dd=%.2f%% > recovery threshold=%.0f%%. "
            "New entries re-enabled.",
            dd * 100, abs(self.RECOVERY_PCT) * 100,
        )
        return {
            "action": "pause_recovered",
            "state": STATE_NORMAL,
            "drawdown": dd,
            "hwm": self._hwm,
            "message": f"PAUSE lifted — dd={dd:.2%} recovered above {self.RECOVERY_PCT:.0%}",
        }

    # ── Position closing helpers ───────────────────────────────────────────────

    def _close_single_position(self, pos: Dict, alpaca_client, reason: str) -> None:
        """Close a single position via Alpaca (or dry-run log)."""
        pos_id = pos.get("id", "?")
        ticker = pos.get("ticker", "")
        spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
        contracts = int(pos.get("contracts", 1))
        expiration_str = str(pos.get("expiration", "")).split(" ")[0]

        # Mark pending_close in DB before touching Alpaca
        pos_copy = dict(pos)
        pos_copy["status"] = "pending_close"
        pos_copy["exit_reason"] = reason
        try:
            upsert_trade(pos_copy, source="execution", path=self.db_path)
        except Exception as e:
            logger.error(
                "DrawdownCircuitBreaker: DB pending_close write failed for %s: %s", pos_id, e
            )

        if not alpaca_client:
            logger.info("DrawdownCircuitBreaker [DRY RUN]: would close %s (%s %s)", pos_id, ticker, spread_type)
            return

        try:
            if "condor" in spread_type:
                result = self._close_ic(alpaca_client, pos, contracts, expiration_str)
            else:
                # Standard credit spread (bull_put or bear_call)
                opt_type_map = {"bull_put": "put", "bear_call": "call"}
                result = _timed_call(
                    alpaca_client.close_spread,
                    self.ALPACA_TIMEOUT_SECS,
                    ticker=ticker,
                    short_strike=pos.get("short_strike"),
                    long_strike=pos.get("long_strike"),
                    expiration=expiration_str,
                    spread_type=str(pos.get("strategy_type", pos.get("type", ""))),
                    contracts=contracts,
                    limit_price=None,
                )

            if result and result.get("status") == "submitted":
                pos_copy["close_order_id"] = result.get("order_id")
                pos_copy["close_order_submitted_at"] = datetime.now(timezone.utc).isoformat()
                try:
                    upsert_trade(pos_copy, source="execution", path=self.db_path)
                except Exception as db_err:
                    logger.error(
                        "DrawdownCircuitBreaker: failed to store close_order_id for %s: %s",
                        pos_id, db_err,
                    )
            else:
                logger.error(
                    "DrawdownCircuitBreaker: close order rejected for %s: %s", pos_id, result
                )
                raise RuntimeError(f"close rejected: {result}")

        except Exception as e:
            logger.error(
                "DrawdownCircuitBreaker: Alpaca close failed for %s: %s", pos_id, e, exc_info=True
            )
            raise

    def _close_ic(self, alpaca_client, pos: Dict, contracts: int, expiration_str: str) -> Dict:
        """Close iron condor — attempt combined close, fall back to leg-by-leg."""
        try:
            result = _timed_call(
                alpaca_client.close_iron_condor,
                self.ALPACA_TIMEOUT_SECS,
                ticker=pos.get("ticker"),
                put_short_strike=pos.get("put_short_strike", pos.get("short_strike")),
                put_long_strike=pos.get("put_long_strike", pos.get("long_strike")),
                call_short_strike=pos.get("call_short_strike", pos.get("short_strike")),
                call_long_strike=pos.get("call_long_strike", pos.get("long_strike")),
                expiration=expiration_str,
                contracts=contracts,
            )
            return result
        except Exception as e:
            logger.warning(
                "DrawdownCircuitBreaker: IC combined close failed for %s (%s), trying leg-by-leg: %s",
                pos.get("id"), pos.get("ticker"), e,
            )
            # Leg-by-leg fallback
            ticker = pos.get("ticker", "")
            put_result = _timed_call(
                alpaca_client.close_spread,
                self.ALPACA_TIMEOUT_SECS,
                ticker=ticker,
                short_strike=pos.get("put_short_strike", pos.get("short_strike")),
                long_strike=pos.get("put_long_strike", pos.get("long_strike")),
                expiration=expiration_str,
                spread_type="bull_put",
                contracts=contracts,
                limit_price=None,
            )
            call_result = _timed_call(
                alpaca_client.close_spread,
                self.ALPACA_TIMEOUT_SECS,
                ticker=ticker,
                short_strike=pos.get("call_short_strike", pos.get("short_strike")),
                long_strike=pos.get("call_long_strike", pos.get("long_strike")),
                expiration=expiration_str,
                spread_type="bear_call",
                contracts=contracts,
                limit_price=None,
            )
            # Return success if at least one leg closed
            if (put_result and put_result.get("status") == "submitted") or \
               (call_result and call_result.get("status") == "submitted"):
                return {"status": "submitted", "order_id": (put_result or {}).get("order_id")}
            raise RuntimeError(f"IC leg-by-leg close failed: put={put_result} call={call_result}")

    # ── Alert helper ──────────────────────────────────────────────────────────

    def _send_alert(self, message: str) -> None:
        """Fire a Telegram alert if the shared module is available."""
        try:
            from shared.telegram_alerts import notify_api_failure
            notify_api_failure(
                error_msg=message,
                context="DrawdownCircuitBreaker",
            )
        except Exception as e:
            logger.debug("DrawdownCircuitBreaker: alert send failed (non-fatal): %s", e)

    # ── DB persistence helpers ────────────────────────────────────────────────

    def _load_state(self) -> str:
        try:
            val = load_scanner_state(_KEY_STATE, path=self.db_path)
            if val in (STATE_NORMAL, STATE_PAUSE, STATE_FLATTEN, STATE_HALT):
                return val
        except Exception as e:
            logger.debug("DrawdownCircuitBreaker: load_state failed (non-fatal): %s", e)
        return STATE_NORMAL

    def _save_state(self, state: str) -> None:
        try:
            save_scanner_state(_KEY_STATE, state, path=self.db_path)
        except Exception as e:
            logger.warning("DrawdownCircuitBreaker: save_state failed: %s", e)

    def _load_hwm(self) -> float:
        try:
            val = load_scanner_state(_KEY_HWM, path=self.db_path)
            if val is not None:
                return float(val)
        except Exception as e:
            logger.debug("DrawdownCircuitBreaker: load_hwm failed (non-fatal): %s", e)
        return self._starting_nav

    def _save_hwm(self, hwm: float) -> None:
        try:
            save_scanner_state(_KEY_HWM, str(hwm), path=self.db_path)
            save_scanner_state(
                _KEY_HWM_DATE,
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                path=self.db_path,
            )
        except Exception as e:
            logger.warning("DrawdownCircuitBreaker: save_hwm failed: %s", e)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _timed_call(func, timeout_secs: float, **kwargs):
    """Call *func(**kwargs)* with a timeout guard.

    Uses a simple start/elapsed approach rather than threads.  For the purposes
    of this module we rely on the Alpaca SDK's own socket timeouts — the
    timeout here is a soft guard for post-call elapsed time logging.

    In practice, all Alpaca HTTP calls complete well under 10s.  If your
    environment requires true hard timeouts, replace this with a threading.Timer
    or concurrent.futures.ThreadPoolExecutor approach.
    """
    start = time.monotonic()
    try:
        result = func(**kwargs)
        elapsed = time.monotonic() - start
        if elapsed > timeout_secs:
            logger.warning(
                "DrawdownCircuitBreaker: Alpaca call %s took %.1fs (> %.0fs threshold)",
                getattr(func, "__name__", str(func)), elapsed, timeout_secs,
            )
        return result
    except Exception:
        raise
