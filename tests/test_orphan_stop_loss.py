"""
Tests for orphan stop-loss monitoring (fix/stop-monitor-orphans).

Covers:
  - _parse_occ_symbol: correct parsing of OCC option symbols
  - _detect_orphans: synthetic-monitor record creation, recovery promotion,
    long-leg skipping
  - Stop-loss fires on synthetic records via _check_exit_conditions
  - Single-leg close path in _close_position
  - _reconcile_external_closes grace period (_EXTERNAL_CLOSE_GRACE_CYCLES)
  - _startup_reconciliation logging
"""
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from execution.position_monitor import (
    PositionMonitor,
    _EXTERNAL_CLOSE_GRACE_CYCLES,
    _parse_occ_symbol,
)
from shared.database import get_trades, init_db, upsert_trade


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _occ(ticker: str, expiration: str, strike: float, opt_type: str) -> str:
    """Build an OCC symbol string the same way AlpacaProvider does."""
    cp = "C" if opt_type.lower().startswith("c") else "P"
    strike_int = int(round(float(strike) * 1000))
    parts = str(expiration).split("-")
    yymmdd = parts[0][2:] + parts[1] + parts[2]
    return f"{ticker.upper():<6}{yymmdd}{cp}{strike_int:08d}".replace(" ", "")


def _make_alpaca(positions: Optional[List[Dict]] = None):
    """Return a minimal mock AlpacaProvider."""
    mock = MagicMock()
    mock.get_positions.return_value = positions or []
    mock.close_spread.return_value = {"status": "submitted", "order_id": "ord-spread-001"}
    mock.submit_single_leg.return_value = {"status": "submitted", "order_id": "ord-single-001"}
    mock.get_orders.return_value = []
    mock.get_order_status.return_value = {}

    def _build_occ(ticker, expiration, strike, opt_type):
        return _occ(ticker, expiration, strike, opt_type)

    mock._build_occ_symbol.side_effect = _build_occ
    return mock


def _config(sl_mult: float = 1.25, profit_target: float = 55.0) -> Dict:
    return {
        "risk": {
            "profit_target": profit_target,
            "stop_loss_multiplier": sl_mult,
        },
        "strategy": {"manage_dte": 0},
    }


def _monitor(alpaca=None, db_path: Optional[str] = None, **cfg_kw) -> PositionMonitor:
    return PositionMonitor(
        alpaca_provider=alpaca or _make_alpaca(),
        config=_config(**cfg_kw),
        db_path=db_path,
    )


def _setup_db(tmp_path) -> str:
    db = str(tmp_path / "test.db")
    init_db(db)
    return db


# ---------------------------------------------------------------------------
# 1. _parse_occ_symbol
# ---------------------------------------------------------------------------

class TestParseOccSymbol:
    def test_spy_call(self):
        sym = _occ("SPY", "2026-04-17", 668.0, "call")
        result = _parse_occ_symbol(sym)
        assert result is not None
        ticker, exp, opt_type, strike = result
        assert ticker == "SPY"
        assert exp == "2026-04-17"
        assert opt_type == "call"
        assert abs(strike - 668.0) < 0.001

    def test_spy_put(self):
        sym = _occ("SPY", "2026-04-17", 649.0, "put")
        result = _parse_occ_symbol(sym)
        assert result is not None
        ticker, exp, opt_type, strike = result
        assert ticker == "SPY"
        assert opt_type == "put"
        assert abs(strike - 649.0) < 0.001

    def test_multichar_ticker_soxx(self):
        sym = _occ("SOXX", "2026-04-17", 200.0, "call")
        result = _parse_occ_symbol(sym)
        assert result is not None
        assert result[0] == "SOXX"

    def test_fractional_strike(self):
        sym = _occ("SPY", "2026-04-17", 570.5, "put")
        result = _parse_occ_symbol(sym)
        assert result is not None
        assert abs(result[3] - 570.5) < 0.001

    def test_invalid_symbol_returns_none(self):
        assert _parse_occ_symbol("NOT_AN_OCC_SYMBOL") is None
        assert _parse_occ_symbol("") is None
        assert _parse_occ_symbol("12345") is None


