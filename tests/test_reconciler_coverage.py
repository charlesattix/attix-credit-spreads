"""
Tests to raise shared/reconciler.py coverage from ~36% to 70%+.

Covers: ReconciliationResult, tier methods (reconcile_tier2, reconcile_eod,
reconcile_morning, reconcile), activity-based close detection,
_compute_external_close_pnl, _process_expirations, _reconcile_open_positions,
scheduling helpers, _entry_commission, _trade_age_hours, error paths.

All tests use mock Alpaca providers and in-memory SQLite — no real API calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from shared.database import (
    get_trades,
    init_db,
    load_scanner_state,
    save_scanner_state,
    upsert_trade,
)
from shared.reconciler import (
    PositionReconciler,
    ReconciliationResult,
    _DEFAULT_COMMISSION_PER_CONTRACT,
    _PENDING_MAX_AGE_HOURS,
    _TERMINAL_ORDER_STATES,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _db(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def _mock_alpaca(
    positions=None,
    batch_orders=None,
    activities=None,
    per_order=None,
):
    """Create a mock Alpaca provider with configurable responses."""
    alpaca = MagicMock()
    alpaca.get_positions.return_value = positions or []
    alpaca.get_orders.return_value = batch_orders or []
    alpaca.get_order_by_client_id.return_value = per_order
    alpaca.get_account_activities.return_value = activities or []

    def _build_occ(ticker, exp, strike, opt_type):
        dt = exp.replace("-", "")
        strike_int = round(strike * 1000)
        return f"{ticker:<6}{dt}{opt_type[0].upper()}{strike_int:08d}"

    alpaca._build_occ_symbol.side_effect = _build_occ
    return alpaca


def _open_trade(db_path, trade_id="cs-open-001", ticker="SPY",
                credit=1.50, contracts=2, expiration="2026-05-16",
                entry_date=None, spread_type="bull_put",
                short_strike=540.0, long_strike=535.0):
    """Insert an open trade."""
    t = {
        "id": trade_id,
        "ticker": ticker,
        "strategy_type": spread_type,
        "status": "open",
        "short_strike": short_strike,
        "long_strike": long_strike,
        "expiration": expiration,
        "credit": credit,
        "contracts": contracts,
        "entry_date": entry_date or datetime.now(timezone.utc).isoformat(),
    }
    upsert_trade(t, source="test", path=db_path)
    return t


def _pending_trade(db_path, trade_id="cs-pend-001", entry_date=None,
                   client_order_id=None, dry_run=False):
    """Insert a pending_open trade."""
    t = {
        "id": trade_id,
        "ticker": "SPY",
        "strategy_type": "bull_put",
        "status": "pending_open",
        "short_strike": 540.0,
        "long_strike": 535.0,
        "expiration": "2026-05-16",
        "credit": 1.50,
        "contracts": 1,
        "entry_date": entry_date or datetime.now(timezone.utc).isoformat(),
        "alpaca_client_order_id": client_order_id or trade_id,
    }
    if dry_run:
        t["dry_run"] = True
    upsert_trade(t, source="test", path=db_path)
    return t


# ─────────────────────────────────────────────────────────────────────────────
# ReconciliationResult
# ─────────────────────────────────────────────────────────────────────────────

class TestReconciliationResult:

    def test_empty_is_falsy(self):
        r = ReconciliationResult()
        assert not r
        assert repr(r) == "ReconciliationResult(nothing)"

    def test_resolved_is_truthy(self):
        r = ReconciliationResult()
        r.pending_resolved = 1
        assert r
        assert "resolved=1" in repr(r)

    def test_failed_is_truthy(self):
        r = ReconciliationResult()
        r.pending_failed = 2
        assert r
        assert "failed=2" in repr(r)

    def test_phantom_repr(self):
        r = ReconciliationResult()
        r.phantom_resolved = 3
        assert "phantoms=3" in repr(r)

    def test_orphan_repr(self):
        r = ReconciliationResult()
        r.orphans_detected = 1
        assert "orphans=1" in repr(r)

    def test_externally_closed_repr(self):
        r = ReconciliationResult()
        r.externally_closed = 2
        assert "ext_closed=2" in repr(r)

    def test_expirations_repr(self):
        r = ReconciliationResult()
        r.expirations_processed = 4
        assert "expirations=4" in repr(r)

    def test_activities_repr(self):
        r = ReconciliationResult()
        r.activities_processed = 5
        assert "activities=5" in repr(r)

    def test_errors_repr(self):
        r = ReconciliationResult()
        r.errors.append("test_error")
        assert r
        assert "errors=1" in repr(r)

    def test_combined_repr(self):
        r = ReconciliationResult()
        r.pending_resolved = 1
        r.orphans_detected = 2
        r.errors.append("e1")
        text = repr(r)
        assert "resolved=1" in text
        assert "orphans=2" in text
        assert "errors=1" in text


# ─────────────────────────────────────────────────────────────────────────────
# _entry_commission
# ─────────────────────────────────────────────────────────────────────────────

class TestEntryCommission:

    def test_default_commission(self):
        c = PositionReconciler._entry_commission(1, 2)
        assert c == _DEFAULT_COMMISSION_PER_CONTRACT * 1 * 2

    def test_custom_commission(self):
        c = PositionReconciler._entry_commission(3, 4, commission_per_contract=1.00)
        assert c == 12.0  # 1.00 * 3 * 4

    def test_iron_condor_4_legs(self):
        c = PositionReconciler._entry_commission(2, 4)
        assert c == _DEFAULT_COMMISSION_PER_CONTRACT * 2 * 4

    def test_zero_contracts(self):
        c = PositionReconciler._entry_commission(0, 2)
        assert c == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# _trade_age_hours
# ─────────────────────────────────────────────────────────────────────────────

class TestTradeAgeHours:

    def test_recent_trade(self):
        now = datetime.now(timezone.utc)
        trade = {"entry_date": (now - timedelta(hours=2)).isoformat()}
        age = PositionReconciler._trade_age_hours(trade, now)
        assert abs(age - 2.0) < 0.1

    def test_old_trade(self):
        now = datetime.now(timezone.utc)
        trade = {"entry_date": (now - timedelta(hours=10)).isoformat()}
        age = PositionReconciler._trade_age_hours(trade, now)
        assert abs(age - 10.0) < 0.1

    def test_missing_entry_date_returns_99(self):
        now = datetime.now(timezone.utc)
        trade = {}
        age = PositionReconciler._trade_age_hours(trade, now)
        assert age == 99.0

    def test_malformed_entry_date_returns_99(self):
        now = datetime.now(timezone.utc)
        trade = {"entry_date": "not-a-date"}
        age = PositionReconciler._trade_age_hours(trade, now)
        assert age == 99.0

    def test_naive_datetime_treated_as_utc(self):
        now = datetime.now(timezone.utc)
        trade = {"entry_date": (now - timedelta(hours=3)).replace(tzinfo=None).isoformat()}
        age = PositionReconciler._trade_age_hours(trade, now)
        assert abs(age - 3.0) < 0.1

    def test_created_at_fallback(self):
        now = datetime.now(timezone.utc)
        trade = {"created_at": (now - timedelta(hours=5)).isoformat()}
        age = PositionReconciler._trade_age_hours(trade, now)
        assert abs(age - 5.0) < 0.1


# ─────────────────────────────────────────────────────────────────────────────
# _compute_external_close_pnl
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeExternalClosePnl:

    def setup_method(self):
        self.reconciler = PositionReconciler(alpaca=MagicMock())

    def test_expired_worthless_credit_spread(self):
        """Credit spread expired OTM — keep the full credit."""
        trade = {"credit": 1.50, "contracts": 2, "strategy_type": "bull_put"}
        activities = [
            {"activity_type": "OPEXP", "net_amount": "0", "symbol": "SPY260516P00540000", "id": "act-1"},
            {"activity_type": "OPEXP", "net_amount": "0", "symbol": "SPY260516P00535000", "id": "act-2"},
        ]
        pnl, reason, act_id = self.reconciler._compute_external_close_pnl(trade, activities)
        # pnl = credit * contracts * 100 - entry_commission
        expected = 1.50 * 2 * 100 - _DEFAULT_COMMISSION_PER_CONTRACT * 2 * 2
        assert pnl == pytest.approx(expected)
        assert reason == "expired_worthless"
        assert act_id == "act-1"

    def test_expired_itm(self):
        """Credit spread expired ITM — broker settled with negative net_amount."""
        trade = {"credit": 1.50, "contracts": 1, "strategy_type": "bull_put"}
        activities = [
            {"activity_type": "OPEXP", "net_amount": "-200", "symbol": "SPY260516P00540000", "id": "act-1"},
        ]
        pnl, reason, act_id = self.reconciler._compute_external_close_pnl(trade, activities)
        expected = 1.50 * 1 * 100 + (-200) - _DEFAULT_COMMISSION_PER_CONTRACT * 1 * 2
        assert pnl == pytest.approx(expected)
        assert reason == "expired_itm"

    def test_assignment_returns_assignment(self):
        """OASGN (assignment) is genuinely manual — flagged as 'assignment'."""
        trade = {"credit": 1.50, "contracts": 1, "strategy_type": "bull_put"}
        activities = [
            {"activity_type": "OASGN", "net_amount": "-500", "symbol": "SPY260516P00540000", "id": "asgn-1"},
        ]
        pnl, reason, act_id = self.reconciler._compute_external_close_pnl(trade, activities)
        assert pnl is None
        assert reason == "assignment"   # caller routes this to needs_investigation
        assert act_id == "asgn-1"

    def test_external_fill_defers_to_pending_without_orders(self):
        """FILL close now values PnL from /v2/orders (Option B). With no orders
        available it defers to 'pending' rather than fabricating PnL from the
        activities feed."""
        trade = {"credit": 2.00, "contracts": 1, "strategy_type": "bull_put",
                 "ticker": "SPY", "expiration": "2026-05-16",
                 "short_strike": 540.0, "long_strike": 535.0}
        activities = [
            {"activity_type": "FILL", "net_amount": "-80", "symbol": "SPY260516P00540000", "id": "fill-1"},
        ]
        pnl, reason, _ = self.reconciler._compute_external_close_pnl(trade, activities)
        assert pnl is None
        assert reason == "pending"

    def test_iron_condor_uses_4_legs(self):
        """IC has 4 legs, so commission is doubled."""
        trade = {"credit": 3.00, "contracts": 1, "strategy_type": "iron_condor"}
        activities = [
            {"activity_type": "OPEXP", "net_amount": "0", "id": "act-1"},
        ]
        pnl, reason, _ = self.reconciler._compute_external_close_pnl(trade, activities)
        expected = 3.00 * 1 * 100 - _DEFAULT_COMMISSION_PER_CONTRACT * 1 * 4
        assert pnl == pytest.approx(expected)

    def test_no_activities_returns_none(self):
        trade = {"credit": 1.50, "contracts": 1, "strategy_type": "bull_put"}
        pnl, reason, act_id = self.reconciler._compute_external_close_pnl(trade, [])
        assert pnl is None
        assert reason is None
        assert act_id is None

    def test_zero_credit_trade(self):
        """Trade with zero credit (debit spread)."""
        trade = {"credit": 0, "contracts": 1, "strategy_type": "bull_put"}
        activities = [
            {"activity_type": "OPEXP", "net_amount": "0", "id": "act-1"},
        ]
        pnl, reason, _ = self.reconciler._compute_external_close_pnl(trade, activities)
        expected = 0 * 1 * 100 - _DEFAULT_COMMISSION_PER_CONTRACT * 1 * 2
        assert pnl == pytest.approx(expected)
        assert reason == "expired_worthless"

    def test_multiple_fill_activities_defer_to_pending_without_orders(self):
        """FILL close requires order fills to value PnL (Option B); with none
        available it defers to 'pending' (no PnL fabricated from activities)."""
        trade = {"credit": 1.00, "contracts": 1, "strategy_type": "bull_put",
                 "ticker": "SPY", "expiration": "2026-05-16",
                 "short_strike": 540.0, "long_strike": 535.0}
        activities = [
            {"activity_type": "FILL", "net_amount": "-30", "symbol": "A", "id": "f1"},
            {"activity_type": "FILL", "net_amount": "-20", "symbol": "B", "id": "f2"},
        ]
        pnl, reason, _ = self.reconciler._compute_external_close_pnl(trade, activities)
        assert pnl is None
        assert reason == "pending"


# ─────────────────────────────────────────────────────────────────────────────
# reconcile() — full reconciliation pass
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcileFull:

    def test_empty_db_empty_alpaca(self, tmp_path):
        """No trades, no positions → clean pass."""
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile()
        assert not result  # nothing to do

    def test_pending_resolved_via_reconcile(self, tmp_path):
        """reconcile_pending_only() resolves a pending_open trade."""
        db_path = _db(tmp_path)
        _pending_trade(db_path, trade_id="cs-r1", client_order_id="cs-r1")
        filled_order = {
            "client_order_id": "cs-r1",
            "status": "filled",
            "filled_avg_price": "1.25",
            "id": "ord-1",
        }
        alpaca = _mock_alpaca(batch_orders=[filled_order])
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_pending_only()
        assert result.pending_resolved == 1
        trades = get_trades(status="open", path=db_path)
        assert len(trades) == 1

    def test_pending_terminal_order(self, tmp_path):
        """Terminal order status → failed_open."""
        db_path = _db(tmp_path)
        _pending_trade(db_path, trade_id="cs-t1", client_order_id="cs-t1")
        cancelled_order = {
            "client_order_id": "cs-t1",
            "status": "cancelled",
            "id": "ord-1",
        }
        alpaca = _mock_alpaca(batch_orders=[cancelled_order])
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile()
        assert result.pending_failed == 1

    def test_dry_run_pending_promoted(self, tmp_path):
        """Dry run pending trades are promoted to open without Alpaca order.
        dry_run trades have no alpaca_client_order_id — the no-order-id path
        checks the dry_run flag and promotes to open.
        """
        db_path = _db(tmp_path)
        t = {
            "id": "cs-dry",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "pending_open",
            "short_strike": 540.0,
            "long_strike": 535.0,
            "expiration": "2026-05-16",
            "credit": 1.50,
            "contracts": 1,
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "dry_run": True,
            # No alpaca_client_order_id — this triggers the dry_run path
        }
        upsert_trade(t, source="test", path=db_path)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_pending_only()
        assert result.pending_resolved == 1
        trades = get_trades(status="open", path=db_path)
        assert len(trades) == 1

    def test_no_client_order_id_not_dry_run(self, tmp_path):
        """Pending trade with no client_order_id and not dry_run → failed."""
        db_path = _db(tmp_path)
        t = {
            "id": "cs-no-cid",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "pending_open",
            "short_strike": 540.0,
            "long_strike": 535.0,
            "expiration": "2026-05-16",
            "credit": 1.50,
            "contracts": 1,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        }
        upsert_trade(t, source="test", path=db_path)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile()
        assert result.pending_failed == 1

    def test_alpaca_positions_fetch_failure(self, tmp_path):
        """Alpaca positions API failure → steps 3+4 skipped gracefully."""
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        alpaca.get_positions.side_effect = ConnectionError("API down")
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile()
        assert not result  # nothing happened, but no crash

    def test_pending_order_still_live(self, tmp_path):
        """Order status=submitted → leave as pending_open."""
        db_path = _db(tmp_path)
        _pending_trade(db_path, trade_id="cs-live", client_order_id="cs-live")
        live_order = {
            "client_order_id": "cs-live",
            "status": "submitted",
            "id": "ord-1",
        }
        alpaca = _mock_alpaca(batch_orders=[live_order])
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile()
        assert result.pending_resolved == 0
        assert result.pending_failed == 0
        trades = get_trades(status="pending_open", path=db_path)
        assert len(trades) == 1


# ─────────────────────────────────────────────────────────────────────────────
# reconcile_tier2
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcileTier2:

    def test_tier2_empty(self, tmp_path):
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_tier2()
        assert not result

    def test_tier2_with_prefetched_positions(self, tmp_path):
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_tier2(alpaca_positions={})
        assert not result

    def test_tier2_activity_failure_logged(self, tmp_path):
        """Activity check failure in tier2 doesn't crash."""
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        alpaca.get_account_activities.side_effect = ConnectionError("API err")
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_tier2()
        assert not result  # no crash

    def test_tier2_detects_orphan(self, tmp_path):
        db_path = _db(tmp_path)
        sym = "SPY   260516P00540000"
        alpaca = _mock_alpaca(positions=[
            {"symbol": sym, "asset_class": "us_option", "qty": "-1", "market_value": "-50.0"},
        ])
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_tier2()
        assert result.orphans_detected == 1


