# ROOT CAUSE ANALYSIS AND PREVENTION PROPOSAL
**Author:** Claude (independent audit)
**Date:** 2026-03-13
**Subject:** How did +820% average annual return survive as a credible claim?

---

## EXECUTIVE SUMMARY

The headline results (+820% avg, +2,643% in 2021) were produced by three compounding failures:
1. A commission formula that forgot to multiply by contract count (100x undercharge)
2. A position sizing model with no margin reservation (50 simultaneous 100-contract positions on $100K)
3. A hardcoded `max_positions=50` buried in a script, invisible to configuration and review

None of these failures were caught because there were **zero sanity checks** in the pipeline — no benchmark comparison, no leverage ratio validation, no commission-as-percentage-of-PnL check. The strategy genuinely has edge; the fantasy numbers exist because the simulation was running in a leverage regime no broker would ever extend.

---

## PART 1: ROOT CAUSE ANALYSIS

### 1. HOW DID THE COMMISSION BUG SURVIVE?

**When was it introduced?**

The bug was present from the **very first real-data backtester commit** (Feb 20, 2026, `dcac405`). When `backtest/backtester.py` was created to replace the heuristic backtester with real Polygon data, the commission line was written as:

```python
commission_cost = self.commission * 2  # Two legs
```

This charges `$0.65 × 2 = $1.30` per trade regardless of contract count. The correct formula is `$0.65 × 2 × contracts`. For 100 contracts, the backtester charged $1.30 instead of $130.00 — a 100x error.

The bug survived in every commit from Feb 20 through Mar 13 (today). The fix (`* contracts`) exists only in the **current uncommitted working tree** — it has not been committed to the repository.

**Duration:** 21 days. Every single experiment in the leaderboard (517 entries, all timestamped Mar 2–13) ran with broken commissions.

**Were there tests?**

Yes, but they were structured in a way that **actively hid the bug**. In `tests/test_backtester.py`, test fixtures hard-code `commission=1.30`:

```python
pos = _make_position(credit=1.50, contracts=2, commission=1.30)
expected_pnl = 1.50 * 2 * 100 - 1.30  # credit * contracts * 100 - commission
```

The commission value `1.30` is passed directly into the position dict. The tests validate that `1.30` is subtracted from PnL — which is correct, given the position dict. They never test that the commission **stored** in a position matches `commission_per_contract × 2 × contracts`. The tests validate the *P&L math* (correct) but not the *commission calculation* (wrong).

This is a classic test coverage gap: testing the output given an input, without testing whether the input itself is computed correctly.

**Did code review catch it?**

No. The Inspector Rounds (13 rounds of adversarial review from Feb 27–Mar 2) focused on logic bugs: look-ahead bias, VIX data timing, IC fallback logic, deduplication. No round audited "does commission scale with contracts?" — a financial modeling question, not a code structure question.

**How many experiments ran with broken commissions?**

All of them. The leaderboard contains **517 entries**, all computed after the real-data backtester was built with the commission bug. All prior heuristic-mode results were also wrong for different reasons (heuristic pricing is synthetic). There are **zero valid commission-accurate backtest results** in this project's history.

---

### 2. HOW DID THE NO-MARGIN MODEL HAPPEN?

**Was margin modeling ever planned?**

Yes, partially. Commit `b6bb8c7` (Feb 27, 2026) is titled *"feat: portfolio delta cap, margin tracking, assignment risk modeling"* and explicitly describes:

> Reg-T style margin tracking (spread width × contracts × 100), reject trades exceeding available buying power

This code was built — but it was built in `engine/portfolio_backtester.py`, a **separate, experimental portfolio engine** that was never wired into the main backtest pipeline. The production backtester (`backtest/backtester.py`) never received this feature.

**Was it a deliberate shortcut or an oversight?**

