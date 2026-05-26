"""
Central alert routing pipeline.

Pipeline stages:
  1. Convert opportunity dicts → Alert objects
  2. Deduplicate cross-scan (same ticker+direction within 30 min, persisted across restarts)
  3. Position sizing  ← moved before risk gate so risk_pct reflects real sized risk
  4. Update alert.risk_pct from sizing result (BUG #7 fix)
  5. Risk-gate check (now sees real risk_pct)
  6. Prioritize (by type priority, then score)
  7. Dispatch loop — each alert checked for within-scan dedup before processing:
       a. Within-scan dedup re-check (prevents multiple same-ticker/direction orders
          in one scan when Step 2 ran before any were marked — FREQ BUG fix)
       b. Telegram alert
       c. SQLite persistence
       d. Execute via ExecutionEngine (optional)
       e. Mark dedup ledger AFTER successful execution (BUG #15 fix)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from alerts.alert_position_sizer import AlertPositionSizer
from alerts.alert_schema import Alert, AlertType
from alerts.formatters.telegram import TelegramAlertFormatter
from compass.risk_gate import RiskGate
from shared.database import delete_old_dedup_entries, insert_alert, load_dedup_entries, upsert_dedup_entry

logger = logging.getLogger(__name__)

# Type priority order (lower index = higher priority)
_TYPE_PRIORITY = {
    AlertType.credit_spread: 0,
    AlertType.iron_condor: 1,
    AlertType.straddle_strangle: 2,
    AlertType.momentum_swing: 3,
    AlertType.earnings_play: 4,
    AlertType.gamma_lotto: 5,
}

# Dedup window in seconds
_DEDUP_WINDOW = 30 * 60  # 30 minutes


class AlertRouter:
    """Central pipeline: validate → dedup → risk-check → size → prioritize → dispatch → execute."""

    def __init__(
        self,
        risk_gate: RiskGate,
        position_sizer: AlertPositionSizer,
        telegram_bot,
        formatter: TelegramAlertFormatter,
        execution_engine=None,
        config: Optional[Dict] = None,
    ):
        self.risk_gate = risk_gate
        self.position_sizer = position_sizer
        self.telegram_bot = telegram_bot
        self.formatter = formatter
        self.execution_engine = execution_engine  # None = alert-only mode
        self.config = config or {}

        # In-memory dedup ledger: (ticker, direction, alert_type) → last_routed_at
        # Populated from SQLite on startup so restarts don't lose dedup state (BUG #17).
        self._dedup_ledger: Dict[tuple, datetime] = {}
        self._load_dedup_from_db()

    def route_opportunities(
        self,
        opportunities: List[dict],
        account_state: dict,
        iv_rank: float = 30.0,
    ) -> List[Alert]:
        """Run the full routing pipeline.

        Args:
            opportunities: Raw opportunity dicts from the scanner.
            account_state: Account state dict for risk checks.
            iv_rank: Current IV rank (0–100) for position sizing.

        Returns:
            List of successfully dispatched Alert objects.
        """
        account_value = account_state.get("account_value", 0)
        current_portfolio_risk = sum(
            p.get("risk_pct", 0) * account_value
            for p in account_state.get("open_positions", [])
        )
        weekly_breach = self.risk_gate.weekly_loss_breach(account_state)

        # 1. Convert all valid opportunities.
        # Score gate removed — the backtester has no scoring system; entry is
        # decided solely by regime + credit floor + momentum filter.  The risk
        # gate (rules 1-10) enforces all hard exposure limits downstream.
        alerts: List[Alert] = []
        for opp in opportunities:
            try:
                alert = Alert.from_opportunity(opp)
                alerts.append(alert)
            except Exception as e:
                logger.warning("Failed to convert opportunity %s: %s", opp.get("ticker"), e)

        if not alerts:
            logger.info("AlertRouter: no qualifying opportunities")
            return []

        # 2. Deduplicate — key includes type so IC and straddle don't conflict
        now = datetime.now(timezone.utc)
        deduped: List[Alert] = []
        for alert in alerts:
            key = (alert.ticker, alert.direction.value, alert.type.value)
            last_routed = self._dedup_ledger.get(key)
            if last_routed and (now - last_routed).total_seconds() < _DEDUP_WINDOW:
                logger.debug("AlertRouter: dedup skip %s %s %s", alert.ticker, alert.direction.value, alert.type.value)
                continue
            deduped.append(alert)

        # 3. Size FIRST — so risk gate sees the real position-sized risk_pct (BUG #7 fix).
        #    Pipeline order changed: Size → update risk_pct → Risk gate.
        for alert in deduped:
            try:
                sizing = self.position_sizer.size(
                    alert=alert,
                    account_value=account_value,
                    iv_rank=iv_rank,
                    current_portfolio_risk=current_portfolio_risk,
                    weekly_loss_breach=weekly_breach,
                )
                alert.sizing = sizing
                # Update risk_pct on the alert with the real sized value so that
                # risk gate rules 1 & 2 enforce actual exposure, not the default 2%.
                if sizing and sizing.risk_pct > 0:
                    alert.risk_pct = sizing.risk_pct
            except Exception as e:
                logger.warning("AlertRouter: sizing failed for %s: %s", alert.ticker, e)

        # 4. Risk-check (now has real risk_pct from step 3)
        approved: List[Alert] = []
        for alert in deduped:
            passed, reason = self.risk_gate.check(alert, account_state)
            if passed:
                approved.append(alert)
            else:
                logger.info("AlertRouter: rejected %s — %s", alert.ticker, reason)

        # 5. Prioritize — by type priority then score, take top 5
        approved.sort(
            key=lambda a: (_TYPE_PRIORITY.get(a.type, 99), -a.score)
        )
        top = approved[:5]

        # 6. Dispatch
        # Within-scan dedup: keyed by (ticker, expiration, alert_type) so that two
        # ICs on DIFFERENT expirations are both dispatched (different contracts),
        # while two ICs on the SAME expiration are blocked after the first.
        dispatched: List[Alert] = []
        scan_dispatched_keys: set = set()
        for alert in top:
            # 7a. Within-scan dedup re-check.
            #
            # WHY: Step 2 (batch dedup) checked the ledger before ANY alert in this
            # scan was dispatched, so multiple same-ticker/direction alerts all saw
            # an empty ledger and passed simultaneously.  This per-scan set sees
            # what has been dispatched SO FAR in this scan, so the 2nd/3rd
            # SPY iron_condor on the SAME expiration is caught here.
            #
            # Key includes expiration so that different-expiration ICs on the same
            # ticker are NOT blocked (they are different contracts).
            exp_str = str(alert.legs[0].expiration).split(" ")[0] if alert.legs else ""
            scan_key = (alert.ticker, exp_str, alert.type.value, alert.direction.value)
            if scan_key in scan_dispatched_keys:
                logger.info(
                    "AlertRouter: within-scan dedup blocked %s %s %s exp=%s "
                    "(already dispatched this scan — frequency guard)",
                    alert.ticker, alert.direction.value, alert.type.value, exp_str,
                )
                continue

            try:
                msg = self.formatter.format_entry_alert(alert)
                self.telegram_bot.send_alert(msg)
            except Exception as e:
                logger.warning("AlertRouter: Telegram send failed for %s: %s", alert.ticker, e)

            try:
                insert_alert(alert.to_dict())
            except Exception as e:
                logger.warning("AlertRouter: DB persist failed for %s: %s", alert.ticker, e)

            # 7. Execute — dedup ledger is only marked AFTER successful execution (BUG #15 fix).
            #    If Alpaca rejects the order, the next scan can retry.
            execution_succeeded = True
            if self.execution_engine:
                dte_ok, dte_reason = self._validate_dte(alert)
                if not dte_ok:
                    logger.warning(
                        "AlertRouter: DTE gate blocked execution for %s — %s",
                        alert.ticker, dte_reason,
                    )
                    # DTE-blocked: mark dedup (alert was valid, DTE is deterministic)
                    self._mark_dedup(alert.ticker, alert.direction.value, now, alert.type.value)
                    dispatched.append(alert)
                    continue
                try:
                    opp_dict = self._build_execution_dict(alert)
                    if alert.sizing:
                        opp_dict["contracts"] = alert.sizing.contracts
                    result = self.execution_engine.submit_opportunity(opp_dict)
                    exec_status = result.get("status", "unknown")
                    logger.info(
                        "AlertRouter: execution %s for %s (%s)",
                        exec_status, alert.ticker, result.get("client_order_id", ""),
                    )
                    alert.execution_result = result
                    if exec_status not in ("submitted", "accepted", "pending_new"):
                        execution_succeeded = False
                except Exception as e:
                    logger.error("AlertRouter: execution failed for %s: %s", alert.ticker, e)
                    execution_succeeded = False

            # Only mark dedup when execution actually succeeded (or no engine configured)
            if execution_succeeded:
                scan_dispatched_keys.add(scan_key)
                self._mark_dedup(alert.ticker, alert.direction.value, now, alert.type.value)
            else:
                logger.info(
                    "AlertRouter: skipping dedup mark for %s — execution failed, will retry next scan",
                    alert.ticker,
                )

            dispatched.append(alert)

        logger.info("AlertRouter: dispatched %d alerts", len(dispatched))
        return dispatched

    # ------------------------------------------------------------------
    # Execution dict builder
    # ------------------------------------------------------------------

    def _build_execution_dict(self, alert: Alert) -> dict:
        """Build an execution-ready dict from an Alert for ExecutionEngine.

        Alert.to_dict() (via asdict()) stores expiration/strikes/credit inside
        legs[], not at the top level.  ExecutionEngine.submit_opportunity()
        reads top-level keys: expiration, short_strike, long_strike, credit,
        and type (as spread_type).  This method extracts those fields from legs
        and promotes them to the top level so execution works correctly.
        """
        d = alert.to_dict()
        legs = d.get("legs", [])

        # --- expiration: take from first leg ---
        if not d.get("expiration") and legs:
            d["expiration"] = str(legs[0].get("expiration", "")).split(" ")[0]

        # --- spread_type: map direction → bull_put / bear_call ---
        # Alert.type is "credit_spread", "iron_condor", or "straddle_strangle";
        # execution engine needs specific type strings.
        alert_type_val = d.get("type", "")
        if "condor" not in alert_type_val and "straddle" not in alert_type_val and "strangle" not in alert_type_val:
            d["type"] = "bull_put" if alert.direction.value == "bullish" else "bear_call"

        # --- strikes: extract sell leg → short, buy leg → long ---
        sell_legs = [leg for leg in legs if leg.get("action") == "sell"]
        buy_legs = [leg for leg in legs if leg.get("action") == "buy"]
        if sell_legs and not d.get("short_strike"):
            d["short_strike"] = sell_legs[0].get("strike", 0)
        if buy_legs and not d.get("long_strike"):
            d["long_strike"] = buy_legs[0].get("strike", 0)

        # --- credit: entry_price is the credit received ---
        if not d.get("credit"):
            d["credit"] = d.get("entry_price", 0)

        # --- iron condor: extract per-wing strikes from legs ---
        if "condor" in alert_type_val:
            put_legs = [leg for leg in legs if leg.get("option_type") == "put"]
            call_legs = [leg for leg in legs if leg.get("option_type") == "call"]
            ps = next((leg for leg in put_legs if leg.get("action") == "sell"), None)
            pb = next((leg for leg in put_legs if leg.get("action") == "buy"), None)
            cs = next((leg for leg in call_legs if leg.get("action") == "sell"), None)
            cb = next((leg for leg in call_legs if leg.get("action") == "buy"), None)
            if ps:
                d["put_short_strike"] = ps["strike"]
            if pb:
                d["put_long_strike"] = pb["strike"]
            if cs:
                d["call_short_strike"] = cs["strike"]
            if cb:
                d["call_long_strike"] = cb["strike"]

        # --- straddle/strangle: extract call/put strikes from legs ---
        if "straddle" in alert_type_val or "strangle" in alert_type_val:
            call_leg = next((l for l in legs if l.get("option_type") == "call"), None)
            put_leg = next((l for l in legs if l.get("option_type") == "put"), None)
            if call_leg:
                d["call_strike"] = call_leg["strike"]
            if put_leg:
                d["put_strike"] = put_leg["strike"]

        return d

    # ------------------------------------------------------------------
    # Dedup persistence helpers (BUG #17 fix)
    # ------------------------------------------------------------------

    def _load_dedup_from_db(self) -> None:
        """Load recent dedup entries from SQLite on startup."""
        try:
            db_path = os.environ.get("ATTIX_DB_PATH")
            delete_old_dedup_entries(window_seconds=_DEDUP_WINDOW, path=db_path)
            entries = load_dedup_entries(window_seconds=_DEDUP_WINDOW, path=db_path)
            for entry in entries:
                key = (entry["ticker"], entry["direction"], entry["alert_type"])
                try:
                    ts = datetime.fromisoformat(entry["last_routed_at"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    self._dedup_ledger[key] = ts
                except (ValueError, TypeError):
                    pass
            if entries:
                logger.info("AlertRouter: loaded %d dedup entries from DB", len(entries))
        except Exception as e:
            logger.warning("AlertRouter: could not load dedup entries from DB (non-fatal): %s", e)

    def _mark_dedup(self, ticker: str, direction: str, ts: datetime, alert_type: str = "credit_spread") -> None:
        """Mark a (ticker, direction, type) triple as recently routed in memory and DB."""
        key = (ticker, direction, alert_type)
        self._dedup_ledger[key] = ts
        try:
            db_path = os.environ.get("ATTIX_DB_PATH")
            upsert_dedup_entry(ticker, direction, alert_type, ts.isoformat(), path=db_path)
        except Exception as e:
            logger.warning("AlertRouter: could not persist dedup entry (non-fatal): %s", e)

    # ------------------------------------------------------------------
    # DTE validation (defense-in-depth: mirrors backtester min/max DTE)
    # ------------------------------------------------------------------

    def _validate_dte(self, alert: Alert):
        """Check that the alert's expiration is within the configured DTE window.

        Returns:
            (True, "") if DTE is acceptable or no config is set.
            (False, reason) if DTE is out of range.
        """
        strategy = self.config.get("strategy", {})
        min_dte = strategy.get("min_dte")
        max_dte = strategy.get("max_dte")

        if min_dte is None and max_dte is None:
            return True, ""  # no DTE config — pass through

        if not alert.legs:
            return True, ""  # no legs to inspect — pass through

        expiration_str = str(alert.legs[0].expiration).split(" ")[0]
        try:
            from datetime import date
            exp_date = date.fromisoformat(expiration_str)
            dte = (exp_date - date.today()).days
        except (ValueError, TypeError):
            logger.warning("AlertRouter: cannot parse expiration '%s' for DTE check", expiration_str)
            return True, ""  # cannot parse — don't block

        if min_dte is not None and dte < int(min_dte):
            return False, f"DTE={dte} < min_dte={min_dte}"
        if max_dte is not None and dte > int(max_dte):
            return False, f"DTE={dte} > max_dte={max_dte}"

        return True, ""