# ─────────────────────────────────────────────────────────────────────────────
# reconcile_eod
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcileEOD:

    def test_eod_empty(self, tmp_path):
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_eod()
        assert isinstance(result, ReconciliationResult)

    def test_eod_saves_last_run(self, tmp_path):
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        rec.reconcile_eod()
        saved = load_scanner_state("last_eod_reconcile_date", path=db_path)
        assert saved is not None

    def test_eod_activity_failure_doesnt_crash(self, tmp_path):
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        alpaca.get_account_activities.side_effect = TimeoutError("slow")
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_eod()
        assert isinstance(result, ReconciliationResult)

    def test_eod_processes_expired_credit_trade(self, tmp_path):
        """EOD processes an expired credit spread as expired_worthless."""
        db_path = _db(tmp_path)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        _open_trade(db_path, trade_id="cs-exp1", credit=1.50, contracts=2,
                    expiration=yesterday)
        alpaca = _mock_alpaca()
        # No activities found → estimated worthless
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_eod()
        assert result.expirations_processed == 1
        closed = get_trades(status="closed_profit", path=db_path) + \
                 get_trades(status="closed_loss", path=db_path)
        # The trade should no longer be open
        open_trades = get_trades(status="open", path=db_path)
        assert len(open_trades) == 0

    def test_eod_debit_position_needs_investigation(self, tmp_path):
        """Expired debit position (credit=0) → needs_investigation."""
        db_path = _db(tmp_path)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        _open_trade(db_path, trade_id="cs-deb1", credit=0.0, contracts=1,
                    expiration=yesterday)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_eod()
        assert result.phantom_resolved == 1
        ni = get_trades(status="needs_investigation", path=db_path)
        assert len(ni) == 1


