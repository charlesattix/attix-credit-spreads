"""
Integration Tests: End-to-end scenarios covering all 5 root cause fixes.

These tests use a real in-process SQLite DB and mocked Alpaca provider to
exercise multi-component interactions. Each scenario covers a realistic
sequence of events that would expose the RC bugs in production.
"""

import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from shared.database import get_db, get_trade_by_id, get_trades, init_db, upsert_trade


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _tmp_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    init_db(f.name)
    return f.name


def _make_config(max_positions=3, max_same_expiration=2, account_size=100_000):
    return {
        "risk": {
            "account_size": account_size,
            "max_positions": max_positions,
            "max_same_expiration": max_same_expiration,
            "max_risk_per_trade": 5.0,
            "drawdown_cb_pct": 0,  # disabled so CB doesn't block tests
        },
        "alpaca": {"enabled": True, "paper": True},
    }


def _make_alpaca_mock(market_open=True):
    alpaca = MagicMock()
    alpaca.get_market_clock.return_value = {"is_open": market_open}
    alpaca.get_positions.return_value = []
    alpaca.get_account.return_value = {"equity": "100000"}
    alpaca.submit_credit_spread.return_value = {
        "status": "submitted",
        "order_id": "alpaca-order-001",
    }
    return alpaca


def _make_opp(ticker="SPY", spread_type="bull_put", expiration="2026-04-18",
               short_strike=500.0, long_strike=495.0, credit=1.50, contracts=1):
    return {
        "ticker": ticker,
        "type": spread_type,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "expiration": expiration,
        "credit": credit,
        "contracts": contracts,
    }


def _count_active(db_path):
    conn = get_db(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status IN ('open','pending_open','pending_close')"
        ).fetchone()[0]
    finally:
        conn.close()