# ---------------------------------------------------------------------------
# 2. _detect_orphans — synthetic record creation
# ---------------------------------------------------------------------------

class TestDetectOrphansSynthetic:

    def test_short_orphan_creates_synthetic_record(self, tmp_path):
        """Short orphan option with no DB record → synthetic-monitor-* created with status=open."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 668.0, "call")
        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": "-5",
                "asset_class": "us_option",
                "avg_entry_price": "2.50",
                "market_value": "-1500.0",
            }
        }
        mon = _monitor(db_path=db)
        mon._detect_orphans([], alpaca_positions)

        trades = get_trades(status="open", path=db)
        assert len(trades) == 1
        rec = trades[0]
        assert rec["id"].startswith("synthetic-monitor-")
        assert rec["ticker"] == "SPY"
        assert rec["strategy_type"] == "bear_call"
        assert abs(rec["short_strike"] - 668.0) < 0.001
        assert rec["long_strike"] is None
        assert abs(rec["credit"] - 2.50) < 0.001
        assert rec["contracts"] == 5
        assert rec["expiration"] == "2026-04-17"

    def test_long_orphan_no_synthetic_created(self, tmp_path):
        """Long (positive qty) orphan option → no synthetic record created."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 682.0, "call")
        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": "5",   # positive = long
                "asset_class": "us_option",
                "avg_entry_price": "1.00",
                "market_value": "500.0",
            }
        }
        mon = _monitor(db_path=db)
        mon._detect_orphans([], alpaca_positions)

        all_trades = get_trades(path=db)
        synthetic = [t for t in all_trades if str(t.get("id", "")).startswith("synthetic-monitor-")]
        assert len(synthetic) == 0

    def test_managed_symbol_not_flagged(self, tmp_path):
        """Symbol tracked by an open DB trade is not treated as orphan."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 649.0, "put")
        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": "-14",
                "asset_class": "us_option",
                "avg_entry_price": "4.86",
                "market_value": "-68040.0",
            }
        }
        # Simulate an open DB trade covering this symbol
        open_pos = {
            "id": "t-managed",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "open",
            "short_strike": 649.0,
            "long_strike": 661.0,
            "expiration": "2026-04-17",
            "credit": 4.86,
            "contracts": 14,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        }
        upsert_trade(open_pos, source="execution", path=db)

        mon = _monitor(db_path=db)
        open_positions = get_trades(status="open", path=db)
        mon._detect_orphans(open_positions, alpaca_positions)

        all_trades = get_trades(path=db)
        synthetic = [t for t in all_trades if str(t.get("id", "")).startswith("synthetic-monitor-")]
        assert len(synthetic) == 0

    def test_non_option_position_ignored(self, tmp_path):
        """Non-option Alpaca positions are not treated as orphans."""
        db = _setup_db(tmp_path)
        alpaca_positions = {
            "SPY": {
                "symbol": "SPY",
                "qty": "100",
                "asset_class": "us_equity",
                "avg_entry_price": "550.0",
                "market_value": "55000.0",
            }
        }
        mon = _monitor(db_path=db)
        mon._detect_orphans([], alpaca_positions)

        all_trades = get_trades(path=db)
        assert len(all_trades) == 0

    def test_synthetic_record_idempotent(self, tmp_path):
        """Calling _detect_orphans twice for the same orphan doesn't create duplicate records."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 668.0, "call")
        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": "-5",
                "asset_class": "us_option",
                "avg_entry_price": "2.50",
                "market_value": "-1500.0",
            }
        }
        mon = _monitor(db_path=db)
        mon._detect_orphans([], alpaca_positions)
        mon._detect_orphans([], alpaca_positions)  # second call

        trades = get_trades(status="open", path=db)
        assert len(trades) == 1  # still one record


# ---------------------------------------------------------------------------
# 3. _detect_orphans — recovery (pending_open / closed_external promotion)
# ---------------------------------------------------------------------------