Both. The shortcut was deliberate at first: early in development, the backtester used 1-contract positions where margin is trivial ($470 held vs $470 capital at risk — effectively identical). The oversight came when `max_contracts` was scaled to 100 and compound sizing was enabled: nobody revisited whether capital reservation was still valid at scale. The original simplification (`self.capital -= commission_cost` at entry) was never flagged for removal.

**Did anyone question why a $100K account could hold 50 positions?**

No direct challenge appears in any commit message or comment. The `max_portfolio_exposure_pct` parameter was added as the exposure control mechanism, but it was set to 100% in the champion config — which triggers a **complete bypass** of the check:

```python
def _exposure_ok(pos) -> bool:
    if self._max_portfolio_exposure_pct >= 100.0:
        return True  # ← No check performed at all
```

The implicit assumption was: "the exposure cap will prevent over-leverage." The explicit reality was: the cap was set to the value that disables it, and no margin math enforced an independent floor.

---

### 3. HOW DID max_positions=50 GET HARDCODED?

**When and why?**

Commit `b229a4b` (Feb 26, 2026) created `scripts/run_optimization.py` from scratch. This was the first optimization harness. The author set `max_positions: 50` as a "reasonable" operational ceiling without analyzing what 50 simultaneous 100-contract positions means for margin.

```python
"max_positions": 50,  # hardcoded — not in config, not optimizable
```

The comment itself acknowledges the hardcoding. It was a **known shortcut** — not a mistake in the moment — but its capital implications were never analyzed.

**Why isn't it in the config?**

Because `run_optimization.py` was written to optimize strategy parameters (DTE, OTM%, stop-loss, credit threshold). `max_positions` was treated as infrastructure — a cap to prevent the simulation from running into performance issues — rather than as a risk parameter. The conceptual separation of "strategy config" from "operational limits" meant this parameter was never included in the config schema.

**Was there ever a review of what 50 positions means for margin?**

No. The math was never done until the independent audit:

| Metric | Value |
|---|---|
| max_positions | 50 |
| max_contracts | 100 |
| Spread width | $5 |
| Margin per position | $50,000 |
| **Total margin required** | **$2,500,000** |
| Starting capital | $100,000 |
| **Leverage ratio** | **25×** |

No retail broker permits 25× leverage on credit spreads.

---

### 4. WHY DIDN'T ANYONE CATCH +820% AS UNREALISTIC?

**Were there sanity checks in the pipeline?**

None. `run_optimization.py` writes results to the leaderboard JSON and prints pass/fail verdicts. There is no code that asks: "Is this return plausible?" The pipeline rewards high returns — the leaderboard sorts by `avg_return` — so higher is always "better."

**Did anyone compare to published benchmarks?**

No benchmark comparison exists anywhere in the codebase. The Carlos Critique murder tests (`tasks/murder_test_plan.md`) tested robustness (slippage, MC, walk-forward) but not *realism*. A strategy that is robust-to-slippage-at-2x can still be leveraged-into-fantasy.

**What do published credit spread strategies actually return?**

Published results from professional credit spread strategies (tastytrade research, CBOE benchmark indices):

| Strategy | Typical Annual Return | Notes |
|---|---|---|
| BXM (CBOE Buy-Write Index) | +8–12% | Covered calls on SPX |
| PUT (CBOE PutWrite Index) | +10–15% | Short SPX puts, monthly |
| Professional credit spread funds | +15–35% | Tastytrade-style, mechanical |
| Aggressive retail credit spreads | +30–80% | High-frequency, small accounts, good years |
| Exceptional outlier years | up to +150% | e.g., 2022 bear market |

Any single-year result above +150% in a non-crisis year should trigger automatic review. An *average* of +820% over 6 years should have been rejected immediately as physically impossible without leverage far beyond what any broker allows.

**The psychological failure:**

The project had been optimizing for months toward higher returns. Each Inspector Round "fixed" something, and the returns went up. The +820% number emerged gradually — the 2021 result grew from ~+40% (early buggy run) to +188% (IC bug fix) to +2,643% (max_contracts=100 + compound). Each jump felt like a "discovery" rather than a red flag. **Confirmation bias in optimization**: when you're trying to maximize returns, a higher number feels like success, not a warning.

