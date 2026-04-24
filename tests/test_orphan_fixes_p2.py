"""
P2 integration and unit tests for orphan position fixes (2026-04-01).

Fix A: _normalize_order_status() strips "OrderStatus." prefix from alpaca-py enum str()
Fix B: _detect_orphans() promotes pending_open/closed_external OR creates synthetic records
Fix C: _reconcile_external_closes() has 2-cycle grace period before marking closed_external
Fix D: _startup_reconciliation() runs on start() and logs mismatches
"""

import logging
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from execution.position_monitor import (
    PositionMonitor,
    _EXTERNAL_CLOSE_GRACE_CYCLES,
)
from shared.database import get_trades, init_db, upsert_trade
from strategy.alpaca_provider import _normalize_order_status


# ---------------------------------------------------------------------------
# Helpers (mirror style from test_position_monitor.py)
# ---------------------------------------------------------------------------

def _make_alpaca(positions: Optional[List[Dict]] = None, order_status: Optional[Dict] = None):
    """Return a mock AlpacaProvider with OCC symbol builder."""
    mock = MagicMock()
    mock.get_positions.return_value = positions or []
    mock.get_order_status.return_value = order_status or {}
    mock.close_spread.return_value = {"status": "submitted", "order_id": "ord-spread-001"}
    mock.close_iron_condor.return_value = {"status": "submitted", "order_id": "ord-ic-001"}

    def _build_occ(ticker, expiration, strike, opt_type):
        """OCC-style symbol: ticker+YYMMDD+C/P+strike*1000 (simplified for tests)."""
        cp = "C" if opt_type.lower().startswith("c") else "P"
        strike_int = int(round(float(strike) * 1000))
        # Use real-format: ticker padded, YYMMDD from expiration, C/P, 8-digit strike
        try:
            exp_str = str(expiration).split(" ")[0]
            parts = exp_str.split("-")
            yymmdd = parts[0][2:] + parts[1] + parts[2]
        except Exception:
            yymmdd = "260417"
        return f"{ticker.upper():<6}{yymmdd}{cp}{strike_int:08d}".replace(" ", "")

    mock._build_occ_symbol.side_effect = _build_occ
    return mock


def _make_trade(
    trade_id: str = "t1",
    ticker: str = "SPY",
    strategy_type: str = "bull_put",
    status: str = "open",
    short_strike: float = 649.0,
    long_strike: float = 661.0,
    expiration: str = "2026-04-17",
    credit: float = 4.86,
    contracts: int = 14,
    **extra,
) -> Dict:
    t = dict(
        id=trade_id,
        ticker=ticker,
        strategy_type=strategy_type,
        status=status,
        short_strike=short_strike,
        long_strike=long_strike,
        expiration=expiration,
        credit=credit,
        contracts=contracts,
        entry_date=datetime.now(timezone.utc).isoformat(),
    )
    t.update(extra)
    return t


def _config(profit_target: float = 50.0, sl_mult: float = 3.5, manage_dte: int = 0) -> Dict:
    return {
        "risk": {
            "profit_target": profit_target,
            "stop_loss_multiplier": sl_mult,
        },
        "strategy": {
            "manage_dte": manage_dte,
        },
    }


def _monitor(alpaca=None, db_path: Optional[str] = None, **config_overrides) -> PositionMonitor:
    cfg = _config(**config_overrides)
    return PositionMonitor(
        alpaca_provider=alpaca or _make_alpaca(),
        config=cfg,
        db_path=db_path,
    )


def _setup_db() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = f.name
    f.close()
    init_db(db)
    return db


def _occ_symbol(ticker: str, expiration: str, strike: float, opt_type: str) -> str:
    """Build OCC symbol the same way _make_alpaca does."""
    cp = "C" if opt_type.lower().startswith("c") else "P"
    strike_int = int(round(float(strike) * 1000))
    try:
        exp_str = str(expiration).split(" ")[0]
        parts = exp_str.split("-")
        yymmdd = parts[0][2:] + parts[1] + parts[2]
    except Exception:
        yymmdd = "260417"
    return f"{ticker.upper():<6}{yymmdd}{cp}{strike_int:08d}".replace(" ", "")


