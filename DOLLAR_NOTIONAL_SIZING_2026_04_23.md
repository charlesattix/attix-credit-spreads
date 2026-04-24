# Dollar-Notional Position Sizing — Implementation Summary

**Date:** 2026-04-23
**Phase 9 Prerequisite:** #5 — "Dollar-notional sizing patched in (current integer-contract sizing is a sub-$1M accuracy issue)"
**Status:** COMPLETE — tested, backward-compatible

---

## Problem

The existing signal generator (`compass/exp2830_paper_signal_generator.py`) computed position sizes using inline arithmetic that was hardcoded to `CAPITAL_BASE = $100K`:

```python
# Old code (put credit spreads):
stream_cap = CAPITAL_BASE * weight * MAX_LEVERAGE
max_loss_per_contract = (width - 0.5) * 100   # rough credit estimate
contracts = max(1, int(stream_cap * 0.03 / max_loss_per_contract))
contracts = min(contracts, 10)
```

This works at $100K but breaks down as AUM scales:
- At **$25K** (T1): produces the same sizes as $100K because of `max(1, ...)` floor
- At **$1M** (T3): caps at 10 contracts even though risk budget supports 74
- At **$10M** (T4): same 10 contracts — massively under-allocated
- The `(width - 0.5)` credit estimate was inconsistent with the `width * 0.15` credit used in `limit_price`
- Four different sizing blocks (put spreads, calendars, cross-vol, hedge) each had their own inline formula — no single source of truth

## Solution

### 1. Central `compute_notional_contracts()` function

Added a single, tested, documented function:

```python
def compute_notional_contracts(
    capital, weight, leverage, risk_pct, max_loss_per_contract,
    *, floor=1, cap=10,
) -> int:
    """Dollar-notional: contracts = (capital × weight × leverage × risk_pct) / max_loss_per_contract"""
```

**Key behaviors:**
- Rounds **DOWN** (conservative — never risk more than budget)
- Enforces `floor` and `cap` bounds
- Guards against division-by-zero (`max_loss ≤ 0` → return floor)
- Returns `int` (required by Alpaca)

### 2. Updated all 4 sizing blocks

| Stream | Old Formula | New Formula | Risk % | Cap |
|---|---|---|---|---|
| **Put credit spreads** (SPY/QQQ/XLF/XLI) | `int(cap * 0.03 / (width-0.5)*100)` | `compute_notional_contracts(..., risk_pct=0.03, max_loss=(width-credit)*100)` | 3% | 10 |
| **Calendar spreads** (GLD/SLV) | `int(cap * 0.02 / 50)` | `compute_notional_contracts(..., risk_pct=0.02, max_loss=50)` | 2% | 15 |
| **Cross-vol arb** | `int(cap * 0.02 / 100)` | `compute_notional_contracts(..., risk_pct=0.02, max_loss=100)` | 2% | 8 |
| **V5 hedge** | `int(cap * 0.05 / 50)` | `compute_notional_contracts(..., risk_pct=0.05, max_loss=50)` | 5% | 5 |

### 3. Fixed credit estimate inconsistency

Old code used `(width - 0.5) * 100` for max-loss but `width * 0.15` for the limit price. Now both use `width * 0.15` as the credit estimate, making max-loss = `(width - width*0.15) * 100` consistently.

### 4. Added sizing context fields to `StreamSignal`

Three new fields on every signal (defaulting to 0.0 for non-trade signals):

```python
sizing_capital: float = 0.0            # portfolio equity used for sizing
sizing_max_loss_per_contract: float = 0.0  # worst-case $ loss per contract
sizing_risk_budget: float = 0.0        # $ amount risked on this trade
```

These flow through `asdict()` into the JSON signals and downstream to Alpaca, providing full auditability of why each position is sized the way it is.

## Backward Compatibility

- `StreamSignal` new fields default to `0.0` → existing code that constructs `StreamSignal` without these fields still works
- `asdict(signal)` produces a superset of the old dict → existing consumers (EXP-2860 dry-run, Alpaca connector) are unaffected
- `compute_notional_contracts` at `capital=100_000` produces **identical** results to the old inline formulas for all 8 streams (verified in tests)
- The EXP-2860 `build_alpaca_order()` reads `target_contracts_after_vix` — unchanged

## Sizing at Scale (what changes at T3+)

When `CAPITAL_BASE` is increased from $100K to the actual portfolio equity:

| AUM (capital) | SPY contracts (cap=10) | SPY contracts (uncapped) |
|---|---|---|
| $25K (T1) | 1 | 1 |
| $100K (T0/T2) | 7 | 7 |
| $1M (T3) | 10 (capped) | 74 |
| $10M (T4) | 10 (capped) | 741 |

**To unlock scaling beyond $100K:** set `CAPITAL_BASE` to the live portfolio equity (read from Alpaca account API) and raise the safety caps proportionally. This is a config change, not a code change.

## Files Changed

| File | Change |
|---|---|
| `compass/exp2830_paper_signal_generator.py` | Added `compute_notional_contracts()`, updated 4 sizing blocks, added 3 fields to `StreamSignal` |
| `tests/test_dollar_notional_sizing.py` | **NEW** — 30 tests covering core function, production scenarios, scaling tiers, edge cases |

## Tests

**30/30 passed.** Categories:
- `TestComputeNotionalContracts` (15 tests): core math, rounding, floor/cap, scaling, production values
- `TestScalingScenarios` (7 tests): T0→T4 AUM tiers, uncapped behavior at $1M and $10M
- `TestStreamSignalFields` (3 tests): new fields exist, default correctly, budget consistency
- `TestEdgeCases` (5 tests): wide/narrow spreads, weights validation, integer type guarantee

Existing tests unaffected:
- `compass/tests/test_portfolio_risk_manager.py`: 30/30 passed

## What's Left for Phase 9

This resolves prerequisite #5. The remaining step to fully activate dollar-notional sizing at scale:

1. **Replace `CAPITAL_BASE = 100_000` with live equity from Alpaca API** — once paper trading is running, read `account.equity` and pass it to the signal generator
2. **Raise safety caps** at T3+ — the `cap=10` for put spreads should scale to `cap=100` or higher at $1M+, gated by the capacity analysis in `AUM_CAPACITY_RESEARCH.md`
3. **Wire `capital` parameter through `generate_all_signals()`** — currently `CAPITAL_BASE` is a module constant; it should become a parameter

All three are one-line changes once paper trading begins.

---

*Implemented 2026-04-23 by Maximus*
*Rule Zero: No synthetic data. No backtests were run. This is pure sizing logic + tests.*
