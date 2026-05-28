"""
PositionMonitor — background daemon for automated position management.

Runs every 5 minutes during market hours (Mon–Fri 9:30–16:00 ET) and:
  1. Reconciles pending_close positions (polls Alpaca for fill status, records P&L)
  2. Detects externally-closed positions (disappeared from Alpaca) → marks closed_external
  3. Checks open positions for exit conditions:
       a. DTE management: close when DTE <= manage_dte (default 0 = disabled; matches backtester)
       b. Profit target:  close when P&L >= profit_target_pct% of credit (default 50%)
       c. Stop loss:      close when spread value >= (1 + stop_loss_mult) × credit (default 3.5x)

Supports 2-leg credit spreads, 4-leg iron condors, and 2-leg straddles/strangles
(both long/debit and short/credit).

P&L reconciliation (Bug 2 fix):
  After submitting a close order, the order_id is stored in the trade record.
  On each subsequent cycle, Alpaca is polled for fill status. On fill:
    pnl = (credit_received - fill_debit) * contracts * 100
  DB is updated with final status, pnl, exit_date.

Thread safety: threading.Event for clean stop signal. All DB writes via
upsert_trade / close_trade (per-call connections, SQLite WAL mode).
"""

import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:                        # pragma: no cover — Python < 3.9
    from backports.zoneinfo import ZoneInfo  # type: ignore

from shared.database import (
    close_trade,
    get_open_leg_symbols,
    get_trades,
    init_db,
    upsert_equity_point,
    upsert_trade,
)
from shared.strategy_adapter import trade_dict_to_position
from shared.telegram_alerts import notify_api_failure as _notify_api_failure_raw
from strategies.base import MarketSnapshot, PositionAction

logger = logging.getLogger(__name__)


def notify_api_failure(**kwargs):
    """Wrap notify_api_failure so a Telegram error never kills the scan loop."""
    try:
        _notify_api_failure_raw(**kwargs)
    except Exception as _alert_err:
        logger.warning("notify_api_failure itself failed: %s", _alert_err)

# Tier 1: how often to run pending resolution + activity check (seconds)
_CHECK_INTERVAL_SECONDS = 60

# Tier 2: minimum interval between full position-check cycles (seconds)
_TIER2_INTERVAL_SECONDS = 300  # 5 minutes

# Tier 3 EOD reconciliation window: 16:15–16:30 ET (runs once per day)
_EOD_RECONCILE_HOUR_ET = 16
_EOD_RECONCILE_MIN_ET = 15
_EOD_RECONCILE_END_MIN_ET = 30  # stop trying after 16:30 (shouldn't matter)

# Tier 3b morning reconciliation window: 9:35–9:45 ET (runs once per day)
_MORNING_RECONCILE_HOUR_ET = 9
_MORNING_RECONCILE_MIN_ET = 35
_MORNING_RECONCILE_END_HOUR_ET = 10  # stop trying after 10:00

# Eastern time zone for market hours gate
_ET = ZoneInfo("America/New_York")
_MARKET_OPEN_HOUR, _MARKET_OPEN_MIN = 9, 30
_MARKET_CLOSE_HOUR, _MARKET_CLOSE_MIN = 16, 0
_MARKET_DAYS = frozenset({0, 1, 2, 3, 4})  # Mon–Fri (weekday() values)

# Alpaca order statuses where the close order is terminal but did NOT fill
_TERMINAL_NO_FILL = frozenset({"cancelled", "canceled", "expired", "replaced"})

# US market full holidays 2026-2030 — system skips these entirely (BUG #24 fix)
# Source: NYSE market holiday calendar
_MARKET_HOLIDAYS = frozenset({
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed; Jul 4 is Saturday)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving Day
    "2026-12-25",  # Christmas Day (Friday)
    # 2027
    "2027-01-01",  # New Year's Day
    "2027-01-18",  # Martin Luther King Jr. Day
    "2027-02-15",  # Presidents Day
    "2027-03-26",  # Good Friday
    "2027-05-31",  # Memorial Day
    "2027-06-18",  # Juneteenth (observed; Jun 19 is Saturday)
    "2027-07-05",  # Independence Day (observed; Jul 4 is Sunday)
    "2027-09-06",  # Labor Day
    "2027-11-25",  # Thanksgiving Day
    "2027-12-24",  # Christmas Day (observed; Dec 25 is Saturday)
    # 2028
    "2028-01-17",  # Martin Luther King Jr. Day (Jan 1 falls on Saturday, observed Dec 31 2027)
    "2028-02-21",  # Presidents Day
    "2028-04-14",  # Good Friday
    "2028-05-29",  # Memorial Day
    "2028-06-19",  # Juneteenth
    "2028-07-04",  # Independence Day
    "2028-09-04",  # Labor Day
    "2028-11-23",  # Thanksgiving Day
    "2028-12-25",  # Christmas Day
    # 2029
    "2029-01-01",  # New Year's Day
    "2029-01-15",  # Martin Luther King Jr. Day
    "2029-02-19",  # Presidents Day
    "2029-03-30",  # Good Friday
    "2029-05-28",  # Memorial Day
    "2029-06-19",  # Juneteenth
    "2029-07-04",  # Independence Day
    "2029-09-03",  # Labor Day
    "2029-11-22",  # Thanksgiving Day
    "2029-12-25",  # Christmas Day
    # 2030
    "2030-01-01",  # New Year's Day
    "2030-01-21",  # Martin Luther King Jr. Day
    "2030-02-18",  # Presidents Day
    "2030-04-19",  # Good Friday
    "2030-05-27",  # Memorial Day
    "2030-06-19",  # Juneteenth
    "2030-07-04",  # Independence Day
    "2030-09-02",  # Labor Day
    "2030-11-28",  # Thanksgiving Day
    "2030-12-25",  # Christmas Day
})
# Backward-compat alias used in _is_market_hours checks below
_MARKET_HOLIDAYS_2026 = _MARKET_HOLIDAYS

# Early close days 2026-2030 — market closes at 1:00 PM ET instead of 4:00 PM ET
# Format: "YYYY-MM-DD" → close hour (24h, ET)
_EARLY_CLOSE_DATES: Dict[str, int] = {
    # 2026
    "2026-11-25": 13,  # Day before Thanksgiving
    "2026-12-24": 13,  # Christmas Eve
    # 2027
    "2027-11-24": 13,  # Day before Thanksgiving
    "2027-12-23": 13,  # Christmas Eve (observed)
    # 2028
    "2028-11-22": 13,  # Day before Thanksgiving
    "2028-12-22": 13,  # Day before Christmas Eve
    # 2029
    "2029-11-21": 13,  # Day before Thanksgiving
    "2029-12-24": 13,  # Christmas Eve
    # 2030
    "2030-11-27": 13,  # Day before Thanksgiving
    "2030-12-24": 13,  # Christmas Eve
}
# Backward-compat alias
_EARLY_CLOSE_DATES_2026 = _EARLY_CLOSE_DATES

# Warn when a pending_close order has been unfilled for this many minutes
_STALE_CLOSE_MINUTES = 10
# Maximum cancel-and-resubmit retries for stale close orders before alerting
_STALE_CLOSE_MAX_RETRIES = 3

# Number of consecutive missing-leg cycles before a position is marked closed_external.
# Grace period prevents false positives from transient Alpaca API blips.
_EXTERNAL_CLOSE_GRACE_CYCLES = 2