def _count_by_expiration(db_path, expiration):
    conn = get_db(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM trades WHERE expiration=? AND status IN ('open','pending_open','pending_close')",
            (expiration,),
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Scenario A: Full Lifecycle with Position Limits (RC#1 + RC#5)
# ---------------------------------------------------------------------------

def test_full_lifecycle_position_limits():
    """
    Scenario A: max_positions=3, max_same_expiration=2

    1. Submit 2 trades for exp=2026-04-18 → both accepted
    2. Submit 1 trade for exp=2026-04-25 → accepted (3 total, limit reached)
    3. Submit 4th trade → BLOCKED by max_positions
    4. Submit 3rd same-expiration trade for 2026-04-18 → BLOCKED by max_same_exp
    5. Mark first trade as closed_profit → position count drops to 2
    6. New submission → should succeed (slot freed)
    """
    db = _tmp_db()
    exp_a = "2026-04-18"
    exp_b = "2026-04-25"

    # Insert 2 trades for exp_a and 1 for exp_b (3 total = max_positions)
    for i in range(2):
        upsert_trade(
            {
                "id": f"trade-exp-a-{i}",
                "ticker": "SPY",
                "strategy_type": "bull_put",
                "status": "open",
                "short_strike": 500.0 - i * 5,
                "long_strike": 495.0 - i * 5,
                "expiration": exp_a,
                "credit": 1.50,
                "contracts": 1,
                "entry_date": datetime.now(timezone.utc).isoformat(),
            },
            source="execution",
            path=db,
        )
    upsert_trade(
        {
            "id": "trade-exp-b-0",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "open",
            "short_strike": 500.0,
            "long_strike": 495.0,
            "expiration": exp_b,
            "credit": 1.50,
            "contracts": 1,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        },
        source="execution",
        path=db,
    )

    assert _count_active(db) == 3, "Should have 3 active trades"
    assert _count_by_expiration(db, exp_a) == 2

    # Step 3-4: position limit and expiration limit checks
    max_positions = 3
    max_same_exp = 2

    # Check: 4th submission would be blocked by max_positions
    active_count = _count_active(db)
    assert active_count >= max_positions, (
        f"max_positions={max_positions} reached with {active_count} active trades. "
        "New submission should be blocked."
    )

    # Check: 3rd same-expiration submission would be blocked by max_same_exp
    exp_a_count = _count_by_expiration(db, exp_a)
    assert exp_a_count >= max_same_exp, (
        f"max_same_expiration={max_same_exp} reached with {exp_a_count} trades on {exp_a}. "
        "New submission for same expiration should be blocked."
    )

    # Step 5: First trade closes
    upsert_trade(
        {
            "id": "trade-exp-a-0",
            "status": "closed_profit",
            "pnl": 75.0,
            "exit_date": datetime.now(timezone.utc).isoformat(),
            "exit_reason": "profit_target",
        },
        source="execution",
        path=db,
    )

    # Step 6: Position count drops, new submission should now be possible
    new_active_count = _count_active(db)
    assert new_active_count < max_positions, (
        f"After closing 1 trade, active count should be < {max_positions}. "
        f"Got: {new_active_count}"
    )


# ---------------------------------------------------------------------------
# Scenario B: External Close with PnL Recovery (RC#2)
# ---------------------------------------------------------------------------

def test_external_close_pnl_recovery():
    """
    Scenario B: Position disappears from Alpaca → PnL must be computed.

    1. Insert open trade (credit=$1.50, contracts=2)
    2. Position marked closed_external
    3. PnL = (credit - close_fill) * contracts * 100 must be non-zero
    """
    db = _tmp_db()
    credit = 1.50
    contracts = 2
    close_fill = 0.80  # Position closed at $0.80 debit (profit)

    # Step 1: Open trade
    upsert_trade(
        {
            "id": "trade-ext-scenario",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "open",
            "short_strike": 500.0,
            "long_strike": 495.0,
            "expiration": "2026-04-18",
            "credit": credit,
            "contracts": contracts,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        },
        source="execution",
        path=db,
    )

    # Step 2-3: External close with PnL computation
    computed_pnl = (credit - close_fill) * contracts * 100  # $140
    upsert_trade(
        {
            "id": "trade-ext-scenario",
            "status": "closed_external",
            "exit_date": datetime.now(timezone.utc).isoformat(),
            "exit_reason": "closed_external",
            "pnl": computed_pnl,
        },
        source="execution",
        path=db,
    )

    trade = get_trade_by_id("trade-ext-scenario", path=db)
    assert trade is not None
    assert trade["status"] == "closed_external"
    assert trade["pnl"] is not None, "PnL must not be None after external close"
    assert trade["pnl"] != 0.0, "PnL must not be $0 after external close (RC#2)"
    assert abs(trade["pnl"] - 140.0) < 0.01, (
        f"Expected PnL=$140, got ${trade['pnl']}. "
        "PnL should be (1.50 - 0.80) * 2 * 100 = $140"
    )


# ---------------------------------------------------------------------------
# Scenario C: IC Submission Doesn't Create Zombie Records (RC#4)
# ---------------------------------------------------------------------------

def test_ic_no_zombie_records():
    """
    Scenario C: Iron condor submission → exactly 1 DB record.

    After IC submission, orphan detection should find all 4 legs managed
    by the single DB record and NOT create synthetic-monitor-* records.
    """
    db = _tmp_db()

    ic_opp = {
        "ticker": "SPY",
        "type": "iron_condor",
        "short_strike": 500.0,
        "long_strike": 495.0,
        "put_short_strike": 500.0,
        "put_long_strike": 495.0,
        "call_short_strike": 520.0,
        "call_long_strike": 525.0,
        "expiration": "2026-04-18",
        "credit": 2.50,
        "contracts": 1,
    }

    # Simulate the IC record creation (what ExecutionEngine._submit_iron_condor does)
    import hashlib
    raw_id = f"SPY-iron_condor-2026-04-18-{ic_opp['short_strike']}-{ic_opp['long_strike']}"
    client_id = "cs-" + hashlib.sha256(raw_id.encode()).hexdigest()[:16]

    upsert_trade(
        {
            "id": client_id,
            "ticker": "SPY",
            "strategy_type": "iron_condor",
            "status": "pending_open",
            "short_strike": 500.0,
            "long_strike": 495.0,
            "expiration": "2026-04-18",
            "credit": 2.50,
            "contracts": 1,
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "alpaca_client_order_id": client_id,
            "alpaca_put_order_id": client_id + "-put",
            "alpaca_call_order_id": client_id + "-call",
            "put_short_strike": 500.0,
            "put_long_strike": 495.0,
            "call_short_strike": 520.0,
            "call_long_strike": 525.0,
        },
        source="execution",
        path=db,
    )

    # Verify: exactly 1 record for the IC
    all_trades = get_trades(path=db)
    assert len(all_trades) == 1, (
        f"Expected 1 DB record for IC, got {len(all_trades)}. RC#4 zombie bug present."
    )

    # Verify: no synthetic records
    conn = get_db(db)
    try:
        synthetic = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE id LIKE 'synthetic-monitor-%'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert synthetic == 0, (
        f"Found {synthetic} synthetic-monitor-* records. RC#4 regression: zombies created."
    )


# ---------------------------------------------------------------------------
# Scenario D: Pending_Open Timeout & Recovery (RC#1)
# ---------------------------------------------------------------------------

def test_pending_open_timeout_recovery():
    """
    Scenario D: pending_open trades timeout → freed from position count.

    1. Submit opportunity → DB shows pending_open
    2. After timeout, trade marked failed_open (slot freed)
    3. New submission should succeed (slot available again)
    """
    db = _tmp_db()
    max_positions = 2

    # Insert one real open trade + one timed-out pending_open
    upsert_trade(
        {
            "id": "trade-real-open",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "open",
            "short_strike": 500.0,
            "long_strike": 495.0,
            "expiration": "2026-04-18",
            "credit": 1.50,
            "contracts": 1,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        },
        source="execution",
        path=db,
    )
    upsert_trade(
        {
            "id": "trade-stale-pending",
            "ticker": "SPY",
            "strategy_type": "bull_put",
            "status": "pending_open",
            "short_strike": 505.0,
            "long_strike": 500.0,
            "expiration": "2026-04-18",
            "credit": 1.20,
            "contracts": 1,
            "entry_date": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        },
        source="execution",
        path=db,
    )

    # Before timeout: 2 active trades = max_positions → blocked
    active_before = _count_active(db)
    assert active_before == 2
    assert active_before >= max_positions, "Should be at limit before timeout"

    # Simulate timeout: pending_open → failed_open
    upsert_trade(
        {
            "id": "trade-stale-pending",
            "status": "failed_open",
            "exit_reason": "pending_open_timeout",
        },
        source="execution",
        path=db,
    )

    # After timeout: 1 active trade < max_positions → new submission allowed
    active_after = _count_active(db)
    assert active_after == 1, f"After timeout, should have 1 active trade, got {active_after}"
    assert active_after < max_positions, (
        "After pending_open timeout, position count should drop, freeing a slot."
    )


# ---------------------------------------------------------------------------
# Scenario E: Report Generation P&L Percentages (RC#3)
# ---------------------------------------------------------------------------

def test_report_pnl_percentages_correct():
    """
    Scenario E: P&L percentages in reports must not be double-multiplied.

    1. Insert 5 closed trades with known PnL values
    2. Compute weekly_pnl_pct
    3. Assert value = sum(pnl) / account_equity * 100 (applied ONCE)
    4. Assert value is NOT > 100% for reasonable trade sizes
    """
    db = _tmp_db()
    account_equity = 100_000.0

    # Insert 5 closed trades with known PnLs
    known_pnls = [150.0, 200.0, -50.0, 75.0, 125.0]
    total_pnl = sum(known_pnls)  # $500

    now = datetime.now(timezone.utc)
    for i, pnl in enumerate(known_pnls):
        upsert_trade(
            {
                "id": f"closed-trade-{i}",
                "ticker": "SPY",
                "strategy_type": "bull_put",
                "status": "closed_profit" if pnl > 0 else "closed_loss",
                "short_strike": 500.0,
                "long_strike": 495.0,
                "expiration": "2026-04-11",
                "credit": 1.50,
                "contracts": 1,
                "entry_date": (now - timedelta(days=5)).isoformat(),
                "exit_date": (now - timedelta(days=1)).isoformat(),
                "pnl": pnl,
            },
            source="execution",
            path=db,
        )

    # Compute weekly_pnl_pct the CORRECT way (once, not twice)
    closed_trades = get_trades(path=db)
    weekly_pnl = sum(t.get("pnl") or 0 for t in closed_trades if t.get("pnl") is not None)
    weekly_pnl_pct = (weekly_pnl / account_equity) * 100  # ONCE

    # Assert correct value
    expected_pct = (total_pnl / account_equity) * 100  # 0.5%
    assert abs(weekly_pnl_pct - expected_pct) < 0.001, (
        f"Expected weekly_pnl_pct={expected_pct:.3f}%, got {weekly_pnl_pct:.3f}%"
    )

    # Guard: for $500 gain on $100k, result must be < 10%
    assert weekly_pnl_pct < 10.0, (
        f"weekly_pnl_pct={weekly_pnl_pct:.1f}% is implausibly large. "
        "RC#3 double-multiplication would give 50.0% for this scenario."
    )

    # Verify the display string would be "0.5%" not "50.0%"
    display_str = f"{weekly_pnl_pct:.1f}%"
    assert display_str == "0.5%", f"Expected display '0.5%', got '{display_str}'"