class TestDetectOrphansRecovery:

    def test_pending_open_promoted_to_open(self, tmp_path):
        """pending_open trade with matching Alpaca position is promoted to open."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 649.0, "put")
        trade = {
            "id": "t-pending-001",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "pending_open",
            "short_strike": 649.0,
            "long_strike": 661.0,
            "expiration": "2026-04-17",
            "credit": 4.86,
            "contracts": 14,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        }
        upsert_trade(trade, source="execution", path=db)

        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": "-14",
                "asset_class": "us_option",
                "avg_entry_price": "4.86",
                "market_value": "-68040.0",
            }
        }
        mon = _monitor(db_path=db)
        mon._detect_orphans([], alpaca_positions)

        promoted = get_trades(status="open", path=db)
        assert len(promoted) == 1
        assert promoted[0]["id"] == "t-pending-001"

    def test_closed_external_promoted_to_open(self, tmp_path):
        """closed_external trade with matching Alpaca position is promoted to open."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 649.0, "put")
        trade = {
            "id": "t-closed-ext-001",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "closed_external",
            "short_strike": 649.0,
            "long_strike": 661.0,
            "expiration": "2026-04-17",
            "credit": 4.86,
            "contracts": 14,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        }
        upsert_trade(trade, source="execution", path=db)

        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": "-14",
                "asset_class": "us_option",
                "avg_entry_price": "4.86",
                "market_value": "-68040.0",
            }
        }
        mon = _monitor(db_path=db)
        mon._detect_orphans([], alpaca_positions)

        promoted = get_trades(status="open", path=db)
        assert len(promoted) == 1
        assert promoted[0]["id"] == "t-closed-ext-001"


# ---------------------------------------------------------------------------
# 4. Stop-loss fires on synthetic records
# ---------------------------------------------------------------------------