# OCC option symbol pattern: TICKER(1-5 chars) + YYMMDD + C/P + 8-digit strike×1000
_OCC_SYMBOL_RE = re.compile(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$')


def _parse_occ_symbol(symbol: str) -> Optional[Tuple[str, str, str, float]]:
    """Parse an OCC option symbol into (ticker, expiration_YYYY-MM-DD, opt_type, strike).

    Returns None if the symbol does not match the OCC format.
    """
    m = _OCC_SYMBOL_RE.match(symbol.upper().replace(" ", ""))
    if not m:
        return None
    ticker = m.group(1)
    yymmdd = m.group(2)
    cp = m.group(3)
    strike_raw = int(m.group(4))
    try:
        year = 2000 + int(yymmdd[:2])
        month = int(yymmdd[2:4])
        day = int(yymmdd[4:6])
        exp = f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return None
    opt_type = "call" if cp == "C" else "put"
    strike = strike_raw / 1000.0
    return ticker, exp, opt_type, strike


class PositionMonitor:
    """Background daemon that manages open credit spreads, iron condors, and straddles/strangles.

    Usage::

        monitor = PositionMonitor(alpaca_provider=provider, config=config)
        thread = threading.Thread(target=monitor.start, daemon=True)
        thread.start()
        # ...
        monitor.stop()
    """

    def __init__(self, alpaca_provider, config: Dict, db_path: Optional[str] = None):
        """
        Args:
            alpaca_provider: AlpacaProvider instance.
            config: Full application config dict. Reads risk.profit_target,
                    risk.stop_loss_multiplier, strategy.manage_dte.
            db_path: Optional SQLite path override.
        """
        self.alpaca = alpaca_provider
        self.config = config
        self.db_path = db_path
        self._stop_event = threading.Event()

        risk = config.get("risk", {})
        strategy = config.get("strategy", {})
        self.profit_target_pct = float(risk.get("profit_target", 50))
        self.stop_loss_mult = float(risk.get("stop_loss_multiplier", 3.5))
        self.manage_dte = int(strategy.get("manage_dte", 0))  # 0 = disabled (matches backtester: no DTE exit)
        # Tracks consecutive Alpaca API failures for escalation alerting
        self._consecutive_api_failures = 0

        # Three-tier reconciliation scheduling
        # _last_tier2_run: wall-clock time of the last Tier 2 (full position check) run
        self._last_tier2_run: Optional[datetime] = None
        # _last_eod_date / _last_morning_date: YYYY-MM-DD of last Tier 3 / Tier 3b run
        self._last_eod_date: Optional[str] = None
        self._last_morning_date: Optional[str] = None

        # When True, orphan long positions are automatically sold-to-close at current price.
        # Set to False to disable auto-close and only log/alert.
        self.auto_close_orphan_longs: bool = bool(
            config.get("risk", {}).get("auto_close_orphan_longs", True)
        )

        # Strategy registry — maps strategy_name → strategy instance for manage_position()
        self._strategy_registry: Dict[str, object] = {}
        self._exit_snapshot_cache = None
        self._exit_snapshot_ts = None

        init_db(db_path)

    def start(self):
        """Start the monitoring loop. Blocks until stop() is called."""
        logger.info(
            "PositionMonitor started | profit_target=%.0f%% | SL=%.1fx | manage_dte=%s",
            self.profit_target_pct, self.stop_loss_mult,
            self.manage_dte if self.manage_dte > 0 else "disabled",
        )
        self._startup_reconciliation()
        while not self._stop_event.is_set():
            try:
                self._check_positions()
            except Exception as e:
                logger.error("PositionMonitor: unhandled error in check cycle: %s", e, exc_info=True)
            self._stop_event.wait(timeout=_CHECK_INTERVAL_SECONDS)

        logger.info("PositionMonitor stopped")

    def _startup_reconciliation(self) -> None:
        """Log any DB↔Alpaca mismatches on startup so operators can investigate.

        Checks:
        - DB open trades with no matching Alpaca legs (phantom or externally closed)
        - Alpaca option positions with no DB record (orphan)
        """
        if not self.alpaca:
            return
        try:
            all_alpaca = self.alpaca.get_positions()
            alpaca_positions = {p["symbol"]: p for p in all_alpaca}
        except Exception as e:
            logger.warning("PositionMonitor: startup reconciliation skipped (API error: %s)", e)
            return

        open_trades = get_trades(status="open", source="execution", path=self.db_path)

        # Build the full set of OCC symbols managed by open trades
        managed_symbols: set = set()
        for pos in open_trades:
            ticker = pos.get("ticker", "")
            exp = str(pos.get("expiration", "")).split(" ")[0]
            spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
            if not ticker or not exp:
                continue
            try:
                opt_type = "call" if "call" in spread_type else "put"
                for strike in [pos.get("short_strike"), pos.get("long_strike")]:
                    if strike:
                        managed_symbols.add(
                            self.alpaca._build_occ_symbol(ticker, exp, strike, opt_type)
                        )
            except Exception:
                pass

        # DB-open trades with no Alpaca legs
        for pos in open_trades:
            if self._all_legs_missing(pos, alpaca_positions):
                logger.warning(
                    "PositionMonitor: STARTUP — DB-open trade %s has no legs in Alpaca. "
                    "Possible external close or assignment. Review required.",
                    pos.get("id"),
                )

        # Alpaca option positions with no DB record
        for symbol, pos_data in alpaca_positions.items():
            if "option" not in str(pos_data.get("asset_class", "")).lower():
                continue
            if symbol not in managed_symbols:
                logger.warning(
                    "PositionMonitor: STARTUP — Alpaca option %s has no open DB record. "
                    "Orphan detection will handle this on next cycle.",
                    symbol,
                )

    def stop(self):
        """Signal the monitor to stop after the current check completes."""
        self._stop_event.set()

    def register_strategies(self, strategies: list) -> None:
        """Register strategy instances for manage_position() dispatch.

        Args:
            strategies: List of BaseStrategy instances from build_strategy_list().
        """
        for strat in strategies:
            name = strat.__class__.__name__
            self._strategy_registry[name] = strat
            logger.info("PositionMonitor: registered strategy %s", name)

    def _build_exit_snapshot(self, ticker: str, current_price: float) -> MarketSnapshot:
        """Build a minimal MarketSnapshot for strategy.manage_position().

        Caches for 60s per scan cycle to avoid redundant construction.
        """
        now = datetime.now(timezone.utc)

        # Cache check (60s TTL)
        if (
            self._exit_snapshot_cache is not None
            and self._exit_snapshot_ts is not None
            and (now - self._exit_snapshot_ts).total_seconds() < 60
            and ticker in self._exit_snapshot_cache.prices
        ):
            return self._exit_snapshot_cache

        snapshot = MarketSnapshot(
            date=now,
            price_data={},
            prices={ticker: current_price},
            vix=20.0,
            iv_rank={ticker: 25.0},
            realized_vol={ticker: 0.25},
            rsi={ticker: 50.0},
        )
        self._exit_snapshot_cache = snapshot
        self._exit_snapshot_ts = now
        return snapshot

    _ACTION_TO_REASON = {
        PositionAction.CLOSE_PROFIT: "profit_target",
        PositionAction.CLOSE_STOP: "stop_loss",
        PositionAction.CLOSE_EXPIRY: "expiration_today",
        PositionAction.CLOSE_DTE: "dte_management",
        PositionAction.CLOSE_TIME: "dte_management",
        PositionAction.CLOSE_EVENT: "event_exit",
        PositionAction.CLOSE_SIGNAL: "signal_exit",
    }

    # ------------------------------------------------------------------
    # Market hours gate
    # ------------------------------------------------------------------

    @staticmethod
    def _get_market_close_time(date_str: str):
        """Return (close_hour, close_min) for a given YYYY-MM-DD date string.

        Accounts for early-close days (half sessions) where the market closes
        at 1:00 PM ET instead of 4:00 PM ET.  Covers 2026-2030.
        """
        if date_str in _EARLY_CLOSE_DATES:
            return (_EARLY_CLOSE_DATES[date_str], 0)
        return (_MARKET_CLOSE_HOUR, _MARKET_CLOSE_MIN)

    @staticmethod
    def _is_market_hours() -> bool:
        """Return True if current time is within US market hours (Mon–Fri 9:30–close ET).

        Respects:
        - Weekend (Sat/Sun): always False
        - Full market holidays (_MARKET_HOLIDAYS, 2026-2030): always False
        - Early close days (_EARLY_CLOSE_DATES, 2026-2030): closes at 1:00 PM ET
        """
        now_et = datetime.now(_ET)
        if now_et.weekday() not in _MARKET_DAYS:
            return False
        date_str = now_et.strftime("%Y-%m-%d")
        if date_str in _MARKET_HOLIDAYS:
            return False
        close_hour, close_min = PositionMonitor._get_market_close_time(date_str)
        open_mins = _MARKET_OPEN_HOUR * 60 + _MARKET_OPEN_MIN
        close_mins = close_hour * 60 + close_min
        current_mins = now_et.hour * 60 + now_et.minute
        return open_mins <= current_mins < close_mins

    # ------------------------------------------------------------------
    # Equity curve persistence
    # ------------------------------------------------------------------

    def _record_equity_point(self) -> None:
        """Persist today's equity point to the durable equity_history table.

        One canonical point per (experiment, day); the latest scan of the day
        overwrites it (upsert). Best-effort — any failure is logged and ignored
        so it never disrupts position monitoring.
        """
        if not self.alpaca:
            return
        exp_id = os.environ.get("EXPERIMENT_ID", "-")
        try:
            account = self.alpaca.get_account()
            equity = float(account.get("equity") or 0)
            if equity <= 0:
                return  # nothing meaningful to record
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            realized = self._realized_pnl_to_date()
            upsert_equity_point(
                exp_id=exp_id,
                as_of_date=today,
                equity=equity,
                realized_pnl=realized,
                source="position_monitor",
                path=self.db_path,
            )
            logger.info(
                "[equity] exp=%s date=%s equity=%.2f source=position_monitor action=wrote",
                exp_id, today, equity,
            )
        except Exception as e:
            logger.warning("[equity] exp=%s action=skipped error=%s", exp_id, e)

    def _realized_pnl_to_date(self) -> Optional[float]:
        """Cumulative realized P&L from closed trades (best-effort)."""
        try:
            trades = get_trades(path=self.db_path)
            return round(sum(
                float(t.get("pnl") or 0)
                for t in trades
                if str(t.get("status", "")).startswith("closed") and t.get("pnl") is not None
            ), 2)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Core check loop
    # ------------------------------------------------------------------

    def _check_positions(self):
        """Main check cycle — dispatches Tier 1 / Tier 2 / Tier 3 reconciliation.

        Tier 1 (every 60s):
          - Pending-open resolution (promotes intra-day fills → open)
          - Activity-based close detection (OPEXP / FILL from Alpaca activities feed)
          - Pending-close fill tracking

        Tier 2 (every 5 min, market hours only):
          - Full position comparison: load open DB trades, fetch Alpaca positions
          - Detect external closes whose legs have disappeared
          - Check exit conditions (profit target, stop loss, DTE)
          - Orphan detection

        Tier 3 EOD (once daily, 4:15 PM ET):
          - reconcile_eod(): expiration processing + full position pass

        Tier 3b morning (once daily, 9:35 AM ET):
          - reconcile_morning(): catch overnight settlement activity
        """
        now_et = datetime.now(_ET)
        date_str = now_et.strftime("%Y-%m-%d")

        # Tier 3 EOD: runs outside normal market hours — check first
        if self._should_run_eod(now_et, date_str):
            self._run_eod_reconciliation(date_str)

        # Tier 3b morning
        if self._should_run_morning(now_et, date_str):
            self._run_morning_reconciliation(date_str)

        # All Tier 1 / Tier 2 work requires market hours
        if not self._is_market_hours():
            logger.debug("PositionMonitor: market closed, skipping Tier1/2 checks")
            return

        # Tier 1: pending resolution + activity check (every 60s cycle)
        # Critical: without this, orders placed during the session are never monitored
        # for stop-loss or profit-target until the next process restart.
        if self.alpaca:
            self._reconcile_pending_opens()

        # Tier 1: Reconcile pending_close positions (check for fills since last cycle)
        if self.alpaca:
            self._reconcile_pending_closes()

        # Tier 2 gate: only run the expensive position check every 5 min
        if not self._should_run_tier2():
            return
        self._last_tier2_run = datetime.now(timezone.utc)
        logger.debug("PositionMonitor: running Tier2 position check")

        # Step 2: Load open positions (also include pending_open so orphan detection
        # doesn't flag positions whose orders are still in flight)
        open_positions = get_trades(status="open", source="execution", path=self.db_path)
        pending_positions = get_trades(status="pending_open", source="execution", path=self.db_path)
        if open_positions:
            logger.info("PositionMonitor: checking %d open position(s)", len(open_positions))

        # Step 3: Fetch all Alpaca positions once per cycle (reduces API calls).
        # Do this even when open_positions is empty so orphan detection always runs.
        try:
            all_alpaca_positions = self.alpaca.get_positions()
            alpaca_positions = {p["symbol"]: p for p in all_alpaca_positions}
            # Reset failure counter on success
            if self._consecutive_api_failures > 0:
                logger.info(
                    "PositionMonitor: Alpaca API recovered after %d failed cycle(s)",
                    self._consecutive_api_failures,
                )
            self._consecutive_api_failures = 0
        except Exception as e:
            self._consecutive_api_failures += 1
            logger.error(
                "PositionMonitor: failed to fetch Alpaca positions (consecutive_failures=%d): %s",
                self._consecutive_api_failures, e,
            )
            try:
                _open = get_trades(status="open", source="execution", path=self.db_path)
                _unmonitored = len(_open) if _open else 0
            except Exception:
                _unmonitored = -1
            notify_api_failure(
                error_msg=str(e),
                context="get_positions",
                unmonitored_positions=max(0, _unmonitored),
            )
            if self._consecutive_api_failures >= 3:
                logger.critical(
                    "PositionMonitor: Alpaca API unreachable for %d consecutive cycles. "
                    "Positions are unmonitored. Manual intervention may be required.",
                    self._consecutive_api_failures,
                )
            return

        # Step 3a: Persist today's equity point (durable equity curve for the
        # dashboard chart). Additive + best-effort: failures never disrupt the
        # monitoring cycle.
        self._record_equity_point()

        # Step 3b: Detect unexpected equity positions (possible early assignment)
        if open_positions:
            self._detect_assignment(open_positions, alpaca_positions)

        # Step 3c: Detect option positions in Alpaca with no DB record (orphans).
        # Runs unconditionally — orphans can appear even when we have no open trades.
        self._detect_orphans(open_positions + pending_positions, alpaca_positions)

        if not open_positions:
            logger.debug("PositionMonitor: no open positions to check")
            return

        # Step 4: Detect positions that disappeared from Alpaca (external closes)
        self._reconcile_external_closes(open_positions, alpaca_positions)

        # Step 5: Check exit conditions for remaining open positions
        for pos in open_positions:
            if pos.get("status") != "open":
                continue  # already handled by _reconcile_external_closes
            try:
                exit_reason = self._check_exit_conditions(pos, alpaca_positions)
                if exit_reason:
                    self._close_position(pos, exit_reason)
            except Exception as e:
                logger.error(
                    "PositionMonitor: error checking position %s: %s", pos.get("id"), e
                )

    # ------------------------------------------------------------------
    # Exit condition checks
    # ------------------------------------------------------------------

    def _check_exit_conditions(self, pos: Dict, alpaca_positions: Dict) -> Optional[str]:
        """Return an exit reason if the position should be closed, else None."""

        # 1. DTE-based exit — check first, no pricing needed
        expiration_str = str(pos.get("expiration", ""))
        if expiration_str:
            try:
                exp_date = datetime.fromisoformat(expiration_str.split(" ")[0])
                if exp_date.tzinfo is None:
                    exp_date = exp_date.replace(tzinfo=timezone.utc)
                dte = (exp_date - datetime.now(timezone.utc)).days
                if dte <= 0:
                    # Expiring today — close immediately to avoid pin risk and assignment.
                    # NOTE (E7): This intentionally differs from the backtester, which holds
                    # through the full expiration day and settles at the closing price.
                    # Live trading exits at market open on expiration day to avoid pin risk.
                    # For spreads expiring worthless the P&L impact is negligible. For spreads
                    # near the short strike, the live system exits earlier (at open) vs the
                    # backtester which sees the full day's intraday moves before settlement.
                    logger.warning(
                        "PositionMonitor: %s expires TODAY (DTE=%d) — urgent close "
                        "(pin risk / assignment avoidance)",
                        pos.get("id"), dte,
                    )
                    return "expiration_today"
                if self.manage_dte > 0 and dte <= self.manage_dte:
                    logger.info(
                        "PositionMonitor: %s DTE=%d <= %d → closing (dte_management)",
                        pos.get("id"), dte, self.manage_dte,
                    )
                    return "dte_management"
            except (ValueError, TypeError) as e:
                logger.warning(
                    "PositionMonitor: cannot parse expiration '%s': %s", expiration_str, e
                )

        # RC4: Guard against synthetic single-leg records (no hedge) before pricing.
        # When both strikes are None but credit is present, fall through to
        # formula-only SL/PT (no spread_width cap).  Only skip when there's
        # truly no data to work with.
        _spread_type_guard = str(pos.get("strategy_type", pos.get("type", ""))).lower()
        _has_credit = pos.get("credit") is not None and float(pos.get("credit", 0)) > 0
        if (
            pos.get("long_strike") is None
            and pos.get("short_strike") is None
            and not _has_credit
            and "straddle" not in _spread_type_guard
            and "strangle" not in _spread_type_guard
        ):
            logger.error(
                "PositionMonitor: %s has no strikes and no credit — skipping SL/PT "
                "(likely a synthetic orphan record). Manual review required.",
                pos.get("id"),
            )
            return None

        # 1b. Strategy dispatch — delegate to strategy.manage_position()
        strategy_name = pos.get("strategy_name", "")
        strategy = self._strategy_registry.get(strategy_name) if strategy_name else None
        if strategy is not None:
            try:
                position = trade_dict_to_position(pos)
                # Get current price for snapshot (from Alpaca mid-price)
                current_price = float(pos.get("current_price", 0))
                if current_price <= 0:
                    try:
                        ticker = pos.get("ticker", "")
                        quote = self.alpaca.get_quote(ticker) if hasattr(self.alpaca, 'get_quote') else None
                        if quote:
                            current_price = float(quote.get("mid", quote.get("last", 0)))
                    except Exception:
                        pass
                if current_price > 0:
                    snapshot = self._build_exit_snapshot(pos.get("ticker", ""), current_price)
                    action = strategy.manage_position(position, snapshot)
                    if action != PositionAction.HOLD:
                        reason = self._ACTION_TO_REASON.get(action, "signal_exit")
                        logger.info(
                            "PositionMonitor: %s strategy %s → %s → closing (%s)",
                            pos.get("id"), strategy_name, action.value, reason,
                        )
                        return reason
            except Exception as e:
                logger.warning(
                    "PositionMonitor: strategy dispatch failed for %s: %s",
                    pos.get("id"), e,
                )

        # 2. Current spread value from Alpaca market data
        current_value = self._get_spread_value(pos, alpaca_positions)
        if current_value is None:
            return None  # Cannot price — skip this cycle

        credit = float(pos.get("credit") or 0)
        spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
        is_debit = pos.get("is_debit", False) or credit < 0

        if is_debit:
            # --- Debit (long) position P&L ---
            # pnl = current_value - debit_paid; pnl_pct relative to debit cost
            debit_paid = abs(credit)
            if debit_paid <= 0:
                return None
            pnl = current_value - debit_paid
            pnl_pct = (pnl / debit_paid) * 100

            # Use per-trade targets (set by strategy adapter from Signal)
            pt_pct = float(pos.get("profit_target_pct", self.profit_target_pct))
            sl_pct = float(pos.get("stop_loss_pct", 50.0))

            # Profit target
            if pnl_pct >= pt_pct:
                logger.info(
                    "PositionMonitor: %s debit profit target hit: %.1f%% >= %.0f%% → closing",
                    pos.get("id"), pnl_pct, pt_pct,
                )
                return "profit_target"

            # Stop loss: value dropped below debit by stop %
            loss_pct = (-pnl / debit_paid) * 100
            if loss_pct >= sl_pct:
                logger.warning(
                    "PositionMonitor: %s debit stop loss hit: loss=%.1f%% >= %.0f%% → closing",
                    pos.get("id"), loss_pct, sl_pct,
                )
                return "stop_loss"

            logger.debug(
                "PositionMonitor: %s OK (debit) | val=%.4f debit=%.4f pnl=%.1f%%",
                pos.get("id"), current_value, debit_paid, pnl_pct,
            )
            return None

        # --- Credit position P&L (existing logic) ---
        if credit <= 0:
            logger.warning(
                "PositionMonitor: %s has zero credit — skipping PT/SL checks", pos.get("id")
            )
            return None

        # P&L = credit received at open – cost to close now
        pnl = credit - current_value
        pnl_pct = (pnl / credit) * 100

        # 3. Profit target — per-trade value with global fallback
        #    Per-trade profit_target_pct from Signal is a fraction (e.g. 0.50 = 50%);
        #    global self.profit_target_pct is already in percentage form (e.g. 50).
        pt_raw = pos.get("profit_target_pct")
        if pt_raw is not None:
            pt_val = float(pt_raw)
            # Convert fraction → percentage if needed (Signal stores 0.50, config stores 50)
            pt_pct = pt_val * 100 if pt_val < 1.0 else pt_val
        else:
            pt_pct = self.profit_target_pct

        if pnl_pct >= pt_pct:
            logger.info(
                "PositionMonitor: %s profit target hit: %.1f%% >= %.0f%% → closing",
                pos.get("id"), pnl_pct, pt_pct,
            )
            return "profit_target"

        # 4. Stop loss — per-trade value with global fallback
        #    Per-trade stop_loss_pct from Signal is a multiplier (e.g. 2.5 = 2.5x credit
        #    for CS, 0.50 = 50% for SS); same convention as self.stop_loss_mult.
        #
        #    Two complementary checks:
        #   (a) Loss-based (matches backtester semantics):
        #       Fires when LOSS (current_value - credit) >= stop_loss_mult × credit
        #       i.e., current_value >= (1 + mult) × credit
        #
        #   (b) Spread-width cap (safety backstop):
        #       Fires at 90% of the spread width regardless of credit amount.
        #       Skipped for straddles/strangles (no defined spread width).
        #
        #   The effective SL threshold is the LOWER of (a) and (b).
        sl_mult = float(pos.get("stop_loss_pct", self.stop_loss_mult))
        sl_threshold = (1.0 + sl_mult) * credit

        # Sanity: sl_threshold must not exceed the spread's max possible value per contract.
        # Fires when credit is in wrong units (e.g., per-contract instead of per-share).
        _sw = abs(float(pos.get("short_strike") or 0) - float(pos.get("long_strike") or 0))
        if _sw > 0 and sl_threshold > _sw * 100:
            logger.warning(
                "PositionMonitor: %s sl_threshold=%.2f exceeds spread_width*100=%.2f "
                "(credit=%.4f × (1+%.1f), width=%.0f) — verify credit field units",
                pos.get("id"), sl_threshold, _sw * 100, credit, sl_mult, _sw,
            )

        if current_value >= sl_threshold:
            logger.warning(
                "PositionMonitor: %s stop loss hit: current=%.4f >= threshold=%.4f "
                "(credit=%.4f × (1 + %.1f)) → closing",
                pos.get("id"), current_value, sl_threshold,
                credit, sl_mult,
            )
            return "stop_loss"

        logger.debug(
            "PositionMonitor: %s OK | val=%.4f credit=%.4f pnl=%.1f%%",
            pos.get("id"), current_value, credit, pnl_pct,
        )
        return None

    # ------------------------------------------------------------------
    # Spread valuation — Bug 1 fix: IC support
    # ------------------------------------------------------------------

    def _get_spread_value(self, pos: Dict, alpaca_positions: Dict) -> Optional[float]:
        """Current cost-to-close per share. Routes to IC, straddle/strangle, or 2-leg path."""
        spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
        if "straddle" in spread_type or "strangle" in spread_type:
            return self._get_straddle_value(pos, alpaca_positions)
        if "condor" in spread_type:
            return self._get_ic_value(pos, alpaca_positions)
        opt_type = "call" if "call" in spread_type else "put"
        return self._get_2leg_value(
            pos, alpaca_positions,
            short_strike=pos.get("short_strike"),
            long_strike=pos.get("long_strike"),
            opt_type=opt_type,
        )

    def _get_2leg_value(
        self,
        pos: Dict,
        alpaca_positions: Dict,
        short_strike,
        long_strike,
        opt_type: str,
    ) -> Optional[float]:
        """Cost-to-close per share for a single 2-leg wing.

        Returns None if either leg is missing (position may be externally closed,
        or this is just a pricing gap — caller decides).
        """
        ticker = pos.get("ticker", "")
        expiration_str = str(pos.get("expiration", "")).split(" ")[0]
        contracts = int(pos.get("contracts", 1))

        if not all([ticker, expiration_str, short_strike]):
            logger.warning("PositionMonitor: %s missing fields for pricing", pos.get("id"))
            return None

        try:
            short_sym = self.alpaca._build_occ_symbol(ticker, expiration_str, short_strike, opt_type)
            long_sym = (
                self.alpaca._build_occ_symbol(ticker, expiration_str, long_strike, opt_type)
                if long_strike else None
            )
        except Exception as e:
            logger.warning("PositionMonitor: OCC symbol error for %s: %s", pos.get("id"), e)
            return None

        short_pos = alpaca_positions.get(short_sym)
        if not short_pos:
            return None  # caller distinguishes pricing gap from external close

        long_pos = alpaca_positions.get(long_sym) if long_sym else None
        if long_sym and not long_pos:
            return None  # 2-leg spread: need both legs to price

        try:
            short_mv = float(short_pos["market_value"])  # negative (liability we owe)
            long_mv = float(long_pos["market_value"]) if long_pos else 0.0
            # cost to close = buy back short (pay |short_mv|) – sell long (receive long_mv)
            cost_total = abs(short_mv) - long_mv
            cost_per_share = cost_total / (contracts * 100) if contracts > 0 else 0.0
            return max(0.0, cost_per_share)
        except (ValueError, TypeError, ZeroDivisionError) as e:
            logger.warning(
                "PositionMonitor: market value error for %s: %s", pos.get("id"), e
            )
            return None

    def _get_ic_value(self, pos: Dict, alpaca_positions: Dict) -> Optional[float]:
        """Cost-to-close per share for a 4-leg iron condor (sum of both wings)."""
        put_short = pos.get("put_short_strike") or pos.get("short_strike")
        put_long = pos.get("put_long_strike") or pos.get("long_strike")
        call_short = pos.get("call_short_strike")
        call_long = pos.get("call_long_strike")

        if not all([put_short, put_long, call_short, call_long]):
            logger.warning(
                "PositionMonitor: IC %s missing wing strikes — cannot price", pos.get("id")
            )
            return None

        put_val = self._get_2leg_value(pos, alpaca_positions, put_short, put_long, "put")
        call_val = self._get_2leg_value(pos, alpaca_positions, call_short, call_long, "call")

        if put_val is None or call_val is None:
            return None

        return put_val + call_val

    def _get_straddle_value(self, pos: Dict, alpaca_positions: Dict) -> Optional[float]:
        """Current value per share for a straddle/strangle position.

        For long: value = combined market value of both legs (what we'd get selling).
        For short: value = cost to buy back both legs.
        Returns per-share value (divided by contracts * 100).
        """
        ticker = pos.get("ticker", "")
        expiration_str = str(pos.get("expiration", "")).split(" ")[0]
        contracts = int(pos.get("contracts", 1))
        call_strike = pos.get("call_strike")
        put_strike = pos.get("put_strike")

        if not all([ticker, expiration_str, call_strike, put_strike]):
            logger.warning("PositionMonitor: %s missing straddle fields for pricing", pos.get("id"))
            return None

        try:
            call_sym = self.alpaca._build_occ_symbol(ticker, expiration_str, call_strike, "call")
            put_sym = self.alpaca._build_occ_symbol(ticker, expiration_str, put_strike, "put")
        except Exception as e:
            logger.warning("PositionMonitor: OCC symbol error for straddle %s: %s", pos.get("id"), e)
            return None

        call_pos = alpaca_positions.get(call_sym)
        put_pos = alpaca_positions.get(put_sym)

        if not call_pos or not put_pos:
            return None

        try:
            call_mv = float(call_pos["market_value"])
            put_mv = float(put_pos["market_value"])

            spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
            is_long = spread_type.startswith("long_")

            if is_long:
                # Long position: both MVs positive (assets we hold)
                value = (call_mv + put_mv) / (contracts * 100) if contracts > 0 else 0.0
            else:
                # Short position: both MVs negative (liabilities)
                value = (abs(call_mv) + abs(put_mv)) / (contracts * 100) if contracts > 0 else 0.0

            return max(0.0, value)
        except (ValueError, TypeError, ZeroDivisionError) as e:
            logger.warning("PositionMonitor: straddle value error for %s: %s", pos.get("id"), e)
            return None

    # ------------------------------------------------------------------
    # External close detection — Bug 3 fix
    # ------------------------------------------------------------------

    def _all_legs_missing(self, pos: Dict, alpaca_positions: Dict) -> bool:
        """Return True when every leg of this position is absent from Alpaca.

        Only returns True when we can fully determine all legs — returns False
        on any data gap to avoid false positives.
        """
        ticker = pos.get("ticker", "")
        expiration_str = str(pos.get("expiration", "")).split(" ")[0]
        spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()

        if not ticker or not expiration_str:
            return False

        try:
            if "straddle" in spread_type or "strangle" in spread_type:
                call_strike = pos.get("call_strike")
                put_strike = pos.get("put_strike")
                if not all([call_strike, put_strike]):
                    return False
                syms = [
                    self.alpaca._build_occ_symbol(ticker, expiration_str, call_strike, "call"),
                    self.alpaca._build_occ_symbol(ticker, expiration_str, put_strike, "put"),
                ]
            elif "condor" in spread_type:
                put_short = pos.get("put_short_strike") or pos.get("short_strike")
                put_long = pos.get("put_long_strike") or pos.get("long_strike")
                call_short = pos.get("call_short_strike")
                call_long = pos.get("call_long_strike")
                if not all([put_short, put_long, call_short, call_long]):
                    return False
                syms = [
                    self.alpaca._build_occ_symbol(ticker, expiration_str, put_short, "put"),
                    self.alpaca._build_occ_symbol(ticker, expiration_str, put_long, "put"),
                    self.alpaca._build_occ_symbol(ticker, expiration_str, call_short, "call"),
                    self.alpaca._build_occ_symbol(ticker, expiration_str, call_long, "call"),
                ]
            else:
                short_strike = pos.get("short_strike")
                long_strike = pos.get("long_strike")
                if not short_strike:
                    return False
                opt_type = "call" if "call" in spread_type else "put"
                syms = [
                    self.alpaca._build_occ_symbol(ticker, expiration_str, short_strike, opt_type),
                ]
                if long_strike:
                    syms.append(
                        self.alpaca._build_occ_symbol(ticker, expiration_str, long_strike, opt_type)
                    )
        except Exception:
            return False

        return all(sym not in alpaca_positions for sym in syms)

    def _reconcile_external_closes(
        self, open_positions: List[Dict], alpaca_positions: Dict
    ) -> None:
        """Mark positions whose legs are all gone from Alpaca as closed_external.

        Uses a grace period of _EXTERNAL_CLOSE_GRACE_CYCLES consecutive missing cycles
        before marking closed_external.  This prevents false positives caused by transient
        Alpaca API gaps or a single failed get_positions call.  The missing-cycle counter
        is persisted in the trade's metadata (_missing_legs_count) so it survives restarts.
        """
        for pos in open_positions:
            pos_id = pos.get("id", "?")
            if not self._all_legs_missing(pos, alpaca_positions):
                # Legs present — reset counter if it was elevated
                if pos.get("_missing_legs_count", 0):
                    pos["_missing_legs_count"] = 0
                    try:
                        upsert_trade(pos, source="execution", path=self.db_path)
                    except Exception:
                        pass
                continue

            missing_count = int(pos.get("_missing_legs_count", 0)) + 1
            pos["_missing_legs_count"] = missing_count

            if missing_count < _EXTERNAL_CLOSE_GRACE_CYCLES:
                logger.debug(
                    "PositionMonitor: %s legs not in Alpaca (missing_count=%d/%d) — "
                    "grace period active",
                    pos_id, missing_count, _EXTERNAL_CLOSE_GRACE_CYCLES,
                )
                try:
                    upsert_trade(pos, source="execution", path=self.db_path)
                except Exception as e:
                    logger.error(
                        "PositionMonitor: DB write failed updating missing count for %s: %s",
                        pos_id, e,
                    )
                continue

            logger.warning(
                "PositionMonitor: %s legs not found in Alpaca for %d consecutive cycle(s) — "
                "marking closed_external",
                pos_id, missing_count,
            )
            # Mutate in place so the subsequent exit-check loop skips this position
            pos["status"] = "closed_external"
            pos["exit_date"] = datetime.now(timezone.utc).isoformat()
            pos["exit_reason"] = "closed_external"
            pos.pop("_missing_legs_count", None)

            pnl = self._estimate_external_close_pnl(pos)
            if pnl is not None:
                try:
                    close_trade(pos_id, pnl, "closed_external", path=self.db_path)
                    pos["pnl"] = pnl
                    logger.info(
                        "PositionMonitor: %s closed_external — pnl=%.2f%s",
                        pos_id, pnl,
                        " [estimated]" if pos.get("pnl_estimated") else "",
                    )
                except Exception as e:
                    logger.error(
                        "PositionMonitor: DB close_trade failed for external close %s: %s",
                        pos_id, e,
                    )
            else:
                pos["pnl_needs_review"] = True
                logger.warning(
                    "PositionMonitor: %s closed_external — PnL could not be determined, "
                    "manual review required",
                    pos_id,
                )
                try:
                    upsert_trade(pos, source="execution", path=self.db_path)
                except Exception as e:
                    logger.error(
                        "PositionMonitor: DB write failed for external close %s: %s", pos_id, e
                    )

    def _estimate_external_close_pnl(self, pos: Dict) -> Optional[float]:
        """Estimate PnL for an externally-closed position.

        Strategy A (preferred): Query Alpaca OPEXP/FILL activities for the position's
        underlying symbol after entry_date. If an expiration activity is found the
        spread expired worthless and full credit is kept.

        Strategy B (fallback): If today is past the expiration date we assume the
        spread expired worthless (covers >80 % of external-close cases for credit
        spreads). Sets pos["pnl_estimated"] = True to flag for audit.

        Returns the estimated PnL in dollars, or None if we cannot determine it
        (non-expiration external close without matching Alpaca activity).
        """
        credit = float(pos.get("credit") or 0)
        contracts = int(pos.get("contracts", 1))
        spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
        num_legs = 4 if "condor" in spread_type else 2
        pos_id = pos.get("id", "?")

        commission_per_contract = float(
            self.config.get("execution", {}).get("commission_per_contract", 0.65)
        )
        # Entry-side only (no close order submitted): open legs × contracts
        entry_commission = commission_per_contract * contracts * num_legs

        # --- Strategy A: Alpaca account activities ---
        if self.alpaca:
            try:
                since = pos.get("entry_date")
                ticker = pos.get("ticker", "")
                # OPEXP = option expiration; FILL covers manual/assignment closes
                for act_type in ("OPEXP", "FILL"):
                    activities = self.alpaca.get_account_activities(
                        activity_type=act_type, since=since
                    )
                    for act in activities:
                        sym = str(act.get("symbol", ""))
                        # Match by underlying ticker prefix (OCC symbol starts with ticker)
                        if not sym.upper().startswith(ticker.upper()):
                            continue
                        if act_type == "OPEXP":
                            # Expired worthless — full credit kept (entry commission only)
                            pnl = credit * contracts * 100 - entry_commission
                            logger.info(
                                "PositionMonitor: %s — OPEXP activity matched (%s), "
                                "pnl=%.2f (credit=%.4f × %d × 100 - comm=%.2f)",
                                pos_id, sym, pnl, credit, contracts, entry_commission,
                            )
                            pos["pnl_estimated"] = True
                            return pnl
                        # FILL activity: use net_amount if available
                        net = act.get("net_amount")
                        if net is not None:
                            try:
                                pnl = float(net)
                                logger.info(
                                    "PositionMonitor: %s — FILL activity matched (%s), "
                                    "net_amount=%.2f",
                                    pos_id, sym, pnl,
                                )
                                return pnl
                            except (TypeError, ValueError):
                                pass
            except Exception as e:
                logger.warning(
                    "PositionMonitor: %s — Alpaca activities query failed: %s", pos_id, e
                )

        # --- Strategy B: expiration-date fallback ---
        exp_str = str(pos.get("expiration", "")).split(" ")[0]
        try:
            exp_date = datetime.fromisoformat(exp_str)
            if exp_date.tzinfo is None:
                exp_date = exp_date.replace(tzinfo=timezone.utc)
            expired = datetime.now(timezone.utc) > exp_date
        except (ValueError, TypeError):
            expired = False

        if expired and credit > 0:
            pnl = credit * contracts * 100 - entry_commission
            logger.info(
                "PositionMonitor: %s — expiration fallback (exp=%s), "
                "pnl=%.2f (credit=%.4f × %d × 100 - comm=%.2f) [estimated]",
                pos_id, exp_str, pnl, credit, contracts, entry_commission,
            )
            pos["pnl_estimated"] = True
            return pnl

        # Cannot determine PnL — caller will flag pnl_needs_review
        return None

    # ------------------------------------------------------------------
    # Closing
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_close_limit(pos: Dict) -> float:
        """Compute a limit price (debit) for closing a credit spread.

        Uses the current value if available, otherwise falls back to a
        percentage of the original credit to ensure fills.
        """
        current_value = pos.get("current_value")
        credit = pos.get("credit", 0) or 0

        if current_value is not None and float(current_value) > 0:
            # Add 10% buffer above current value for fill probability
            return round(float(current_value) * 1.10, 2)

        if credit > 0:
            # Profit target close: use 50% of original credit as max debit
            return round(float(credit) * 0.50, 2)

        # Absolute fallback: $0.05 debit
        return 0.05

    def _close_position(self, pos: Dict, reason: str) -> None:
        """Mark pending_close in DB, submit close order, store order_id for fill tracking."""
        ticker = pos.get("ticker", "")
        spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
        contracts = int(pos.get("contracts", 1))
        expiration_str = str(pos.get("expiration", "")).split(" ")[0]

        logger.info(
            "PositionMonitor: closing %s (%s) reason=%s", pos.get("id"), ticker, reason
        )

        # Mark pending_close BEFORE touching Alpaca (prevents orphans on crash)
        pos["status"] = "pending_close"
        pos["exit_reason"] = reason
        try:
            upsert_trade(pos, source="execution", path=self.db_path)
        except Exception as e:
            logger.error(
                "PositionMonitor: DB pending_close write failed for %s: %s", pos.get("id"), e
            )

        if not self.alpaca:
            logger.info("PositionMonitor [DRY RUN]: would close %s", pos.get("id"))
            return

        try:
            if "condor" in spread_type:
                result = self._submit_ic_close(pos, contracts, expiration_str)
            elif "straddle" in spread_type or "strangle" in spread_type:
                result = self._submit_straddle_close(pos, contracts, expiration_str)
            elif not pos.get("long_strike"):
                # Single-leg synthetic orphan record — buy back the short leg only
                opt_type = "call" if "call" in spread_type else "put"
                logger.warning(
                    "PositionMonitor: %s is a single-leg synthetic record — "
                    "submitting single-leg BTC order for %s %s",
                    pos.get("id"), ticker, opt_type,
                )
                result = self.alpaca.submit_single_leg(
                    ticker=ticker,
                    strike=pos.get("short_strike"),
                    expiration=expiration_str,
                    option_type=opt_type,
                    side="buy",  # buy to close a short position
                    contracts=contracts,
                    limit_price=self._compute_close_limit(pos),
                )
            else:
                # Compute limit price for close: use current debit value or
                # fall back to a small debit to ensure the order fills.
                close_limit = self._compute_close_limit(pos)
                result = self.alpaca.close_spread(
                    ticker=ticker,
                    short_strike=pos.get("short_strike"),
                    long_strike=pos.get("long_strike"),
                    expiration=expiration_str,
                    spread_type=str(pos.get("strategy_type", pos.get("type", ""))),
                    contracts=contracts,
                    limit_price=close_limit,
                )

            if result.get("status") == "submitted":
                # Store order_id + submission timestamp so _reconcile_pending_closes
                # can poll for fills and detect stale (unfilled) close orders.
                pos["close_order_id"] = result.get("order_id")
                pos["close_order_submitted_at"] = datetime.now(timezone.utc).isoformat()
                # Straddle/strangle closes have a second order for the put leg
                if result.get("put_order_id"):
                    pos["close_put_order_id"] = result.get("put_order_id")
                try:
                    upsert_trade(pos, source="execution", path=self.db_path)
                except Exception as e:
                    logger.error(
                        "PositionMonitor: failed to store close_order_id for %s: %s",
                        pos.get("id"), e,
                    )
                logger.info(
                    "PositionMonitor: close submitted for %s order_id=%s%s",
                    pos.get("id"), pos["close_order_id"],
                    f" put_order_id={pos.get('close_put_order_id')}" if pos.get("close_put_order_id") else "",
                )
            elif result.get("status") == "partial_close":
                # IC close failed after all retries — _submit_ic_close already logged CRITICAL
                # and set ic_partial_close=True in the DB. Leave position in pending_close
                # state so it is NOT automatically retried (which would loop indefinitely).
                # Manual intervention required to close remaining open legs.
                logger.critical(
                    "PositionMonitor: IC %s is in PARTIAL_CLOSE state — manual leg-by-leg "
                    "close required. Position left in pending_close to prevent auto-retry loop.",
                    pos.get("id"),
                )
            else:
                # Close order was rejected by Alpaca (non-submitted result).
                # Reset status back to "open" so the exit-condition check retries on
                # the next cycle rather than leaving the position stuck in pending_close.
                logger.error(
                    "PositionMonitor: close order FAILED for %s: %s — "
                    "resetting to open for retry on next cycle",
                    pos.get("id"), result.get("message"),
                )
                pos["status"] = "open"
                pos.pop("close_order_id", None)
                pos.pop("exit_reason", None)
                pos.pop("close_order_submitted_at", None)
                try:
                    upsert_trade(pos, source="execution", path=self.db_path)
                except Exception as reset_err:
                    logger.error(
                        "PositionMonitor: failed to reset %s to open after close failure: %s",
                        pos.get("id"), reset_err,
                    )

        except Exception as e:
            logger.error(
                "PositionMonitor: exception submitting close for %s: %s",
                pos.get("id"), e, exc_info=True,
            )
            notify_api_failure(
                error_msg=str(e),
                context=f"submit_close ({pos.get('ticker', '?')} / {pos.get('id', '?')})",
            )

    def _submit_ic_close(self, pos: Dict, contracts: int, expiration_str: str) -> Dict:
        """Delegate 4-leg iron condor close to AlpacaProvider, with retry on failure.

        Retries up to 2 additional times (3 total) with a 5-second delay between
        attempts to handle transient Alpaca errors (e.g. rate limits, brief outages).

        If all attempts fail, the position is flagged as partial_close in the DB
        and a CRITICAL alert is logged. Manual intervention is required to close
        any remaining open legs to avoid unhedged exposure.

        Returns:
            Dict with status 'submitted', 'error', or 'partial_close'.
        """
        import time

        ticker = pos.get("ticker", "")
        put_short = pos.get("put_short_strike") or pos.get("short_strike")
        put_long = pos.get("put_long_strike") or pos.get("long_strike")
        call_short = pos.get("call_short_strike")
        call_long = pos.get("call_long_strike")

        if not all([put_short, put_long, call_short, call_long]):
            return {"status": "error", "message": "IC missing wing strikes — cannot close"}

        _MAX_ATTEMPTS = 3
        last_result: Dict = {}

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            last_result = self.alpaca.close_iron_condor(
                ticker=ticker,
                put_short_strike=put_short,
                put_long_strike=put_long,
                call_short_strike=call_short,
                call_long_strike=call_long,
                expiration=expiration_str,
                contracts=contracts,
                limit_price=None,
            )
            if last_result.get("status") == "submitted":
                return last_result

            logger.warning(
                "PositionMonitor: IC close attempt %d/%d FAILED for %s (%s): %s",
                attempt, _MAX_ATTEMPTS, pos.get("id"), ticker,
                last_result.get("message", last_result),
            )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(5)

        # All attempts exhausted — mark partial_close and escalate
        logger.critical(
            "PositionMonitor: IC CLOSE FAILED after %d attempts for %s (%s). "
            "Position flagged as partial_close. Manual intervention required to "
            "close all 4 legs and eliminate unhedged exposure.",
            _MAX_ATTEMPTS, pos.get("id"), ticker,
        )
        pos["ic_partial_close"] = True
        try:
            upsert_trade(pos, source="execution", path=self.db_path)
        except Exception as db_err:
            logger.error(
                "PositionMonitor: failed to persist ic_partial_close flag for %s: %s",
                pos.get("id"), db_err,
            )

        return {
            "status": "partial_close",
            "message": f"IC close failed after {_MAX_ATTEMPTS} attempts — manual close required",
            "last_error": last_result.get("message"),
        }

    def _submit_straddle_close(self, pos: Dict, contracts: int, expiration_str: str) -> Dict:
        """Close a straddle/strangle by submitting two single-leg close orders.

        For long: sell-to-close call + sell-to-close put.
        For short: buy-to-close call + buy-to-close put.
        """
        ticker = pos.get("ticker", "")
        call_strike = pos.get("call_strike")
        put_strike = pos.get("put_strike")
        spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
        is_long = spread_type.startswith("long_")

        if not all([call_strike, put_strike]):
            return {"status": "error", "message": "Straddle missing strikes — cannot close"}

        close_side = "sell" if is_long else "buy"

        call_result = self.alpaca.submit_single_leg(
            ticker=ticker,
            strike=call_strike,
            expiration=expiration_str,
            option_type="call",
            side=close_side,
            contracts=contracts,
            limit_price=None,
            client_order_id=f"{pos.get('id', '')}-close-call",
        )

        if call_result.get("status") != "submitted":
            return {"status": "error", "message": f"Call close failed: {call_result}"}

        put_result = self.alpaca.submit_single_leg(
            ticker=ticker,
            strike=put_strike,
            expiration=expiration_str,
            option_type="put",
            side=close_side,
            contracts=contracts,
            limit_price=None,
            client_order_id=f"{pos.get('id', '')}-close-put",
        )

        if put_result.get("status") != "submitted":
            # Attempt to cancel call close
            call_order_id = call_result.get("order_id")
            if call_order_id:
                try:
                    self.alpaca.cancel_order(call_order_id)
                except Exception:
                    logger.error(
                        "PositionMonitor: CRITICAL — call close cancel FAILED for %s",
                        pos.get("id"),
                    )
            return {"status": "error", "message": f"Put close failed: {put_result}"}

        return {
            "status": "submitted",
            "order_id": call_result.get("order_id"),
            "put_order_id": put_result.get("order_id"),
        }

    # ------------------------------------------------------------------
    # Pending-open reconciliation (intra-day fill tracking)
    # ------------------------------------------------------------------

    def _reconcile_pending_opens(self) -> None:
        """Promote pending_open trades to open when Alpaca confirms fill.

        Called every check cycle so orders placed during the session become
        monitored for stop-loss and profit-target as soon as they fill.
        Delegates to PositionReconciler for the actual Alpaca order polling.
        """
        try:
            from shared.reconciler import PositionReconciler
            reconciler = PositionReconciler(alpaca=self.alpaca, db_path=self.db_path)
            result = reconciler.reconcile_pending_only()
            if result.pending_resolved or result.pending_failed:
                logger.info(
                    "PositionMonitor: pending_open reconcile — resolved=%d failed=%d",
                    result.pending_resolved, result.pending_failed,
                )
        except Exception as e:
            logger.warning("PositionMonitor: pending_open reconciliation error: %s", e)

    # ------------------------------------------------------------------
    # Three-tier scheduling helpers
    # ------------------------------------------------------------------

    def _should_run_tier2(self) -> bool:
        """Return True if enough time has elapsed since the last Tier 2 run."""
        if self._last_tier2_run is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_tier2_run).total_seconds()
        return elapsed >= _TIER2_INTERVAL_SECONDS

    def _should_run_eod(self, now_et: datetime, date_str: str) -> bool:
        """Return True if the EOD reconciliation should fire now.

        Fires once per trading day between 4:15 PM and 4:30 PM ET.
        Only fires on weekdays (market days — holiday check is lightweight here
        since EOD is low-volume; the reconciler itself handles no-op gracefully).
        """
        if now_et.weekday() not in _MARKET_DAYS:
            return False
        now_mins = now_et.hour * 60 + now_et.minute
        window_start = _EOD_RECONCILE_HOUR_ET * 60 + _EOD_RECONCILE_MIN_ET
        window_end = _EOD_RECONCILE_HOUR_ET * 60 + _EOD_RECONCILE_END_MIN_ET
        if not (window_start <= now_mins < window_end):
            return False
        return self._last_eod_date != date_str

    def _should_run_morning(self, now_et: datetime, date_str: str) -> bool:
        """Return True if the morning reconciliation should fire now.

        Fires once per trading day between 9:35 AM and 10:00 AM ET.
        """
        if now_et.weekday() not in _MARKET_DAYS:
            return False
        now_mins = now_et.hour * 60 + now_et.minute
        window_start = _MORNING_RECONCILE_HOUR_ET * 60 + _MORNING_RECONCILE_MIN_ET
        window_end = _MORNING_RECONCILE_END_HOUR_ET * 60
        if not (window_start <= now_mins < window_end):
            return False
        return self._last_morning_date != date_str

    def _run_eod_reconciliation(self, date_str: str) -> None:
        """Execute Tier 3 EOD reconciliation via PositionReconciler."""
        if not self.alpaca:
            logger.info("PositionMonitor: EOD reconciliation skipped (no Alpaca client)")
            self._last_eod_date = date_str
            return
        try:
            from shared.reconciler import PositionReconciler
            reconciler = PositionReconciler(alpaca=self.alpaca, db_path=self.db_path)
            result = reconciler.reconcile_eod()
            logger.info(
                "PositionMonitor: EOD reconciliation complete — %s", result
            )
        except Exception as e:
            logger.error("PositionMonitor: EOD reconciliation failed: %s", e, exc_info=True)
        finally:
            self._last_eod_date = date_str

    def _run_morning_reconciliation(self, date_str: str) -> None:
        """Execute Tier 3b morning reconciliation via PositionReconciler."""
        if not self.alpaca:
            logger.info("PositionMonitor: morning reconciliation skipped (no Alpaca client)")
            self._last_morning_date = date_str
            return
        try:
            from shared.reconciler import PositionReconciler
            reconciler = PositionReconciler(alpaca=self.alpaca, db_path=self.db_path)
            result = reconciler.reconcile_morning()
            logger.info(
                "PositionMonitor: morning reconciliation complete — %s", result
            )
        except Exception as e:
            logger.error("PositionMonitor: morning reconciliation failed: %s", e, exc_info=True)
        finally:
            self._last_morning_date = date_str

    # ------------------------------------------------------------------
    # Assignment detection
    # ------------------------------------------------------------------

    def _detect_assignment(
        self, open_positions: list, alpaca_positions: dict
    ) -> None:
        """Check for unexpected equity positions that may indicate early assignment.

        When a short put is assigned, the option position disappears and a short
        stock position appears. This method alerts on any equity position whose
        underlying ticker matches one of our open spreads.
        """
        # Collect tickers we're managing
        managed_tickers = {pos.get("ticker", "") for pos in open_positions}
        if not managed_tickers:
            return

        for symbol, pos_data in alpaca_positions.items():
            asset_class = str(pos_data.get("asset_class", "")).lower()
            if "option" in asset_class:
                continue  # normal option position

            # This is an equity position — check if it matches a managed ticker
            for ticker in managed_tickers:
                if ticker and symbol.upper() == ticker.upper():
                    qty = pos_data.get("qty", "?")
                    logger.warning(
                        "PositionMonitor: POSSIBLE ASSIGNMENT DETECTED — "
                        "equity position %s qty=%s found while managing %s spreads. "
                        "Manual review required.",
                        symbol, qty, ticker,
                    )

    # ------------------------------------------------------------------
    # Orphan position detection
    # ------------------------------------------------------------------

    def _detect_orphans(
        self, open_positions: List[Dict], alpaca_positions: Dict
    ) -> None:
        """Reconcile option positions in Alpaca that have no corresponding open DB record.

        For each unmanaged Alpaca option position:
          1. Check pending_open / closed_external DB records for a matching OCC symbol.
             If found → promote that record to status=open so it gets monitored.
          2. If the position is SHORT (qty < 0) and no recovery candidate exists →
             create a ``synthetic-monitor-*`` record with status=open so the normal
             stop-loss / profit-target checks fire on the next cycle.
          3. Long legs (qty >= 0) are skipped — they are hedge legs of an untracked
             spread and cannot be independently managed.
        """
        # RC4: Seed managed_symbols from trade_legs table (exact OCC match, no reconstruction)
        try:
            managed_symbols: set = get_open_leg_symbols(path=self.db_path)
        except Exception as _legs_err:
            logger.warning(
                "PositionMonitor: trade_legs lookup failed (%s) — falling back to strike reconstruction",
                _legs_err,
            )
            managed_symbols = set()
        for pos in open_positions:
            ticker = pos.get("ticker", "")
            exp = str(pos.get("expiration", "")).split(" ")[0]
            spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
            if not ticker or not exp:
                continue
            try:
                if "straddle" in spread_type or "strangle" in spread_type:
                    for strike, opt_type in [
                        (pos.get("call_strike"), "call"),
                        (pos.get("put_strike"), "put"),
                    ]:
                        if strike:
                            managed_symbols.add(
                                self.alpaca._build_occ_symbol(ticker, exp, strike, opt_type)
                            )
                elif "condor" in spread_type:
                    for strike, opt_type in [
                        (pos.get("put_short_strike") or pos.get("short_strike"), "put"),
                        (pos.get("put_long_strike") or pos.get("long_strike"), "put"),
                        (pos.get("call_short_strike"), "call"),
                        (pos.get("call_long_strike"), "call"),
                    ]:
                        if strike:
                            managed_symbols.add(
                                self.alpaca._build_occ_symbol(ticker, exp, strike, opt_type)
                            )
                else:
                    opt_type = "call" if "call" in spread_type else "put"
                    for strike in [pos.get("short_strike"), pos.get("long_strike")]:
                        if strike:
                            managed_symbols.add(
                                self.alpaca._build_occ_symbol(ticker, exp, strike, opt_type)
                            )
            except Exception:
                pass

        # Build recovery candidates from pending_open and closed_external records.
        # These are trades the DB knows about but which are not yet (or no longer)
        # in status=open — matching an Alpaca position means we should re-open them.
        # We try both call and put variants of each strike to handle cases where the
        # option type in the DB trade may not match what actually filled in Alpaca.
        recovery_by_symbol: Dict[str, Dict] = {}
        for rec_status in ("pending_open", "closed_external"):
            candidates = get_trades(status=rec_status, source="execution", path=self.db_path)
            for trade in candidates:
                t_ticker = trade.get("ticker", "")
                t_exp = str(trade.get("expiration", "")).split(" ")[0]
                if not t_ticker or not t_exp:
                    continue
                for strike in [trade.get("short_strike"), trade.get("long_strike")]:
                    if not strike:
                        continue
                    for ot in ("call", "put"):
                        try:
                            sym = self.alpaca._build_occ_symbol(t_ticker, t_exp, strike, ot)
                            recovery_by_symbol.setdefault(sym, trade)
                        except Exception:
                            pass

        for symbol, pos_data in alpaca_positions.items():
            asset_class = str(pos_data.get("asset_class", "")).lower()
            if "option" not in asset_class:
                continue
            if symbol in managed_symbols:
                continue

            qty_str = str(pos_data.get("qty", "0"))
            try:
                qty = float(qty_str)
            except (ValueError, TypeError):
                qty = 0.0

            # Recovery: pending_open or closed_external record matches this Alpaca position
            if symbol in recovery_by_symbol:
                trade = recovery_by_symbol[symbol]
                trade_id = trade.get("id", "?")
                old_status = trade.get("status", "?")
                logger.warning(
                    "PositionMonitor: RECOVERY — orphan %s matches %s trade %s. "
                    "Promoting to open so stop-loss monitoring resumes.",
                    symbol, old_status, trade_id,
                )
                trade["status"] = "open"
                trade.pop("exit_reason", None)
                trade.pop("exit_date", None)
                try:
                    upsert_trade(trade, source="execution", path=self.db_path)
                except Exception as e:
                    logger.error(
                        "PositionMonitor: recovery DB write failed for %s: %s", trade_id, e
                    )
                continue

            # Long positions (positive qty): untracked orphan long — warn and auto-close
            if qty >= 0:
                current_price_str = str(pos_data.get("current_price", "0"))
                try:
                    current_price = float(current_price_str)
                except (ValueError, TypeError):
                    current_price = 0.0

                logger.warning(
                    "PositionMonitor: ORPHAN LONG POSITION %s qty=%s current_price=%s — "
                    "no DB record found. %s",
                    symbol, qty_str, current_price_str,
                    "Auto-closing." if self.auto_close_orphan_longs else "auto_close_orphan_longs disabled — manual intervention required.",
                )

                try:
                    notify_api_failure(
                        error_msg=f"Orphan long option {symbol} qty={qty_str} — {'auto-closing' if self.auto_close_orphan_longs else 'manual close required'}",
                        context="orphan_long_auto_close",
                    )
                except Exception as alert_err:
                    logger.error(
                        "PositionMonitor: Telegram alert failed for orphan long %s: %s", symbol, alert_err
                    )

                if self.auto_close_orphan_longs and current_price > 0:
                    try:
                        contracts = max(1, int(abs(qty)))
                        result = self.alpaca.sell_to_close_by_occ_symbol(
                            occ_symbol=symbol,
                            contracts=contracts,
                            limit_price=current_price,
                        )
                        if result.get("status") == "submitted":
                            logger.warning(
                                "PositionMonitor: orphan long %s sell-to-close order submitted: %s",
                                symbol, result.get("order_id"),
                            )
                            # Auto-clear any position_conflict records whose long_strike
                            # matches this orphan's strike so the dedup gate allows re-entry.
                            try:
                                orphan_strike = int(symbol[13:21]) / 1000.0
                            except (ValueError, IndexError):
                                orphan_strike = None
                            if orphan_strike is not None:
                                try:
                                    conflict_trades = get_trades(
                                        status="position_conflict", path=self.db_path
                                    )
                                    for ct in conflict_trades:
                                        ct_long_strike = ct.get("long_strike")
                                        if ct_long_strike is None:
                                            continue
                                        try:
                                            if abs(float(ct_long_strike) - orphan_strike) < 0.001:
                                                ct["status"] = "failed_open"
                                                upsert_trade(
                                                    ct, source="execution", path=self.db_path
                                                )
                                                logger.warning(
                                                    "PositionMonitor: position_conflict trade %s "
                                                    "auto-cleared to failed_open — orphan long %s "
                                                    "(strike=%.3f) was closed",
                                                    ct.get("id"), symbol, orphan_strike,
                                                )
                                        except Exception as _match_err:
                                            logger.error(
                                                "PositionMonitor: error clearing position_conflict "
                                                "trade %s: %s",
                                                ct.get("id"), _match_err,
                                            )
                                except Exception as _cf_err:
                                    logger.error(
                                        "PositionMonitor: failed to query position_conflict trades "
                                        "for orphan %s: %s",
                                        symbol, _cf_err,
                                    )
                        else:
                            logger.error(
                                "PositionMonitor: orphan long %s sell-to-close failed: %s",
                                symbol, result.get("message"),
                            )
                    except Exception as close_err:
                        logger.error(
                            "PositionMonitor: error submitting sell-to-close for orphan long %s: %s",
                            symbol, close_err,
                        )

                continue

            # RC4: Short orphan with no recovery candidate — alert only, do NOT create
            # a synthetic-monitor record. Synthetic records have long_strike=None and
            # cause mispriced SL/PT checks, accumulating as zombie positions.
            logger.critical(
                "PositionMonitor: UNTRACKED SHORT POSITION %s qty=%s — "
                "no DB record found. Manual intervention required. "
                "INVESTIGATE: how did this position become unmanaged?",
                symbol, qty_str,
            )
            try:
                notify_api_failure(
                    error_msg=f"Untracked short option {symbol} qty={qty_str}",
                    context="orphan_detection_critical",
                )
            except Exception as alert_err:
                logger.error(
                    "PositionMonitor: Telegram alert failed for orphan %s: %s", symbol, alert_err
                )

    # ------------------------------------------------------------------
    # P&L reconciliation — Bug 2 fix
    # ------------------------------------------------------------------

    def _reconcile_pending_closes(self) -> None:
        """Poll Alpaca for fill status of pending_close orders; record P&L when filled.

        Straddle/strangle positions have dual close orders (call + put).
        Both must fill before P&L is recorded. If one fills and the other fails,
        the position stays pending_close and logs a warning.
        """
        pending = get_trades(status="pending_close", source="execution", path=self.db_path)
        if not pending:
            return

        logger.debug("PositionMonitor: reconciling %d pending_close position(s)", len(pending))

        for pos in pending:
            order_id = pos.get("close_order_id")
            if not order_id:
                # No order_id stored — close was submitted before this fix; leave for manual
                continue

            put_order_id = pos.get("close_put_order_id")

            try:
                order = self.alpaca.get_order_status(order_id)
            except Exception as e:
                logger.warning(
                    "PositionMonitor: order status fetch failed for %s: %s", order_id, e
                )
                notify_api_failure(
                    error_msg=str(e),
                    context=f"get_order_status (order_id={order_id})",
                )
                continue

            if not order:
                continue

            order_status = str(order.get("status", "")).lower()

            # --- Dual-leg close (straddle/strangle) ---
            if put_order_id:
                try:
                    put_order = self.alpaca.get_order_status(put_order_id)
                except Exception as e:
                    logger.warning(
                        "PositionMonitor: put close order status fetch failed for %s: %s",
                        put_order_id, e,
                    )
                    continue

                put_status = str(put_order.get("status", "")).lower() if put_order else ""

                # Both legs filled → record combined P&L
                if "filled" in order_status and "filled" in put_status:
                    combined_order = self._combine_straddle_fills(order, put_order)
                    self._record_close_pnl(pos, combined_order)
                # One leg terminal-no-fill → reset to open for retry
                elif order_status in _TERMINAL_NO_FILL or put_status in _TERMINAL_NO_FILL:
                    logger.warning(
                        "PositionMonitor: straddle close partial failure for %s — "
                        "call=%s put=%s — resetting to open",
                        pos.get("id"), order_status, put_status,
                    )
                    self._reset_to_open(pos)
                # One leg filled, other still pending → warn but wait
                elif "filled" in order_status or "filled" in put_status:
                    logger.warning(
                        "PositionMonitor: straddle %s partial close fill — "
                        "call=%s put=%s — waiting for second leg",
                        pos.get("id"), order_status, put_status,
                    )
                    self._check_stale_close(pos, order_id, order_status)
                else:
                    # Both still pending — check staleness
                    self._check_stale_close(pos, order_id, order_status)
                continue

            # --- Single-order close (credit spread, iron condor) ---
            if "filled" in order_status:
                # Partial fill detection: compare filled_qty to expected contracts
                filled_qty_str = order.get("filled_qty")
                expected_contracts = int(pos.get("contracts", 1))
                if filled_qty_str:
                    try:
                        filled_qty = int(float(filled_qty_str))
                        if filled_qty != expected_contracts:
                            logger.warning(
                                "PositionMonitor: PARTIAL FILL detected for %s — "
                                "filled=%d expected=%d. Adjusting contracts to filled qty.",
                                pos.get("id"), filled_qty, expected_contracts,
                            )
                            pos["contracts"] = filled_qty
                    except (ValueError, TypeError):
                        pass
                self._record_close_pnl(pos, order)

            elif order_status in _TERMINAL_NO_FILL:
                logger.warning(
                    "PositionMonitor: close order %s terminal status '%s' for %s — resetting to open",
                    order_id, order_status, pos.get("id"),
                )
                self._reset_to_open(pos)
            else:
                self._check_stale_close(pos, order_id, order_status)

    def _combine_straddle_fills(self, call_order: Dict, put_order: Dict) -> Dict:
        """Combine two single-leg fill orders into a synthetic combined order for P&L calc.

        The filled_avg_price is the sum of both legs' fill prices (total cost/credit per share).
        """
        call_fill = float(call_order.get("filled_avg_price") or 0)
        put_fill = float(put_order.get("filled_avg_price") or 0)
        combined_fill = call_fill + put_fill

        return {
            "status": "filled",
            "filled_avg_price": str(combined_fill),
            "filled_at": call_order.get("filled_at") or put_order.get("filled_at"),
            "filled_qty": call_order.get("filled_qty") or put_order.get("filled_qty"),
        }

    def _reset_to_open(self, pos: Dict) -> None:
        """Reset a failed pending_close position back to open for retry."""
        pos["status"] = "open"
        pos.pop("close_order_id", None)
        pos.pop("close_put_order_id", None)
        pos.pop("exit_reason", None)
        pos.pop("close_order_submitted_at", None)
        try:
            upsert_trade(pos, source="execution", path=self.db_path)
        except Exception as e:
            logger.error(
                "PositionMonitor: failed to reset %s to open: %s", pos.get("id"), e
            )

    def _check_stale_close(self, pos: Dict, order_id: str, order_status: str) -> None:
        """Detect stale close orders and auto-retry (cancel + resubmit).

        When a close order has been pending > _STALE_CLOSE_MINUTES:
        - Attempts to cancel the stale order and resubmit via _close_position().
        - Tracks retries in pos["close_order_retry_count"].
        - After _STALE_CLOSE_MAX_RETRIES exhausted, sends a Telegram alert and
          stops retrying (leaves position in pending_close for manual review).
        """
        submitted_at_str = pos.get("close_order_submitted_at")
        if not submitted_at_str:
            return
        try:
            submitted_at = datetime.fromisoformat(submitted_at_str)
            if submitted_at.tzinfo is None:
                submitted_at = submitted_at.replace(tzinfo=timezone.utc)
            age_minutes = (
                datetime.now(timezone.utc) - submitted_at
            ).total_seconds() / 60
        except (ValueError, TypeError):
            return

        if age_minutes < _STALE_CLOSE_MINUTES:
            return

        retry_count = int(pos.get("close_order_retry_count", 0))
        pos_id = pos.get("id", "?")

        if retry_count >= _STALE_CLOSE_MAX_RETRIES:
            logger.critical(
                "PositionMonitor: STALE CLOSE — %s exhausted %d retries "
                "(order_id=%s status=%s age=%.0f min). Manual intervention required.",
                pos_id, _STALE_CLOSE_MAX_RETRIES, order_id, order_status, age_minutes,
            )
            try:
                notify_api_failure(
                    error_msg=(
                        f"Stale close order unfilled after {retry_count} retries "
                        f"(order_id={order_id}, age={age_minutes:.0f}min)"
                    ),
                    context=f"stale_close_retry ({pos.get('ticker', '?')} / {pos_id})",
                )
            except Exception as alert_err:
                logger.error("PositionMonitor: Telegram alert for stale close failed: %s", alert_err)
            return

        logger.warning(
            "PositionMonitor: STALE CLOSE ORDER — %s pending %.0f min "
            "(order_id=%s status=%s) — cancelling and resubmitting (retry %d/%d)",
            pos_id, age_minutes, order_id, order_status,
            retry_count + 1, _STALE_CLOSE_MAX_RETRIES,
        )

        # Cancel the stale order
        if self.alpaca:
            try:
                self.alpaca.cancel_order(order_id)
                logger.info(
                    "PositionMonitor: stale close order cancelled order_id=%s for %s",
                    order_id, pos_id,
                )
            except Exception as cancel_err:
                logger.warning(
                    "PositionMonitor: cancel of stale order %s failed: %s — "
                    "will still attempt resubmit",
                    order_id, cancel_err,
                )
            # Cancel the put order too if this is a dual-leg close
            put_order_id = pos.get("close_put_order_id")
            if put_order_id:
                try:
                    self.alpaca.cancel_order(put_order_id)
                except Exception:
                    pass

        # Clear old order info and increment retry counter
        exit_reason = pos.get("exit_reason", "stale_retry")
        pos["close_order_retry_count"] = retry_count + 1
        pos.pop("close_order_id", None)
        pos.pop("close_put_order_id", None)
        pos.pop("close_order_submitted_at", None)
        pos["status"] = "open"  # _close_position will set it back to pending_close
        try:
            upsert_trade(pos, source="execution", path=self.db_path)
        except Exception as db_err:
            logger.error(
                "PositionMonitor: DB update for stale retry failed (%s): %s", pos_id, db_err
            )

        # Resubmit
        self._close_position(pos, exit_reason)

    def _record_close_pnl(self, pos: Dict, order: Dict) -> None:
        """Calculate realized P&L from fill data and update DB with final closed status."""
        pos_id = pos.get("id", "?")
        credit = float(pos.get("credit") or 0)
        contracts = int(pos.get("contracts", 1))
        exit_reason = pos.get("exit_reason", "monitor")

        fill_price_str = order.get("filled_avg_price")
        try:
            fill_price = float(fill_price_str) if fill_price_str else 0.0
        except (ValueError, TypeError):
            fill_price = 0.0

        # P&L depends on whether this is a credit or debit position.
        # Credit positions: pnl = (credit_received - cost_to_close) * contracts * 100
        # Debit positions:  pnl = (proceeds_from_close - debit_paid) * contracts * 100
        is_debit = pos.get("is_debit", False) or credit < 0
        if is_debit:
            pnl = (fill_price - abs(credit)) * contracts * 100
        else:
            pnl = (credit - fill_price) * contracts * 100

        # Commission deduction — defaults to $0.65/contract matching backtester default.
        # Set execution.commission_per_contract: 0 in config to disable.
        #
        # E6 AUDIT — CONFIRMED MATCH with backtester:
        # Backtester (backtester.py line 1349 IC / line 1596 2-leg):
        #   commission_cost = self.commission * N_legs  (entry-side only)
        #   At entry: capital -= commission_cost
        #   At exit:  pnl -= pos['commission']  (= commission_cost again)
        #   => round-trip = 2 × N_legs × $0.65/contract
        #
        # Live (here): commission = 0.65 × contracts × N_legs × 2  (round-trip in one shot)
        #   IC (4 legs):  $0.65 × 1 × 4 × 2 = $5.20/contract ✓ matches backtester
        #   2-leg:        $0.65 × 1 × 2 × 2 = $2.60/contract ✓ matches backtester
        commission_per_contract = float(
            self.config.get("execution", {}).get("commission_per_contract", 0.65)
        )
        if commission_per_contract > 0:
            spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
            num_legs = 4 if "condor" in spread_type else 2
            # round trip: open (num_legs) + close (num_legs)
            commission = commission_per_contract * contracts * num_legs * 2
            pnl -= commission
            logger.info(
                "PositionMonitor: %s commission=%.2f (%d legs × %d contracts × $%.2f × 2 sides)",
                pos_id, commission, num_legs, contracts, commission_per_contract,
            )

        logger.info(
            "PositionMonitor: recording close for %s | fill=%.4f credit=%.4f pnl=$%.2f",
            pos_id, fill_price, credit, pnl,
        )

        try:
            close_trade(pos_id, pnl, exit_reason, path=self.db_path)
        except Exception as e_close:
            logger.error(
                "PositionMonitor: close_trade DB write failed for %s: %s — "
                "writing to WAL for recovery on next startup",
                pos_id, e_close,
            )
            # WAL write ensures this fill is not silently lost even if the DB is unavailable.
            # On next startup, replay_wal() will re-apply these entries before trading resumes.
            try:
                from shared.wal import write_wal_entry
                write_wal_entry({
                    "type": "close_trade",
                    "trade_id": pos_id,
                    "pnl": pnl,
                    "exit_reason": exit_reason,
                    "fill_price": fill_price,
                    "credit": credit,
                    "contracts": contracts,
                }, wal_path=self.config.get("execution", {}).get("wal_path"))
            except Exception as wal_err:
                logger.critical(
                    "PositionMonitor: WAL write ALSO failed for %s: %s. "
                    "Manual reconciliation required.",
                    pos_id, wal_err,
                )

        # ML-1: Log trade outcome for feature logger
        try:
            from shared.feature_logger import FeatureLogger
            from datetime import datetime as _dt, timezone as _tz
            # Determine outcome
            if abs(pnl) < 0.01:
                outcome = "scratch"
            elif pnl > 0:
                outcome = "win"
            else:
                outcome = "loss"
            # Calculate pnl_pct relative to max_loss (risk)
            max_loss_val = float(pos.get("max_loss", 0) or 0)
            if max_loss_val == 0:
                short_s = float(pos.get("short_strike", 0) or 0)
                long_s = float(pos.get("long_strike", 0) or 0)
                if short_s and long_s:
                    max_loss_val = abs(short_s - long_s) * contracts * 100
            pnl_pct = round(pnl / max_loss_val * 100, 2) if max_loss_val > 0 else 0.0
            # Calculate hold_days
            entry_date_str = pos.get("entry_date") or pos.get("created_at", "")
            hold_days = 0.0
            if entry_date_str:
                try:
                    entry_dt = _dt.fromisoformat(entry_date_str)
                    if entry_dt.tzinfo is None:
                        entry_dt = entry_dt.replace(tzinfo=_tz.utc)
                    hold_days = round((_dt.now(_tz.utc) - entry_dt).total_seconds() / 86400, 2)
                except (ValueError, TypeError):
                    pass
            fl = FeatureLogger(db_path=self.db_path)
            fl.log_outcome(pos_id, outcome, pnl_pct, hold_days)
        except Exception as e_ml:
            logger.warning("PositionMonitor: feature outcome logging failed for %s (non-fatal): %s", pos_id, e_ml)

        # INF-5: Record per-trade deviation (paper vs backtest expectations)
        try:
            from shared.deviation_tracker import record_deviation
            record_deviation(
                trade=pos,
                pnl=pnl,
                fill_price=fill_price,
                db_path=self.db_path,
                config=self.config,
            )
        except Exception as e_dev:
            logger.warning("PositionMonitor: deviation tracking failed for %s (non-fatal): %s", pos_id, e_dev)