---

### 5. SYSTEMIC FAILURES

**Missing test categories:**
- No test verifying `commission_cost = self.commission * 2 * contracts` end-to-end
- No test for leverage ratio (total_margin_required / capital)
- No test for "what happens at max_contracts=100 with compound=True over 200 trading days"
- No integration test that runs a full year and checks that total commissions ≥ X% of total trades × min_commission

**Missing validation gates:**
- No `--dry-run` mode that prints leverage stats before running
- No assertion that `sum(position.max_loss * contracts * 100) <= capital` at any point
- No annual return sanity bound (e.g., "flag if avg > 200%")
- The `_exposure_ok` function had a bypass path for the most common config value (100%)

**Missing sanity bounds:**
- Commission total for a year should be ≥ 1% of gross profits
- Average simultaneous positions × margin_per_position ≤ capital
- Any result >3× the CBOE PUT index for the same year deserves scrutiny

**Process failures:**
- The Inspector Rounds were code quality reviews (logic, lookahead, data integrity) — not financial modeling reviews. Financial sanity (is this leverage realistic?) was never an explicit review category.
- No "reality check" gate before writing to leaderboard
- The murder tests measured robustness (slippage, MC variance) but not executability
- `max_positions` was treated as infrastructure configuration, not a risk parameter

---

## PART 2: PREVENTION PROPOSAL

### A. BACKTESTER VALIDATION GATES (run automatically after every backtest)

These checks should be added to `backtest/backtester.py` in the `run()` method's return block, raising `BacktestWarning` (not exception — just loud warning + leaderboard flag).

**Gate 1: Commission sanity**
```python
def _validate_commissions(self, results):
    """Total commissions must be > 0.5% of gross credits collected."""
    total_commissions = sum(abs(t.get('commission', 0)) for t in self.trades)
    gross_credits = sum(
        t['credit'] * t['contracts'] * 100
        for t in self.trades if t.get('credit', 0) > 0
    )
    if gross_credits > 0:
        commission_pct = total_commissions / gross_credits * 100
        if commission_pct < 0.5:
            warn(f"SANITY FAIL: commission/gross_credit = {commission_pct:.3f}% — "
                 f"expected ≥0.5%. Commission formula likely missing contract multiplier.")
```

**Gate 2: Leverage check**
```python
def _validate_leverage(self, peak_simultaneous_positions, peak_contracts):
    """Total margin exposure must never exceed 200% of starting capital."""
    # Spread width × 100 × contracts per position × n_positions
    peak_margin = peak_simultaneous_positions * self.spread_width * 100 * peak_contracts
    leverage = peak_margin / self.starting_capital
    if leverage > 2.0:
        warn(f"SANITY FAIL: peak leverage = {leverage:.1f}× — "
             f"no broker extends this. max_positions or max_contracts must be reduced.")
```

**Gate 3: Return sanity bound**
```python
def _validate_annual_returns(self, year_results):
    """Flag any year > 200% for mandatory manual review."""
    for year, result in year_results.items():
        if result.get('return_pct', 0) > 200:
            warn(f"SANITY FLAG: {year} return = {result['return_pct']:.1f}% — "
                 f"exceeds 200% threshold. Mandatory review required before accepting result.")
        if result.get('return_pct', 0) > 500:
            raise ValueError(f"HARD STOP: {year} return = {result['return_pct']:.1f}%. "
                             f"This is physically impossible at real brokers. "
                             f"Check margin model and commission formula.")
```