# ─────────────────────────────────────────────────────────────────────────────
# reconcile_morning
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcileMorning:

    def test_morning_empty(self, tmp_path):
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile_morning()
        assert isinstance(result, ReconciliationResult)

    def test_morning_saves_last_run(self, tmp_path):
        db_path = _db(tmp_path)
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        rec.reconcile_morning()
        saved = load_scanner_state("last_morning_reconcile_date", path=db_path)
        assert saved is not None


# ─────────────────────────────────────────────────────────────────────────────
# _reconcile_from_activities
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcileFromActivities:

    def test_no_activities_no_change(self, tmp_path):
        db_path = _db(tmp_path)
        _open_trade(db_path, trade_id="cs-act1")
        alpaca = _mock_alpaca(activities=[])
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = ReconciliationResult()
        rec._reconcile_from_activities(result)
        assert result.externally_closed == 0

    def test_activity_api_failure_per_type(self, tmp_path):
        """Individual activity type failure doesn't block others."""
        db_path = _db(tmp_path)
        _open_trade(db_path, trade_id="cs-act2")
        alpaca = _mock_alpaca()

        call_count = [0]
        def _side_effect(**kwargs):
            call_count[0] += 1
            if kwargs.get("activity_type") == "OPEXP":
                raise ConnectionError("fail")
            return []

        alpaca.get_account_activities.side_effect = _side_effect
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = ReconciliationResult()
        rec._reconcile_from_activities(result)
        # Should not crash; OPEXP fails but OASGN/FILL succeed
        assert call_count[0] == 3

    def test_opexp_activity_closes_trade(self, tmp_path):
        """OPEXP activity closes an open trade."""
        db_path = _db(tmp_path)
        trade = _open_trade(db_path, trade_id="cs-opexp", credit=1.50,
                           contracts=1, expiration="2026-05-16")
        occ_short = "SPY   20260516P00540000"
        occ_long = "SPY   20260516P00535000"
        alpaca = _mock_alpaca()

        def _act_side_effect(**kwargs):
            if kwargs.get("activity_type") == "OPEXP":
                return [
                    {"symbol": occ_short, "activity_type": "OPEXP",
                     "net_amount": "0", "id": "opexp-1"},
                ]
            return []

        alpaca.get_account_activities.side_effect = _act_side_effect
        alpaca._build_occ_symbol.side_effect = lambda t, e, s, o: {
            (540.0, "put"): occ_short,
            (535.0, "put"): occ_long,
        }.get((s, o), f"UNK{s}")

        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = ReconciliationResult()
        rec._reconcile_from_activities(result)
        assert result.externally_closed == 1

    def test_undetermined_pnl_marks_needs_investigation(self, tmp_path):
        """OASGN activity → needs_investigation."""
        db_path = _db(tmp_path)
        _open_trade(db_path, trade_id="cs-asgn", credit=1.50, contracts=1)
        occ_short = "SPY   20260516P00540000"
        alpaca = _mock_alpaca()

        def _act_side_effect(**kwargs):
            if kwargs.get("activity_type") == "OASGN":
                return [
                    {"symbol": occ_short, "activity_type": "OASGN",
                     "net_amount": "-500", "id": "asgn-1"},
                ]
            return []

        alpaca.get_account_activities.side_effect = _act_side_effect
        alpaca._build_occ_symbol.side_effect = lambda t, e, s, o: {
            (540.0, "put"): occ_short,
        }.get((s, o), f"UNK{s}")

        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = ReconciliationResult()
        rec._reconcile_from_activities(result)
        assert result.phantom_resolved == 1
        ni = get_trades(status="needs_investigation", path=db_path)
        assert len(ni) == 1