class TestOrphanStopLoss:
    """
    After a synthetic record is created by _detect_orphans, _check_exit_conditions
    must fire the stop-loss when the option value exceeds (1 + sl_mult) * credit.
    """

    def _make_synthetic(
        self,
        db: str,
        symbol: str,
        ticker: str = "SPY",
        strike: float = 668.0,
        expiration: str = "2026-04-17",
        opt_type: str = "call",
        credit: float = 2.50,
        contracts: int = 5,
    ) -> Dict:
        strategy_type = "bear_call" if opt_type == "call" else "bull_put"
        rec = {
            "id": f"synthetic-monitor-{symbol}",
            "ticker": ticker,
            "strategy_type": strategy_type,
            "status": "open",
            "short_strike": strike,
            "long_strike": None,
            "expiration": expiration,
            "credit": credit,
            "contracts": contracts,
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "alpaca_symbol": symbol,
        }
        upsert_trade(rec, source="execution", path=db)
        return rec

    def test_stop_loss_fires_when_loss_exceeds_threshold(self, tmp_path):
        """Stop-loss fires when single-leg cost-to-close >= (1 + sl_mult) * credit."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 668.0, "call")
        credit = 2.50
        sl_mult = 1.25
        contracts = 5

        # At stop: current cost = (1 + 1.25) * 2.50 = 5.625 per share
        # market_value of short position = -(5.625 * 5 * 100) = -2812.5
        cost_at_stop = (1 + sl_mult) * credit
        mv_at_stop = -(cost_at_stop * contracts * 100)

        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": f"-{contracts}",
                "asset_class": "us_option",
                "avg_entry_price": str(credit),
                "market_value": str(mv_at_stop),
            }
        }
        mon = _monitor(db_path=db, sl_mult=sl_mult)
        pos = self._make_synthetic(db, sym, credit=credit, contracts=contracts)

        exit_reason = mon._check_exit_conditions(pos, alpaca_positions)
        assert exit_reason == "stop_loss"

    def test_stop_loss_does_not_fire_below_threshold(self, tmp_path):
        """Stop-loss does NOT fire when loss is below the threshold."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 668.0, "call")
        credit = 2.50
        sl_mult = 1.25
        contracts = 5

        # Below threshold: cost = 1.0x credit = 2.50 per share
        cost_below_stop = 1.0 * credit
        mv_below_stop = -(cost_below_stop * contracts * 100)

        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": f"-{contracts}",
                "asset_class": "us_option",
                "avg_entry_price": str(credit),
                "market_value": str(mv_below_stop),
            }
        }
        mon = _monitor(db_path=db, sl_mult=sl_mult)
        pos = self._make_synthetic(db, sym, credit=credit, contracts=contracts)

        exit_reason = mon._check_exit_conditions(pos, alpaca_positions)
        assert exit_reason is None

    def test_no_credit_skips_stop_loss(self, tmp_path):
        """Synthetic with no credit (avg_entry_price=0) skips stop-loss check."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 668.0, "call")

        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": "-5",
                "asset_class": "us_option",
                "avg_entry_price": "0",
                "market_value": "-500.0",
            }
        }
        mon = _monitor(db_path=db)
        pos = self._make_synthetic(db, sym, credit=0.0)

        exit_reason = mon._check_exit_conditions(pos, alpaca_positions)
        assert exit_reason is None  # credit=0 → skip

    def test_full_cycle_detect_and_stop(self, tmp_path):
        """Full cycle: orphan detected → synthetic created → stop fires → close submitted."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 668.0, "call")
        credit = 2.50
        sl_mult = 1.25
        contracts = 5

        cost_at_stop = (1 + sl_mult) * credit
        mv_at_stop = -(cost_at_stop * contracts * 100)

        alpaca_positions = {
            sym: {
                "symbol": sym,
                "qty": f"-{contracts}",
                "asset_class": "us_option",
                "avg_entry_price": str(credit),
                "market_value": str(mv_at_stop),
            }
        }
        alpaca = _make_alpaca(
            positions=[
                {
                    "symbol": sym,
                    "qty": f"-{contracts}",
                    "asset_class": "us_option",
                    "avg_entry_price": str(credit),
                    "market_value": str(mv_at_stop),
                }
            ]
        )
        mon = _monitor(alpaca=alpaca, db_path=db, sl_mult=sl_mult)

        # Step 1: detect orphan → creates synthetic record
        mon._detect_orphans([], alpaca_positions)

        synthetics = get_trades(status="open", path=db)
        assert len(synthetics) == 1
        assert synthetics[0]["credit"] == credit

        # Step 2: check exit conditions → stop fires
        pos = synthetics[0]
        exit_reason = mon._check_exit_conditions(pos, alpaca_positions)
        assert exit_reason == "stop_loss"

        # Step 3: close order submitted via single-leg path
        mon._close_position(pos, exit_reason)
        alpaca.submit_single_leg.assert_called_once()
        call_kwargs = alpaca.submit_single_leg.call_args[1]
        assert call_kwargs["side"] == "buy"
        assert call_kwargs["option_type"] == "call"


# ---------------------------------------------------------------------------
# 5. Single-leg close path in _close_position
# ---------------------------------------------------------------------------

class TestSingleLegClose:

    def test_single_leg_calls_submit_single_leg(self, tmp_path):
        """_close_position with no long_strike uses submit_single_leg, not close_spread."""
        db = _setup_db(tmp_path)
        sym = _occ("SPY", "2026-04-17", 668.0, "call")
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db)

        pos = {
            "id": "synthetic-monitor-test",
            "ticker": "SPY",
            "strategy_type": "bear_call",
            "status": "open",
            "short_strike": 668.0,
            "long_strike": None,
            "expiration": "2026-04-17",
            "credit": 2.50,
            "contracts": 5,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        }
        upsert_trade(pos, source="execution", path=db)

        mon._close_position(pos, "stop_loss")

        alpaca.submit_single_leg.assert_called_once()
        alpaca.close_spread.assert_not_called()

    def test_two_leg_calls_close_spread(self, tmp_path):
        """_close_position with both strikes uses close_spread (normal path)."""
        db = _setup_db(tmp_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db)

        pos = {
            "id": "t-two-leg",
            "ticker": "SPY",
            "strategy_type": "bear_call",
            "status": "open",
            "short_strike": 668.0,
            "long_strike": 682.0,
            "expiration": "2026-04-17",
            "credit": 1.50,
            "contracts": 5,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        }
        upsert_trade(pos, source="execution", path=db)

        mon._close_position(pos, "stop_loss")

        alpaca.close_spread.assert_called_once()
        alpaca.submit_single_leg.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Grace period for external closes