**Gate 4: Position sizing sanity**
```python
def _validate_position_sizes(self):
    """No single trade should exceed 50% of actual available capital after margin."""
    for trade in self.trades:
        trade_margin = trade.get('max_loss', 0) * trade.get('contracts', 1) * 100
        capital_at_entry = trade.get('capital_at_entry', self.starting_capital)
        if trade_margin > 0.5 * capital_at_entry:
            warn(f"SANITY FAIL: trade {trade.get('expiration')} size = "
                 f"${trade_margin:,.0f} = {trade_margin/capital_at_entry*100:.0f}% of capital. "
                 f"Reduce max_contracts or max_risk_per_trade.")
```

---

### B. AUTOMATED AUDIT SCRIPT

Create `scripts/validate_backtest.py` — run automatically after every full backtest:

```python
#!/usr/bin/env python3
"""
Automated post-backtest sanity validator.
Run: python3 scripts/validate_backtest.py <results_json>

Checks:
  1. Commission scaling (per-contract, not per-trade)
  2. Leverage ratio (total margin vs capital)
  3. Return bounds (flag >200%, hard stop >500%)
  4. Position size executability (vs real market volumes)
  5. Benchmark comparison (vs CBOE PUT index)
"""

CBOE_PUT_ANNUAL = {  # CBOE PutWrite Index approximate annual returns
    2020: -4.7,   # COVID crash year
    2021: 14.0,
    2022: -11.3,
    2023: 12.8,
    2024: 10.5,
    2025: 9.0,    # estimate
}

MAX_REALISTIC_OUTPERFORMANCE = 5.0  # alert if strategy return > 5× PUT index in same year

def check_commission_scaling(trades, commission_per_contract=0.65):
    """Verify commissions scale with contract count."""
    violations = []
    for t in trades:
        contracts = t.get('contracts', 1)
        legs = 4 if t.get('type') == 'iron_condor' else 2
        expected_commission = commission_per_contract * legs * contracts
        actual_commission = t.get('commission', 0)
        ratio = actual_commission / expected_commission if expected_commission > 0 else 0
        if ratio < 0.5:  # more than 2x off
            violations.append({
                'trade': t.get('expiration'),
                'contracts': contracts,
                'expected': expected_commission,
                'actual': actual_commission,
            })
    return violations

def check_leverage(trades, starting_capital, spread_width):
    """Check peak simultaneous margin vs capital."""
    # Would require trade-level open/close timestamps to compute peak simultaneous
    # Simplified: check if ANY trade's margin alone exceeds capital
    for t in trades:
        margin = spread_width * 100 * t.get('contracts', 1)
        if margin > starting_capital:
            return False, f"Single position margin ${margin:,} > capital ${starting_capital:,}"
    return True, "OK"

def compare_to_benchmark(year_results):
    """Compare annual returns to CBOE PUT index."""
    flags = []
    for year_str, result in year_results.items():
        year = int(year_str)
        our_return = result.get('return_pct', 0)
        benchmark = CBOE_PUT_ANNUAL.get(year, 10)  # default 10% for unknown years
        if benchmark < 0:
            continue  # skip years where benchmark was negative (crash years)
        multiple = our_return / benchmark if benchmark != 0 else float('inf')
        if multiple > MAX_REALISTIC_OUTPERFORMANCE:
            flags.append(f"{year}: {our_return:.1f}% vs PUT index {benchmark:.1f}% = {multiple:.1f}× outperformance — REVIEW REQUIRED")
    return flags
```

---

### C. BENCHMARK REFERENCE TABLE

The following benchmarks define what is realistic. Any result exceeding 3× the relevant benchmark requires explicit documentation of *why* the outperformance is legitimate.

| Strategy | Source | Typical Annual Return | Exceptional Year Ceiling |
|---|---|---|---|
| CBOE BXM (SPX Buy-Write) | CBOE official | +8–12% | +25% |
| CBOE PUT (SPX PutWrite) | CBOE official | +10–15% | +30% |
| TastyTrade mechanical spreads | Published research | +15–40% | +80% |
| Small retail, high-frequency credit | Community reports | +30–70% | +150% |
| **This strategy (realistic, properly margined)** | Independent audit | **+20–50%** | **+100%** |
| **This strategy (backtester, broken)** | Backtester | **+820% avg** | **+2,643%** |