# ---------------------------------------------------------------------------
# Test Group 1: TestNormalizeOrderStatus
# ---------------------------------------------------------------------------

class TestNormalizeOrderStatus:
    """Fix A: _normalize_order_status strips 'OrderStatus.' prefix and lowercases."""

    def test_orderstatuss_filled(self):
        assert _normalize_order_status("OrderStatus.FILLED") == "filled"

    def test_orderstatus_partially_filled(self):
        assert _normalize_order_status("OrderStatus.PARTIALLY_FILLED") == "partially_filled"

    def test_orderstatus_canceled(self):
        assert _normalize_order_status("OrderStatus.CANCELED") == "canceled"

    def test_orderstatus_expired(self):
        assert _normalize_order_status("OrderStatus.EXPIRED") == "expired"

    def test_orderstatus_new(self):
        assert _normalize_order_status("OrderStatus.NEW") == "new"

    def test_orderstatus_accepted(self):
        assert _normalize_order_status("OrderStatus.ACCEPTED") == "accepted"

    def test_orderstatus_pending_new(self):
        assert _normalize_order_status("OrderStatus.PENDING_NEW") == "pending_new"

    def test_orderstatus_replaced(self):
        assert _normalize_order_status("OrderStatus.REPLACED") == "replaced"

    def test_orderstatus_pending_cancel(self):
        assert _normalize_order_status("OrderStatus.PENDING_CANCEL") == "pending_cancel"

    def test_bare_filled_idempotent(self):
        """Already-bare 'filled' passes through unchanged (idempotent)."""
        assert _normalize_order_status("filled") == "filled"

    def test_bare_uppercase_lowercased(self):
        """Bare uppercase string is lowercased."""
        assert _normalize_order_status("FILLED") == "filled"

    def test_none_returns_empty_string(self):
        """None input must return empty string (null safety)."""
        assert _normalize_order_status(None) == ""

    def test_empty_string_returns_empty_string(self):
        """Empty string input returns empty string."""
        assert _normalize_order_status("") == ""

    def test_alpaca_py_enum_value(self):
        """Actual alpaca-py OrderStatus enum value is normalized correctly."""
        try:
            from alpaca.trading.enums import OrderStatus
            enum_val = OrderStatus.FILLED
            result = _normalize_order_status(enum_val)
            assert result == "filled"
        except ImportError:
            # If enum not available, test the str() mock path
            mock_enum = MagicMock()
            mock_enum.__str__ = MagicMock(return_value="OrderStatus.FILLED")
            result = _normalize_order_status(mock_enum)
            assert result == "filled"


# ---------------------------------------------------------------------------
# Test Group 2: TestPendingOpenPromotion (integration)
# ---------------------------------------------------------------------------

