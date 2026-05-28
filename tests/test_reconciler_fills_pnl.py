"""Tests for fills-based PnL + pending_reconciliation self-heal (Option B + D).

Covers:
  * PnL computed from /v2/orders fills (profit/loss, options ×100 multiplier)
  * pending_reconciliation retry counting + escalation after MAX retries
  * self-heal sweep recovers a stuck trade (the EXP-3303B race)
  * integration: /v2/positions lagging fills → trade closes cleanly, never
    hitting needs_investigation
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from shared.database import get_trade_by_id, init_db, upsert_trade
from shared.reconciler import (
    MAX_RECONCILE_RETRIES,
    NEEDS_INVESTIGATION,
    PENDING_RECONCILIATION,
    PositionReconciler,
    ReconciliationResult,
)

NOW = datetime.now(timezone.utc)
ENTRY = NOW - timedelta(hours=1)          # well outside the 90s entry-fill grace


def _occ(ticker, exp, strike, opt_type):
    t = "P" if "put" in opt_type.lower() else "C"
    return f"{ticker}{exp.replace('-', '')}{t}{int(round(float(strike) * 1000)):08d}"


def _db(tmp_path):
    path = str(tmp_path / "recon.db")
    init_db(path)
    return path


def _trade(**over):
    t = {
        "id": "cs-test-1", "ticker": "SPY", "strategy_type": "bull_put",
        "status": "open", "short_strike": 540.0, "long_strike": 535.0,
        "expiration": "2026-06-12", "credit": 1.00, "contracts": 10,
        "entry_date": ENTRY.isoformat(),
    }
    t.update(over)
    return t


def _simple_order(oid, symbol, side, qty, price, filled_at=NOW, status="filled"):
    return {
        "id": oid, "client_order_id": oid, "status": status, "side": side,
        "symbol": symbol, "qty": str(qty), "filled_avg_price": str(price),
        "filled_at": filled_at.isoformat(), "legs": [],
    }


def _entry_mleg(trade):
    short = _occ("SPY", "2026-06-12", trade["short_strike"], "put")
    long_ = _occ("SPY", "2026-06-12", trade["long_strike"], "put")
    return {
        "id": "entry-mleg", "client_order_id": "entry-mleg", "status": "filled",
        "side": "sell", "symbol": "SPY", "qty": str(trade["contracts"]),
        "filled_avg_price": str(trade["credit"]), "filled_at": ENTRY.isoformat(),
        "legs": [
            {"symbol": short, "side": "sell", "qty": str(trade["contracts"]), "status": "filled"},
            {"symbol": long_, "side": "buy", "qty": str(trade["contracts"]), "status": "filled"},
        ],
    }


def _close_orders(trade, short_buy_price, long_sell_price):
    short = _occ("SPY", "2026-06-12", trade["short_strike"], "put")
    long_ = _occ("SPY", "2026-06-12", trade["long_strike"], "put")
    return [
        _simple_order("close-short", short, "buy", trade["contracts"], short_buy_price),
        _simple_order("close-long", long_, "sell", trade["contracts"], long_sell_price),
    ]


def _alpaca(orders=None, positions=None, activities=None):
    a = MagicMock()
    a._build_occ_symbol.side_effect = _occ
    a.get_orders.return_value = orders or []
    a.get_positions.return_value = positions or []

    def _acts(activity_type=None, since=None):
        return (activities or {}).get(activity_type, [])
    a.get_account_activities.side_effect = _acts
    return a


# ---------------------------------------------------------------------------
# 1. PnL from fills
# ---------------------------------------------------------------------------

def test_pnl_from_fills_profit():
    trade = _trade()
    orders = [_entry_mleg(trade)] + _close_orders(trade, short_buy_price=0.40, long_sell_price=0.10)
    rec = PositionReconciler(alpaca=_alpaca(orders=orders))
    pnl, oid = rec._compute_close_pnl_from_orders(trade)
    # credit 1.00*10*100=1000 ; close_cash = -0.40*10*100 + 0.10*10*100 = -300
    # roundtrip comm = 0.65*10*2 * 2 = 26 ; pnl = 1000 - 300 - 26 = 674
    assert round(pnl, 2) == 674.0
    assert oid in ("close-short", "close-long")


def test_pnl_from_fills_loss():
    trade = _trade()
    orders = [_entry_mleg(trade)] + _close_orders(trade, short_buy_price=1.50, long_sell_price=0.30)
    rec = PositionReconciler(alpaca=_alpaca(orders=orders))
    pnl, _ = rec._compute_close_pnl_from_orders(trade)
    # close_cash = -1500 + 300 = -1200 ; pnl = 1000 - 1200 - 26 = -226
    assert round(pnl, 2) == -226.0


def test_pnl_options_multiplier_applied():
    trade = _trade(contracts=1, credit=2.00)
    orders = [_entry_mleg(trade)] + _close_orders(trade, short_buy_price=0.50, long_sell_price=0.00)
    rec = PositionReconciler(alpaca=_alpaca(orders=orders))
    pnl, _ = rec._compute_close_pnl_from_orders(trade)
    # 2.00*1*100=200 ; close_cash=-50+0=-50 ; comm=0.65*1*2*2=2.6 ; pnl=200-50-2.6=147.4
    assert round(pnl, 2) == 147.4


def test_pnl_none_when_a_leg_has_no_closing_order():
    trade = _trade()
    short = _occ("SPY", "2026-06-12", trade["short_strike"], "put")
    # only the short leg has a closing order; long leg still open
    orders = [_entry_mleg(trade), _simple_order("close-short", short, "buy", 10, 0.40)]
    rec = PositionReconciler(alpaca=_alpaca(orders=orders))
    assert rec._compute_close_pnl_from_orders(trade) is None


def test_pnl_ignores_entry_fills_within_grace():
    """An entry order filled at entry_date must not be counted as a close."""
    trade = _trade()
    # No closing orders at all — only the entry MLEG present.
    rec = PositionReconciler(alpaca=_alpaca(orders=[_entry_mleg(trade)]))
    assert rec._compute_close_pnl_from_orders(trade) is None


# ---------------------------------------------------------------------------
# 2. pending_reconciliation transitions + retry escalation
# ---------------------------------------------------------------------------

def test_pending_retry_then_escalate(tmp_path):
    db = _db(tmp_path)
    upsert_trade(_trade(id="cs-pend"), source="execution", path=db)
    rec = PositionReconciler(alpaca=_alpaca(), db_path=db)
    result = ReconciliationResult()

    statuses = []
    for _ in range(MAX_RECONCILE_RETRIES + 1):
        t = get_trade_by_id("cs-pend", path=db)
        rec._mark_pending_or_escalate(t, result, reason="awaiting_close_fills")
        statuses.append(get_trade_by_id("cs-pend", path=db)["status"])

    # First MAX_RECONCILE_RETRIES (5) attempts stay pending, then escalate.
    assert statuses[:MAX_RECONCILE_RETRIES] == [PENDING_RECONCILIATION] * MAX_RECONCILE_RETRIES
    assert statuses[-1] == NEEDS_INVESTIGATION
    final = get_trade_by_id("cs-pend", path=db)
    assert int(final["retry_count"]) == MAX_RECONCILE_RETRIES + 1


def test_retry_count_increments(tmp_path):
    db = _db(tmp_path)
    upsert_trade(_trade(id="cs-r"), source="execution", path=db)
    rec = PositionReconciler(alpaca=_alpaca(), db_path=db)
    result = ReconciliationResult()
    rec._mark_pending_or_escalate(get_trade_by_id("cs-r", path=db), result, reason="x")
    assert int(get_trade_by_id("cs-r", path=db)["retry_count"]) == 1
    rec._mark_pending_or_escalate(get_trade_by_id("cs-r", path=db), result, reason="x")
    assert int(get_trade_by_id("cs-r", path=db)["retry_count"]) == 2


# ---------------------------------------------------------------------------
# 3. self-heal sweep
# ---------------------------------------------------------------------------

def test_self_heal_closes_stuck_trade_legs_gone(tmp_path):
    """EXP-3303B scenario: stuck needs_investigation, legs flat, fills present."""
    db = _db(tmp_path)
    upsert_trade(_trade(id="cs-stuck", status=NEEDS_INVESTIGATION,
                        exit_reason="activity_undetermined_pnl"),
                 source="execution", path=db)
    trade = _trade(id="cs-stuck")
    orders = [_entry_mleg(trade)] + _close_orders(trade, 0.40, 0.10)
    rec = PositionReconciler(alpaca=_alpaca(orders=orders, positions=[]), db_path=db)
    result = ReconciliationResult()

    rec._resolve_stuck_trades(result, alpaca_positions={})  # legs absent

    healed = get_trade_by_id("cs-stuck", path=db)
    assert healed["status"] == "closed_profit"
    assert round(healed["pnl"], 2) == 674.0
    assert result.externally_closed == 1


def test_self_heal_skips_when_legs_still_present(tmp_path):
    db = _db(tmp_path)
    upsert_trade(_trade(id="cs-live", status=PENDING_RECONCILIATION),
                 source="execution", path=db)
    trade = _trade(id="cs-live")
    short = _occ("SPY", "2026-06-12", 540.0, "put")
    orders = [_entry_mleg(trade)] + _close_orders(trade, 0.40, 0.10)
    rec = PositionReconciler(alpaca=_alpaca(orders=orders), db_path=db)
    result = ReconciliationResult()

    # Alpaca still reports the short leg as held → genuinely open, don't close.
    rec._resolve_stuck_trades(result, alpaca_positions={short: {"qty": "-10"}})

    assert get_trade_by_id("cs-live", path=db)["status"] == PENDING_RECONCILIATION
    assert result.externally_closed == 0


def test_self_heal_skips_assignment(tmp_path):
    db = _db(tmp_path)
    upsert_trade(_trade(id="cs-asgn", status=NEEDS_INVESTIGATION, exit_reason="assignment"),
                 source="execution", path=db)
    trade = _trade(id="cs-asgn")
    orders = [_entry_mleg(trade)] + _close_orders(trade, 0.40, 0.10)
    rec = PositionReconciler(alpaca=_alpaca(orders=orders), db_path=db)
    result = ReconciliationResult()
    rec._resolve_stuck_trades(result, alpaca_positions={})
    # Assignments are left for manual reconciliation.
    assert get_trade_by_id("cs-asgn", path=db)["status"] == NEEDS_INVESTIGATION
    assert result.externally_closed == 0


# ---------------------------------------------------------------------------
# 4. integration — positions lagging fills must NOT mark needs_investigation
# ---------------------------------------------------------------------------

def test_close_via_fills_while_positions_lag(tmp_path):
    db = _db(tmp_path)
    upsert_trade(_trade(id="cs-lag", status="open"), source="execution", path=db)
    trade = _trade(id="cs-lag")
    short = _occ("SPY", "2026-06-12", 540.0, "put")
    long_ = _occ("SPY", "2026-06-12", 535.0, "put")

    orders = [_entry_mleg(trade)] + _close_orders(trade, 0.40, 0.10)
    # FILL close activities present (closing intent, after grace)…
    fills = [
        {"id": "f1", "symbol": short, "activity_type": "FILL",
         "activity_subtype": "buy_to_close", "transaction_time": NOW.isoformat(),
         "net_amount": -400.0},
        {"id": "f2", "symbol": long_, "activity_type": "FILL",
         "activity_subtype": "sell_to_close", "transaction_time": NOW.isoformat(),
         "net_amount": 100.0},
    ]
    # …but /v2/positions STILL reports both legs (broker propagation lag).
    positions = [{"symbol": short, "qty": "-10"}, {"symbol": long_, "qty": "10"}]
    rec = PositionReconciler(
        alpaca=_alpaca(orders=orders, positions=positions,
                       activities={"FILL": fills, "OPEXP": [], "OASGN": []}),
        db_path=db,
    )
    result = ReconciliationResult()
    rec._reconcile_from_activities(result)

    closed = get_trade_by_id("cs-lag", path=db)
    assert closed["status"] == "closed_profit"        # closed from fills…
    assert closed["status"] != NEEDS_INVESTIGATION    # …never flagged
    assert round(closed["pnl"], 2) == 674.0
    assert result.externally_closed == 1