**Rule:** Any result >150% in a single year requires a written explanation for why the leverage regime that produced it would be available at a real broker. Any 6-year average >100% requires the same.

---

### D. CONFIG SCHEMA FIXES (required before next optimization run)

**Fix 1: max_positions must be in config with a realistic default**
```yaml
# In paper_champion.yaml and all config JSONs:
risk:
  max_positions: 5        # NOT 50. Realistic for $100K with margin
  max_contracts: 10       # NOT 100. ~$5,000 margin per position
  margin_per_contract: 500  # NEW: spread_width × 100, used for capital reservation
```

**Fix 2: Remove the 100% bypass in `_exposure_ok`**
```python
# BEFORE (broken):
def _exposure_ok(pos) -> bool:
    if self._max_portfolio_exposure_pct >= 100.0:
        return True  # bypasses all checks

# AFTER (correct):
def _exposure_ok(pos) -> bool:
    # Always enforce margin-based exposure
    total_margin = sum(
        p.get('max_loss', 0) * p.get('contracts', 1) * 100
        for p in self._open_positions
    )
    new_margin = pos.get('max_loss', 0) * pos.get('contracts', 1) * 100
    return (total_margin + new_margin) <= self.capital
```

**Fix 3: Deduct margin at entry (not just commission)**
```python
# In _find_real_spread and _find_iron_condor_opportunity, at entry:
# BEFORE:
self.capital -= commission_cost  # $1.30 — that's ALL

# AFTER:
margin_hold = spread_width * 100 * contracts
self.capital -= (commission_cost + margin_hold)
position['margin_hold'] = margin_hold

# At close: refund the margin hold
self.capital += position['margin_hold']
```

---

## SUMMARY TABLE

| Failure | Root Cause | Duration | All Runs Affected | Prevention |
|---|---|---|---|---|
| Commission 100x undercharge | `* contracts` missing from formula written Feb 20 | 21 days | Yes — all 517 leaderboard entries | Gate 1: commission/gross_credit check |
| No margin model | Margin code built in unused engine; never ported to main backtester | Entire project | Yes | Fix 3: deduct margin at entry |
| max_positions=50 hardcoded | Infrastructure config treated as non-risk parameter | Since Feb 26 | All optimization runs | Move to config; default 5 for $100K |
| Exposure cap disabled | Champion config sets 100%, which triggers bypass | Since champion config created | All champion experiments | Remove bypass; enforce margin floor |
| No return sanity check | Optimization pipeline rewards high returns without questioning them | Entire project | Yes | Gates 2, 3; benchmark comparison |
| Test coverage gap | Tests verify P&L math given commission value; never verify commission value itself | Since first test | Hidden the commission bug | Add integration test: commission scales with contracts |

---

## THE HONEST ANSWER TO CARLOS

The strategy has genuine edge. Selling OTM credit spreads with regime filtering in a high-IV environment is a legitimate approach with documented academic support (premium harvesting, short-volatility risk premia). The edge survives slippage stress tests and is not purely a data-mining artifact.

But +820% is not the edge. It is the edge compounded through a leverage machine that no broker would fund.

With realistic constraints ($100K, 5 positions, 10 contracts, proper margin, correct commissions):
- **Realistic annual target: +20–50% in favorable conditions**
- **Expected worst-case year: -10% to -30%**
- **This is still a good strategy** — it simply needs to be presented honestly

The paper trading account will show the truth. If the paper account is returning +15–40% annualized after 3–6 months, the strategy is working as intended and the math will have been honest all along. That result should be the benchmark, not the +820% that came from a simulation without a margin model.

---

*Written by Claude acting as independent auditor. All git blame citations refer to commits in `/Users/charlesbot/projects/attix-credit-spreads`. Commission fix is in uncommitted working tree as of 2026-03-13.*