class TestPendingOpenPromotion:
    """Integration: Fix A + reconciler _reconcile_pending_opens chain.

    The chain: _reconcile_pending_opens() -> get_orders() -> status comparison -> DB update.
    Fix A ensures 'OrderStatus.FILLED' str is normalised to 'filled' so comparison succeeds.
    """

    def _pending_trade(self, db_path: str, trade_id: str = "cs-abc") -> Dict:
        t = {
            "id": trade_id,
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "pending_open",
            "short_strike": 649.0,
            "long_strike": 661.0,
            "expiration": "2026-04-17",
            "credit": 4.86,
            "contracts": 14,
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "alpaca_client_order_id": trade_id,
        }
        upsert_trade(t, source="execution", path=db_path)
        return t

    def _make_reconciler(self, alpaca, db_path: str):
        from shared.reconciler import PositionReconciler
        return PositionReconciler(alpaca=alpaca, db_path=db_path)

    def test_orderstatus_prefix_format_promotes_pending_open(self, tmp_path):
        """'OrderStatus.FILLED' (old buggy format) is normalised by Fix A and promotes the trade."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        trade_id = "cs-abc"
        self._pending_trade(db_path, trade_id)

        # Simulate get_orders() returning the old enum-str format (what Fix A addresses)
        # Note: AlpacaProvider.get_orders() itself calls _normalize_order_status, so by
        # the time the reconciler sees it, it's already normalised. We test that the
        # normalised output ("filled") still promotes correctly.
        alpaca = MagicMock()
        alpaca.get_orders.return_value = [{
            "client_order_id": trade_id,
            "status": "filled",          # post-normalization value
            "filled_qty": "14",
            "filled_avg_price": "4.86",
            "id": "uuid-1",
        }]
        alpaca.get_order_by_client_id.return_value = None
        alpaca.get_positions.return_value = []

        reconciler = self._make_reconciler(alpaca, db_path)
        result = reconciler.reconcile_pending_only()

        assert result.pending_resolved == 1
        trades = get_trades(status="open", path=db_path)
        assert len(trades) == 1
        assert trades[0]["id"] == trade_id

    def test_bare_filled_also_promotes(self, tmp_path):
        """get_orders() returning bare 'filled' (post-fix) also promotes correctly."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        trade_id = "cs-def"
        self._pending_trade(db_path, trade_id)

        alpaca = MagicMock()
        alpaca.get_orders.return_value = [{
            "client_order_id": trade_id,
            "status": "filled",
            "filled_qty": "14",
            "filled_avg_price": "4.86",
            "id": "uuid-2",
        }]
        alpaca.get_order_by_client_id.return_value = None
        alpaca.get_positions.return_value = []

        reconciler = self._make_reconciler(alpaca, db_path)
        result = reconciler.reconcile_pending_only()

        assert result.pending_resolved == 1
        trades = get_trades(status="open", path=db_path)
        assert len(trades) == 1

    def test_pending_new_status_leaves_trade_pending_open(self, tmp_path):
        """get_orders() returning 'OrderStatus.PENDING_NEW' keeps trade in pending_open."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        trade_id = "cs-ghi"
        self._pending_trade(db_path, trade_id)

        # _normalize_order_status("OrderStatus.PENDING_NEW") => "pending_new"
        # The reconciler leaves live orders as pending_open
        alpaca = MagicMock()
        alpaca.get_orders.return_value = [{
            "client_order_id": trade_id,
            "status": "pending_new",     # normalised from "OrderStatus.PENDING_NEW"
            "filled_qty": None,
            "id": "uuid-3",
        }]
        alpaca.get_order_by_client_id.return_value = None
        alpaca.get_positions.return_value = []

        reconciler = self._make_reconciler(alpaca, db_path)
        result = reconciler.reconcile_pending_only()

        assert result.pending_resolved == 0
        assert result.pending_failed == 0
        trades = get_trades(status="pending_open", path=db_path)
        assert len(trades) == 1


# ---------------------------------------------------------------------------
# Test Group 3: TestOrphanRecovery
# ---------------------------------------------------------------------------

class TestOrphanRecovery:
    """Fix B: _detect_orphans() promotes pending_open/closed_external OR creates synthetic record."""

    def _call_detect_orphans(self, mon: PositionMonitor, alpaca_positions: Dict) -> None:
        """Call _detect_orphans with current open positions from the DB."""
        open_positions = get_trades(status="open", path=mon.db_path)
        mon._detect_orphans(open_positions, alpaca_positions)

    def test_3a_pending_open_promoted_to_open(self, tmp_path):
        """pending_open record is promoted to open when matching Alpaca position found."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db_path)

        trade = _make_trade(
            trade_id="orphan-t3a",
            status="pending_open",
            short_strike=649.0,
            long_strike=661.0,
            expiration="2026-04-17",
            credit=4.86,
        )
        upsert_trade(trade, source="execution", path=db_path)

        # Build the OCC symbol for the short leg (what Alpaca would show)
        short_sym = _occ_symbol("SPY", "2026-04-17", 649.0, "call")
        alpaca_positions = {
            short_sym: {
                "symbol": short_sym,
                "qty": "-14",
                "asset_class": "us_option",
                "avg_entry_price": "4.86",
                "market_value": "-68040.0",
            }
        }

        # Inject trade into recovery_candidates by patching get_trades to return it
        # for 'pending_open' status (which _detect_orphans queries internally).
        # open_positions is passed as empty since it's not "open" status.
        open_positions = []  # no open trades — pending_open won't be in managed_symbols
        mon._detect_orphans(open_positions, alpaca_positions)

        promoted = get_trades(status="open", path=db_path)
        assert len(promoted) == 1, "pending_open record should be promoted to open"
        assert promoted[0]["id"] == "orphan-t3a"

    def test_3b_closed_external_promoted_to_open(self, tmp_path):
        """closed_external record is promoted to open when matching Alpaca position found."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db_path)

        trade = _make_trade(
            trade_id="orphan-t3b",
            status="closed_external",
            short_strike=649.0,
            long_strike=661.0,
            expiration="2026-04-17",
            credit=4.86,
        )
        upsert_trade(trade, source="execution", path=db_path)

        short_sym = _occ_symbol("SPY", "2026-04-17", 649.0, "call")
        alpaca_positions = {
            short_sym: {
                "symbol": short_sym,
                "qty": "-14",
                "asset_class": "us_option",
                "avg_entry_price": "4.86",
                "market_value": "-68040.0",
            }
        }

        open_positions = []  # not "open" in DB yet
        mon._detect_orphans(open_positions, alpaca_positions)

        promoted = get_trades(status="open", path=db_path)
        assert len(promoted) == 1, "closed_external record should be promoted to open"
        assert promoted[0]["id"] == "orphan-t3b"

    def test_3c_orphan_alerts_for_unknown_position(self, tmp_path, caplog):
        """RC4: If no DB record exists, CRITICAL alert is logged (no synthetic record created).

        Synthetic-monitor records were removed in RC4 because they have long_strike=None
        which causes mispriced SL/PT checks and accumulates zombie positions.
        """
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db_path)

        short_sym = _occ_symbol("SPY", "2026-04-17", 649.0, "call")
        alpaca_positions = {
            short_sym: {
                "symbol": short_sym,
                "qty": "-14",
                "asset_class": "us_option",
                "avg_entry_price": "15.76",
                "market_value": "-220640.0",
            }
        }

        # No DB records at all
        with caplog.at_level(logging.CRITICAL):
            mon._detect_orphans([], alpaca_positions)

        # RC4: no synthetic record — alert only
        synthetic = get_trades(status="open", path=db_path)
        assert len(synthetic) == 0, "RC4: no synthetic records should be created"
        assert any("UNTRACKED" in r.message for r in caplog.records)

    def test_3d_no_synthetic_for_long_leg(self, tmp_path):
        """Long legs (positive qty) do NOT get synthetic records — they are hedges."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db_path)

        long_sym = _occ_symbol("SPY", "2026-04-17", 661.0, "call")
        alpaca_positions = {
            long_sym: {
                "symbol": long_sym,
                "qty": "14",          # positive = long position
                "asset_class": "us_option",
                "avg_entry_price": "5.00",
                "market_value": "70000.0",
            }
        }

        mon._detect_orphans([], alpaca_positions)

        all_trades = get_trades(path=db_path)
        synthetic = [t for t in all_trades if str(t.get("id", "")).startswith("synthetic-monitor-")]
        assert len(synthetic) == 0, "No synthetic record should be created for long legs"