# ─────────────────────────────────────────────────────────────────────────────
# _reconcile_open_positions (phantom detection)
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcileOpenPositions:

    def test_all_legs_present_no_phantom(self, tmp_path):
        """Open trade with legs in Alpaca → no action."""
        db_path = _db(tmp_path)
        _open_trade(db_path, trade_id="cs-ok1")
        occ_short = "SPY   20260516P00540000"
        occ_long = "SPY   20260516P00535000"
        alpaca = _mock_alpaca()
        alpaca._build_occ_symbol.side_effect = lambda t, e, s, o: {
            (540.0, "put"): occ_short,
            (535.0, "put"): occ_long,
        }.get((s, o), f"UNK{s}")

        positions = {occ_short: {"qty": "-2"}, occ_long: {"qty": "2"}}
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = ReconciliationResult()
        rec._reconcile_open_positions(result, positions)
        assert result.phantom_resolved == 0

    def test_all_legs_missing_marks_needs_investigation(self, tmp_path):
        """Open trade with ALL legs missing and no activities → needs_investigation."""
        db_path = _db(tmp_path)
        _open_trade(db_path, trade_id="cs-phantom1", expiration="2026-12-31")
        occ_short = "SPY   20261231P00540000"
        occ_long = "SPY   20261231P00535000"
        alpaca = _mock_alpaca()
        alpaca._build_occ_symbol.side_effect = lambda t, e, s, o: {
            (540.0, "put"): occ_short,
            (535.0, "put"): occ_long,
        }.get((s, o), f"UNK{s}")

        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = ReconciliationResult()
        rec._reconcile_open_positions(result, {})  # empty positions
        assert result.phantom_resolved == 1
        ni = get_trades(status="needs_investigation", path=db_path)
        assert len(ni) == 1

    def test_phantom_expired_credit_estimated(self, tmp_path):
        """Phantom + expired + credit > 0 → estimated expired_worthless."""
        db_path = _db(tmp_path)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        _open_trade(db_path, trade_id="cs-ph-exp", credit=1.50, contracts=1,
                    expiration=yesterday)
        occ_short = f"SPY   {yesterday.replace('-', '')}P00540000"
        occ_long = f"SPY   {yesterday.replace('-', '')}P00535000"
        alpaca = _mock_alpaca()
        alpaca._build_occ_symbol.side_effect = lambda t, e, s, o: {
            (540.0, "put"): occ_short,
            (535.0, "put"): occ_long,
        }.get((s, o), f"UNK{s}")

        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = ReconciliationResult()
        rec._reconcile_open_positions(result, {})
        assert result.phantom_resolved == 1
        assert result.expirations_processed == 1


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestSchedulingHelpers:

    def test_should_run_tier2_no_prior(self, tmp_path):
        db_path = _db(tmp_path)
        rec = PositionReconciler(alpaca=_mock_alpaca(), db_path=db_path)
        assert rec._should_run_tier2() is True

    def test_should_run_tier2_recent(self, tmp_path):
        db_path = _db(tmp_path)
        recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        save_scanner_state("last_tier2_reconcile", recent, path=db_path)
        rec = PositionReconciler(alpaca=_mock_alpaca(), db_path=db_path)
        assert rec._should_run_tier2() is False

    def test_should_run_tier2_stale(self, tmp_path):
        db_path = _db(tmp_path)
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        save_scanner_state("last_tier2_reconcile", stale, path=db_path)
        rec = PositionReconciler(alpaca=_mock_alpaca(), db_path=db_path)
        assert rec._should_run_tier2() is True

    def test_should_run_tier2_malformed(self, tmp_path):
        db_path = _db(tmp_path)
        save_scanner_state("last_tier2_reconcile", "not-a-date", path=db_path)
        rec = PositionReconciler(alpaca=_mock_alpaca(), db_path=db_path)
        assert rec._should_run_tier2() is True

    def test_save_last_tier2(self, tmp_path):
        db_path = _db(tmp_path)
        rec = PositionReconciler(alpaca=_mock_alpaca(), db_path=db_path)
        rec._save_last_tier2()
        saved = load_scanner_state("last_tier2_reconcile", path=db_path)
        assert saved is not None

    def test_save_last_eod_run(self, tmp_path):
        db_path = _db(tmp_path)
        rec = PositionReconciler(alpaca=_mock_alpaca(), db_path=db_path)
        rec._save_last_eod_run()
        saved = load_scanner_state("last_eod_reconcile_date", path=db_path)
        assert saved is not None

    def test_save_last_morning_run(self, tmp_path):
        db_path = _db(tmp_path)
        rec = PositionReconciler(alpaca=_mock_alpaca(), db_path=db_path)
        rec._save_last_morning_run()
        saved = load_scanner_state("last_morning_reconcile_date", path=db_path)
        assert saved is not None


