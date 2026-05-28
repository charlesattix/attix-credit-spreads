"""Stress tests for shared.reconciler — the module that heals position mismatches.

Covers: edge cases (empty portfolios, missing fields, NaN prices),
PnL computation (zero credit, negative, partial), phantom/orphan detection,
activity-based close logic, expiration processing, pending resolution,
and deduplication.

All Alpaca/DB calls mocked.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.reconciler import (
    PositionReconciler,
    ReconciliationResult,
    _DEFAULT_COMMISSION_PER_CONTRACT,
    _PENDING_MAX_AGE_HOURS,
    _TERMINAL_ORDER_STATES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_alpaca():
    """Minimal mock of AlpacaProvider with the methods reconciler calls."""
    alpaca = MagicMock()
    alpaca.get_order_by_client_id = MagicMock(return_value=None)
    alpaca.get_orders = MagicMock(return_value=[])
    alpaca.get_positions = MagicMock(return_value=[])
    alpaca.get_account_activities = MagicMock(return_value=[])
    alpaca._build_occ_symbol = MagicMock(
        side_effect=lambda ticker, exp, strike, ot: f"O:{ticker}{exp.replace('-','')}{ot[0].upper()}{int(strike*1000):08d}"
    )
    return alpaca


@pytest.fixture
def mock_db_funcs():
    """Patch shared.database functions used by reconciler internals."""
    with patch("shared.database.get_trades") as mock_get, \
         patch("shared.database.close_trade") as mock_close, \
         patch("shared.database.upsert_trade") as mock_upsert, \
         patch("shared.database.insert_reconciliation_event") as mock_event, \
         patch("shared.database.load_scanner_state", return_value=None) as mock_load, \
         patch("shared.database.save_scanner_state") as mock_save, \
         patch("shared.database.get_db") as mock_getdb:
        mock_get.return_value = []
        yield {
            "get_trades": mock_get,
            "close_trade": mock_close,
            "upsert_trade": mock_upsert,
            "insert_event": mock_event,
            "load_state": mock_load,
            "save_state": mock_save,
            "get_db": mock_getdb,
        }


def _make_reconciler(mock_alpaca, db_path=None):
    return PositionReconciler(alpaca=mock_alpaca, db_path=db_path)


def _make_trade(trade_id="t1", ticker="SPY", exp="2026-05-15", credit=2.50,
                contracts=3, status="open", short_strike=540, long_strike=535,
                strategy_type="put_credit_spread", entry_date=None, **kwargs):
    """Build a minimal trade dict matching the DB schema reconciler expects."""
    if entry_date is None:
        entry_date = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    t = {
        "id": trade_id,
        "ticker": ticker,
        "expiration": exp,
        "credit": credit,
        "contracts": contracts,
        "status": status,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "strategy_type": strategy_type,
        "entry_date": entry_date,
    }
    t.update(kwargs)
    return t


# ---------------------------------------------------------------------------
# 1. ReconciliationResult edge cases
# ---------------------------------------------------------------------------

class TestReconciliationResult:
    def test_empty_result_is_falsy(self):
        assert not ReconciliationResult()

    def test_error_only_is_truthy(self):
        r = ReconciliationResult()
        r.errors.append("something broke")
        assert bool(r)
        assert "errors=1" in repr(r)

    def test_all_counters_in_repr(self):
        r = ReconciliationResult()
        r.pending_resolved = 1
        r.pending_failed = 2
        r.phantom_resolved = 3
        r.orphans_detected = 4
        r.externally_closed = 5
        r.expirations_processed = 6
        r.activities_processed = 7
        s = repr(r)
        assert "resolved=1" in s
        assert "failed=2" in s
        assert "orphans=4" in s


# ---------------------------------------------------------------------------
# 2. _compute_external_close_pnl — the PnL engine
# ---------------------------------------------------------------------------

class TestComputeExternalClosePnl:
    def setup_method(self):
        self.alpaca = MagicMock()
        self.rec = _make_reconciler(self.alpaca)

    def test_expired_worthless_credit_spread(self):
        """OTM expiration: keep full credit minus commission."""
        trade = _make_trade(credit=2.50, contracts=3, strategy_type="put_credit_spread")
        activities = [{"activity_type": "OPEXP", "net_amount": "0.0", "id": "act1", "symbol": "X"}]
        pnl, reason, act_id = self.rec._compute_external_close_pnl(trade, activities)
        expected_comm = _DEFAULT_COMMISSION_PER_CONTRACT * 3 * 2  # 2 legs
        assert reason == "expired_worthless"
        assert abs(pnl - (2.50 * 3 * 100 - expected_comm)) < 0.01

    def test_expired_itm_with_settlement(self):
        """ITM expiration: credit received + settlement net_amount - commission."""
        trade = _make_trade(credit=2.50, contracts=2)
        activities = [{"activity_type": "OPEXP", "net_amount": "-300.0", "id": "act2", "symbol": "X"}]
        pnl, reason, _ = self.rec._compute_external_close_pnl(trade, activities)
        expected_comm = _DEFAULT_COMMISSION_PER_CONTRACT * 2 * 2
        assert reason == "expired_itm"
        assert abs(pnl - (2.50 * 2 * 100 + (-300.0) - expected_comm)) < 0.01

    def test_external_fill_defers_to_pending_without_orders(self):
        """FILL close now values PnL from /v2/orders (Option B); with no orders
        available it defers to 'pending' instead of valuing from activities."""
        trade = _make_trade(credit=3.00, contracts=1)
        activities = [{"activity_type": "FILL", "net_amount": "-150.0", "id": "act3", "symbol": "X"}]
        pnl, reason, _ = self.rec._compute_external_close_pnl(trade, activities)
        assert pnl is None
        assert reason == "pending"

    def test_assignment_returns_assignment(self):
        """OASGN is genuinely manual — flagged 'assignment' (→ needs_investigation)."""
        trade = _make_trade()
        activities = [{"activity_type": "OASGN", "id": "act4", "symbol": "X"}]
        pnl, reason, act_id = self.rec._compute_external_close_pnl(trade, activities)
        assert pnl is None
        assert reason == "assignment"
        assert act_id == "act4"

    def test_zero_credit_expired_worthless(self):
        """A debit spread that expires worthless: credit=0, should still compute."""
        trade = _make_trade(credit=0.0, contracts=2)
        activities = [{"activity_type": "OPEXP", "net_amount": "0.0", "id": "act5", "symbol": "X"}]
        pnl, reason, _ = self.rec._compute_external_close_pnl(trade, activities)
        expected_comm = _DEFAULT_COMMISSION_PER_CONTRACT * 2 * 2
        assert reason == "expired_worthless"
        assert abs(pnl - (0 - expected_comm)) < 0.01  # net loss = commission only

    def test_missing_net_amount_treated_as_zero(self):
        """If net_amount is None or missing, treat as 0."""
        trade = _make_trade(credit=1.00, contracts=1)
        activities = [{"activity_type": "OPEXP", "net_amount": None, "id": "act6", "symbol": "X"}]
        pnl, reason, _ = self.rec._compute_external_close_pnl(trade, activities)
        assert reason == "expired_worthless"
        assert pnl is not None

    def test_iron_condor_4_leg_commission(self):
        """IC trades have 4 legs, so commission doubles."""
        trade = _make_trade(credit=4.00, contracts=2, strategy_type="iron_condor")
        activities = [{"activity_type": "OPEXP", "net_amount": "0.0", "id": "act7", "symbol": "X"}]
        pnl, reason, _ = self.rec._compute_external_close_pnl(trade, activities)
        expected_comm = _DEFAULT_COMMISSION_PER_CONTRACT * 2 * 4  # 4 legs
        assert abs(pnl - (4.00 * 2 * 100 - expected_comm)) < 0.01

    def test_no_activities_returns_none(self):
        """Empty activity list → can't determine close."""
        trade = _make_trade()
        pnl, reason, act_id = self.rec._compute_external_close_pnl(trade, [])
        assert pnl is None
        assert reason is None
        assert act_id is None

    def test_multiple_opexp_activities_sum_net_amounts(self):
        """Multiple OPEXP events (one per leg) — net_amounts should sum."""
        trade = _make_trade(credit=2.00, contracts=1)
        activities = [
            {"activity_type": "OPEXP", "net_amount": "-50.0", "id": "a1", "symbol": "X"},
            {"activity_type": "OPEXP", "net_amount": "-30.0", "id": "a2", "symbol": "Y"},
        ]
        pnl, reason, _ = self.rec._compute_external_close_pnl(trade, activities)
        comm = _DEFAULT_COMMISSION_PER_CONTRACT * 1 * 2
        assert reason == "expired_itm"
        assert abs(pnl - (2.00 * 100 + (-80.0) - comm)) < 0.01


