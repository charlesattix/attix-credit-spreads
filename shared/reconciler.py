"""
Position reconciler for Attix paper trading.

Compares SQLite state against Alpaca order/position reality and heals
discrepancies that can arise from crashes, network failures, or process
restarts mid-trade-lifecycle.

Design principles:
- Works directly with the database — no dependency on PaperTrader state
- Idempotent: safe to call multiple times; repeated runs produce the same result
- Conservative: never marks a trade as "failed" without confirming with Alpaca

Three-tier reconciliation schedule:
  Tier 1  (~60s)     reconcile_pending_only()  — pending resolution + activity check
  Tier 2  (~5 min)   reconcile_tier2()         — full position comparison + orphan detection
  Tier 3  (4:15 PM ET)  reconcile_eod()        — expirations + full reconcile
  Tier 3b (9:35 AM ET)  reconcile_morning()    — overnight settlement catch-up

Reconciliation targets:
  pending_open  → open           (order confirmed filled by Alpaca)
  pending_open  → failed_open    (order in terminal non-fill state, or 404)
  open          → no change      (normal case; Alpaca position still exists)
  open          → closed_profit  (expired worthless / external fill with gain)
  open          → closed_loss    (expired ITM / external fill at a loss)
  open          → needs_investigation  (legs missing, no matching activity)
  failed_open   → open           (Fix 4: live position found matching a mismarked trade)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# How often to run orphan detection from reconcile_pending_only() (minutes)
_ORPHAN_CHECK_INTERVAL_MINUTES = 30

# Tier 2 minimum interval (minutes) between full position-comparison runs
_TIER2_INTERVAL_MINUTES = 5

# EOD reconciliation time (ET): 16:15 = 15 min after close
_EOD_HOUR_ET = 16
_EOD_MIN_ET = 15

# Morning reconciliation time (ET): 9:35 = 5 min after open
_MORNING_HOUR_ET = 9
_MORNING_MIN_ET = 35

logger = logging.getLogger(__name__)

# Alpaca order statuses that mean the order will never fill
_TERMINAL_ORDER_STATES = frozenset({
    "cancelled", "expired", "rejected", "replaced", "done_for_day",
})

# If a pending_open trade is older than this, assume the order is dead
_PENDING_MAX_AGE_HOURS = 4

# Activity types that indicate an option was closed outside our system
_CLOSE_ACTIVITY_TYPES = ("OPEXP", "OASGN", "FILL")

# Default commission per contract ($) — matches backtester default
_DEFAULT_COMMISSION_PER_CONTRACT = 0.65

# Grace window after a trade's entry_date during which FILL activities are
# assumed to be the entry order's own fills (Alpaca records FILL activities
# asynchronously — typically 1-60s after the order is submitted). Activities
# within this window are NOT treated as evidence of an external close.
#
# Without this guard, the reconciler matches a trade's own opening FILLs to
# itself and force-marks it `external_fill`, which then triggers orphan
# placeholder creation downstream. See:
# /Users/charlesbot/.openclaw/workspace/reports/orphan-spreads-root-cause-2026-05-02.html
ENTRY_FILL_GRACE_SECONDS = 90


class ReconciliationResult:
    """Summary of what the reconciler did in one pass."""

    def __init__(self):
        self.pending_resolved: int = 0    # pending_open → open (fill confirmed)
        self.pending_failed: int = 0      # pending_open → failed_open (terminal state)
        self.phantom_resolved: int = 0    # open → needs_investigation (not in Alpaca)
        self.orphans_detected: int = 0    # Alpaca positions not in DB (logged, record created)
        self.externally_closed: int = 0   # open → closed_* via activity reconciliation
        self.expirations_processed: int = 0  # open → closed_* for expired-today trades
        self.activities_processed: int = 0   # total Alpaca activities matched and handled
        self.errors: List[str] = []

    def __bool__(self) -> bool:
        return bool(
            self.pending_resolved or self.pending_failed
            or self.phantom_resolved or self.orphans_detected
            or self.externally_closed or self.expirations_processed
            or self.activities_processed or self.errors
        )

    def __repr__(self) -> str:
        parts = []
        if self.pending_resolved:
            parts.append(f"resolved={self.pending_resolved}")
        if self.pending_failed:
            parts.append(f"failed={self.pending_failed}")
        if self.phantom_resolved:
            parts.append(f"phantoms={self.phantom_resolved}")
        if self.orphans_detected:
            parts.append(f"orphans={self.orphans_detected}")
        if self.externally_closed:
            parts.append(f"ext_closed={self.externally_closed}")
        if self.expirations_processed:
            parts.append(f"expirations={self.expirations_processed}")
        if self.activities_processed:
            parts.append(f"activities={self.activities_processed}")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return f"ReconciliationResult({', '.join(parts) or 'nothing'})"


class PositionReconciler:
    """Reconciles SQLite trade state against the Alpaca broker.

    Usage::

        reconciler = PositionReconciler(alpaca_provider)
        result = reconciler.reconcile()
        logger.info("Reconciliation: %s", result)

    Three-tier scheduling is driven by the PositionMonitor loop; each public
    ``reconcile_*`` method is designed to be called at the appropriate interval.
    """

    def __init__(self, alpaca, db_path: Optional[str] = None):
        """
        Args:
            alpaca: AlpacaProvider instance (must have get_order_by_client_id,
                    get_account_activities).
            db_path: Optional path override for the SQLite database.
        """
        self.alpaca = alpaca
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Public tier methods
    # ------------------------------------------------------------------

    def reconcile_pending_only(self) -> ReconciliationResult:
        """Tier 1: resolve pending_open orders + activity-based close detection.

        Called every monitor cycle (~60s) to:
        - Promote intra-day fills (pending_open → open)
        - Detect external closes via Alpaca account activities (OPEXP/FILL)
        - Run periodic orphan detection (every 30 min, Fix 5)

        Returns:
            ReconciliationResult with pending_resolved / pending_failed /
            externally_closed / activities_processed counts.
        """
        result = ReconciliationResult()
        self._reconcile_pending_opens(result)

        # Activity-based close detection: catches expirations and external
        # fills as soon as they appear in the Alpaca activities feed.
        if self.alpaca:
            try:
                self._reconcile_from_activities(result)
            except Exception as e:
                logger.warning("Reconciler: activity check failed: %s", e)

        # Fix 5: periodic orphan detection so intraday orphans don't persist
        # until the next restart.  Throttled to every 30 min to avoid API spam.
        if self._should_run_orphan_check():
            alpaca_positions = self._fetch_alpaca_positions()
            if alpaca_positions is not None:
                self._detect_orphan_positions(result, alpaca_positions)
                self._save_last_orphan_check()

        if result:
            logger.info("Tier1 reconciliation: %s", result)
        return result

    def reconcile_tier2(
        self, alpaca_positions: Optional[Dict] = None
    ) -> ReconciliationResult:
        """Tier 2: full position comparison against Alpaca (~5 min interval).

        Detects phantoms (DB-open positions missing from Alpaca), resolves
        them via the activities API, and runs orphan detection.

        Args:
            alpaca_positions: Pre-fetched position map {symbol: data}.
                If None, fetches from Alpaca.

        Returns:
            ReconciliationResult.
        """
        result = ReconciliationResult()

        # Activity check first (picks up any closes since last Tier 1 run)
        if self.alpaca:
            try:
                self._reconcile_from_activities(result)
            except Exception as e:
                logger.warning("Reconciler Tier2: activity check failed: %s", e)

        # Full position comparison
        if alpaca_positions is None:
            alpaca_positions = self._fetch_alpaca_positions()

        if alpaca_positions is not None:
            self._reconcile_open_positions(result, alpaca_positions)
            self._detect_orphan_positions(result, alpaca_positions)

        if result:
            logger.info("Tier2 reconciliation: %s", result)
        return result

    def reconcile_eod(self) -> ReconciliationResult:
        """Tier 3 EOD: expiration processing + full reconcile (runs at 4:15 PM ET).

        After market close:
        - Processes all trades expiring today (credit spreads expired OTM = max profit)
        - Runs a full activity check for any closes that occurred during the session
        - Full position comparison to catch anything still open unexpectedly

        Returns:
            ReconciliationResult.
        """
        result = ReconciliationResult()
        logger.info("Reconciler: starting EOD reconciliation")

        # Activity check covers anything that closed during the day
        if self.alpaca:
            try:
                self._reconcile_from_activities(result)
            except Exception as e:
                logger.warning("Reconciler EOD: activity check failed: %s", e)

        # Expiration processing (must come after activity check to avoid double-close)
        if self.alpaca:
            try:
                self._process_expirations(result)
            except Exception as e:
                logger.warning("Reconciler EOD: expiration processing failed: %s", e)

        # Full position comparison for any remaining open positions
        alpaca_positions = self._fetch_alpaca_positions()
        if alpaca_positions is not None:
            self._reconcile_open_positions(result, alpaca_positions)
            self._detect_orphan_positions(result, alpaca_positions)

        self._save_last_eod_run()
        logger.info("EOD reconciliation complete: %s", result)
        return result

    def reconcile_morning(self) -> ReconciliationResult:
        """Tier 3b morning: catch overnight settlement activity (runs at 9:35 AM ET).

        At market open, processes any overnight expirations or assignments that
        Alpaca settled after the previous day's close.

        Returns:
            ReconciliationResult.
        """
        result = ReconciliationResult()
        logger.info("Reconciler: starting morning reconciliation")

        # Catch overnight activity (expirations, assignments settled overnight)
        if self.alpaca:
            try:
                self._reconcile_from_activities(result)
            except Exception as e:
                logger.warning("Reconciler morning: activity check failed: %s", e)

        # Also run expiration processing to catch DTE=0 positions from yesterday
        if self.alpaca:
            try:
                self._process_expirations(result)
            except Exception as e:
                logger.warning("Reconciler morning: expiration processing failed: %s", e)

        # Full position comparison
        alpaca_positions = self._fetch_alpaca_positions()
        if alpaca_positions is not None:
            self._reconcile_open_positions(result, alpaca_positions)
            self._detect_orphan_positions(result, alpaca_positions)

        self._save_last_morning_run()
        logger.info("Morning reconciliation complete: %s", result)
        return result

    def reconcile(self) -> ReconciliationResult:
        """Run a full reconciliation pass against Alpaca's live state.

        Steps:
          1. Resolve pending_open orders (check fills, terminal states).
          2. Activity-based close detection (OPEXP / FILL).
          3. Detect phantom positions: DB says open but Alpaca has no matching legs.
          4. Detect orphan positions: Alpaca has option positions not in DB.

        Safe to call at any time; idempotent (repeated calls converge to same state).

        Returns:
            ReconciliationResult summarising what changed.
        """
        result = ReconciliationResult()

        # Step 1: pending_open → open / failed_open
        self._reconcile_pending_opens(result)

        # Step 2: activity-based close detection
        if self.alpaca:
            try:
                self._reconcile_from_activities(result)
            except Exception as e:
                logger.warning("Reconciler: activity check failed: %s", e)

        # Steps 3+4: only possible if we can fetch Alpaca positions
        alpaca_positions = self._fetch_alpaca_positions()
        if alpaca_positions is not None:
            # Step 3: detect open DB trades whose legs have disappeared from Alpaca
            self._reconcile_open_positions(result, alpaca_positions)
            # Step 4: detect Alpaca option positions with no DB record
            self._detect_orphan_positions(result, alpaca_positions)

            # Step 5: promote needs_investigation → open when Alpaca confirms legs exist
            self._promote_investigation_trades(result, alpaca_positions)

        if result:
            logger.info("Reconciliation complete: %s", result)
        else:
            logger.info("Reconciliation complete: nothing to do")

        return result

    # ------------------------------------------------------------------
    # Scheduling helpers: Tier 2 / EOD / morning
    # ------------------------------------------------------------------

    def _should_run_tier2(self) -> bool:
        """Return True if enough time has elapsed since the last Tier 2 run."""
        from shared.database import load_scanner_state
        last_str = load_scanner_state("last_tier2_reconcile", path=self.db_path)
        if not last_str:
            return True
        try:
            last = datetime.fromisoformat(last_str)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60
            return elapsed >= _TIER2_INTERVAL_MINUTES
        except (ValueError, TypeError):
            return True

    def _save_last_tier2(self) -> None:
        from shared.database import save_scanner_state
        try:
            save_scanner_state(
                "last_tier2_reconcile",
                datetime.now(timezone.utc).isoformat(),
                path=self.db_path,
            )
        except Exception as e:
            logger.warning("Reconciler: could not save last_tier2_reconcile: %s", e)

    def _should_run_eod(self) -> bool:
        """Return True if EOD reconciliation is due today and hasn't run yet."""
        from shared.database import load_scanner_state
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore
        now_et = datetime.now(ZoneInfo("America/New_York"))
        # Only fire after 4:15 PM ET
        if now_et.hour < _EOD_HOUR_ET or (
            now_et.hour == _EOD_HOUR_ET and now_et.minute < _EOD_MIN_ET
        ):
            return False
        today = now_et.strftime("%Y-%m-%d")
        last_str = load_scanner_state("last_eod_reconcile_date", path=self.db_path)
        return last_str != today

    def _save_last_eod_run(self) -> None:
        from shared.database import save_scanner_state
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        try:
            save_scanner_state(
                "last_eod_reconcile_date", today, path=self.db_path
            )
        except Exception as e:
            logger.warning("Reconciler: could not save last_eod_reconcile_date: %s", e)

    def _should_run_morning(self) -> bool:
        """Return True if morning reconciliation is due today and hasn't run yet."""
        from shared.database import load_scanner_state
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore
        now_et = datetime.now(ZoneInfo("America/New_York"))
        # Only fire after 9:35 AM ET
        if now_et.hour < _MORNING_HOUR_ET or (
            now_et.hour == _MORNING_HOUR_ET and now_et.minute < _MORNING_MIN_ET
        ):
            return False
        # Stop firing after 10:00 AM (avoid re-running mid-session)
        if now_et.hour >= 10:
            today = now_et.strftime("%Y-%m-%d")
            last_str = load_scanner_state("last_morning_reconcile_date", path=self.db_path)
            return last_str != today
        today = now_et.strftime("%Y-%m-%d")
        last_str = load_scanner_state("last_morning_reconcile_date", path=self.db_path)
        return last_str != today

    def _save_last_morning_run(self) -> None:
        from shared.database import save_scanner_state
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        try:
            save_scanner_state(
                "last_morning_reconcile_date", today, path=self.db_path
            )
        except Exception as e:
            logger.warning("Reconciler: could not save last_morning_reconcile_date: %s", e)

    # ------------------------------------------------------------------
    # Activity-based close detection (new — core of Tier 1 augmentation)
    # ------------------------------------------------------------------

    def _reconcile_from_activities(self, result: ReconciliationResult) -> None:
        """Use Alpaca account activities to detect closes the system didn't initiate.

        Fetches OPEXP (option expiration), OASGN (assignment), and FILL activities
        since the last check watermark.  Matches each activity to open DB trades
        by OCC symbol and computes PnL.  Updates the watermark on completion.

        Side-effects:
            Calls close_trade() for matched positions.
            Appends reconciliation_events entries.
            Updates scanner_state["last_activity_check"] watermark.
        """
        from shared.database import (
            close_trade, get_trades, insert_reconciliation_event,
            load_scanner_state, save_scanner_state, upsert_trade,
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        since = load_scanner_state("last_activity_check", path=self.db_path)

        # Fetch all close-relevant activity types
        opexp_acts: List[Dict] = []
        oasgn_acts: List[Dict] = []
        fill_acts: List[Dict] = []
        for act_type, target in (
            ("OPEXP", opexp_acts),
            ("OASGN", oasgn_acts),
            ("FILL", fill_acts),
        ):
            try:
                acts = self.alpaca.get_account_activities(
                    activity_type=act_type, since=since
                )
                target.extend(acts)
            except Exception as e:
                logger.warning(
                    "Reconciler: get_account_activities(%s) failed: %s", act_type, e
                )

        all_activities = opexp_acts + oasgn_acts + fill_acts
        if not all_activities:
            save_scanner_state("last_activity_check", now_iso, path=self.db_path)
            return

        # Index activities by OCC symbol for O(1) lookup
        opexp_by_sym: Dict[str, Dict] = {
            a["symbol"]: a for a in opexp_acts if a.get("symbol")
        }
        oasgn_by_sym: Dict[str, Dict] = {
            a["symbol"]: a for a in oasgn_acts if a.get("symbol")
        }
        fill_by_sym: Dict[str, List[Dict]] = {}
        for a in fill_acts:
            sym = a.get("symbol")
            if sym:
                fill_by_sym.setdefault(sym, []).append(a)

        # Get all open trades once
        open_trades = get_trades(status="open", path=self.db_path)
        if not open_trades:
            save_scanner_state("last_activity_check", now_iso, path=self.db_path)
            return

        processed_ids: set = set()
        for trade in open_trades:
            trade_id = trade.get("id", "?")
            if trade_id in processed_ids:
                continue

            ticker = trade.get("ticker", "")
            exp = str(trade.get("expiration", "")).split(" ")[0]
            spread_type = str(trade.get("strategy_type", trade.get("type", ""))).lower()
            if not ticker or not exp:
                continue

            try:
                expected_syms = self._expected_symbols(trade, ticker, exp, spread_type)
            except Exception as e:
                logger.debug(
                    "Reconciler: OCC symbol error for %s: %s — skip", trade_id, e
                )
                continue

            # Gather all activities touching this trade's legs
            trade_activities: List[Dict] = []
            for sym in expected_syms:
                if sym in opexp_by_sym:
                    trade_activities.append(opexp_by_sym[sym])
                if sym in oasgn_by_sym:
                    trade_activities.append(oasgn_by_sym[sym])
                trade_activities.extend(fill_by_sym.get(sym, []))

            if not trade_activities:
                continue

            # Compute PnL and close the trade
            pnl, close_reason, activity_id = self._compute_external_close_pnl(
                trade, trade_activities
            )
            if pnl is not None and close_reason:
                try:
                    close_trade(
                        trade_id, pnl, close_reason,
                        path=self.db_path,
                        close_source=close_reason,
                        alpaca_close_activity_id=activity_id,
                    )
                    insert_reconciliation_event(
                        trade_id, f"activity_close:{close_reason}",
                        {
                            "pnl": pnl,
                            "close_reason": close_reason,
                            "activity_id": activity_id,
                            "activity_count": len(trade_activities),
                        },
                        self.db_path,
                    )
                    result.externally_closed += 1
                    result.activities_processed += len(trade_activities)
                    processed_ids.add(trade_id)
                    logger.info(
                        "Reconciler: %s closed via %s activity — pnl=%.2f",
                        trade_id, close_reason, pnl,
                    )
                except Exception as e:
                    logger.error(
                        "Reconciler: close_trade failed for %s (activity): %s", trade_id, e
                    )
                    result.errors.append(f"activity_close_fail:{trade_id}")
            else:
                # OASGN or ambiguous — can't auto-compute PnL safely; flag for review
                logger.warning(
                    "Reconciler: %s has close activity (%d events) but PnL is "
                    "undetermined — marking needs_investigation",
                    trade_id, len(trade_activities),
                )
                trade["status"] = "needs_investigation"
                trade["exit_reason"] = "activity_undetermined_pnl"
                try:
                    upsert_trade(trade, source="reconciler", path=self.db_path)
                    insert_reconciliation_event(
                        trade_id, "needs_investigation",
                        {
                            "reason": "activity_undetermined_pnl",
                            "activity_count": len(trade_activities),
                        },
                        self.db_path,
                    )
                    result.phantom_resolved += 1
                    processed_ids.add(trade_id)
                except Exception as e:
                    logger.error(
                        "Reconciler: DB write failed for activity-investigation %s: %s",
                        trade_id, e,
                    )
                    result.errors.append(f"activity_investigation_fail:{trade_id}")

        save_scanner_state("last_activity_check", now_iso, path=self.db_path)

    def _compute_external_close_pnl(
        self, trade: Dict, activities: List[Dict]
    ) -> Tuple[Optional[float], Optional[str], Optional[str]]:
        """Compute PnL for a position closed outside our system.

        Returns (pnl_dollars, close_reason, primary_activity_id).
        Returns (None, None, None) when PnL cannot be computed safely
        (e.g. assignment — requires manual reconciliation).

        Cases handled:
          OPEXP with near-zero net_amount → expired_worthless
              pnl = credit × contracts × 100 − entry_commission
          OPEXP with significant net_amount → expired_itm
              pnl = credit × contracts × 100 + total_net_amount − entry_commission
          FILL (genuine closing fill, see below) → external_fill
              pnl derived from net_amount of fill activities
          OASGN → returns (None, None, None) — assignment logic is complex

        Race-condition guards on FILL classification (see ENTRY_FILL_GRACE_SECONDS
        comment for context — orphan-spreads-root-cause-2026-05-02.html):
          1. Drop FILL activities within ENTRY_FILL_GRACE_SECONDS of entry_date
             (Alpaca records entry-order fills asynchronously, 1-60s after entry)
          2. Drop FILL activities whose activity_subtype is `*_to_open`
             (Alpaca distinguishes opening from closing intent on each leg)
          3. Even after filtering, refuse to declare external_fill if Alpaca
             /v2/positions still reports any of the trade's legs as live —
             a contradiction means we missed an entry fill somewhere.
        """
        credit = float(trade.get("credit") or 0)
        contracts = int(trade.get("contracts", 1))
        spread_type = str(trade.get("strategy_type", trade.get("type", ""))).lower()
        num_legs = 4 if "condor" in spread_type else 2
        entry_comm = self._entry_commission(contracts, num_legs)

        opexp_acts = [a for a in activities if a.get("activity_type") == "OPEXP"]
        oasgn_acts = [a for a in activities if a.get("activity_type") == "OASGN"]
        raw_fill_acts = [a for a in activities if a.get("activity_type") == "FILL"]

        # Filter FILL activities to exclude the trade's own entry-order fills.
        # OPEXP and OASGN are unambiguously close events, so no filtering needed
        # for those.
        fill_acts = self._filter_close_fills(trade, raw_fill_acts)

        # OASGN (assignment): too complex to auto-compute — return None
        if oasgn_acts:
            return None, None, oasgn_acts[0].get("id")

        # OPEXP (expiration): one or more legs expired
        if opexp_acts:
            total_net = sum(float(a.get("net_amount") or 0) for a in opexp_acts)
            primary_id = opexp_acts[0].get("id")
            if abs(total_net) < 1.0:
                # All legs expired worthless — keep the full credit
                pnl = credit * contracts * 100 - entry_comm
                return pnl, "expired_worthless", primary_id
            else:
                # ITM expiration — broker settled; net_amount is negative (we paid out)
                pnl = credit * contracts * 100 + total_net - entry_comm
                return pnl, "expired_itm", primary_id

        # FILL (external close by someone buying/selling our legs)
        if fill_acts:
            # Paranoid double-check: if any leg is still listed in /v2/positions,
            # the FILL we matched cannot have been a closing fill — refuse to
            # classify as external_fill rather than corrupt the trade row.
            if self._trade_legs_still_open(trade):
                logger.warning(
                    "Reconciler: %s has close-eligible FILL activity but legs "
                    "still appear in /v2/positions — aborting external_fill "
                    "classification (possible missed entry fill in filter)",
                    trade.get("id", "?"),
                )
                return None, None, None

            # net_amount for closing fills: negative means we paid (bought back)
            total_net = sum(float(a.get("net_amount") or 0) for a in fill_acts)
            primary_id = fill_acts[0].get("id")
            # total_net is the net dollar flow from the close transaction
            # For a credit spread bought back: total_net < 0 (we paid the debit)
            # pnl = original_credit_received + net_amount_from_close
            pnl = credit * contracts * 100 + total_net - entry_comm
            return pnl, "external_fill", primary_id

        return None, None, None

    def _filter_close_fills(
        self, trade: Dict, fills: List[Dict]
    ) -> List[Dict]:
        """Drop FILL activities that are the trade's own entry-order fills.

        Two-pronged filter:
          - Subtype: drop activities whose activity_subtype is `*_to_open`
            (e.g. ``sell_to_open``, ``buy_to_open``). Alpaca distinguishes open
            from close on each FILL via activity_subtype.
          - Grace period: drop activities whose transaction_time is within
            ENTRY_FILL_GRACE_SECONDS of the trade's entry_date. Alpaca records
            entry-order FILL activities asynchronously (1-60s after entry).

        If activity_subtype is missing/unknown (older accounts, paper API quirks),
        the grace period alone protects the entry-fill window. If both are
        missing, we let the activity through — and the /v2/positions guard in
        `_compute_external_close_pnl` is the last line of defense.
        """
        if not fills:
            return fills

        trade_id = trade.get("id", "?")
        entry_dt = self._parse_iso(trade.get("entry_date"))
        grace_cutoff = (
            entry_dt + timedelta(seconds=ENTRY_FILL_GRACE_SECONDS)
            if entry_dt is not None
            else None
        )

        kept: List[Dict] = []
        for a in fills:
            subtype = (a.get("activity_subtype") or "").lower()
            if subtype.endswith("_to_open"):
                logger.debug(
                    "Reconciler: %s — dropping FILL %s (subtype=%s, entry fill)",
                    trade_id, a.get("id"), subtype,
                )
                continue

            if grace_cutoff is not None:
                tx_dt = self._parse_iso(a.get("transaction_time"))
                if tx_dt is not None and tx_dt < grace_cutoff:
                    logger.debug(
                        "Reconciler: %s — dropping FILL %s "
                        "(transaction_time %s within %ds grace of entry %s)",
                        trade_id, a.get("id"), a.get("transaction_time"),
                        ENTRY_FILL_GRACE_SECONDS, trade.get("entry_date"),
                    )
                    continue

            kept.append(a)
        return kept

    def _trade_legs_still_open(self, trade: Dict) -> bool:
        """Return True if any of the trade's expected OCC legs are still in /v2/positions.

        Used as a paranoid guard before declaring `external_fill` — if the broker
        still reports the legs as live, the FILL we matched cannot have been a
        closing fill, so we refuse the classification rather than corrupt the row.

        Returns False on any error (the guard is best-effort; primary defense is
        the grace+subtype filter in `_filter_close_fills`).
        """
        ticker = trade.get("ticker", "")
        exp = str(trade.get("expiration", "")).split(" ")[0]
        spread_type = str(trade.get("strategy_type", trade.get("type", ""))).lower()
        try:
            expected = set(self._expected_symbols(trade, ticker, exp, spread_type))
        except Exception as e:
            logger.debug("Reconciler: position guard expected_symbols failed: %s", e)
            return False
        if not expected:
            return False

        try:
            positions = self.alpaca.get_positions() or []
        except Exception as e:
            logger.debug("Reconciler: position guard get_positions failed: %s", e)
            return False

        held = {p.get("symbol") for p in positions if p.get("symbol")}
        return bool(expected & held)

    @staticmethod
    def _parse_iso(value: Optional[str]) -> Optional[datetime]:
        """Parse an ISO-8601 timestamp string into a timezone-aware datetime.

        Tolerates `Z` suffix and naive strings (treated as UTC). Returns None
        on any parse error.
        """
        if not value:
            return None
        s = str(value)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _fetch_activities_for_trade(
        self, trade: Dict, since: Optional[str] = None
    ) -> List[Dict]:
        """Fetch Alpaca activities that match any leg of *trade*.

        Makes separate API calls for OPEXP, OASGN, FILL and returns all
        activities whose OCC symbol matches one of the trade's expected legs.
        Returns empty list if self.alpaca is None or on API error.
        """
        if not self.alpaca:
            return []

        ticker = trade.get("ticker", "")
        exp = str(trade.get("expiration", "")).split(" ")[0]
        spread_type = str(trade.get("strategy_type", trade.get("type", ""))).lower()

        try:
            expected_syms = set(self._expected_symbols(trade, ticker, exp, spread_type))
        except Exception:
            return []

        matched: List[Dict] = []
        for act_type in _CLOSE_ACTIVITY_TYPES:
            try:
                acts = self.alpaca.get_account_activities(
                    activity_type=act_type, since=since
                )
                for a in acts:
                    if a.get("symbol") in expected_syms:
                        matched.append(a)
            except Exception as e:
                logger.debug(
                    "Reconciler: activity fetch %s for %s failed: %s",
                    act_type, trade.get("id"), e,
                )
        return matched

    # ------------------------------------------------------------------
    # End-of-day expiration processing (new — Tier 3)
    # ------------------------------------------------------------------

    def _process_expirations(self, result: ReconciliationResult) -> None:
        """Process all open trades that expired today or earlier.

        Called as part of EOD and morning reconciliation passes.  For each
        expired open trade:
        1. Query Alpaca activities to find the actual close event.
        2. If OPEXP found → compute exact PnL.
        3. If no activity but credit > 0 → assume expired worthless (estimated),
           set pnl_estimated=True in metadata.
        4. If credit == 0 or debit position → mark needs_investigation.

        Idempotent: only processes trades still in 'open' status.
        """
        from shared.database import (
            close_trade, get_db, get_trades, insert_reconciliation_event,
            upsert_trade,
        )
        import json

        open_trades = get_trades(status="open", path=self.db_path)
        if not open_trades:
            return

        today = datetime.now(timezone.utc).date()

        for trade in open_trades:
            trade_id = trade.get("id", "?")
            exp_str = str(trade.get("expiration", "")).split(" ")[0]
            if not exp_str:
                continue

            try:
                exp_date = datetime.fromisoformat(exp_str).date()
            except (ValueError, TypeError):
                continue

            if exp_date > today:
                continue  # not expired yet

            credit = float(trade.get("credit") or 0)
            contracts = int(trade.get("contracts", 1))
            spread_type = str(trade.get("strategy_type", trade.get("type", ""))).lower()
            num_legs = 4 if "condor" in spread_type else 2
            entry_comm = self._entry_commission(contracts, num_legs)

            logger.info(
                "Reconciler EOD: %s expired (exp=%s credit=%.4f contracts=%d)",
                trade_id, exp_str, credit, contracts,
            )

            # Try activities API first (entry_date as lower bound to avoid stale data)
            activities = self._fetch_activities_for_trade(
                trade, since=trade.get("entry_date")
            )

            if activities:
                pnl, close_reason, activity_id = self._compute_external_close_pnl(
                    trade, activities
                )
                if pnl is not None and close_reason:
                    try:
                        close_trade(
                            trade_id, pnl, close_reason,
                            path=self.db_path,
                            close_source="expiration",
                            alpaca_close_activity_id=activity_id,
                        )
                        insert_reconciliation_event(
                            trade_id, f"eod_expiration:{close_reason}",
                            {
                                "pnl": pnl,
                                "exp_date": exp_str,
                                "close_reason": close_reason,
                                "activity_id": activity_id,
                            },
                            self.db_path,
                        )
                        result.expirations_processed += 1
                        logger.info(
                            "Reconciler EOD: %s expired — %s pnl=%.2f",
                            trade_id, close_reason, pnl,
                        )
                    except Exception as e:
                        logger.error(
                            "Reconciler EOD: close_trade failed for %s: %s", trade_id, e
                        )
                        result.errors.append(f"eod_expiration_fail:{trade_id}")
                    continue
                # else: OASGN or undetermined — fall through to needs_investigation

            # No activity found — fallback estimate for credit positions
            if credit > 0:
                pnl = credit * contracts * 100 - entry_comm
                try:
                    close_trade(
                        trade_id, pnl, "expired_worthless",
                        path=self.db_path,
                        close_source="expiration_estimated",
                    )
                    # Tag metadata with pnl_estimated for audit trail
                    metadata = trade.get("metadata") or {}
                    if isinstance(metadata, str):
                        try:
                            import json as _json
                            metadata = _json.loads(metadata)
                        except Exception:
                            metadata = {}
                    metadata["pnl_estimated"] = True
                    conn = get_db(self.db_path)
                    try:
                        conn.execute(
                            "UPDATE trades SET metadata=?, updated_at=datetime('now') WHERE id=?",
                            (json.dumps(metadata), trade_id),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    insert_reconciliation_event(
                        trade_id, "eod_expiration:estimated_worthless",
                        {
                            "pnl": pnl,
                            "exp_date": exp_str,
                            "pnl_estimated": True,
                        },
                        self.db_path,
                    )
                    result.expirations_processed += 1
                    logger.info(
                        "Reconciler EOD: %s expired [estimated worthless] pnl=%.2f",
                        trade_id, pnl,
                    )
                except Exception as e:
                    logger.error(
                        "Reconciler EOD: estimated close failed for %s: %s", trade_id, e
                    )
                    result.errors.append(f"eod_expiration_estimated_fail:{trade_id}")
            else:
                # Debit position or zero credit — can't estimate safely
                logger.warning(
                    "Reconciler EOD: %s expired but PnL cannot be estimated "
                    "(credit=%.4f) — marking needs_investigation",
                    trade_id, credit,
                )
                trade["status"] = "needs_investigation"
                trade["exit_reason"] = "expired_pnl_unknown"
                try:
                    upsert_trade(trade, source="reconciler", path=self.db_path)
                    insert_reconciliation_event(
                        trade_id, "needs_investigation",
                        {"reason": "expired_pnl_unknown", "exp_date": exp_str},
                        self.db_path,
                    )
                    result.phantom_resolved += 1
                except Exception as e:
                    logger.error(
                        "Reconciler EOD: DB write failed for unknown-expiry %s: %s", trade_id, e
                    )
                    result.errors.append(f"eod_expiry_unknown_fail:{trade_id}")

    # ------------------------------------------------------------------
    # Position comparison (enhanced)
    # ------------------------------------------------------------------

    def _fetch_alpaca_positions(self) -> Optional[Dict]:
        """Fetch all current Alpaca positions as {symbol: pos_dict}.

        Returns None if the API call fails (non-fatal — Steps 2+3 are skipped).
        """
        try:
            positions = self.alpaca.get_positions()
            return {p["symbol"]: p for p in positions}
        except Exception as e:
            logger.warning(
                "Reconciler: could not fetch Alpaca positions (%s) — "
                "skipping open-position and orphan reconciliation",
                e,
            )
            return None

    def _reconcile_open_positions(
        self, result: ReconciliationResult, alpaca_positions: Dict
    ) -> None:
        """Detect phantom positions: DB status=open but ALL legs missing from Alpaca.

        Resolution order (proposal §3.5):
          1. Query Alpaca activities API — if matched, compute PnL and close_trade().
          2. Check if the trade has passed its expiration date — if so, estimate
             expired-worthless PnL for credit positions.
          3. Fallback: mark needs_investigation (as before).

        This replaces the previous behaviour of immediately marking
        needs_investigation without attempting resolution.
        """
        from shared.database import close_trade, get_trades, insert_reconciliation_event, upsert_trade

        open_trades = get_trades(status="open", path=self.db_path)
        if not open_trades:
            return

        today = datetime.now(timezone.utc).date()

        for trade in open_trades:
            trade_id = trade.get("id", "?")
            ticker = trade.get("ticker", "")
            exp = str(trade.get("expiration", "")).split(" ")[0]
            spread_type = str(trade.get("strategy_type", trade.get("type", ""))).lower()

            if not ticker or not exp:
                continue

            try:
                syms = self._expected_symbols(trade, ticker, exp, spread_type)
            except Exception as e:
                logger.warning(
                    "Reconciler: OCC symbol error for trade %s: %s — skipping", trade_id, e
                )
                continue

            if not syms:
                continue

            all_missing = all(sym not in alpaca_positions for sym in syms)
            if not all_missing:
                continue  # at least one leg still present — all good

            logger.warning(
                "Reconciler: PHANTOM — %s (open) all legs missing from Alpaca "
                "(expected: %s). Attempting activity resolution.",
                trade_id, syms,
            )

            # --- Step 1: Activities API ---
            activities = self._fetch_activities_for_trade(
                trade, since=trade.get("entry_date")
            )
            if activities:
                pnl, close_reason, activity_id = self._compute_external_close_pnl(
                    trade, activities
                )
                if pnl is not None and close_reason:
                    try:
                        close_trade(
                            trade_id, pnl, close_reason,
                            path=self.db_path,
                            close_source=close_reason,
                            alpaca_close_activity_id=activity_id,
                        )
                        insert_reconciliation_event(
                            trade_id, f"phantom_resolved:{close_reason}",
                            {
                                "pnl": pnl,
                                "close_reason": close_reason,
                                "activity_id": activity_id,
                                "expected_symbols": syms,
                            },
                            self.db_path,
                        )
                        result.externally_closed += 1
                        result.phantom_resolved += 1
                        logger.info(
                            "Reconciler: %s phantom resolved via activity (%s) pnl=%.2f",
                            trade_id, close_reason, pnl,
                        )
                    except Exception as e:
                        logger.error(
                            "Reconciler: close_trade failed for phantom %s: %s", trade_id, e
                        )
                        result.errors.append(f"phantom_close_fail:{trade_id}")
                    continue

            # --- Step 2: Expiration fallback ---
            try:
                exp_date = datetime.fromisoformat(exp).date()
                is_expired = exp_date <= today
            except (ValueError, TypeError):
                is_expired = False

            if is_expired:
                credit = float(trade.get("credit") or 0)
                contracts = int(trade.get("contracts", 1))
                num_legs = 4 if "condor" in spread_type else 2
                entry_comm = self._entry_commission(contracts, num_legs)

                if credit > 0:
                    pnl = credit * contracts * 100 - entry_comm
                    try:
                        close_trade(
                            trade_id, pnl, "expired_worthless",
                            path=self.db_path,
                            close_source="expiration_estimated",
                        )
                        insert_reconciliation_event(
                            trade_id, "phantom_resolved:expired_estimated",
                            {
                                "pnl": pnl,
                                "exp": exp,
                                "pnl_estimated": True,
                                "expected_symbols": syms,
                            },
                            self.db_path,
                        )
                        result.expirations_processed += 1
                        result.phantom_resolved += 1
                        logger.info(
                            "Reconciler: %s phantom resolved as expired [estimated] pnl=%.2f",
                            trade_id, pnl,
                        )
                    except Exception as e:
                        logger.error(
                            "Reconciler: expired-phantom close failed for %s: %s", trade_id, e
                        )
                        result.errors.append(f"phantom_expired_fail:{trade_id}")
                    continue

            # --- Step 3: Fallback — needs_investigation ---
            logger.warning(
                "Reconciler: PHANTOM POSITION — trade %s (open) has no legs in Alpaca "
                "and no matching activities. Marking needs_investigation.",
                trade_id,
            )
            trade["status"] = "needs_investigation"
            trade["exit_reason"] = "legs_not_found_in_alpaca"
            try:
                upsert_trade(trade, source="reconciler", path=self.db_path)
                insert_reconciliation_event(
                    trade_id, "needs_investigation",
                    {"reason": "legs_not_found_in_alpaca", "expected_symbols": syms},
                    self.db_path,
                )
            except Exception as e:
                logger.error(
                    "Reconciler: DB write failed for phantom %s: %s", trade_id, e
                )
                result.errors.append(f"phantom_write_fail:{trade_id}")
            result.phantom_resolved += 1

    def _promote_investigation_trades(
        self, result: ReconciliationResult, alpaca_positions: Dict
    ) -> None:
        """Promote ``needs_investigation`` trades to ``open`` when Alpaca confirms
        both legs still exist.

        This handles the common case where the DB was lost during a redeploy
        and the reconciler created records but couldn't determine credit/PnL.
        """
        inv_trades = get_trades(status="needs_investigation", source="execution", path=self.db_path)
        if not inv_trades:
            return

        alpaca_symbols = {p.get("symbol", "") for p in alpaca_positions.values()} if isinstance(alpaca_positions, dict) else set()
        if not alpaca_symbols:
            # Try list format
            if isinstance(alpaca_positions, list):
                alpaca_symbols = {p.get("symbol", "") for p in alpaca_positions}

        for trade in inv_trades:
            trade_id = trade.get("trade_id", "")
            short_leg = trade.get("short_leg", "")
            long_leg = trade.get("long_leg", "")

            # Check if at least the short leg is in Alpaca (confirms position is real)
            has_short = short_leg in alpaca_symbols if short_leg else False
            has_long = long_leg in alpaca_symbols if long_leg else False

            if has_short or has_long:
                logger.info(
                    "Reconciler: promoting %s from needs_investigation → open "
                    "(Alpaca confirms legs: short=%s long=%s)",
                    trade_id, has_short, has_long,
                )
                try:
                    trade["status"] = "open"
                    upsert_trade(trade, source="execution", path=self.db_path)
                except Exception as e:
                    logger.error("Reconciler: failed to promote %s: %s", trade_id, e)

    def _detect_orphan_positions(
        self, result: ReconciliationResult, alpaca_positions: Dict
    ) -> None:
        """Detect orphan option positions: in Alpaca but not in any DB open trade.

        Orphans are logged and a minimal DB record is created with
        ``status=unmanaged`` so they show up in reports and are not silently ignored.
        The system does NOT attempt to close or manage orphans automatically.
        """
        from shared.database import get_trades, insert_reconciliation_event, upsert_trade

        # Include both open and pending_open — pending_open trades have live orders in flight
        open_trades = get_trades(status="open", path=self.db_path) + \
                      get_trades(status="pending_open", path=self.db_path)

        # Build the full set of OCC symbols managed by open/pending_open DB trades
        managed_symbols: set = set()
        for trade in open_trades:
            ticker = trade.get("ticker", "")
            exp = str(trade.get("expiration", "")).split(" ")[0]
            spread_type = str(trade.get("strategy_type", trade.get("type", ""))).lower()
            if not ticker or not exp:
                continue
            try:
                for sym in self._expected_symbols(trade, ticker, exp, spread_type):
                    managed_symbols.add(sym)
            except Exception:
                pass

        # Fix 4: build the set of failed_open trades and their expected OCC symbols
        # so we can recover mismarked trades instead of creating unmanaged records.
        failed_trades = get_trades(status="failed_open", path=self.db_path)
        failed_trade_by_symbol: Dict[str, Dict] = {}
        for ft in failed_trades:
            ft_ticker = ft.get("ticker", "")
            ft_exp = str(ft.get("expiration", "")).split(" ")[0]
            ft_type = str(ft.get("strategy_type", ft.get("type", ""))).lower()
            if not ft_ticker or not ft_exp:
                continue
            try:
                for sym in self._expected_symbols(ft, ft_ticker, ft_exp, ft_type):
                    failed_trade_by_symbol[sym] = ft
            except Exception:
                pass

        for symbol, pos_data in alpaca_positions.items():
            asset_class = str(pos_data.get("asset_class", "")).lower()
            if "option" not in asset_class:
                continue
            if symbol in managed_symbols:
                continue

            # Fix 4: check if this orphan matches a failed_open trade — recover it
            matched_failed = failed_trade_by_symbol.get(symbol)
            if matched_failed is not None:
                trade_id = matched_failed.get("id", "?")
                logger.warning(
                    "Reconciler: RECOVERY — orphan %s matches failed_open trade %s. "
                    "Promoting failed_open → open.",
                    symbol, trade_id,
                )
                matched_failed["status"] = "open"
                matched_failed.pop("exit_reason", None)
                try:
                    upsert_trade(matched_failed, source="reconciler", path=self.db_path)
                    insert_reconciliation_event(
                        trade_id, "recovered_to_open",
                        {"reason": "orphan_matched_failed_open", "matched_symbol": symbol},
                        self.db_path,
                    )
                    result.pending_resolved += 1
                    # Remove all symbols of this trade from failed_trade_by_symbol
                    # so we don't attempt to recover the same trade twice.
                    for sym in list(failed_trade_by_symbol.keys()):
                        if failed_trade_by_symbol[sym].get("id") == trade_id:
                            del failed_trade_by_symbol[sym]
                except Exception as e:
                    logger.error(
                        "Reconciler: DB write failed for recovery of %s: %s", trade_id, e
                    )
                    result.errors.append(f"recovery_write_fail:{trade_id}")
                continue

            qty = pos_data.get("qty", "?")
            logger.warning(
                "Reconciler: ORPHAN POSITION — %s qty=%s has no DB record. "
                "Creating unmanaged record. Manual review required.",
                symbol, qty,
            )
            orphan_id = f"orphan-{symbol[:20]}"
            orphan_record = {
                "id": orphan_id,
                "ticker": symbol[:3],
                "strategy_type": "unknown",
                "status": "unmanaged",
                "credit": 0.0,
                "contracts": 0,
                "short_strike": 0.0,
                "long_strike": 0.0,
                "expiration": "",
                "entry_date": datetime.now(timezone.utc).isoformat(),
                "alpaca_symbol": symbol,
            }
            try:
                upsert_trade(orphan_record, source="reconciler", path=self.db_path)
            except Exception as e:
                logger.error(
                    "Reconciler: failed to create orphan record for %s: %s", symbol, e
                )
                result.errors.append(f"orphan_write_fail:{symbol}")
            result.orphans_detected += 1

    # ------------------------------------------------------------------
    # Orphan-check throttle helpers (Fix 5)
    # ------------------------------------------------------------------

    def _should_run_orphan_check(self) -> bool:
        """Return True if enough time has elapsed since the last orphan detection run."""
        from shared.database import load_scanner_state
        last_str = load_scanner_state("last_orphan_check", path=self.db_path)
        if not last_str:
            return True
        try:
            last = datetime.fromisoformat(last_str)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60
            return elapsed >= _ORPHAN_CHECK_INTERVAL_MINUTES
        except (ValueError, TypeError):
            return True

    def _save_last_orphan_check(self) -> None:
        """Persist the current timestamp as the last orphan detection run time."""
        from shared.database import save_scanner_state
        try:
            save_scanner_state(
                "last_orphan_check",
                datetime.now(timezone.utc).isoformat(),
                path=self.db_path,
            )
        except Exception as e:
            logger.warning("Reconciler: could not save last_orphan_check timestamp: %s", e)

    # ------------------------------------------------------------------
    # Pending-open resolution
    # ------------------------------------------------------------------

    def _reconcile_pending_opens(self, result: ReconciliationResult) -> None:
        """Resolve all pending_open trades.

        Uses a batch order fetch (handles MLEG orders that fail get_order_by_client_id).
        IC (iron condor) trades use their wing client_order_ids (Fix 1): both wings must
        be filled for the IC to become open; any terminal wing → failed_open.
        Falls back to per-trade get_order_by_client_id for orders not in the batch.
        Only marks failed_open when order is confirmed absent AND older than
        _PENDING_MAX_AGE_HOURS (avoids race conditions on just-submitted orders).
        """
        from shared.database import get_trades, insert_reconciliation_event, upsert_trade

        pending = get_trades(status="pending_open", path=self.db_path)
        if not pending:
            return

        logger.info("Reconciling %d pending_open trade(s)", len(pending))
        now = datetime.now(timezone.utc)

        # Pre-fetch all recent orders indexed by client_order_id (handles MLEG 404 issue)
        orders_by_client_id = self._fetch_recent_orders_by_client_id()

        for trade in pending:
            trade_id = trade.get("id", "?")
            client_order_id = trade.get("alpaca_client_order_id")

            # Case 1: No Alpaca order ID — order was never submitted to the broker
            if not client_order_id:
                if trade.get('dry_run'):
                    status = 'open'
                else:
                    status = 'failed_open'
                trade["status"] = status
                upsert_trade(trade, source="scanner", path=self.db_path)
                insert_reconciliation_event(
                    trade_id, status,
                    {"reason": "no_alpaca_order_id", "dry_run": bool(trade.get('dry_run'))},
                    self.db_path,
                )
                if status == 'open':
                    result.pending_resolved += 1
                    logger.info("Trade %s promoted to open (dry_run)", trade_id)
                else:
                    result.pending_failed += 1
                    logger.warning("Trade %s marked failed_open (no order ID, not dry_run)", trade_id)
                continue

            # Fix 1: IC (iron condor) trades are submitted as two MLEG orders with
            # suffixed client_order_ids ("-put" and "-call").  Reconcile them together.
            spread_type = str(trade.get("strategy_type", trade.get("type", ""))).lower()
            if "condor" in spread_type:
                self._reconcile_ic_pending(trade, orders_by_client_id, result, now)
                continue

            # Case 2: Regular spread — look up from batch first (handles MLEG 404 issue)
            order = orders_by_client_id.get(client_order_id)
            if order is None:
                order = self.alpaca.get_order_by_client_id(client_order_id)

            if order is None:
                # Fix 3: Belt-and-suspenders — check wing suffixes before age-based failure.
                wing_order = (
                    orders_by_client_id.get(client_order_id + "-put")
                    or orders_by_client_id.get(client_order_id + "-call")
                )
                if wing_order is not None:
                    logger.debug(
                        "Trade %s found via IC wing suffix in batch — leaving pending_open "
                        "(will be reconciled as IC on next cycle)",
                        trade_id,
                    )
                    continue

                # Not found via either method — only fail if old enough to rule out race condition
                age_hours = self._trade_age_hours(trade, now)
                if age_hours >= _PENDING_MAX_AGE_HOURS:
                    trade["status"] = "failed_open"
                    trade["exit_reason"] = "alpaca_order_not_found"
                    upsert_trade(trade, source="reconciler", path=self.db_path)
                    insert_reconciliation_event(
                        trade_id, "failed_open",
                        {"reason": "order_not_found", "age_hours": age_hours},
                        self.db_path,
                    )
                    result.pending_failed += 1
                    logger.warning(
                        "Trade %s marked failed_open (order not found, %.1fh old)", trade_id, age_hours
                    )
                else:
                    logger.debug(
                        "Trade %s not found in Alpaca yet (%.1fh old) — leaving pending_open",
                        trade_id, age_hours,
                    )
                continue

            order_status = order.get("status", "")

            if order_status == "filled":
                trade["status"] = "open"
                trade["alpaca_status"] = "filled"
                fill_price = order.get("filled_avg_price")
                if fill_price:
                    trade["alpaca_fill_price"] = fill_price
                upsert_trade(trade, source="scanner", path=self.db_path)
                insert_reconciliation_event(
                    trade_id, "confirmed_filled",
                    {"fill_price": fill_price, "alpaca_order_id": order.get("id")},
                    self.db_path,
                )
                result.pending_resolved += 1
                logger.info(
                    "Trade %s confirmed filled (fill_price=%s)", trade_id, fill_price
                )

            elif order_status in _TERMINAL_ORDER_STATES:
                trade["status"] = "failed_open"
                trade["exit_reason"] = f"alpaca_{order_status}"
                trade["alpaca_status"] = order_status
                upsert_trade(trade, source="reconciler", path=self.db_path)
                insert_reconciliation_event(
                    trade_id, "failed_open",
                    {"order_status": order_status, "alpaca_order_id": order.get("id")},
                    self.db_path,
                )
                result.pending_failed += 1
                logger.warning(
                    "Trade %s marked failed_open (order %s)", trade_id, order_status
                )

            else:
                # Order still live (submitted, pending_new, partially_filled, etc.)
                age_hours = self._trade_age_hours(trade, now)
                logger.debug(
                    "Trade %s order status=%s (%.1fh old) — leaving as pending_open",
                    trade_id, order_status, age_hours,
                )

    def _reconcile_ic_pending(
        self,
        trade: Dict,
        orders_by_client_id: Dict,
        result: "ReconciliationResult",
        now: datetime,
    ) -> None:
        """Reconcile a single pending_open iron condor trade.

        ICs are submitted as two MLEG orders: client_id + "-put" and client_id + "-call".
        Both wings must be filled for the IC to become open.
        Any wing in a terminal failure state → failed_open.
        If neither wing is found and the trade is old enough → failed_open.
        """
        from shared.database import insert_reconciliation_event, upsert_trade

        trade_id = trade.get("id", "?")
        client_order_id = trade.get("alpaca_client_order_id", trade_id)

        # Use stored wing IDs if available (Fix 1); derive from bare client_id as fallback
        put_cid = trade.get("alpaca_put_order_id") or (client_order_id + "-put")
        call_cid = trade.get("alpaca_call_order_id") or (client_order_id + "-call")

        put_order = orders_by_client_id.get(put_cid)
        call_order = orders_by_client_id.get(call_cid)

        # Fall back to per-trade lookup if not in batch (may still return None for MLEG)
        if put_order is None:
            put_order = self.alpaca.get_order_by_client_id(put_cid)
        if call_order is None:
            call_order = self.alpaca.get_order_by_client_id(call_cid)

        put_status = put_order.get("status", "") if put_order else None
        call_status = call_order.get("status", "") if call_order else None

        # Both wings filled → promote to open
        if put_status == "filled" and call_status == "filled":
            trade["status"] = "open"
            trade["alpaca_status"] = "filled"
            put_fill = put_order.get("filled_avg_price")
            call_fill = call_order.get("filled_avg_price")
            if put_fill and call_fill:
                try:
                    trade["alpaca_fill_price"] = float(put_fill) + float(call_fill)
                except (ValueError, TypeError):
                    pass
            upsert_trade(trade, source="scanner", path=self.db_path)
            insert_reconciliation_event(
                trade_id, "confirmed_filled",
                {"ic_put_fill": put_fill, "ic_call_fill": call_fill},
                self.db_path,
            )
            result.pending_resolved += 1
            logger.info("IC trade %s confirmed filled (put=%s call=%s)", trade_id, put_fill, call_fill)
            return

        # Any wing in terminal failure state → failed_open
        if put_status in _TERMINAL_ORDER_STATES or call_status in _TERMINAL_ORDER_STATES:
            failed_wing = "put" if put_status in _TERMINAL_ORDER_STATES else "call"
            failed_status = put_status if put_status in _TERMINAL_ORDER_STATES else call_status
            trade["status"] = "failed_open"
            trade["exit_reason"] = f"ic_{failed_wing}_alpaca_{failed_status}"
            trade["alpaca_status"] = failed_status
            upsert_trade(trade, source="reconciler", path=self.db_path)
            insert_reconciliation_event(
                trade_id, "failed_open",
                {"ic_failed_wing": failed_wing, "order_status": failed_status},
                self.db_path,
            )
            result.pending_failed += 1
            logger.warning(
                "IC trade %s marked failed_open (%s wing: %s)", trade_id, failed_wing, failed_status
            )
            return

        # Neither wing found — only fail if old enough
        if put_order is None and call_order is None:
            age_hours = self._trade_age_hours(trade, now)
            if age_hours >= _PENDING_MAX_AGE_HOURS:
                trade["status"] = "failed_open"
                trade["exit_reason"] = "ic_wings_not_found_in_alpaca"
                upsert_trade(trade, source="reconciler", path=self.db_path)
                insert_reconciliation_event(
                    trade_id, "failed_open",
                    {"reason": "ic_wings_not_found", "age_hours": age_hours},
                    self.db_path,
                )
                result.pending_failed += 1
                logger.warning(
                    "IC trade %s marked failed_open (wings not found, %.1fh old)", trade_id, age_hours
                )
            else:
                logger.debug(
                    "IC trade %s wings not found yet (%.1fh old) — leaving pending_open",
                    trade_id, age_hours,
                )
            return

        # At least one wing found but not in terminal state — still in flight
        age_hours = self._trade_age_hours(trade, now)
        logger.debug(
            "IC trade %s put_status=%s call_status=%s (%.1fh old) — leaving as pending_open",
            trade_id, put_status, call_status, age_hours,
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _expected_symbols(
        self, trade: Dict, ticker: str, exp: str, spread_type: str
    ) -> List[str]:
        """Return the list of OCC symbols expected for this trade's legs."""
        syms = []
        if "condor" in spread_type:
            legs = [
                (trade.get("put_short_strike") or trade.get("short_strike"), "put"),
                (trade.get("put_long_strike") or trade.get("long_strike"), "put"),
                (trade.get("call_short_strike"), "call"),
                (trade.get("call_long_strike"), "call"),
            ]
        else:
            opt_type = "call" if "call" in spread_type else "put"
            legs = [
                (trade.get("short_strike"), opt_type),
                (trade.get("long_strike"), opt_type),
            ]
        for strike, ot in legs:
            if strike:
                syms.append(self.alpaca._build_occ_symbol(ticker, exp, strike, ot))
        return syms

    @staticmethod
    def _entry_commission(
        contracts: int,
        num_legs: int,
        commission_per_contract: float = _DEFAULT_COMMISSION_PER_CONTRACT,
    ) -> float:
        """Entry-side only commission (no close order for externally-closed positions)."""
        return commission_per_contract * contracts * num_legs

    def _fetch_recent_orders_by_client_id(self) -> dict:
        """Batch-fetch recent Alpaca orders and index them by client_order_id.

        get_order_by_client_id() returns 404 for MLEG (multi-leg) orders — a known
        Alpaca paper trading limitation. get_orders() returns them correctly.
        We pre-fetch all recent orders once and use that dict for all lookups,
        which also reduces per-trade API calls.
        """
        try:
            orders = self.alpaca.get_orders(status="all", limit=500)
            return {o["client_order_id"]: o for o in orders if o.get("client_order_id")}
        except Exception as e:
            logger.warning("Reconciler: could not batch-fetch orders (%s) — will use per-trade lookup", e)
            return {}

    @staticmethod
    def _trade_age_hours(trade: Dict, now: datetime) -> float:
        """Return how many hours old a trade is based on its entry_date."""
        entry_str = trade.get("entry_date") or trade.get("created_at", "")
        try:
            entry_time = datetime.fromisoformat(entry_str)
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            return (now - entry_time).total_seconds() / 3600
        except (ValueError, TypeError):
            return 99.0  # unknown age → treat as old