# ---------------------------------------------------------------------------

class TestExternalCloseGracePeriod:

    def test_constant_value(self):
        assert _EXTERNAL_CLOSE_GRACE_CYCLES == 2

    def test_single_missing_cycle_stays_open(self, tmp_path):
        """1 consecutive missing cycle: grace period, trade stays open."""
        db = _setup_db(tmp_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db)

        pos = {
            "id": "grace-t1",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "open",
            "short_strike": 649.0,
            "long_strike": 661.0,
            "expiration": "2026-04-17",
            "credit": 4.86,
            "contracts": 14,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        }
        upsert_trade(pos, source="execution", path=db)

        open_positions = get_trades(status="open", path=db)
        mon._reconcile_external_closes(open_positions, {})  # legs absent

        trades = get_trades(path=db)
        assert trades[0]["status"] == "open"

    def test_two_consecutive_missing_cycles_closed_external(self, tmp_path):
        """2 consecutive missing cycles: grace exhausted, marked closed_external."""
        db = _setup_db(tmp_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db)

        pos = {
            "id": "grace-t2",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "open",
            "short_strike": 649.0,
            "long_strike": 661.0,
            "expiration": "2026-04-17",
            "credit": 4.86,
            "contracts": 14,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        }
        upsert_trade(pos, source="execution", path=db)

        # Cycle 1
        open_positions = get_trades(status="open", path=db)
        mon._reconcile_external_closes(open_positions, {})
        assert get_trades(path=db)[0]["status"] == "open"

        # Cycle 2 — re-fetch so _missing_legs_count from DB is loaded
        open_positions = get_trades(status="open", path=db)
        mon._reconcile_external_closes(open_positions, {})

        trades = get_trades(path=db)
        assert trades[0]["status"] == "closed_external"
        assert trades[0]["exit_reason"] == "closed_external"
        assert trades[0]["exit_date"] is not None

    def test_counter_resets_when_legs_reappear(self, tmp_path):
        """Miss → present → miss: counter resets on presence; still open after 3rd call."""
        db = _setup_db(tmp_path)
        alpaca = _make_alpaca()
        mon = _monitor(alpaca=alpaca, db_path=db)

        pos = {
            "id": "grace-t3",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "open",
            "short_strike": 649.0,
            "long_strike": 661.0,
            "expiration": "2026-04-17",
            "credit": 4.86,
            "contracts": 14,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        }
        upsert_trade(pos, source="execution", path=db)

        short_sym = _occ("SPY", "2026-04-17", 649.0, "put")
        long_sym = _occ("SPY", "2026-04-17", 661.0, "put")
        present = {
            short_sym: {"symbol": short_sym, "market_value": "-100.0"},
            long_sym: {"symbol": long_sym, "market_value": "20.0"},
        }

        # Cycle 1: absent → _missing_legs_count = 1
        open_positions = get_trades(status="open", path=db)
        mon._reconcile_external_closes(open_positions, {})
        assert get_trades(path=db)[0]["status"] == "open"

        # Cycle 2: present → counter reset to 0
        open_positions = get_trades(status="open", path=db)
        mon._reconcile_external_closes(open_positions, present)
        assert get_trades(path=db)[0]["status"] == "open"

        # Cycle 3: absent again → _missing_legs_count = 1 (reset, not 2)
        open_positions = get_trades(status="open", path=db)
        mon._reconcile_external_closes(open_positions, {})
        assert get_trades(path=db)[0]["status"] == "open", \
            "Counter was reset; one more miss should not trigger closed_external"
