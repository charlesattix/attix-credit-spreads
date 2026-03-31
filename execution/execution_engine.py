"""
ExecutionEngine — submits approved alert opportunities as live orders to Alpaca.

Design principles:
- Write to DB FIRST in pending_open state before calling Alpaca (prevents orphans on crash)
- Deterministic client_order_id for idempotency (safe to replay on restart)
- Returns result dict; never raises — callers decide how to handle errors
- Dry-run mode when alpaca_provider is None (alert-only mode)
"""

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from shared.database import get_trade_by_id, get_trades, init_db, load_scanner_state, save_scanner_state, upsert_trade

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Submits approved opportunities as live orders to Alpaca.

    Usage::

        engine = ExecutionEngine(alpaca_provider=provider, db_path=None)
        result = engine.submit_opportunity(opp_dict)
        # result['status'] == 'submitted' | 'dry_run' | 'error'
    """

    def __init__(self, alpaca_provider, db_path: Optional[str] = None, config: Optional[Dict] = None):
        """
        Args:
            alpaca_provider: AlpacaProvider instance, or None for dry-run/alert-only mode.
            db_path: Optional override for the SQLite database path.
            config: Application config dict.  Reads ``execution.atomic_ic_execution``
                    (default False).  When True (future: requires Alpaca 4-leg OTO support),
                    iron condors will be submitted as a single atomic order instead of two
                    separate 2-leg orders.  Currently this flag only controls a log warning;
                    the two-order path is always used until Alpaca supports atomic 4-leg ICs.
        """
        self.alpaca = alpaca_provider
        self.db_path = db_path
        self.config = config or {}

        # Derive experiment identity from DB path so client_order_ids are
        # namespaced per experiment.  Two experiments scanning the same
        # strike/expiry on the same day must never share a DB key.
        # e.g. data/pilotai_exp600.db → "exp600", data/pilotai_champion.db → "champion"
        _db = db_path or ""
        _base = os.path.basename(_db).replace("pilotai_", "").replace(".db", "")
        self._exp_id = _base if _base else "unk"
        # PARTIAL #8: atomic_ic_execution flag — reserved for future Alpaca 4-leg OTO support
        self._atomic_ic = bool(
            self.config.get("execution", {}).get("atomic_ic_execution", False)
        )
        self._drawdown_cb_pct = float(
            self.config.get("risk", {}).get("drawdown_cb_pct", 40)
        )
        if self._atomic_ic:
            logger.warning(
                "ExecutionEngine: atomic_ic_execution=True is set but not yet supported "
                "by Alpaca. IC orders will still be submitted as two 2-leg orders. "
                "This flag will activate automatic 4-leg submission once Alpaca adds support."
            )
        init_db(db_path)

    def _check_drawdown_cb(self) -> Optional[str]:
        """Return a human-readable block reason if the drawdown circuit breaker is tripped, else None.

        Mirrors backtester.py: if account equity has dropped more than
        risk.drawdown_cb_pct% from the high-water mark, block new entries.

        CB blocks new entries ONLY — does NOT force-close existing positions.
        Peak equity is persisted to the DB via scanner_state so it survives restarts.

        Returns None when:
        - drawdown_cb_pct is 0 or unset (CB disabled)
        - alpaca is None (dry-run mode — no account data available)
        - account equity fetch fails (fail-open: don't block on API error)
        """
        cb_pct = float(self.config.get("risk", {}).get("drawdown_cb_pct", 0))
        if cb_pct <= 0:
            return None  # CB disabled in config

        if not self.alpaca:
            return None  # dry-run / alert-only mode

        try:
            account = self.alpaca.get_account()
            current_equity = float(account.get("equity") or account.get("portfolio_value") or 0)
        except Exception as e:
            logger.warning(
                "ExecutionEngine: drawdown CB — failed to fetch account equity: %s. "
                "Failing open (not blocking entry).", e,
            )
            return None

        if current_equity <= 0:
            return None  # Cannot compute drawdown without valid equity

        # Load and update persisted high-water mark
        peak_str = load_scanner_state("peak_equity", path=self.db_path)
        peak_equity = float(peak_str) if peak_str else current_equity

        if current_equity > peak_equity:
            peak_equity = current_equity
            save_scanner_state("peak_equity", str(peak_equity), path=self.db_path)

        drawdown_pct = (current_equity - peak_equity) / peak_equity
        threshold = -abs(cb_pct) / 100.0

        if drawdown_pct < threshold:
            logger.critical(
                "ExecutionEngine: DRAWDOWN CIRCUIT BREAKER TRIPPED — "
                "equity=%.2f peak=%.2f drawdown=%.1f%% threshold=%.1f%%. "
                "Blocking new entries. Existing positions continue to be managed.",
                current_equity, peak_equity,
                drawdown_pct * 100, threshold * 100,
            )
            return (
                f"drawdown_cb_tripped: dd={drawdown_pct:.1%} exceeds threshold={threshold:.1%}"
            )

        logger.debug(
            "ExecutionEngine: drawdown CB OK — equity=%.2f peak=%.2f dd=%.1f%%",
            current_equity, peak_equity, drawdown_pct * 100,
        )
        return None

    def submit_opportunity(self, opp: Dict) -> Dict:
        """Submit a single opportunity as a live order.

        Args:
            opp: Opportunity dict from the scanner. Expected keys:
                 ticker, type (bull_put/bear_call/iron_condor), expiration,
                 short_strike, long_strike, credit, contracts.

                 For iron condors, also needs: put_short_strike, put_long_strike,
                 call_short_strike, call_long_strike.

        Returns:
            Dict with keys: status, order_id (if submitted), client_order_id, message.
        """
        ticker = opp.get("ticker", "UNK")
        spread_type = opp.get("type", opp.get("strategy_type", "unknown"))
        expiration = opp.get("expiration", "")
        short_strike = round(float(opp.get("short_strike", 0) or 0), 2)
        long_strike = round(float(opp.get("long_strike", 0) or 0), 2)
        credit = float(opp.get("credit", opp.get("credit_per_spread", 0)) or 0)
        contracts = int(opp.get("contracts", 1))

        # Build deterministic client_order_id.  Experiment ID is included in
        # the hash input so two experiments scanning the same strike/expiry
        # always produce different DB keys — preventing cross-experiment
        # dedup collisions.  Format: cs-{exp_id}-{sha256[:12]}
        raw_id = f"{self._exp_id}-{ticker}-{spread_type}-{expiration}-{short_strike}-{long_strike}"
        client_id = f"cs-{self._exp_id}-" + hashlib.sha256(raw_id.encode()).hexdigest()[:12]

        # Alpaca permanently tracks client_order_id and rejects reuse, even for
        # orders that were rejected or cancelled.  Generate a unique submission
        # tag per scan attempt by appending a millisecond timestamp suffix.
        # client_id (the hash) remains the stable DB key; alpaca_client_id is
        # used exclusively for the actual Alpaca API calls.
        alpaca_client_id = f"{client_id}-{int(time.time() * 1000) % 10_000_000:07d}"

        # Bug #3 fix: defense-in-depth duplicate check before submitting.
        # If dedup layer fails (Bug #2) the same opportunity can arrive again.
        try:
            existing = get_trade_by_id(client_id, path=self.db_path)
            if existing and existing.get("status") not in ("rejected", "cancelled", "failed_open"):
                logger.info(
                    "ExecutionEngine: trade %s already exists (status=%s), skipping duplicate",
                    client_id, existing.get("status"),
                )
                return {"status": "duplicate", "client_order_id": client_id,
                        "message": f"trade already exists with status={existing.get('status')}"}
        except Exception as e:
            logger.debug("ExecutionEngine: duplicate check failed (non-fatal): %s", e)

        # Drawdown circuit breaker — block new orders if equity drawdown exceeds threshold
        if self._check_drawdown_cb():
            logger.warning(
                "ExecutionEngine: DRAWDOWN CIRCUIT BREAKER triggered (>%.0f%%) — "
                "blocking order for %s %s (client_id=%s)",
                self._drawdown_cb_pct, ticker, spread_type, client_id,
            )
            return {
                "status": "drawdown_blocked",
                "client_order_id": client_id,
                "message": f"drawdown circuit breaker triggered (>{self._drawdown_cb_pct}%)",
            }

        # Write to DB FIRST in pending_open state before touching Alpaca
        trade_record = {
            "id": client_id,
            "ticker": ticker,
            "strategy_type": spread_type,
            "status": "pending_open",
            "short_strike": short_strike,
            "long_strike": long_strike,
            "expiration": str(expiration),
            "credit": credit,
            "contracts": contracts,
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "alpaca_client_order_id": alpaca_client_id,
        }
        # For iron condors, preserve per-wing strikes in metadata so PositionMonitor
        # can build OCC symbols for all 4 legs when pricing and closing.
        # Wing order IDs use alpaca_client_id (not client_id) so they are unique
        # to this submission attempt and won't collide if the trade is retried.
        spread_lower = spread_type.lower()
        if "condor" in spread_lower:
            trade_record["put_short_strike"] = round(float(opp.get("put_short_strike", short_strike) or short_strike), 2)
            trade_record["put_long_strike"] = round(float(opp.get("put_long_strike", long_strike) or long_strike), 2)
            trade_record["call_short_strike"] = round(float(opp.get("call_short_strike", short_strike) or short_strike), 2)
            trade_record["call_long_strike"] = round(float(opp.get("call_long_strike", long_strike) or long_strike), 2)
            trade_record["alpaca_put_order_id"] = alpaca_client_id + "-put"
            trade_record["alpaca_call_order_id"] = alpaca_client_id + "-call"
        elif "straddle" in spread_lower or "strangle" in spread_lower:
            trade_record["call_strike"] = round(float(opp.get("call_strike", 0) or 0), 2)
            trade_record["put_strike"] = round(float(opp.get("put_strike", 0) or 0), 2)
            trade_record["is_debit"] = opp.get("is_debit", False)
        try:
            upsert_trade(trade_record, source="execution", path=self.db_path)
        except Exception as e:
            logger.error("ExecutionEngine: DB write failed for %s: %s", client_id, e)
            return {"status": "error", "message": f"DB write failed: {e}", "client_order_id": client_id}

        # ML-1: Log trade features for future ML training
        try:
            from shared.feature_logger import FeatureLogger, _extract_features_from_opportunity
            fl = FeatureLogger(db_path=self.db_path)
            features = _extract_features_from_opportunity(opp)
            fl.log_entry(client_id, features)
        except Exception as e:
            logger.warning("ExecutionEngine: feature logging failed for %s (non-fatal): %s", client_id, e)

        # Dry-run mode: no Alpaca provider configured
        if not self.alpaca:
            if "straddle" in spread_lower or "strangle" in spread_lower:
                is_debit = opp.get("is_debit", False) or credit < 0
                direction = "DEBIT" if is_debit else "CREDIT"
                event_type = opp.get("event_type", opp.get("metadata", {}).get("event_type", "unknown"))
                logger.info(
                    "ExecutionEngine [DRY RUN]: would submit %s %s x%d | "
                    "call=$%.2f put=$%.2f | %s $%.2f | event=%s (client_id=%s)",
                    ticker, spread_type, contracts,
                    round(float(opp.get("call_strike", 0) or 0), 2),
                    round(float(opp.get("put_strike", 0) or 0), 2),
                    direction, abs(credit), event_type, client_id,
                )
            else:
                logger.info(
                    "ExecutionEngine [DRY RUN]: would submit %s %s x%d @ %.2f credit (client_id=%s)",
                    ticker, spread_type, contracts, credit, client_id,
                )
            return {"status": "dry_run", "client_order_id": client_id, "message": "alpaca not configured"}

        # Market hours guard — check Alpaca clock before submitting any order
        clock = self.alpaca.get_market_clock()
        is_open = clock.get("is_open")
        if is_open is False:
            logger.warning(
                "ExecutionEngine: market is CLOSED — blocking order for %s %s (client_id=%s). "
                "next_open=%s",
                ticker, spread_type, client_id, clock.get("next_open"),
            )
            return {
                "status": "market_closed",
                "client_order_id": client_id,
                "message": f"market closed; next_open={clock.get('next_open')}",
            }
        # is_open=None means clock check failed — fail open (don't block)

        # Drawdown circuit breaker: block new entries if equity drops below threshold
        cb_reason = self._check_drawdown_cb()
        if cb_reason:
            logger.warning(
                "ExecutionEngine: entry BLOCKED by drawdown CB for %s %s: %s",
                ticker, spread_type, cb_reason,
            )
            return {"status": "drawdown_cb_tripped", "message": cb_reason, "client_order_id": client_id}

        # Submit to Alpaca
        try:
            if "condor" in spread_type.lower():
                result = self._submit_iron_condor(opp, contracts, credit, alpaca_client_id)
            elif "straddle" in spread_type.lower() or "strangle" in spread_type.lower():
                result = self._submit_straddle(opp, contracts, credit, alpaca_client_id)
            else:
                result = self.alpaca.submit_credit_spread(
                    ticker=ticker,
                    short_strike=short_strike,
                    long_strike=long_strike,
                    expiration=str(expiration).split(" ")[0] if expiration else "",
                    spread_type=spread_type,
                    contracts=contracts,
                    limit_price=credit if credit > 0 else None,
                    client_order_id=alpaca_client_id,
                )

            if result.get("status") == "submitted":
                logger.info(
                    "ExecutionEngine: submitted %s %s x%d order_id=%s",
                    ticker, spread_type, contracts, result.get("order_id"),
                )
                # If Alpaca substituted a different expiration, update the DB record to match
                actual_exp = result.get("actual_expiration")
                if actual_exp and actual_exp != str(expiration).split(" ")[0]:
                    logger.warning(
                        "ExecutionEngine: expiration substituted for %s: %s → %s; updating DB",
                        client_id, expiration, actual_exp,
                    )
                    try:
                        upsert_trade(
                            {"id": client_id, "expiration": actual_exp},
                            source="execution",
                            path=self.db_path,
                        )
                    except Exception as db_err:
                        logger.error(
                            "ExecutionEngine: DB expiration update failed for %s: %s", client_id, db_err
                        )
            else:
                logger.warning(
                    "ExecutionEngine: Alpaca returned non-submitted status for %s: %s",
                    client_id, result,
                )
                try:
                    upsert_trade(
                        {
                            "id": client_id,
                            "status": "failed_open",
                            "exit_reason": f"alpaca_rejected: {result.get('message', result.get('status', 'unknown'))}",
                        },
                        source="execution",
                        path=self.db_path,
                    )
                except Exception as db_err:
                    logger.error(
                        "ExecutionEngine: DB update to failed_open failed for %s: %s", client_id, db_err
                    )

            result["client_order_id"] = client_id
            return result

        except Exception as e:
            logger.error("ExecutionEngine: Alpaca submission failed for %s: %s", client_id, e, exc_info=True)
            try:
                upsert_trade(
                    {
                        "id": client_id,
                        "status": "failed_open",
                        "exit_reason": f"alpaca_exception: {e}",
                    },
                    source="execution",
                    path=self.db_path,
                )
            except Exception as db_err:
                logger.error(
                    "ExecutionEngine: DB update to failed_open failed for %s: %s", client_id, db_err
                )
            return {"status": "error", "message": str(e), "client_order_id": client_id}

    def _check_drawdown_cb(self) -> bool:
        """Return True if account drawdown exceeds the configured threshold.

        Computes realised P&L from closed trades, compares cumulative equity
        to peak equity. If drawdown percentage > ``risk.drawdown_cb_pct``
        (default 40%), returns True to block new orders.
        """
        try:
            starting_capital = float(
                self.config.get("risk", {}).get("account_size", 100_000)
            )
            closed = (
                get_trades(status="closed_profit", path=self.db_path)
                + get_trades(status="closed_loss", path=self.db_path)
            )
            total_pnl = sum(float(t.get("pnl") or 0) for t in closed)
            current_equity = starting_capital + total_pnl
            peak_equity = max(starting_capital, current_equity)

            # Track running peak across calls
            if not hasattr(self, "_peak_equity"):
                self._peak_equity = peak_equity
            else:
                self._peak_equity = max(self._peak_equity, current_equity)

            if self._peak_equity > 0:
                drawdown_pct = (self._peak_equity - current_equity) / self._peak_equity * 100
            else:
                drawdown_pct = 0.0

            if drawdown_pct > self._drawdown_cb_pct:
                logger.warning(
                    "ExecutionEngine: drawdown=%.1f%% (peak=$%.0f current=$%.0f) > threshold=%.0f%%",
                    drawdown_pct, self._peak_equity, current_equity, self._drawdown_cb_pct,
                )
                return True
        except Exception as e:
            logger.debug("ExecutionEngine: drawdown CB check failed (non-fatal): %s", e)
        return False

    def _submit_iron_condor(self, opp: Dict, contracts: int, credit: float, client_id: str) -> Dict:
        """Submit iron condor as two separate MLEG orders (put wing + call wing).

        Alpaca supports multi-leg but iron condors may need to be submitted as
        two 2-leg spreads. Submit put side first, then call side.
        """
        ticker = opp.get("ticker", "UNK")
        expiration = str(opp.get("expiration", "")).split(" ")[0]

        put_short = round(float(opp.get("put_short_strike") or opp.get("short_strike", 0)), 2)
        put_long = round(float(opp.get("put_long_strike") or opp.get("long_strike", 0)), 2)
        call_short = round(float(opp.get("call_short_strike") or opp.get("short_strike", 0)), 2)
        call_long = round(float(opp.get("call_long_strike") or opp.get("long_strike", 0)), 2)

        # Split credit approximately 50/50 between wings
        put_credit = credit / 2 if credit > 0 else None
        call_credit = credit / 2 if credit > 0 else None

        put_result = self.alpaca.submit_credit_spread(
            ticker=ticker, short_strike=put_short, long_strike=put_long,
            expiration=expiration, spread_type="bull_put",
            contracts=contracts, limit_price=put_credit,
            client_order_id=client_id + "-put",
        )

        if put_result.get("status") != "submitted":
            logger.error(
                "ExecutionEngine: put wing failed for IC %s — skipping call wing. put_result=%s",
                client_id, put_result,
            )
            return {"status": "partial_error", "put_result": put_result, "call_result": None}

        call_result = self.alpaca.submit_credit_spread(
            ticker=ticker, short_strike=call_short, long_strike=call_long,
            expiration=expiration, spread_type="bear_call",
            contracts=contracts, limit_price=call_credit,
            client_order_id=client_id + "-call",
        )

        if call_result.get("status") != "submitted":
            # Put wing is live — attempt to cancel it to avoid a naked position
            put_order_id = put_result.get("order_id")
            logger.error(
                "ExecutionEngine: call wing failed for IC %s — attempting to cancel put wing order_id=%s. call_result=%s",
                client_id, put_order_id, call_result,
            )
            if put_order_id:
                try:
                    self.alpaca.cancel_order(put_order_id)
                    logger.info("ExecutionEngine: put wing cancel requested for order_id=%s", put_order_id)
                except Exception as cancel_err:
                    logger.error(
                        "ExecutionEngine: CRITICAL — put wing cancel FAILED for order_id=%s: %s. Manual intervention required.",
                        put_order_id, cancel_err,
                    )
            return {"status": "partial_error", "put_result": put_result, "call_result": call_result}

        return {
            "status": "submitted",
            "order_id": put_result.get("order_id"),
            "call_order_id": call_result.get("order_id"),
        }

    def _submit_straddle(self, opp: Dict, contracts: int, credit: float, client_id: str) -> Dict:
        """Submit straddle/strangle as two single-leg orders (call + put).

        For long positions (debit): buy-to-open both legs, limit_price is max per leg.
        For short positions (credit): sell-to-open both legs, limit_price is min per leg.
        Same rollback pattern as IC: if second leg fails, cancel first.
        """
        ticker = opp.get("ticker", "UNK")
        expiration = str(opp.get("expiration", "")).split(" ")[0]
        spread_type = opp.get("type", opp.get("strategy_type", "short_straddle"))
        call_strike = round(float(opp.get("call_strike", 0) or 0), 2)
        put_strike = round(float(opp.get("put_strike", 0) or 0), 2)
        is_long = spread_type.startswith("long_")
        is_debit = opp.get("is_debit", is_long)

        # Determine order sides: long=buy-to-open, short=sell-to-open
        if is_long:
            call_side, put_side = "buy", "buy"
        else:
            call_side, put_side = "sell", "sell"

        # Per-leg limit price: split total credit/debit evenly between legs.
        # For buy orders: limit_price = max we'll pay per contract.
        # For sell orders: limit_price = min we'll accept per contract.
        # abs() ensures positive regardless of credit (positive) or debit (negative).
        per_leg_limit = round(abs(credit / 2), 2) if credit else None

        direction_label = "DEBIT (buy-to-open)" if is_debit else "CREDIT (sell-to-open)"
        logger.info(
            "ExecutionEngine: submitting straddle %s | %s | call=$%.2f put=$%.2f "
            "x%d | %s | per_leg_limit=%s",
            client_id, spread_type, call_strike, put_strike,
            contracts, direction_label, per_leg_limit,
        )

        # Submit call leg
        call_result = self.alpaca.submit_single_leg(
            ticker=ticker,
            strike=call_strike,
            expiration=expiration,
            option_type="call",
            side=call_side,
            contracts=contracts,
            limit_price=per_leg_limit,
            client_order_id=client_id + "-call",
        )

        if call_result.get("status") != "submitted":
            logger.error(
                "ExecutionEngine: call leg failed for straddle %s: %s",
                client_id, call_result,
            )
            return {"status": "partial_error", "call_result": call_result, "put_result": None}

        # Submit put leg
        put_result = self.alpaca.submit_single_leg(
            ticker=ticker,
            strike=put_strike,
            expiration=expiration,
            option_type="put",
            side=put_side,
            contracts=contracts,
            limit_price=per_leg_limit,
            client_order_id=client_id + "-put",
        )

        if put_result.get("status") != "submitted":
            call_order_id = call_result.get("order_id")
            logger.error(
                "ExecutionEngine: put leg failed for straddle %s — "
                "attempting to cancel call leg order_id=%s: %s",
                client_id, call_order_id, put_result,
            )
            if call_order_id:
                cancel_succeeded = self._cancel_with_retry(
                    call_order_id,
                    context=f"straddle call-leg rollback ({ticker}/{client_id})",
                )
                if not cancel_succeeded:
                    # Mark the trade for manual review in DB so ops can see it
                    try:
                        upsert_trade(
                            {
                                "id": client_id,
                                "ticker": ticker,
                                "status": "partial_fill_manual_review",
                                "exit_reason": "straddle_partial_fill_cancel_failed",
                            },
                            source="execution",
                            path=self.db_path,
                        )
                    except Exception as db_err:
                        logger.error(
                            "ExecutionEngine: DB mark for manual review failed (%s): %s",
                            client_id, db_err,
                        )
            return {"status": "partial_error", "call_result": call_result, "put_result": put_result}

        return {
            "status": "submitted",
            "order_id": call_result.get("order_id"),
            "put_order_id": put_result.get("order_id"),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cancel_with_retry(
        self,
        order_id: str,
        context: str = "",
        max_attempts: int = 3,
        backoff_base: float = 2.0,
    ) -> bool:
        """Attempt to cancel *order_id* up to *max_attempts* times with exponential backoff.

        Returns True if the cancel succeeded, False if all attempts failed.
        On total failure, sends a CRITICAL Telegram alert so ops can intervene.
        """
        for attempt in range(1, max_attempts + 1):
            try:
                self.alpaca.cancel_order(order_id)
                logger.info(
                    "ExecutionEngine: cancel succeeded for order_id=%s (attempt %d/%d) [%s]",
                    order_id, attempt, max_attempts, context,
                )
                return True
            except Exception as err:
                logger.warning(
                    "ExecutionEngine: cancel attempt %d/%d failed for order_id=%s [%s]: %s",
                    attempt, max_attempts, order_id, context, err,
                )
                if attempt < max_attempts:
                    time.sleep(backoff_base ** attempt)  # 2s, 4s

        # All attempts exhausted — send CRITICAL alert
        logger.critical(
            "ExecutionEngine: CRITICAL — cancel FAILED after %d attempts "
            "for order_id=%s [%s]. Manual intervention required.",
            max_attempts, order_id, context,
        )
        try:
            from shared.telegram_alerts import notify_api_failure
            notify_api_failure(
                error_msg=f"cancel_order failed after {max_attempts} retries (order_id={order_id})",
                context=context,
            )
        except Exception as alert_err:
            logger.error("ExecutionEngine: Telegram alert for cancel failure failed: %s", alert_err)
        return False