# ---------------------------------------------------------------------------
# 3. _entry_commission
# ---------------------------------------------------------------------------

class TestEntryCommission:
    def test_standard_2leg_spread(self):
        c = PositionReconciler._entry_commission(3, 2)
        assert abs(c - (0.65 * 3 * 2)) < 0.001

    def test_zero_contracts(self):
        assert PositionReconciler._entry_commission(0, 2) == 0.0

    def test_custom_commission_rate(self):
        c = PositionReconciler._entry_commission(2, 4, commission_per_contract=1.00)
        assert abs(c - 8.0) < 0.001


# ---------------------------------------------------------------------------
# 4. _trade_age_hours
# ---------------------------------------------------------------------------

class TestTradeAgeHours:
    def test_recent_trade(self):
        now = datetime.now(timezone.utc)
        trade = {"entry_date": (now - timedelta(hours=2)).isoformat()}
        age = PositionReconciler._trade_age_hours(trade, now)
        assert abs(age - 2.0) < 0.1

    def test_missing_entry_date(self):
        age = PositionReconciler._trade_age_hours({}, datetime.now(timezone.utc))
        assert age == 99.0

    def test_malformed_date(self):
        age = PositionReconciler._trade_age_hours(
            {"entry_date": "not-a-date"}, datetime.now(timezone.utc)
        )
        assert age == 99.0

    def test_naive_datetime(self):
        """Naive datetime should be treated as UTC."""
        now = datetime.now(timezone.utc)
        trade = {"entry_date": (now - timedelta(hours=5)).replace(tzinfo=None).isoformat()}
        age = PositionReconciler._trade_age_hours(trade, now)
        assert abs(age - 5.0) < 0.1

    def test_created_at_fallback(self):
        now = datetime.now(timezone.utc)
        trade = {"created_at": (now - timedelta(hours=3)).isoformat()}
        age = PositionReconciler._trade_age_hours(trade, now)
        assert abs(age - 3.0) < 0.1