# ─────────────────────────────────────────────────────────────────────────────
# _fetch_activities_for_trade
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchActivitiesForTrade:

    def test_no_alpaca_returns_empty(self, tmp_path):
        rec = PositionReconciler(alpaca=None)
        trade = {"ticker": "SPY", "expiration": "2026-05-16",
                 "strategy_type": "bull_put", "short_strike": 540.0,
                 "long_strike": 535.0}
        assert rec._fetch_activities_for_trade(trade) == []

    def test_api_failure_returns_empty(self, tmp_path):
        alpaca = _mock_alpaca()
        alpaca.get_account_activities.side_effect = ConnectionError("fail")
        rec = PositionReconciler(alpaca=alpaca)
        trade = {"ticker": "SPY", "expiration": "2026-05-16",
                 "strategy_type": "bull_put", "short_strike": 540.0,
                 "long_strike": 535.0}
        result = rec._fetch_activities_for_trade(trade)
        assert result == []

    def test_matching_activities_returned(self, tmp_path):
        occ_short = "SPY   20260516P00540000"
        alpaca = _mock_alpaca()
        alpaca._build_occ_symbol.side_effect = lambda t, e, s, o: {
            (540.0, "put"): occ_short,
        }.get((s, o), f"UNK{s}")

        def _act_side_effect(**kwargs):
            if kwargs.get("activity_type") == "OPEXP":
                return [{"symbol": occ_short, "activity_type": "OPEXP",
                         "net_amount": "0", "id": "act-1"}]
            return []

        alpaca.get_account_activities.side_effect = _act_side_effect
        rec = PositionReconciler(alpaca=alpaca)
        trade = {"ticker": "SPY", "expiration": "2026-05-16",
                 "strategy_type": "bull_put", "short_strike": 540.0,
                 "long_strike": 535.0}
        result = rec._fetch_activities_for_trade(trade)
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────────────────────
# _fetch_recent_orders_by_client_id
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchRecentOrders:

    def test_batch_fetch_indexes_by_client_id(self, tmp_path):
        orders = [
            {"client_order_id": "cid-1", "status": "filled"},
            {"client_order_id": "cid-2", "status": "cancelled"},
            {"status": "filled"},  # no client_order_id → skipped
        ]
        alpaca = _mock_alpaca(batch_orders=orders)
        rec = PositionReconciler(alpaca=alpaca)
        result = rec._fetch_recent_orders_by_client_id()
        assert "cid-1" in result
        assert "cid-2" in result
        assert len(result) == 2

    def test_batch_fetch_api_failure(self, tmp_path):
        alpaca = _mock_alpaca()
        alpaca.get_orders.side_effect = ConnectionError("fail")
        rec = PositionReconciler(alpaca=alpaca)
        result = rec._fetch_recent_orders_by_client_id()
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# Pending open — age-based failure
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingAgeBasedFailure:

    def test_young_trade_not_found_stays_pending(self, tmp_path):
        """Trade < 4h old with no order → stay pending."""
        db_path = _db(tmp_path)
        recent = datetime.now(timezone.utc).isoformat()
        _pending_trade(db_path, trade_id="cs-young", entry_date=recent,
                      client_order_id="cs-young")
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile()
        assert result.pending_failed == 0
        trades = get_trades(status="pending_open", path=db_path)
        assert len(trades) == 1

    def test_old_trade_not_found_fails(self, tmp_path):
        """Trade > 4h old with no order → failed_open."""
        db_path = _db(tmp_path)
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        _pending_trade(db_path, trade_id="cs-old", entry_date=old,
                      client_order_id="cs-old")
        alpaca = _mock_alpaca()
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        result = rec.reconcile()
        assert result.pending_failed == 1