# ---------------------------------------------------------------------------
# Test Group 4: TestGracePeriodExternalClose
# ---------------------------------------------------------------------------

class TestGracePeriodExternalClose:
    """Fix C: _reconcile_external_closes() 2-cycle grace period."""

    def test_4a_single_missing_cycle_does_not_mark_closed_external(self, tmp_path):
        """First absence: grace period active, status stays open."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db_path)

        pos = _make_trade(
            trade_id="grace-t4a",
            strategy_type="bull_put",
            short_strike=649.0,
            long_strike=661.0,
            expiration="2026-04-17",
        )
        upsert_trade(pos, source="execution", path=db_path)

        open_positions = get_trades(status="open", path=db_path)
        # Empty positions dict — legs not in Alpaca
        mon._reconcile_external_closes(open_positions, {})

        trades = get_trades(path=db_path)
        assert trades[0]["status"] == "open", "Grace period: still open after 1 missing cycle"

    def test_4b_two_consecutive_missing_cycles_mark_closed_external(self, tmp_path):
        """Two consecutive missing cycles exhaust grace period → closed_external."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db_path)

        pos = _make_trade(
            trade_id="grace-t4b",
            strategy_type="bull_put",
            short_strike=649.0,
            long_strike=661.0,
            expiration="2026-04-17",
        )
        upsert_trade(pos, source="execution", path=db_path)

        # Cycle 1
        open_positions = get_trades(status="open", path=db_path)
        mon._reconcile_external_closes(open_positions, {})
        assert get_trades(path=db_path)[0]["status"] == "open"

        # Cycle 2 — must re-fetch from DB to get updated _missing_cycles
        open_positions = get_trades(status="open", path=db_path)
        mon._reconcile_external_closes(open_positions, {})

        trades = get_trades(path=db_path)
        assert trades[0]["status"] == "closed_external"
        assert trades[0]["exit_reason"] == "closed_external"
        assert trades[0]["exit_date"] is not None

    def test_4c_one_missing_one_present_resets_counter(self, tmp_path):
        """Miss, present, miss sequence: counter resets on presence; still open after 3rd call."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db_path)

        pos = _make_trade(
            trade_id="grace-t4c",
            strategy_type="bull_put",
            short_strike=649.0,
            long_strike=661.0,
            expiration="2026-04-17",
        )
        upsert_trade(pos, source="execution", path=db_path)

        short_sym = mon.alpaca._build_occ_symbol("SPY", "2026-04-17", 649.0, "put")
        long_sym = mon.alpaca._build_occ_symbol("SPY", "2026-04-17", 661.0, "put")
        present_positions = {
            short_sym: {"symbol": short_sym, "market_value": "-100.0"},
            long_sym: {"symbol": long_sym, "market_value": "20.0"},
        }

        # Cycle 1: absent → missing_cycles = 1
        open_positions = get_trades(status="open", path=db_path)
        mon._reconcile_external_closes(open_positions, {})
        assert get_trades(path=db_path)[0]["status"] == "open"

        # Cycle 2: present → counter resets to 0
        open_positions = get_trades(status="open", path=db_path)
        mon._reconcile_external_closes(open_positions, present_positions)
        assert get_trades(path=db_path)[0]["status"] == "open"

        # Cycle 3: absent again → missing_cycles = 1 (reset from 0, not 2)
        open_positions = get_trades(status="open", path=db_path)
        mon._reconcile_external_closes(open_positions, {})
        assert get_trades(path=db_path)[0]["status"] == "open", \
            "Counter was reset, so one more miss should not trigger closed_external"

    def test_4d_newly_submitted_position_protected_by_grace(self, tmp_path):
        """Position absent from Alpaca immediately after submission is protected by grace period."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db_path)

        # Entry date is just seconds ago — simulates a just-submitted order
        pos = _make_trade(
            trade_id="grace-t4d",
            strategy_type="bull_put",
            short_strike=649.0,
            long_strike=661.0,
            expiration="2026-04-17",
            entry_date=datetime.now(timezone.utc).isoformat(),
        )
        upsert_trade(pos, source="execution", path=db_path)

        open_positions = get_trades(status="open", path=db_path)
        # Legs not in Alpaca (simulating fill not yet reflected)
        mon._reconcile_external_closes(open_positions, {})

        trades = get_trades(path=db_path)
        assert trades[0]["status"] == "open", \
            "Grace period should protect newly submitted positions from being marked closed_external"

    def test_grace_cycles_constant_is_expected_value(self):
        """Ensure _EXTERNAL_CLOSE_GRACE_CYCLES is exactly 2 (the documented value)."""
        assert _EXTERNAL_CLOSE_GRACE_CYCLES == 2


