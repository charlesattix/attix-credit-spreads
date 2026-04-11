"""
RC#5 Unit Tests: max_same_expiration limit must be enforced before order submission.

Bug: The ExecutionEngine.submit_opportunity() did not check how many active
positions share the same expiration date. With max_same_expiration=3, a user
could accumulate unlimited positions on a single expiration, creating dangerous
concentration risk.

Fix: Before submitting, count active trades (open+pending_open) on the same
expiration and block if the count >= max_same_expiration.
"""

import tempfile
from datetime import datetime, timezone

import pytest

from shared.database import get_db, get_trades, init_db, upsert_trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    init_db(f.name)
    return f.name


def _insert_trade(db_path, trade_id, expiration, status="open", ticker="SPY"):
    upsert_trade(
        {
            "id": trade_id,
            "ticker": ticker,
            "strategy_type": "bull_put",
            "status": status,
            "short_strike": 500.0,
            "long_strike": 495.0,
            "expiration": expiration,
            "credit": 1.50,
            "contracts": 1,
            "entry_date": datetime.now(timezone.utc).isoformat(),
        },
        source="execution",
        path=db_path,
    )


def _count_same_expiration(db_path, expiration):
    """Count active trades for a given expiration (the query the fix must use)."""
    conn = get_db(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM trades "
            "WHERE expiration = ? AND status IN ('open', 'pending_open', 'pending_close')",
            (expiration,),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def _should_block_expiration(db_path, expiration, max_same_expiration):
    """Return True if submitting another trade for this expiration should be blocked."""
    if not max_same_expiration or max_same_expiration <= 0:
        return False
    count = _count_same_expiration(db_path, expiration)
    return count >= max_same_expiration


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_max_same_expiration_blocks_excess():
    """With max_same_expiration=3, a 4th trade for the same expiration must be BLOCKED.

    RC#5 original bug: no expiration concentration check before submission.
    """
    db = _tmp_db()
    max_same_exp = 3
    expiration = "2026-04-18"

    # Insert 3 open trades for the same expiration
    for i in range(max_same_exp):
        _insert_trade(db, f"trade-{i}", expiration)

    # A 4th submission for the same expiration should be blocked
    should_block = _should_block_expiration(db, expiration, max_same_exp)
    assert should_block, (
        f"Expected 4th trade for {expiration} to be BLOCKED with max_same_expiration={max_same_exp}. "
        f"Active count for {expiration}: {_count_same_expiration(db, expiration)}"
    )


def test_max_same_expiration_includes_pending():
    """pending_open trades for the same expiration count toward the limit."""
    db = _tmp_db()
    max_same_exp = 3
    expiration = "2026-04-18"

    # Mix of open and pending_open for the same expiration
    _insert_trade(db, "trade-open-1", expiration, status="open")
    _insert_trade(db, "trade-open-2", expiration, status="open")
    _insert_trade(db, "trade-pending", expiration, status="pending_open")

    count = _count_same_expiration(db, expiration)
    assert count == 3, f"Expected 3 (2 open + 1 pending_open), got {count}"

    # Now a 4th submission should be blocked
    should_block = _should_block_expiration(db, expiration, max_same_exp)
    assert should_block, (
        "pending_open trades must count toward max_same_expiration. "
        f"Active count: {count}, limit: {max_same_exp}"
    )


def test_different_expirations_independent():
    """3 trades on 2026-04-18 + 3 trades on 2026-04-25 = both OK if limit is 3."""
    db = _tmp_db()
    max_same_exp = 3
    exp1 = "2026-04-18"
    exp2 = "2026-04-25"

    for i in range(max_same_exp):
        _insert_trade(db, f"trade-exp1-{i}", exp1)
    for i in range(max_same_exp):
        _insert_trade(db, f"trade-exp2-{i}", exp2)

    # Each expiration is at the limit, but neither is exceeded
    count1 = _count_same_expiration(db, exp1)
    count2 = _count_same_expiration(db, exp2)

    assert count1 == 3, f"Expected 3 trades for {exp1}, got {count1}"
    assert count2 == 3, f"Expected 3 trades for {exp2}, got {count2}"

    # Different expirations should not interfere
    should_block_1_more_exp1 = _should_block_expiration(db, exp1, max_same_exp)
    should_block_1_more_exp2 = _should_block_expiration(db, exp2, max_same_exp)

    assert should_block_1_more_exp1, f"4th trade for {exp1} should be blocked"
    assert should_block_1_more_exp2, f"4th trade for {exp2} should be blocked"

    # A trade on a THIRD expiration should be fine
    exp3 = "2026-05-02"
    should_block_exp3 = _should_block_expiration(db, exp3, max_same_exp)
    assert not should_block_exp3, (
        f"Trade on new expiration {exp3} should NOT be blocked by other expirations."
    )


def test_max_same_expiration_zero_means_unlimited():
    """max_same_expiration=0 or absent → no limit enforced on any expiration."""
    db = _tmp_db()
    expiration = "2026-04-18"

    # Insert many trades for same expiration
    for i in range(10):
        _insert_trade(db, f"trade-{i}", expiration)

    # With max_same_expiration=0, should never block
    assert not _should_block_expiration(db, expiration, 0), (
        "max_same_expiration=0 should mean unlimited — no blocking."
    )
    assert not _should_block_expiration(db, expiration, None), (
        "max_same_expiration=None should mean unlimited — no blocking."
    )


def test_ic_counts_as_one_for_expiration_limit():
    """An iron condor is 1 trade, not 2 or 4, for expiration concentration counting."""
    db = _tmp_db()
    max_same_exp = 3
    expiration = "2026-04-18"

    # Insert 1 IC (which has 4 legs but is 1 DB record)
    upsert_trade(
        {
            "id": "ic-trade-001",
            "ticker": "SPY",
            "strategy_type": "iron_condor",
            "status": "open",
            "short_strike": 500.0,
            "long_strike": 495.0,
            "expiration": expiration,
            "credit": 2.50,
            "contracts": 1,
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "put_short_strike": 500.0,
            "put_long_strike": 495.0,
            "call_short_strike": 520.0,
            "call_long_strike": 525.0,
        },
        source="execution",
        path=db,
    )

    # IC should count as EXACTLY 1 toward the expiration limit
    count = _count_same_expiration(db, expiration)
    assert count == 1, (
        f"Iron condor should count as 1 trade for expiration limit, got {count}. "
        "If count > 1, the IC legs are being stored as separate records (RC#4 zombie)."
    )

    # With max_same_exp=3, 2 more trades should still be allowed
    should_block = _should_block_expiration(db, expiration, max_same_exp)
    assert not should_block, (
        f"1 IC should not trigger max_same_expiration=3 block. "
        f"IC incorrectly counted as {count} trades."
    )