# ─────────────────────────────────────────────────────────────────────────────
# Filled order details
# ─────────────────────────────────────────────────────────────────────────────

class TestFilledOrderDetails:

    def test_fill_price_stored(self, tmp_path):
        db_path = _db(tmp_path)
        _pending_trade(db_path, trade_id="cs-fp", client_order_id="cs-fp")
        filled_order = {
            "client_order_id": "cs-fp",
            "status": "filled",
            "filled_avg_price": "1.85",
            "id": "ord-fp",
        }
        alpaca = _mock_alpaca(batch_orders=[filled_order])
        rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
        rec.reconcile_pending_only()
        trades = get_trades(status="open", path=db_path)
        assert len(trades) == 1
        assert trades[0].get("alpaca_fill_price") == "1.85"

    def test_terminal_states_all_mark_failed(self, tmp_path):
        """All terminal order states should result in failed_open."""
        for status in _TERMINAL_ORDER_STATES:
            db_path = _db(tmp_path / status)
            tid = f"cs-{status}"
            _pending_trade(db_path, trade_id=tid, client_order_id=tid)
            order = {"client_order_id": tid, "status": status, "id": f"ord-{status}"}
            alpaca = _mock_alpaca(batch_orders=[order])
            rec = PositionReconciler(alpaca=alpaca, db_path=db_path)
            result = rec.reconcile()
            assert result.pending_failed == 1, f"Status '{status}' did not mark failed"