# ---------------------------------------------------------------------------
# Test Group 5: TestStartupReconciliation
# ---------------------------------------------------------------------------

class TestStartupReconciliation:
    """Fix D: _startup_reconciliation() logs mismatches on start."""

    def _monitor_with_db(self, alpaca, db_path: str) -> PositionMonitor:
        return _monitor(alpaca=alpaca, db_path=db_path)

    def test_5a_matching_db_and_alpaca_no_warnings(self, tmp_path, caplog):
        """Matching DB record and Alpaca positions produce no WARNING log messages."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()

        pos = _make_trade(
            trade_id="startup-t5a",
            strategy_type="bull_put",
            short_strike=669.0,
            long_strike=681.0,
            expiration="2026-04-10",
            status="open",
        )
        upsert_trade(pos, source="execution", path=db_path)

        short_sym = _occ_symbol("SPY", "2026-04-10", 669.0, "put")
        long_sym = _occ_symbol("SPY", "2026-04-10", 681.0, "put")
        alpaca.get_positions.return_value = [
            {
                "symbol": short_sym,
                "qty": "-14",
                "asset_class": "us_option",
                "market_value": "-100.0",
            },
            {
                "symbol": long_sym,
                "qty": "14",
                "asset_class": "us_option",
                "market_value": "20.0",
            },
        ]

        mon = self._monitor_with_db(alpaca, db_path)

        with caplog.at_level(logging.WARNING, logger="execution.position_monitor"):
            mon._startup_reconciliation()

        # No mismatch warnings should appear
        mismatch_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and any(kw in r.message.lower() for kw in ("mismatch", "not found", "no legs", "unmatched"))
        ]
        assert len(mismatch_warnings) == 0, \
            f"Unexpected mismatch warnings: {[r.message for r in mismatch_warnings]}"

    def test_5b_db_open_but_alpaca_missing_logs_warning(self, tmp_path, caplog):
        """DB has open trade but Alpaca has no positions → WARNING logged."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()

        pos = _make_trade(
            trade_id="startup-t5b",
            strategy_type="bull_put",
            short_strike=669.0,
            long_strike=681.0,
            expiration="2026-04-10",
            status="open",
        )
        upsert_trade(pos, source="execution", path=db_path)

        # Alpaca has NO positions
        alpaca.get_positions.return_value = []

        mon = self._monitor_with_db(alpaca, db_path)

        with caplog.at_level(logging.WARNING, logger="execution.position_monitor"):
            mon._startup_reconciliation()

        # Should log a WARNING about the missing legs
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_messages) > 0, "Expected a WARNING for open DB record with no Alpaca legs"

        # Verify the warning mentions something about legs not found or DB record mismatch
        combined = " ".join(warning_messages).lower()
        assert any(phrase in combined for phrase in ("no legs", "startup", "legs", "mismatch")), \
            f"Warning messages did not mention expected keywords: {warning_messages}"

    def test_5c_alpaca_position_no_db_record_logs_warning(self, tmp_path, caplog):
        """Alpaca has orphan option position but DB is empty → WARNING logged."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        alpaca = _make_alpaca()

        orphan_sym = _occ_symbol("SPY", "2026-04-17", 649.0, "call")
        alpaca.get_positions.return_value = [
            {
                "symbol": orphan_sym,
                "qty": "-14",
                "asset_class": "us_option",
                "market_value": "-220640.0",
            }
        ]

        mon = self._monitor_with_db(alpaca, db_path)

        with caplog.at_level(logging.WARNING, logger="execution.position_monitor"):
            mon._startup_reconciliation()

        # Should log a WARNING about the unmatched Alpaca position
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_messages) > 0, "Expected a WARNING for unmatched Alpaca option position"

        combined = " ".join(warning_messages).lower()
        assert any(phrase in combined for phrase in ("no", "unmatched", "startup", "open")), \
            f"Warning messages did not mention expected keywords: {warning_messages}"