# ---------------------------------------------------------------------------
# 5. Pending open resolution
# ---------------------------------------------------------------------------

class TestPendingOpenResolution:
    def test_filled_order_promotes_to_open(self, mock_alpaca, mock_db_funcs):
        trade = _make_trade(status="pending_open", alpaca_client_order_id="ord-1")
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = [
            {"client_order_id": "ord-1", "status": "filled", "filled_avg_price": "2.35", "id": "alp-1"}
        ]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_resolved == 1
        upserted = mock_db_funcs["upsert_trade"].call_args[0][0]
        assert upserted["status"] == "open"
        assert upserted["alpaca_fill_price"] == "2.35"

    def test_cancelled_order_marks_failed(self, mock_alpaca, mock_db_funcs):
        trade = _make_trade(status="pending_open", alpaca_client_order_id="ord-2")
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = [
            {"client_order_id": "ord-2", "status": "cancelled", "id": "alp-2"}
        ]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_failed == 1
        upserted = mock_db_funcs["upsert_trade"].call_args[0][0]
        assert upserted["status"] == "failed_open"

    def test_no_order_id_non_dryrun_fails(self, mock_alpaca, mock_db_funcs):
        """Trade with no alpaca_client_order_id and not dry_run → failed_open."""
        trade = _make_trade(status="pending_open")
        # No alpaca_client_order_id key
        trade.pop("alpaca_client_order_id", None)
        mock_db_funcs["get_trades"].return_value = [trade]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_failed == 1

    def test_no_order_id_dryrun_promotes(self, mock_alpaca, mock_db_funcs):
        """Dry-run trade with no order ID → promoted to open."""
        trade = _make_trade(status="pending_open", dry_run=True)
        trade.pop("alpaca_client_order_id", None)
        mock_db_funcs["get_trades"].return_value = [trade]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_resolved == 1

    def test_order_not_found_young_trade_stays_pending(self, mock_alpaca, mock_db_funcs):
        """Trade < 4 hours old with no matching order stays pending_open."""
        trade = _make_trade(
            status="pending_open",
            alpaca_client_order_id="ord-young",
            entry_date=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = []
        mock_alpaca.get_order_by_client_id.return_value = None

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_resolved == 0
        assert result.pending_failed == 0

    def test_order_not_found_old_trade_fails(self, mock_alpaca, mock_db_funcs):
        """Trade > 4 hours old with no matching order → failed_open."""
        trade = _make_trade(
            status="pending_open",
            alpaca_client_order_id="ord-old",
            entry_date=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
        )
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = []
        mock_alpaca.get_order_by_client_id.return_value = None

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_failed == 1

    def test_partially_filled_stays_pending(self, mock_alpaca, mock_db_funcs):
        """partially_filled order should NOT be marked failed or resolved."""
        trade = _make_trade(status="pending_open", alpaca_client_order_id="ord-pf")
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = [
            {"client_order_id": "ord-pf", "status": "partially_filled", "id": "alp-pf"}
        ]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_resolved == 0
        assert result.pending_failed == 0

    def test_all_terminal_states_fail(self, mock_alpaca, mock_db_funcs):
        """Every terminal order state should produce a failed_open."""
        for terminal in _TERMINAL_ORDER_STATES:
            mock_db_funcs["upsert_trade"].reset_mock()
            trade = _make_trade(
                status="pending_open", alpaca_client_order_id=f"ord-{terminal}"
            )
            mock_db_funcs["get_trades"].return_value = [trade]
            mock_alpaca.get_orders.return_value = [
                {"client_order_id": f"ord-{terminal}", "status": terminal, "id": f"alp-{terminal}"}
            ]
            rec = _make_reconciler(mock_alpaca)
            result = ReconciliationResult()
            rec._reconcile_pending_opens(result)
            assert result.pending_failed == 1, f"Terminal state '{terminal}' did not fail"


# ---------------------------------------------------------------------------
# 6. Orphan detection
# ---------------------------------------------------------------------------

class TestOrphanDetection:
    def test_option_not_in_db_creates_unmanaged_record(self, mock_alpaca, mock_db_funcs):
        mock_db_funcs["get_trades"].return_value = []  # no trades in DB
        alpaca_positions = {
            "O:SPY260515P00540000": {"symbol": "O:SPY260515P00540000", "qty": "3", "asset_class": "us_option"},
        }

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._detect_orphan_positions(result, alpaca_positions)

        assert result.orphans_detected == 1
        mock_db_funcs["upsert_trade"].assert_called_once()
        record = mock_db_funcs["upsert_trade"].call_args[0][0]
        assert record["status"] == "unmanaged"

    def test_equity_position_ignored(self, mock_alpaca, mock_db_funcs):
        """Non-option positions (equities) should not be flagged as orphans."""
        mock_db_funcs["get_trades"].return_value = []
        alpaca_positions = {
            "SPY": {"symbol": "SPY", "qty": "100", "asset_class": "us_equity"},
        }

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._detect_orphan_positions(result, alpaca_positions)

        assert result.orphans_detected == 0

    def test_managed_position_not_flagged(self, mock_alpaca, mock_db_funcs):
        """Position matching an open trade should not be flagged."""
        trade = _make_trade(short_strike=540, long_strike=535)
        mock_db_funcs["get_trades"].side_effect = lambda status=None, path=None: (
            [trade] if status in ("open", "pending_open") else []
        )
        # The expected symbols from _expected_symbols
        sym = mock_alpaca._build_occ_symbol("SPY", "2026-05-15", 540, "put")
        alpaca_positions = {
            sym: {"symbol": sym, "qty": "3", "asset_class": "us_option"},
        }

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._detect_orphan_positions(result, alpaca_positions)

        assert result.orphans_detected == 0

    def test_failed_open_recovered_to_open(self, mock_alpaca, mock_db_funcs):
        """Orphan matching a failed_open trade → promote to open (Fix 4)."""
        failed_trade = _make_trade(
            trade_id="ft1", status="failed_open", short_strike=540, long_strike=535
        )
        sym = mock_alpaca._build_occ_symbol("SPY", "2026-05-15", 540, "put")

        def side_effect(status=None, path=None):
            if status == "open":
                return []
            if status == "pending_open":
                return []
            if status == "failed_open":
                return [failed_trade]
            return []

        mock_db_funcs["get_trades"].side_effect = side_effect
        alpaca_positions = {
            sym: {"symbol": sym, "qty": "3", "asset_class": "us_option"},
        }

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._detect_orphan_positions(result, alpaca_positions)

        assert result.pending_resolved == 1
        assert result.orphans_detected == 0
        upserted = mock_db_funcs["upsert_trade"].call_args[0][0]
        assert upserted["status"] == "open"


# ---------------------------------------------------------------------------
# 7. Phantom detection (open in DB but missing from Alpaca)
# ---------------------------------------------------------------------------

class TestPhantomDetection:
    def test_phantom_with_no_activities_marks_investigation(self, mock_alpaca, mock_db_funcs):
        """Open trade whose legs are missing from Alpaca with no activity → needs_investigation."""
        # Far-future expiration so this exercises the no-activity Step-3 path
        # deterministically (not the expired-estimate fallback) regardless of date.
        trade = _make_trade(status="open", exp="2099-12-18")
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_account_activities.return_value = []

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_open_positions(result, {})  # empty alpaca positions

        assert result.phantom_resolved == 1
        upserted = mock_db_funcs["upsert_trade"].call_args[0][0]
        assert upserted["status"] == "needs_investigation"

    def test_phantom_resolved_via_opexp_activity(self, mock_alpaca, mock_db_funcs):
        """Phantom where OPEXP activity exists → close with computed PnL."""
        trade = _make_trade(status="open", credit=2.0, contracts=1)
        mock_db_funcs["get_trades"].return_value = [trade]

        short_sym = mock_alpaca._build_occ_symbol("SPY", "2026-05-15", 540, "put")
        mock_alpaca.get_account_activities.return_value = [
            {"symbol": short_sym, "activity_type": "OPEXP", "net_amount": "0.0", "id": "opx1"}
        ]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_open_positions(result, {})

        assert result.phantom_resolved == 1
        assert result.externally_closed == 1
        mock_db_funcs["close_trade"].assert_called_once()

    def test_phantom_expired_trade_estimated_worthless(self, mock_alpaca, mock_db_funcs):
        """Expired phantom with credit > 0 and no activity → estimated worthless."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        trade = _make_trade(status="open", credit=1.50, contracts=2, exp=yesterday)
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_account_activities.return_value = []

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_open_positions(result, {})

        assert result.phantom_resolved == 1
        assert result.expirations_processed == 1
        mock_db_funcs["close_trade"].assert_called_once()
        args = mock_db_funcs["close_trade"].call_args
        pnl = args[0][1]
        expected_comm = _DEFAULT_COMMISSION_PER_CONTRACT * 2 * 2
        assert abs(pnl - (1.50 * 2 * 100 - expected_comm)) < 0.01


# ---------------------------------------------------------------------------
# 8. Activity-based close detection (_reconcile_from_activities)
# ---------------------------------------------------------------------------

class TestActivityReconciliation:
    def test_opexp_closes_open_trade(self, mock_alpaca, mock_db_funcs):
        """OPEXP activity on an open trade's leg → close trade."""
        trade = _make_trade(status="open", credit=2.00, contracts=1)
        mock_db_funcs["get_trades"].return_value = [trade]

        short_sym = mock_alpaca._build_occ_symbol("SPY", "2026-05-15", 540, "put")
        mock_alpaca.get_account_activities.side_effect = lambda activity_type=None, since=None: (
            [{"symbol": short_sym, "activity_type": "OPEXP", "net_amount": "0.0", "id": "a1"}]
            if activity_type == "OPEXP" else []
        )

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_from_activities(result)

        assert result.externally_closed == 1
        mock_db_funcs["close_trade"].assert_called_once()

    def test_no_open_trades_short_circuits(self, mock_alpaca, mock_db_funcs):
        """If no open trades in DB, skip activity processing entirely."""
        mock_db_funcs["get_trades"].return_value = []

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_from_activities(result)

        assert result.externally_closed == 0
        mock_db_funcs["save_state"].assert_called()  # watermark still saved

    def test_oasgn_marks_needs_investigation(self, mock_alpaca, mock_db_funcs):
        """OASGN (assignment) activity → mark needs_investigation, not auto-close."""
        trade = _make_trade(status="open")
        mock_db_funcs["get_trades"].return_value = [trade]

        short_sym = mock_alpaca._build_occ_symbol("SPY", "2026-05-15", 540, "put")
        mock_alpaca.get_account_activities.side_effect = lambda activity_type=None, since=None: (
            [{"symbol": short_sym, "activity_type": "OASGN", "id": "asgn1"}]
            if activity_type == "OASGN" else []
        )

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_from_activities(result)

        assert result.phantom_resolved == 1  # marked needs_investigation
        assert result.externally_closed == 0  # NOT auto-closed
        mock_db_funcs["close_trade"].assert_not_called()
        # Marking now goes through set_reconcile_state + a needs_investigation event.
        event_types = [c.args[1] for c in mock_db_funcs["insert_event"].call_args_list]
        assert "needs_investigation" in event_types

    def test_activity_api_failure_does_not_crash(self, mock_alpaca, mock_db_funcs):
        """If get_account_activities raises, it should be caught gracefully."""
        trade = _make_trade(status="open")
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_account_activities.side_effect = ConnectionError("api down")

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_from_activities(result)
        # Should not raise, watermark should still be saved
        mock_db_funcs["save_state"].assert_called()

    def test_same_trade_not_closed_twice(self, mock_alpaca, mock_db_funcs):
        """If activities match the same trade via multiple symbols, only close once."""
        trade = _make_trade(status="open", credit=2.00, contracts=1)
        mock_db_funcs["get_trades"].return_value = [trade]

        short_sym = mock_alpaca._build_occ_symbol("SPY", "2026-05-15", 540, "put")
        long_sym = mock_alpaca._build_occ_symbol("SPY", "2026-05-15", 535, "put")
        mock_alpaca.get_account_activities.side_effect = lambda activity_type=None, since=None: (
            [
                {"symbol": short_sym, "activity_type": "OPEXP", "net_amount": "0.0", "id": "a1"},
                {"symbol": long_sym, "activity_type": "OPEXP", "net_amount": "0.0", "id": "a2"},
            ]
            if activity_type == "OPEXP" else []
        )

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_from_activities(result)

        assert result.externally_closed == 1  # only once
        assert mock_db_funcs["close_trade"].call_count == 1


# ---------------------------------------------------------------------------
# 9. Expiration processing
# ---------------------------------------------------------------------------

class TestExpirationProcessing:
    def test_expired_credit_spread_estimated_worthless(self, mock_alpaca, mock_db_funcs):
        """Expired credit spread with no activity → estimated worthless."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        trade = _make_trade(status="open", credit=3.00, contracts=2, exp=yesterday)
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_account_activities.return_value = []

        conn_mock = MagicMock()
        mock_db_funcs["get_db"].return_value = conn_mock

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._process_expirations(result)

        assert result.expirations_processed == 1
        mock_db_funcs["close_trade"].assert_called_once()
        pnl = mock_db_funcs["close_trade"].call_args[0][1]
        comm = _DEFAULT_COMMISSION_PER_CONTRACT * 2 * 2
        assert abs(pnl - (3.00 * 2 * 100 - comm)) < 0.01

    def test_zero_credit_expired_marks_investigation(self, mock_alpaca, mock_db_funcs):
        """Zero-credit expired trade with no activity → needs_investigation."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        trade = _make_trade(status="open", credit=0.0, contracts=1, exp=yesterday)
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_account_activities.return_value = []

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._process_expirations(result)

        assert result.phantom_resolved == 1
        upserted = mock_db_funcs["upsert_trade"].call_args[0][0]
        assert upserted["status"] == "needs_investigation"

    def test_future_expiration_skipped(self, mock_alpaca, mock_db_funcs):
        """Trade expiring next month should not be processed."""
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        trade = _make_trade(status="open", exp=future)
        mock_db_funcs["get_trades"].return_value = [trade]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._process_expirations(result)

        assert result.expirations_processed == 0
        mock_db_funcs["close_trade"].assert_not_called()

    def test_malformed_expiration_date_skipped(self, mock_alpaca, mock_db_funcs):
        """Bad date string should not crash."""
        trade = _make_trade(status="open", exp="not-a-date")
        mock_db_funcs["get_trades"].return_value = [trade]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._process_expirations(result)  # should not raise

        assert result.expirations_processed == 0


# ---------------------------------------------------------------------------
# 10. Full reconcile() integration path
# ---------------------------------------------------------------------------

class TestFullReconcile:
    def test_reconcile_with_no_alpaca(self):
        """reconciler with alpaca=None should not crash."""
        rec = PositionReconciler(alpaca=None)
        with patch("shared.database.get_trades", return_value=[]):
            result = rec.reconcile()
        assert not result  # nothing to do, no crash

    def test_reconcile_with_empty_portfolio(self, mock_alpaca, mock_db_funcs):
        """Full reconcile on empty portfolio — should be a no-op."""
        mock_db_funcs["get_trades"].return_value = []
        mock_alpaca.get_positions.return_value = []

        rec = _make_reconciler(mock_alpaca)
        result = rec.reconcile()

        assert not result

    def test_reconcile_handles_alpaca_position_fetch_failure(self, mock_alpaca, mock_db_funcs):
        """If position fetch fails, pending resolution should still work."""
        trade = _make_trade(status="pending_open", alpaca_client_order_id="ord-x")
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = [
            {"client_order_id": "ord-x", "status": "filled", "filled_avg_price": "1.50", "id": "xx"}
        ]
        mock_alpaca.get_positions.side_effect = ConnectionError("positions API down")

        rec = _make_reconciler(mock_alpaca)
        result = rec.reconcile()

        assert result.pending_resolved == 1  # pending resolution worked despite position failure


# ---------------------------------------------------------------------------
# 11. IC (Iron Condor) reconciliation
# ---------------------------------------------------------------------------

class TestICReconciliation:
    def test_both_wings_filled_promotes_to_open(self, mock_alpaca, mock_db_funcs):
        trade = _make_trade(
            status="pending_open",
            strategy_type="iron_condor",
            alpaca_client_order_id="ic-1",
            alpaca_put_order_id="ic-1-put",
            alpaca_call_order_id="ic-1-call",
        )
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = [
            {"client_order_id": "ic-1-put", "status": "filled", "filled_avg_price": "1.50", "id": "p1"},
            {"client_order_id": "ic-1-call", "status": "filled", "filled_avg_price": "1.00", "id": "c1"},
        ]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_resolved == 1
        upserted = mock_db_funcs["upsert_trade"].call_args[0][0]
        assert upserted["status"] == "open"
        assert abs(upserted["alpaca_fill_price"] - 2.50) < 0.01

    def test_one_wing_cancelled_fails_ic(self, mock_alpaca, mock_db_funcs):
        trade = _make_trade(
            status="pending_open",
            strategy_type="iron_condor",
            alpaca_client_order_id="ic-2",
            alpaca_put_order_id="ic-2-put",
            alpaca_call_order_id="ic-2-call",
        )
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = [
            {"client_order_id": "ic-2-put", "status": "filled", "filled_avg_price": "1.50", "id": "p2"},
            {"client_order_id": "ic-2-call", "status": "cancelled", "id": "c2"},
        ]

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_failed == 1

    def test_ic_neither_wing_found_young_stays_pending(self, mock_alpaca, mock_db_funcs):
        trade = _make_trade(
            status="pending_open",
            strategy_type="iron_condor",
            alpaca_client_order_id="ic-3",
            entry_date=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = []
        mock_alpaca.get_order_by_client_id.return_value = None

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_resolved == 0
        assert result.pending_failed == 0

    def test_ic_neither_wing_found_old_fails(self, mock_alpaca, mock_db_funcs):
        trade = _make_trade(
            status="pending_open",
            strategy_type="iron_condor",
            alpaca_client_order_id="ic-4",
            entry_date=(datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
        )
        mock_db_funcs["get_trades"].return_value = [trade]
        mock_alpaca.get_orders.return_value = []
        mock_alpaca.get_order_by_client_id.return_value = None

        rec = _make_reconciler(mock_alpaca)
        result = ReconciliationResult()
        rec._reconcile_pending_opens(result)

        assert result.pending_failed == 1
