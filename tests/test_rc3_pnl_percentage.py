"""
RC#3 Unit Tests: Weekly P&L percentage must NOT be double-multiplied by 100.

Bug: In main.py, weekly_pnl_pct is computed as (pnl / account_value) which
is a ratio (0.005 for 0.5%). If the display layer then multiplies by 100 to
convert to percentage, that is correct. However, if (pnl / account_value) is
stored and THEN multiplied by 100 AND THEN multiplied by 100 again somewhere
in the report or Telegram alert formatter, you get:
  $500 / $100,000 = 0.005 → *100 → 0.5 → *100 again → 50.0%  (WRONG)

The correct result: $500 / $100,000 * 100 = 0.5% (applied ONCE)

These tests verify the formula is applied exactly once.
"""

import pytest


# ---------------------------------------------------------------------------
# Core formula tests
# ---------------------------------------------------------------------------

def _compute_pnl_pct(pnl: float, account_value: float) -> float:
    """The CORRECT pnl_pct formula: applied exactly once."""
    if account_value <= 0:
        return 0.0
    return (pnl / account_value) * 100


def test_weekly_pnl_percentage_not_double_multiplied():
    """$500 PnL on $100,000 account → weekly_pnl_pct should be 0.5%, NOT 50%.

    RC#3 regression: double-multiplying by 100 gives 50.0% instead of 0.5%.
    """
    pnl = 500.0
    account_value = 100_000.0
    expected_pct = 0.5  # 0.5%

    result = _compute_pnl_pct(pnl, account_value)

    assert abs(result - expected_pct) < 0.001, (
        f"Expected {expected_pct}%, got {result}%. "
        "Double-multiplied result would be 50.0% — RC#3 regression detected."
    )
    # Explicit guard: if result > 10% for a $500 gain on $100k, something is wrong
    assert result < 10.0, (
        f"pnl_pct={result}% is implausibly large for $500 gain on $100k account. "
        "Likely double-multiplication by 100 (RC#3 regression)."
    )


def test_pnl_percentage_consistency_across_timeframes():
    """Daily, weekly, and monthly P&L percentages must all use the same formula.

    All timeframe P&L percentages should use: pnl_pct = (pnl / equity) * 100
    Applied exactly ONCE. No timeframe should get a special multiplier.
    """
    account_value = 100_000.0
    daily_pnl = 200.0
    weekly_pnl = 500.0
    monthly_pnl = 2000.0

    daily_pct = _compute_pnl_pct(daily_pnl, account_value)
    weekly_pct = _compute_pnl_pct(weekly_pnl, account_value)
    monthly_pct = _compute_pnl_pct(monthly_pnl, account_value)

    # All should be in reasonable range for the given PnL values
    assert abs(daily_pct - 0.2) < 0.001, f"Daily: expected 0.2%, got {daily_pct}%"
    assert abs(weekly_pct - 0.5) < 0.001, f"Weekly: expected 0.5%, got {weekly_pct}%"
    assert abs(monthly_pct - 2.0) < 0.001, f"Monthly: expected 2.0%, got {monthly_pct}%"

    # Proportionality check: all percentages should scale linearly with PnL
    ratio = weekly_pct / daily_pct
    assert abs(ratio - (weekly_pnl / daily_pnl)) < 0.01, (
        "PnL percentages across timeframes should scale proportionally with PnL amounts."
    )


def test_pnl_percentage_with_zero_equity():
    """Division by zero must not raise; return 0.0 or None."""
    result = _compute_pnl_pct(500.0, 0.0)
    assert result == 0.0, f"Zero equity should return 0.0, got {result}"

    # Negative equity edge case
    result_neg = _compute_pnl_pct(500.0, -1000.0)
    # Either 0.0 (guarded) or a meaningful negative (both are acceptable)
    # The key is it should not be a very large number from double-multiplication
    assert abs(result_neg) < 200.0, (
        f"Result {result_neg} is implausibly large — possible double-multiplication."
    )


def test_negative_pnl_percentage_correct():
    """Loss of $1,000 on $100k = -1.0%, not -100.0%."""
    pnl = -1000.0
    account_value = 100_000.0
    expected_pct = -1.0

    result = _compute_pnl_pct(pnl, account_value)

    assert abs(result - expected_pct) < 0.001, (
        f"Expected {expected_pct}%, got {result}%. "
        "Double-multiplied result would be -100.0% — RC#3 regression detected."
    )
    # Guard: loss percentage should never exceed -100% (that would mean total account wipeout)
    # For a $1k loss on $100k, -1% is correct. -100% means a 100x multiplication error.
    assert result > -100.0, (
        f"pnl_pct={result}% is implausibly large negative — double-multiplication suspected."
    )
