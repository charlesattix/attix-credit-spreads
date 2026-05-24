"""Regression tests for the `_compute_external_close_pnl` race-condition fix.

Bug being prevented:
    The reconciler matched a trade's *own opening FILL activities* (recorded
    ~1s after entry by Alpaca) as evidence of an external close, force-marked
    the trade `external_fill`, and the downstream orphan detectors then created
    placeholder rows for every leg. See:
    /Users/charlesbot/.openclaw/workspace/reports/orphan-spreads-root-cause-2026-05-02.html

Fix layers (any one is sufficient; together they are belt-and-suspenders):
    A. Grace window — drop FILL activities within ENTRY_FILL_GRACE_SECONDS of
       entry_date.
    B. Subtype filter — drop FILL activities whose activity_subtype is
       `*_to_open`.
    C. /v2/positions guard — even after A+B, refuse to declare `external_fill`
       if any of the trade's legs are still reported as live by Alpaca.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from shared.reconciler import ENTRY_FILL_GRACE_SECONDS, PositionReconciler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_occ_symbol(ticker: str, exp: str, strike, opt_type: str) -> str:
    """Deterministic OCC symbol stub matching Alpaca's format.

    SPY 2026-05-22 P 500.0 → "SPY260522P00500000"
    """
    yymmdd = exp.replace("-", "")[2:]  # YYYY-MM-DD → YYMMDD
    cp = "C" if opt_type.lower().startswith("c") else "P"
    strike_int = int(round(float(strike) * 1000))
    return f"{ticker}{yymmdd}{cp}{strike_int:08d}"


def _mock_alpaca(positions=None):
    """Make an alpaca mock with deterministic helpers."""
    alpaca = MagicMock()
    alpaca.get_positions.return_value = positions or []
    alpaca._build_occ_symbol.side_effect = _build_occ_symbol
    return alpaca


def _trade(entry_offset_seconds=0, credit=1.50, contracts=2,
           ticker="SPY", expiration="2026-05-22",
           short_strike=500.0, long_strike=495.0,
           strategy_type="bull_put"):
    """Fixture trade dict for a credit spread.

    entry_offset_seconds: negative → trade opened that many seconds ago.
                          0        → trade opened just now.
    """
    entry_dt = datetime.now(timezone.utc) + timedelta(seconds=entry_offset_seconds)
    return {
        "id": "trade-test-001",
        "ticker": ticker,
        "strategy_type": strategy_type,
        "status": "open",
        "short_strike": short_strike,
        "long_strike": long_strike,
        "expiration": expiration,
        "credit": credit,
        "contracts": contracts,
        "entry_date": entry_dt.isoformat(),
    }


def _fill_activity(symbol, subtype, seconds_after_entry, entry_dt_iso,
                    net_amount=-150.0, side=None, act_id=None):
    """Build a fake Alpaca FILL activity dict."""
    entry_dt = datetime.fromisoformat(entry_dt_iso.replace("Z", "+00:00"))
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
    tx_time = entry_dt + timedelta(seconds=seconds_after_entry)
    return {
        "id": act_id or f"act-{symbol}-{seconds_after_entry}",
        "activity_type": "FILL",
        "activity_subtype": subtype,
        "symbol": symbol,
        "transaction_time": tx_time.isoformat(),
        "net_amount": net_amount,
        "side": side or ("sell" if "sell" in (subtype or "") else "buy"),
        "qty": "2",
        "price": "0.75",
        "order_id": "order-stub",
        "client_order_id": "trade-test-001",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_entry_fill_within_grace_period_ignored():
    """FILL activities within ENTRY_FILL_GRACE_SECONDS of entry_date must be
    dropped, so the trade is NOT marked external_fill.

    Reproduces the original bug: an opening FILL recorded ~1s after entry was
    misclassified as a close.
    """
    trade = _trade(entry_offset_seconds=0)  # entry = now
    sym_short = _build_occ_symbol("SPY", "2026-05-22", 500.0, "put")
    # Entry fill recorded 1s after open — historically caused false external_fill.
    # We use a benign subtype to confirm the grace window alone catches it.
    activity = _fill_activity(
        sym_short,
        subtype="fill",  # ambiguous subtype — grace must catch this
        seconds_after_entry=1,
        entry_dt_iso=trade["entry_date"],
    )
    reconciler = PositionReconciler(alpaca=_mock_alpaca())
    pnl, reason, act_id = reconciler._compute_external_close_pnl(trade, [activity])
    assert pnl is None and reason is None and act_id is None, (
        f"FILL inside the {ENTRY_FILL_GRACE_SECONDS}s grace window must not "
        f"yield external_fill; got pnl={pnl} reason={reason}"
    )


def test_real_external_close_after_grace():
    """FILL well outside the grace window AND with no live position should be
    classified as external_fill with correct PnL."""
    trade = _trade(entry_offset_seconds=-600)  # entry was 10 minutes ago
    sym_short = _build_occ_symbol("SPY", "2026-05-22", 500.0, "put")
    # Close fill arrives 8 minutes after entry: well outside the 90s window
    close_act = _fill_activity(
        sym_short,
        subtype="buy_to_close",
        seconds_after_entry=480,
        entry_dt_iso=trade["entry_date"],
        net_amount=-80.0,  # paid $80 to close the short
    )
    reconciler = PositionReconciler(alpaca=_mock_alpaca(positions=[]))
    pnl, reason, act_id = reconciler._compute_external_close_pnl(trade, [close_act])
    # credit=1.50 × 2 × 100 = $300, + (-80) net, − entry_comm
    # entry_comm = 0.65 × 2 contracts × 2 legs = $2.60
    expected_pnl = 1.50 * 2 * 100 + (-80.0) - 2.60
    assert reason == "external_fill", f"expected external_fill, got {reason}"
    assert pnl == pytest.approx(expected_pnl, abs=0.01)
    assert act_id == close_act["id"]


def test_mixed_open_fill_and_later_close():
    """When both an open FILL (in grace) and a later close FILL (out of grace)
    exist, only the close should count."""
    trade = _trade(entry_offset_seconds=-600)
    sym_short = _build_occ_symbol("SPY", "2026-05-22", 500.0, "put")
    open_act = _fill_activity(
        sym_short,
        subtype="sell_to_open",
        seconds_after_entry=2,  # inside grace AND open subtype — double-filtered
        entry_dt_iso=trade["entry_date"],
        net_amount=+150.0,  # we received the credit
    )
    close_act = _fill_activity(
        sym_short,
        subtype="buy_to_close",
        seconds_after_entry=480,
        entry_dt_iso=trade["entry_date"],
        net_amount=-80.0,
        act_id="act-close-primary",
    )
    reconciler = PositionReconciler(alpaca=_mock_alpaca(positions=[]))
    pnl, reason, act_id = reconciler._compute_external_close_pnl(
        trade, [open_act, close_act]
    )
    assert reason == "external_fill"
    # PnL must NOT include the open fill's net_amount
    expected_pnl = 1.50 * 2 * 100 + (-80.0) - 2.60
    assert pnl == pytest.approx(expected_pnl, abs=0.01)
    assert act_id == "act-close-primary"


def test_position_still_open_aborts_external_fill():
    """If a FILL passes the grace+subtype filters but /v2/positions still shows
    the leg as held, refuse to declare external_fill."""
    trade = _trade(entry_offset_seconds=-600)
    sym_short = _build_occ_symbol("SPY", "2026-05-22", 500.0, "put")
    # A close-looking FILL outside the grace window
    close_act = _fill_activity(
        sym_short,
        subtype="buy_to_close",
        seconds_after_entry=480,
        entry_dt_iso=trade["entry_date"],
    )
    # But Alpaca still reports the short leg as live → contradiction
    positions = [{"symbol": sym_short, "qty": "2"}]
    reconciler = PositionReconciler(alpaca=_mock_alpaca(positions=positions))
    pnl, reason, act_id = reconciler._compute_external_close_pnl(trade, [close_act])
    assert pnl is None and reason is None and act_id is None, (
        f"Position guard must abort external_fill when legs are still live; "
        f"got pnl={pnl} reason={reason}"
    )


def test_entry_only_fills_never_close():
    """If only `*_to_open` FILL activities exist, the trade must never be
    marked external_fill — regardless of how long the reconciler runs."""
    trade = _trade(entry_offset_seconds=-3600)  # entry was an hour ago
    sym_short = _build_occ_symbol("SPY", "2026-05-22", 500.0, "put")
    sym_long = _build_occ_symbol("SPY", "2026-05-22", 495.0, "put")
    # Both legs only have open fills, recorded after the grace window
    # (i.e. grace filter wouldn't catch them — the subtype filter must)
    open_short = _fill_activity(
        sym_short, subtype="sell_to_open", seconds_after_entry=120,
        entry_dt_iso=trade["entry_date"], net_amount=+150.0,
    )
    open_long = _fill_activity(
        sym_long, subtype="buy_to_open", seconds_after_entry=120,
        entry_dt_iso=trade["entry_date"], net_amount=-50.0,
    )
    reconciler = PositionReconciler(alpaca=_mock_alpaca(positions=[]))
    pnl, reason, act_id = reconciler._compute_external_close_pnl(
        trade, [open_short, open_long]
    )
    assert pnl is None and reason is None and act_id is None, (
        f"Only `*_to_open` FILLs must never yield external_fill regardless "
        f"of age; got pnl={pnl} reason={reason}"
    )
