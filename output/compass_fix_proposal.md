# COMPASS Fix Proposal — Institutional Grade Hardening
### Composite Macro Position & Sector Signal

**Document type**: Pre-implementation proposal for review
**Author**: Claude Code
**Date**: 2026-03-07
**Status**: DRAFT — awaiting Carlos approval before any code changes
**Prerequisite reading**: `output/compass_integration_analysis.md`

---

## 0. Executive Summary

COMPASS has good bones but is not production-ready. The A/B backtest (exp_090 vs exp_101) showed a -6.74pp average annual drag — not because the macro layer is wrong in theory, but because three specific design flaws made it destructive in practice:

1. **Wrong primary signal**: the composite `overall` score (r = -0.106 vs forward returns) is used for sizing instead of `risk_appetite` (r = -0.250) — the only dimension with genuine predictive power.
2. **Broken RRG filter**: the 50% breadth threshold with 15 heterogeneous sectors produces a 54% block rate that is structurally random, not signal-based.
3. **Empty event gate**: `macro_events` has 1 row (a future CPI). Historical 2020–2025 FOMC/CPI/NFP data is absent, making the highest-conviction feature untestable.

Beyond these signal failures, the system has hardening gaps that would be unacceptable in a production quantitative environment:

- No schema versioning or migration support
- No data quality validation on writes or reads
- No staleness detection (stale weekly data presented as current)
- No unit or integration tests for any module
- No API layer (the proposed `macro_api.py` was never built)
- No operational runbook

This proposal specifies fixes across six tracks. **No code is written here — this is specification only.** Each fix is tied to the empirical evidence from `compass_integration_analysis.md` and the source code audit below.

---

## 1. Current State Audit

### 1.1 `shared/macro_snapshot_engine.py` — Issues Found

| # | Issue | Location | Severity |
|---|-------|----------|----------|
| E1 | Single shared `self._conn` SQLite connection is not thread-safe | `__init__`, line 145 | High |
| E2 | Score dimensions use equal 25% weights with no empirical justification | `_compute_macro_score`, line 696 | High |
| E3 | FRED data miss falls back to 50.0 silently — no log, no flag | `_get_fred_value`, `_score()` function | High |
| E4 | `_score_growth()` uses 4 CFNAI observations for 3m avg — insufficient at backfill boundaries | line 563 | Medium |
| E5 | Monthly FRED lag approximated as `31 + lag_days` — ignores 28/29/30/31 day months | `_get_fred_value`, line 381 | Medium |
| E6 | `prefetch_prices()` uses `INSERT OR IGNORE` — silent failures on partial fetches never retried | `_store_prices`, line 253 | Medium |
| E7 | No validation that computed score is within 0–100 (possible NaN propagation) | `_compute_macro_score` | Medium |
| E8 | No snapshot completeness check — partial snapshots (missing sectors) stored without warning | `generate_snapshot` | Medium |
| E9 | No week-over-week velocity tracking — a score cliff-drop is treated identically to a stable low score | entire module | Medium |
| E10 | `ALL_FOMC_DATES` only covers 2025–2026 (inherited from macro_event_gate.py) | N/A (engine doesn't use FOMC directly) | Low |
| E11 | `refresh_price_cache()` doesn't clear `fetch_log` entries — incremental re-fetch may miss gaps | line 773 | Low |
| E12 | HTTP session has no connect timeout separate from read timeout | line 135 | Low |

### 1.2 `shared/macro_state_db.py` — Issues Found

| # | Issue | Location | Severity |
|---|-------|----------|----------|
| D1 | No schema version column — impossible to detect schema drift or run migrations | `init_db` | High |
| D2 | `get_current_macro_score()` returns stale data with no warning if weekly job hasn't run | line 281 | High |
| D3 | `get_event_scaling_factor()` reads from key-value store set by a daily job — if job fails, returns stale 1.0 | line 322 | High |
| D4 | No integrity constraint: `overall` can be NULL in `macro_score` (nullable REAL) | schema, line 86 | Medium |
| D5 | No `macro_score.growth/inflation/fed_policy/risk_appetite` NOT NULL constraints | schema | Medium |
| D6 | `macro_events` table has no `FOMC_type` flag (scheduled vs emergency) | schema, line 104 | Medium |
| D7 | No index on `macro_score(date)` — full table scan on every `ORDER BY date DESC LIMIT 1` | init_db | Low |
| D8 | No backup / WAL checkpoint management for long-running processes | entire module | Low |
| D9 | `get_sector_rankings()` fetches latest date with `MAX(date)` — if sector_rs has a date ahead of snapshots (data anomaly), it returns the wrong snapshot | line 303 | Low |
| D10 | No `updated_at` column on `macro_score` — can't tell when a row was last written | schema | Low |

### 1.3 `shared/macro_event_gate.py` — Issues Found

| # | Issue | Location | Severity |
|---|-------|----------|----------|
| G1 | Hard-coded FOMC dates only for 2025–2026 — missing 2020–2024 (48 scheduled + 2 emergency meetings) | line 30–51 | Critical |
| G2 | No emergency meeting support — March 3 and March 15, 2020 unscheduled cuts are absent | entire module | High |
| G3 | `_cpi_release_date()` uses 12th-of-month approximation — actual BLS dates sometimes fall on 10th–14th | line 77 | Medium |
| G4 | `compute_composite_scaling()` uses `min()` — when FOMC overlaps with CPI (common), floor is already FOMC's 0.50×; CPI adds no additional information | line 177 | Medium |
| G5 | No post-event buffer — volatility persists for 1–2 days after a surprise; the gate only covers the run-up | entire module | Medium |
| G6 | `horizon_days=5` in `run_daily_event_check()` — only looks 5 days ahead; FOMC's 5-day window means the check must run daily without fail | line 201 | Medium |
| G7 | `get_upcoming_events()` delta_months loop is fragile — uses additive month arithmetic that doesn't handle year-end correctly if `today.month + delta_months` wraps from 12→13 | line 133–140 | Low |
| G8 | Scaling factors are hard-coded constants with no empirical validation against historical volatility around events | FOMC_SCALING, CPI_SCALING, NFP_SCALING | Low |

### 1.4 Data State Assessment

| Issue | Detail | Impact |
|-------|--------|--------|
| `macro_events` is empty | 1 future row, zero historical 2020–2025 rows | Event gate cannot be backtested |
| Score thresholds are absolute | `<45` fires 5% of time; `>70` fires 20% (all 2021) | Signal fires during wrong regime |
| RRG uses 15 heterogeneous sectors | 50% threshold = 48–54% block rate by construction | Random trade reducer, not signal |
| Weekly snapshot gap | Last snapshot: 2026-03-06. Today: 2026-03-07 (fresh) | No gap currently |
| Risk appetite weights | 50% VIX + 50% HY OAS — not validated; both are correlated (credit spreads widen with VIX) | Double-counting risk |
| Score velocity absent | No week-over-week delta stored or computed | Cliff drops indistinguishable from stable lows |

---

## 2. Fix Track 1 — Signal Quality

### FIX-S1: Replace Composite Score with Risk-Appetite-Weighted Composite

**Problem**: The current `overall = 0.25 × growth + 0.25 × inflation + 0.25 × fed + 0.25 × risk_appetite` gives equal weight to all four dimensions. The empirical evidence from 323 weeks of data shows:

```
Dimension        Correlation with 4w Forward SPY Return
growth           r = -0.056   (near zero)
inflation        r = +0.095   (slightly positive — inflation good for credit spreads?)
fed_policy       r = -0.004   (noise)
risk_appetite    r = -0.250   (the ONLY meaningful signal)
```

**Proposed fix — Revised composite weights**:

```
overall_v2 = (
    growth       × 0.10   # economic backdrop, minor
  + inflation    × 0.15   # goldilocks inflation matters, but lagging
  + fed_policy   × 0.10   # structural, slow-moving
  + risk_appetite × 0.65  # dominant signal (r=-0.250, 2.5× the other dimensions)
)
```

**Justification**:
- Weights derived from relative |r| values: |r_risk|/Σ|r| = 0.250/(0.056+0.095+0.004+0.250) = 61.4% → rounded to 65% for simplicity
- Growth and fed_policy retain non-zero weights to capture long-horizon structural shifts (e.g., recession detection for multi-month bear markets) even though they're noise at 4-week horizon
- The revised overall will be stored as a new column `overall_v2` in `macro_score` to preserve backward compatibility

**Implementation target**: `macro_snapshot_engine.py`, `_compute_macro_score()`, line 696

**Acceptance criteria**:
- `overall_v2` has |r| > 0.15 with forward 4w SPY returns on the 323-week dataset
- `overall_v2` score for 2020-03-20 (COVID bottom) < 40 (extreme fear signal fires)
- `overall_v2` score for 2021-06-18 (peak complacency) > 75

---

### FIX-S2: Percentile-Based Adaptive Thresholds

**Problem**: Absolute thresholds `<45` (fear) and `>70` (complacency) assume the macro score occupies a stable range. In practice, the score never went below 36.2 in 323 weeks, making `<45` a 5%-trigger event. These thresholds are not calibrated to the actual score distribution and will drift as the macro regime changes.

**Proposed fix — Rolling 3-year percentile thresholds**:

Every time a new weekly snapshot is generated, compute and store:
```
fear_threshold    = P20 of overall_v2 over the trailing 156 weeks (3 years)
neutral_low       = P35 of overall_v2 over trailing 156 weeks
neutral_high      = P65 of overall_v2 over trailing 156 weeks
complacency_threshold = P80 of overall_v2 over trailing 156 weeks
```

Store these four values in `macro_state` key-value table:
```
key: "compass_threshold_fear"       value: "53.1"
key: "compass_threshold_neutral_lo" value: "57.4"
key: "compass_threshold_neutral_hi" value: "64.8"
key: "compass_threshold_greed"      value: "69.5"
key: "compass_threshold_updated"    value: "2026-03-07"
```

**Why rolling 3-year window**: 3 years (~156 weekly snapshots) captures one full business cycle and at least one bear market. Too short (1yr) and thresholds whipsaw; too long (5yr+) and they lag regime changes.

**Transition behavior**:
- For the first 3 years of data (2020–2022), use expanding window
- Switch to rolling 3-year window from 2023 onward

**Integration with sizing**:
```python
# In macro_state_db.py, new function:
def get_compass_thresholds(db_path=None) -> dict:
    """Return current adaptive thresholds, falling back to static defaults."""
    keys = ["compass_threshold_fear", "compass_threshold_neutral_lo",
            "compass_threshold_neutral_hi", "compass_threshold_greed"]
    defaults = {"fear": 53.0, "neutral_lo": 57.0, "neutral_hi": 65.0, "greed": 69.5}
    result = {}
    for key in keys:
        val = get_state(key, db_path=db_path)
        dim = key.replace("compass_threshold_", "")
        result[dim] = float(val) if val else defaults.get(dim, 61.0)
    return result
```

**Sizing table (using risk_appetite directly, percentile-mapped)**:

| risk_appetite zone | Condition | Size multiplier | Rationale |
|-------------------|-----------|----------------|-----------|
| Extreme fear | < P10 of risk_appetite | 1.25× | IV is highest, premium is richest, bounces are strongest |
| Elevated fear | P10–P20 | 1.15× | Still risk-off, elevated volatility premium |
| Mild fear | P20–P35 | 1.05× | Slight premium to premium selling |
| Neutral | P35–P65 | 1.00× | No adjustment |
| Mild complacency | P65–P80 | 0.95× | Minor reduction |
| High complacency | P80–P90 | 0.90× | Meaningful reduction |
| Extreme complacency | > P90 | 0.80× | Maximum reduction — historical mean reversion upcoming |

**Acceptance criteria**:
- Fear regime triggers ~20% of weeks (not 5%)
- Complacency regime triggers ~20% of weeks
- P10/P90 triggers each fire ~10% of time
- 2021 Q1 (peak complacency): risk_appetite in P80–P90 zone → 0.90× (not 1.0× as with current broken threshold)
- 2020-03-20 (COVID bottom): risk_appetite in P10 zone → 1.25×

---

### FIX-S3: RRG Filter Redesign — XLI+XLF Dual-Lagging

**Problem**: The current implementation (`_build_compass_series` in backtester.py) queries ALL 15 sectors from `sector_rs` and applies a 50% breadth threshold. This blocks ~54% of weeks regardless of regime — it's structurally random because by RRG construction, roughly half of sectors are always below the cross-sectional mean.

**Root cause analysis**:
RRG quadrant classification is cross-sectional: at any given week, the 15 sectors are normalized around a cross-sectional mean of 100. By symmetry, approximately half will be above-mean (Leading/Improving) and half below-mean (Lagging/Weakening) at all times. A 50% breadth threshold on a cross-sectionally normalized measure will always block ~50% of observations.

**Evidence**:
```
7 liquid sectors (XLE/XLF/XLV/XLK/XLI/XLU/XLY):
  Average Leading+Improving fraction: 50.3% (near-exactly 50% by construction)
  Block rate at 50% threshold: 48.6% — regardless of year or regime
```

**Proposed fix — Replace breadth filter with dual-sector economic signal**:

The new RRG filter uses the two sectors most predictive of economic conditions AND most relevant to SPY credit spread entries:

**XLI (Industrials)**: Tracks PMI, manufacturing, capital expenditure — the best leading indicator of broad economic momentum. XLI Leading = economic acceleration underway = bull puts should be allowed.

**XLF (Financials)**: Tracks credit conditions, bank lending, yield curve exploitation. XLF Leading = credit is flowing, risk-on = bull puts should be allowed. XLF Lagging = credit stress = caution warranted.

**Rule**: Block bull put entries ONLY when **both XLI AND XLF are simultaneously in Lagging or Weakening quadrant**. This is a genuine economic deterioration signal — not a random coin flip.

**Proposed block rate**:
Need empirical validation on the 323-week dataset (to be measured as part of implementation), but expected ~15–20% based on the observation that XLI and XLF are rarely in coordinated deterioration except during genuine bear markets (2022 H1, COVID crash 2020 Q1).

**Secondary enhancement — Score velocity flag**:
When `overall_v2` drops > 8 points week-over-week, add a "cliff alert" flag regardless of absolute level. A cliff drop from 63 → 54 is more dangerous than a stable reading of 54.

```python
# In macro_snapshot_engine.py, compute_macro_score():
prev_score = get_prev_overall_v2(as_of_date - 7 days)
velocity = overall_v2 - prev_score  # negative = deteriorating
# Store in macro_score table as: score_velocity REAL
```

**Acceptance criteria**:
- Block rate for XLI+XLF both Lagging: ~15–22% of 2020–2025 weeks
- 2020 Q1 (COVID crash): XLI+XLF both Lagging → filter should block bull puts for ~4–8 weeks ✓
- 2021 (bull year): filter mostly inactive → bull puts allowed most weeks ✓
- 2022 H1 (bear market): filter active for ~15–20 weeks ✓
- 2022 H2 (recovery begins): filter lifts as XLI recovers ✓

---

### FIX-S4: Risk Appetite Component Weights

**Problem**: `risk_appetite = 0.50 × VIX_score + 0.50 × HY_score`. VIX and HY OAS are both risk-off indicators — they're positively correlated (both spike together in crises). Equal weighting effectively double-counts the same signal, and the weights have no empirical basis.

**Proposed fix**: Empirically validate VIX vs HY OAS independent contributions and adjust weights. For the proposal, we recommend:

```
risk_appetite_v2 = VIX_score × 0.60 + HY_OAS_score × 0.40
```

**Rationale**:
- VIX is daily (more current), highly liquid, directly reflective of options market fear premium — which is precisely what we're trading
- HY OAS is also daily but reflects credit market stress with different lag dynamics
- VIX receives higher weight because our instrument (SPY options) is more directly priced off VIX than HY spreads
- Validation: compute both components' individual correlations with forward 4w SPY returns using the 323-week dataset

**Additional sub-signal to consider**: The VIX term structure ratio (VIX/VIX3M) is already used by the combo regime detector as a directional signal. The risk_appetite score could be enhanced by:
```
vix_ts_score = _score(VIX/VIX3M, [0.85, 0.90, 0.95, 1.00, 1.05, 1.15], [100, 90, 70, 50, 25, 0])
```
This captures contango (VIX < VIX3M, normal = bullish) vs backwardation (VIX > VIX3M, inverted = fear) independently of the absolute VIX level.

---

### FIX-S5: Add Score Velocity to macro_score Schema

**Problem**: A score of 52 is treated identically whether it has been stable at 52 for 10 weeks or just cliff-dropped from 67. The rate of change carries significant information — rapid deterioration precedes drawdowns.

**Evidence from data**: The worst 4-week forward returns in our dataset occurred AFTER weeks where risk_appetite was high (2020-02-21: risk_appetite=76.0 → next 4w: -31.4%). The crash began while the score was still elevated. Score velocity would have flagged the cliff even before the absolute level crossed a threshold.

**Proposed additions to `macro_score` schema**:
```sql
ALTER TABLE macro_score ADD COLUMN overall_v2 REAL;
ALTER TABLE macro_score ADD COLUMN score_velocity REAL;    -- week-over-week Δ overall_v2
ALTER TABLE macro_score ADD COLUMN risk_app_velocity REAL; -- week-over-week Δ risk_appetite
ALTER TABLE macro_score ADD COLUMN regime_days INTEGER;    -- consecutive days in current BULL_MACRO/BEAR_MACRO/NEUTRAL_MACRO
ALTER TABLE macro_score ADD COLUMN updated_at TEXT DEFAULT (datetime('now'));
```

**Velocity signal integration**:
```
If score_velocity < -8 (rapid deterioration): apply additional 0.90× size multiplier
If score_velocity > +8 (rapid improvement):   apply additional 1.05× size multiplier
```
These multipliers compose with the level-based sizing from FIX-S2.

---

## 3. Fix Track 2 — Data Completeness

### FIX-D1: Backfill macro_events — FOMC/CPI/NFP 2020–2025

**Problem**: The `macro_events` table is effectively empty for all historical backtesting. Without this data, the event gate feature cannot be tested and cannot contribute to the A/B backtest.

**Scope**: 6 years × (8 FOMC + 12 CPI + 12 NFP) ≈ 192 scheduled events. Plus 2 emergency FOMC meetings in 2020.

**FOMC Dates (complete list for 2020–2024)**:

```python
FOMC_2020 = [
    # Scheduled
    date(2020, 1, 29),
    date(2020, 4, 29),
    date(2020, 6, 10),
    date(2020, 7, 29),
    date(2020, 9, 16),
    date(2020, 11, 5),
    date(2020, 12, 16),
    # Emergency inter-meeting cuts (unscheduled)
    date(2020, 3, 3),   # Emergency: -50bps (COVID)
    date(2020, 3, 15),  # Emergency: -100bps + QE restart (COVID)
]
FOMC_2021 = [
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28),
    date(2021, 6, 16), date(2021, 7, 28), date(2021, 9, 22),
    date(2021, 11, 3), date(2021, 12, 15),
]
FOMC_2022 = [
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4),
    date(2022, 6, 15), date(2022, 7, 27), date(2022, 9, 21),
    date(2022, 11, 2), date(2022, 12, 14),
]
FOMC_2023 = [
    date(2023, 2, 1),  date(2023, 3, 22), date(2023, 5, 3),
    date(2023, 6, 14), date(2023, 7, 26), date(2023, 9, 20),
    date(2023, 11, 1), date(2023, 12, 13),
]
FOMC_2024 = [
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1),
    date(2024, 6, 12), date(2024, 7, 31), date(2024, 9, 18),
    date(2024, 11, 7), date(2024, 12, 18),
]
```

**CPI dates**: Use `_cpi_release_date(year, month)` from `macro_event_gate.py` for 2020–2024, verified against BLS historical calendar. The approximation (12th of month+1) is accurate to within ±2 days for 95% of releases.

**NFP dates**: Use `_nfp_release_date(year, month)` from `macro_event_gate.py` for 2020–2024. First Friday of month+1 is accurate for all standard releases.

**Emergency meeting handling**: Add `event_type` flag variants:
- `FOMC_SCHEDULED` — standard 8 per year
- `FOMC_EMERGENCY` — unscheduled inter-meeting (Mar 3, Mar 15, 2020)
- Emergency FOMC: use expanded scaling window (7 days before, not 5) and deeper reduction (0.40× day-of instead of 0.50×) because emergency meetings precede maximum uncertainty

**Required new table column**:
```sql
ALTER TABLE macro_events ADD COLUMN is_emergency INTEGER DEFAULT 0;
```

**Implementation**: A one-time script `scripts/backfill_macro_events.py` that:
1. Generates all FOMC (scheduled + emergency) for 2020–2025
2. Generates all CPI/NFP for 2020–2025 using existing helpers
3. Computes `scaling_factor` and `days_out` relative to each event date
4. For historical events, computes `days_out = 0` (event has passed) and `scaling_factor = 1.0` (retroactively no restriction)
5. For the backtester: the COMPASS series builder must compute `days_out` relative to the trading date being evaluated, not the run date

**Note on retroactive days_out**: The `macro_events` table stores `days_out` as computed at the time of the daily job. For historical events, we need the backtester to compute `days_out = (event_date - trading_date).days` dynamically rather than reading a stored `days_out` value. This requires a change to how the backtester reads event data.

**Acceptance criteria**:
- `macro_events` table has ≥192 rows covering 2020–2025
- Emergency FOMC March 2020 rows present with `is_emergency = 1`
- Querying events for 2022-06-15 (FOMC date) returns correct entry
- Querying events for 2020-03-14 (1 day before emergency March 15) returns scaling_factor ≤ 0.60

---

### FIX-D2: Snapshot Validation Audit

**Problem**: 323 snapshots exist but were never audited for data quality. Silent FRED failures (`_get_fred_value` returns None → `_score()` returns 50.0) could have produced systematically biased scores for some weeks.

**Proposed validation checks** (run once as an audit script `scripts/audit_compass_snapshots.py`):

```
1. Missing dimension scores: flag any row in macro_score where growth IS NULL, inflation IS NULL, etc.
2. Suspicious neutrals: flag any row where all 4 dimensions are within 48–52 (all-neutral = probable FRED cache miss)
3. Sector coverage: flag any snapshot date where sector_rs has < 12 rows (should always have 15)
4. Score range: flag any overall > 100 or < 0
5. Temporal gaps: identify any calendar week (Friday) between 2020-01-03 and 2026-03-06 where snapshot is missing
6. FRED data availability: cross-check that CFNAI, PAYEMS, CPIAUCSL data was available with correct lag for each snapshot date
7. Price cache coverage: verify all 15 ETF tickers have price data for every snapshot date's lookback window
```

**Expected anomalies to find and document**:
- Early 2020 snapshots (Jan-Mar 2020) may have missing 12M RS for some sectors (insufficient history before 2019-01-03)
- CFNAI/PAYEMS releases have revisions — our cached FRED data captures the original release, not revisions (this is correct for live trading but should be documented)
- The March 2020 COVID period may show unusually fast score drops that stress-test the scoring curves

**Audit output**: `output/snapshot_audit_report.md` listing all anomalies with severity classification and recommended remediation (re-run snapshot, accept with note, flag as unreliable).

---

### FIX-D3: Real-Time Staleness Detection

**Problem**: `get_current_macro_score()` reads the most recent row from `macro_score` without any staleness check. If the weekly job fails for 3 weeks, it returns a 3-week-old score with no indication it's stale.

**Proposed fix**:

```python
def get_current_macro_score(
    db_path=None,
    max_staleness_days: int = 10,
    raise_on_stale: bool = False,
) -> tuple[float, dict]:
    """
    Returns (score, metadata).
    metadata: {"date": "...", "days_old": N, "is_stale": bool}

    If is_stale and raise_on_stale: raises MacroDataStaleError.
    If is_stale and not raise_on_stale: logs WARNING and returns stale score.
    """
    row = conn.execute(
        "SELECT date, overall FROM macro_score ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return 50.0, {"date": None, "days_old": None, "is_stale": True}

    snapshot_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
    days_old = (date.today() - snapshot_date).days
    is_stale = days_old > max_staleness_days

    if is_stale:
        msg = f"COMPASS macro score is {days_old} days old (last snapshot: {row['date']})"
        logger.warning(msg)
        if raise_on_stale:
            raise MacroDataStaleError(msg)

    return float(row["overall"]), {
        "date": row["date"],
        "days_old": days_old,
        "is_stale": is_stale,
    }
```

**Add `MacroDataStaleError` to `shared/exceptions.py`** (or create it).

**Apply staleness check in all public API endpoints** — return `{"stale": true, "last_snapshot": "..."}` in response metadata.

---

### FIX-D4: Schema Versioning

**Problem**: The `macro_state.db` has no schema version. Adding columns (as required by FIX-S1 through FIX-S5) will silently fail on existing deployments if the schema isn't migrated. In a production environment, unversioned schema changes cause silent data corruption.

**Proposed fix**: Add a `schema_version` entry to `macro_state` key-value table, managed by a migration system.

```python
# In macro_state_db.py:

CURRENT_SCHEMA_VERSION = 2  # increment with each migration

MIGRATIONS = {
    1: """
        -- v1 → v2: Add score_velocity, overall_v2, is_emergency columns
        ALTER TABLE macro_score ADD COLUMN overall_v2 REAL;
        ALTER TABLE macro_score ADD COLUMN score_velocity REAL;
        ALTER TABLE macro_score ADD COLUMN risk_app_velocity REAL;
        ALTER TABLE macro_score ADD COLUMN updated_at TEXT;
        ALTER TABLE macro_events ADD COLUMN is_emergency INTEGER DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_macro_score_date ON macro_score(date);
    """,
}

def migrate_db(path=None) -> None:
    """Run all pending schema migrations."""
    conn = get_db(path)
    current = int(get_state("schema_version", default="0", db_path=path) or 0)
    for version in sorted(MIGRATIONS.keys()):
        if version >= current:
            conn.executescript(MIGRATIONS[version])
            conn.commit()
            set_state("schema_version", str(version + 1), db_path=path)
    conn.close()
```

**`init_db()` must call `migrate_db()`** so that any new deployment automatically runs pending migrations.

---

## 4. Fix Track 3 — Engine Hardening

### FIX-E1: Thread-Safe DB Connections in MacroSnapshotEngine

**Problem**: `self._conn` in `MacroSnapshotEngine.__init__` is a single shared SQLite connection. SQLite connections are NOT thread-safe by default. If the weekly job is ever run with threading or from multiple processes, this causes corruption or errors.

**Proposed fix**: Remove `self._conn` as an instance variable. Replace all `self._conn.execute(...)` calls with a context manager that opens a fresh connection, executes, and closes:

```python
from contextlib import contextmanager

@contextmanager
def _cache_db(self):
    conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# Usage:
with self._cache_db() as conn:
    rows = conn.execute("SELECT ...").fetchall()
```

---

### FIX-E2: Score Validation on Every Snapshot

**Problem**: `_compute_macro_score()` can return NaN or out-of-range values if FRED data is missing and the `_score()` function's neutral fallback (50.0) is not applied consistently.

**Proposed fix**: Add a `_validate_snapshot()` function called at the end of `generate_snapshot()`:

```python
def _validate_snapshot(self, snap: dict) -> list[str]:
    """Return list of validation warnings. Empty list = pass."""
    warnings = []
    ms = snap.get("macro_score", {})

    # Score range checks
    overall = ms.get("overall")
    if overall is None:
        warnings.append("CRITICAL: overall score is None")
    elif not (0 <= overall <= 100):
        warnings.append(f"WARN: overall score {overall} out of 0-100 range")

    # Dimension completeness
    for dim in ["growth", "inflation", "fed_policy", "risk_appetite"]:
        if ms.get(dim) is None:
            warnings.append(f"WARN: dimension '{dim}' is None (FRED miss?)")
        elif ms.get(dim) == 50.0:
            warnings.append(f"INFO: dimension '{dim}' is exactly 50.0 — possible FRED fallback")

    # Sector coverage
    sectors = snap.get("sector_rankings", [])
    if len(sectors) < 12:
        warnings.append(f"WARN: only {len(sectors)} sectors in snapshot (expected 15)")

    # SPY close sanity
    spy = snap.get("spy_close")
    if spy and not (10 < spy < 10000):
        warnings.append(f"WARN: SPY close {spy} looks implausible")

    return warnings
```

Log all warnings; raise `SnapshotValidationError` if any CRITICAL-level warning is present.

---

### FIX-E3: Retry Logic for FRED and Polygon Fetches

**Problem**: `_fetch_polygon_aggs()` returns `[]` on any exception with a single error log. `_fetch_fred_public_csv()` similarly returns `[]`. There's no retry at the fetch level (only at the requests Session level for HTTP 5xx). A single timeout or connection error silently produces a snapshot with missing sector data or neutral-defaulted FRED scores.

**Proposed fix**: Wrap both fetch methods with explicit retry logic separate from the HTTP-level retry:

```python
import functools, time

def _with_retry(func, max_attempts=3, initial_delay=2.0, backoff=2.0, label=""):
    """Retry func up to max_attempts times with exponential backoff."""
    delay = initial_delay
    for attempt in range(1, max_attempts + 1):
        try:
            result = func()
            if result:  # non-empty result = success
                return result
            logger.warning("%s: empty result on attempt %d/%d", label, attempt, max_attempts)
        except Exception as exc:
            logger.warning("%s: error on attempt %d/%d: %s", label, attempt, max_attempts, exc)
        if attempt < max_attempts:
            time.sleep(delay)
            delay *= backoff
    return []  # all attempts failed
```

Apply to both `_fetch_polygon_aggs()` and `_fetch_fred_public_csv()`.

---

### FIX-E4: Emergency FOMC Handling in macro_event_gate.py

**Problem**: `macro_event_gate.py` only looks up events from the `ALL_FOMC_DATES` list. Emergency FOMC meetings are not in the list (and cannot be computed algorithmically — they are unscheduled). The March 2020 emergency meetings are the most consequential risk events in our 6-year backtest period.

**Proposed fix — Two-part**:

**Part A**: Add historical emergency meetings to `macro_event_gate.py` as a hard-coded list:

```python
# Emergency inter-meeting FOMC actions (unscheduled, require manual addition each occurrence)
FOMC_EMERGENCY_DATES = [
    date(2020, 3, 3),   # COVID: -50bps inter-meeting cut
    date(2020, 3, 15),  # COVID: -100bps + QE restart (Sunday evening announcement)
    # Add future emergency meetings here as they occur
]

# Different scaling for emergency meetings (larger pre-meeting window, deeper cut day-of)
FOMC_EMERGENCY_SCALING: Dict[int, float] = {
    7: 1.00, 6: 0.90, 5: 0.80, 4: 0.70, 3: 0.60, 2: 0.55, 1: 0.45, 0: 0.40
}
```

**Part B**: Add a post-event buffer (1–2 day reduced sizing after a significant event):

The current gate reduces size before events but restores to 1.0× the day after. In practice, volatility persists for 1–2 days post-surprise. Proposed post-event buffer:

```python
FOMC_POST_EVENT_SCALING: Dict[int, float] = {
    -1: 0.75,   # 1 day after event (post-event buffer)
    -2: 0.90,   # 2 days after event
}
```

(Negative days_out = days since the event.)

---

### FIX-E5: Validate FRED Lag Calculation

**Problem**: Monthly FRED series use `cutoff_offset = 31 + lag_days`. This assumes 31 days per month, but February has 28/29 days. For a February observation, the actual month-end is day 28, not day 31 — using 31 introduces a 3-day lookahead for February data.

**Proposed fix**:

```python
def _fred_available_date(self, series_id: str, obs_date: date) -> date:
    """
    Return the calendar date when obs_date's data was actually published on FRED.
    For monthly series, use actual month-end + lag_days.
    """
    lag_days = FRED_SERIES[series_id]["lag_days"]
    freq = FRED_SERIES[series_id]["freq"]
    if freq == "monthly":
        # obs_date is first-of-month (FRED convention)
        # month-end = first day of next month - 1 day
        if obs_date.month == 12:
            month_end = date(obs_date.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(obs_date.year, obs_date.month + 1, 1) - timedelta(days=1)
        return month_end + timedelta(days=lag_days)
    else:
        return obs_date + timedelta(days=lag_days)
```

Replace the current cutoff calculation with `_fred_available_date()`. This eliminates the lookahead bias for February data.

---

### FIX-E6: Structured Logging Throughout

**Problem**: All three modules use `logger.info/warning/error` but with inconsistent formatting and no structured fields. In a production environment, logs must be parseable by monitoring systems.

**Proposed fix**: Add a structured logging wrapper and ensure all key events emit consistent fields:

```python
# Each log call should include these standard fields where applicable:
# snapshot_date, score, ticker, error_type, duration_ms
logger.info(
    "snapshot_generated",
    extra={
        "snapshot_date": snap["date"],
        "overall": ms.get("overall"),
        "risk_appetite": ms.get("risk_appetite"),
        "sector_count": len(sectors),
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "warnings": validation_warnings,
    }
)
```

**Minimum log events to standardize**:
- Snapshot generated (date, overall, risk_appetite, sector_count, duration_ms)
- FRED fetch (series_id, observations_count, fallback_used: bool)
- Polygon fetch (ticker, bars_count, from_date, to_date)
- Snapshot validation warning (date, field, issue)
- Event gate computed (as_of, upcoming_events, composite_scaling)
- DB write (table, rows_written, duration_ms)
- Staleness alert (table, last_date, days_old)

---

## 5. Fix Track 4 — API Layer

### FIX-A1: Create macro_api.py

**Problem**: `scripts/macro_api.py` does not exist. The integration analysis confirmed this. The backtester currently reads directly from SQLite, which is acceptable for backtesting but wrong for live trading (API server and backtester should not share SQLite file directly in production).

**Proposed module**: `scripts/macro_api.py` — a lightweight Flask/FastAPI endpoint exposing COMPASS state as a JSON REST API.

**Design principles**:
- Read-only (no writes through the API)
- Stateless responses with explicit freshness metadata
- Fail-open for non-critical paths (stale data returned with warning vs hard failure)
- Auth via `x-api-key` header (consistent with `docs/ATTIX_STRATEGY_API.md` pattern)

**Endpoints specification**:

#### `GET /compass/score` — Current Macro Score
```json
Request:
  GET /compass/score
  x-api-key: <key>

Response 200:
{
  "overall": 62.7,
  "overall_v2": 58.4,
  "growth": 40.8,
  "inflation": 88.5,
  "fed_policy": 45.1,
  "risk_appetite": 76.3,
  "score_velocity": -1.2,
  "regime": "NEUTRAL_MACRO",
  "snapshot_date": "2026-03-06",
  "days_old": 1,
  "is_stale": false,
  "thresholds": {
    "fear": 53.0,
    "neutral_lo": 57.4,
    "neutral_hi": 64.8,
    "complacency": 69.5
  }
}

Response 503 (stale data):
{
  "error": "stale_data",
  "message": "Last snapshot is 14 days old",
  "last_snapshot_date": "2026-02-21",
  "fallback_overall": 62.7
}
```

#### `GET /compass/sectors` — Sector RRG State
```json
Response 200:
{
  "snapshot_date": "2026-03-06",
  "is_stale": false,
  "sectors": [
    {
      "ticker": "XLI",
      "name": "Industrials",
      "rrg_quadrant": "Leading",
      "rs_3m": 2.1,
      "rs_12m": 8.4,
      "rank_3m": 3
    },
    ...
  ],
  "breadth_signal": {
    "xli_xlf_both_lagging": false,
    "leading_count": 6,
    "improving_count": 4,
    "total_sectors": 15
  }
}
```

#### `GET /compass/events` — Upcoming Macro Events
```json
Request: GET /compass/events?horizon_days=10

Response 200:
{
  "as_of": "2026-03-07",
  "horizon_days": 10,
  "composite_scaling": 0.65,
  "events": [
    {
      "event_date": "2026-03-12",
      "event_type": "CPI",
      "description": "CPI Release (2026-02) — Mar 12, 2026",
      "days_out": 5,
      "scaling_factor": 1.0,
      "is_emergency": false
    }
  ]
}
```

#### `GET /compass/sizing-signal` — Combined Sizing Recommendation
```json
Response 200:
{
  "snapshot_date": "2026-03-06",
  "risk_appetite": 76.3,
  "risk_appetite_zone": "mild_complacency",
  "macro_size_multiplier": 0.95,
  "event_scale_multiplier": 1.0,
  "rrg_bull_put_blocked": false,
  "combined_multiplier": 0.95,
  "velocity_flag": null,
  "is_stale": false
}
```

#### `GET /compass/health` — Health Check
```json
Response 200:
{
  "status": "ok",
  "last_snapshot": "2026-03-06",
  "days_since_snapshot": 1,
  "last_event_check": "2026-03-07",
  "schema_version": 2,
  "db_size_mb": 0.8
}

Response 503:
{
  "status": "degraded",
  "reason": "snapshot_stale",
  "last_snapshot": "2026-02-07",
  "days_since_snapshot": 28
}
```

**Implementation requirements**:
- Use Flask or FastAPI (FastAPI preferred for automatic OpenAPI docs)
- Rate limiting: 100 requests/minute per API key (use `slowapi` or simple in-memory counter)
- All endpoints return `Content-Type: application/json`
- Errors always return JSON `{"error": "...", "message": "..."}`
- Request logging: log endpoint, response_code, duration_ms, api_key_prefix for audit trail
- No direct SQLite access in API layer — use only `macro_state_db.py` functions

---

### FIX-A2: Input Validation

**Problem**: The current `get_upcoming_events()` and `run_daily_event_check()` functions take an `as_of: Optional[date]` parameter with no validation. Passing a string instead of a date object, or a future date far in the future, would produce wrong results silently.

**Proposed fix**: Add explicit type and range validation to all public functions:

```python
def get_upcoming_events(as_of: Optional[date] = None, horizon_days: int = 14) -> List[Dict]:
    today = as_of or date.today()
    if not isinstance(today, date):
        raise TypeError(f"as_of must be a date object, got {type(today)}")
    if not (date(2018, 1, 1) <= today <= date.today() + timedelta(days=365)):
        raise ValueError(f"as_of date {today} is outside expected range")
    if not (1 <= horizon_days <= 30):
        raise ValueError(f"horizon_days must be 1-30, got {horizon_days}")
    ...
```

---

## 6. Fix Track 5 — Testing

### FIX-T1: Unit Tests for Scoring Logic

**File**: `tests/test_macro_scoring.py`

Each scoring function has a piecewise-linear interpolation curve with named breakpoints. These curves embed qualitative judgments that must be validated against historical data. Unit tests should verify:

```python
class TestScoreGrowth:
    """Validate growth score against known CFNAI/payroll values."""

    def test_strong_growth(self):
        # CFNAI_3m = +0.8 (well above +0.7 expansion threshold)
        # Payrolls = +350k/month (strong job market)
        # Expected: growth_score ≈ 85-95
        ...

    def test_recession_signal(self):
        # CFNAI_3m = -1.5 (deep below -0.7 recession threshold)
        # Payrolls = -200k/month (job losses)
        # Expected: growth_score ≈ 5-15
        ...

    def test_covid_crash_period(self):
        # Actual CFNAI April 2020: ~ -19.0 (historic low)
        # Expected: growth_score → 0
        ...

class TestScoreInflation:
    def test_goldilocks(self):
        # CPI YoY = 2.1%, Core CPI = 2.0%, 5y breakeven = 2.2%
        # Expected: inflation_score ≈ 90-100
        ...

    def test_high_inflation(self):
        # CPI YoY = 8.5% (actual June 2022 peak)
        # Expected: inflation_score ≈ 20-35
        ...

class TestScoreRiskAppetite:
    def test_extreme_fear(self):
        # VIX = 82 (actual March 16, 2020 intraday high: 85.47)
        # HY OAS = 18% (COVID peak)
        # Expected: risk_appetite ≈ 0-5
        ...

    def test_peak_complacency(self):
        # VIX = 10 (actual Dec 2017 low, pre-pandemic baseline)
        # HY OAS = 2.0%
        # Expected: risk_appetite ≈ 92-100
        ...

class TestCompositeWeights:
    def test_revised_weights_give_higher_correlation(self):
        """Empirically verify that overall_v2 (65% risk_appetite) correlates
        better with forward 4w SPY returns than the current equal-weight composite."""
        # Load summary.csv, compute both scores, compare correlations
        # Assert: |r(overall_v2, 4w_fwd)| > |r(overall_v1, 4w_fwd)|
        ...
```

---

### FIX-T2: Event Gate Tests

**File**: `tests/test_macro_event_gate.py`

```python
class TestEventGate:
    def test_fomc_5day_window(self):
        # as_of = 5 days before FOMC decision
        # Expected: FOMC event appears with scaling_factor = 1.0 (full size, 5d out)
        ...

    def test_fomc_day_of(self):
        # as_of = FOMC decision day
        # Expected: scaling_factor = 0.50
        ...

    def test_cpi_nfp_overlap(self):
        # Some months have CPI and NFP within 2 days of each other
        # Expected: composite = min(CPI_scaling, NFP_scaling)
        ...

    def test_emergency_fomc_march_3_2020(self):
        # as_of = March 2, 2020 (1 day before emergency cut)
        # Expected: FOMC_EMERGENCY event appears with days_out=1, scaling≤0.55
        ...

    def test_emergency_fomc_march_15_2020_sunday(self):
        # March 15 was a Sunday announcement
        # Expected: scaling applied for the following Monday (March 16) trading day
        ...

    def test_no_false_trigger_far_from_events(self):
        # as_of = random mid-month date with no scheduled events within 5 days
        # Expected: empty events list, composite = 1.0
        ...

    def test_post_event_buffer(self):
        # as_of = 1 day after FOMC (if post-event buffer implemented per FIX-E4)
        # Expected: scaling_factor = 0.75 (not 1.0)
        ...
```

---

### FIX-T3: DB Integration Tests

**File**: `tests/test_macro_state_db.py`

```python
class TestMacroStateDB:
    @pytest.fixture
    def test_db(self, tmp_path):
        """Create a fresh in-memory test DB for each test."""
        db_path = str(tmp_path / "test_macro.db")
        init_db(db_path)
        return db_path

    def test_save_and_retrieve_snapshot(self, test_db):
        snap = {
            "date": "2024-01-05",
            "spy_close": 480.0,
            "macro_score": {"overall": 65.0, "growth": 70.0, ...},
            "sector_rankings": [...],
        }
        save_snapshot(snap, db_path=test_db)
        score = get_current_macro_score(db_path=test_db)
        assert score == 65.0

    def test_staleness_detection(self, test_db):
        # Insert old snapshot (30 days ago)
        old_snap = {"date": (date.today() - timedelta(days=30)).strftime("%Y-%m-%d"), ...}
        save_snapshot(old_snap, db_path=test_db)
        score, meta = get_current_macro_score(db_path=test_db)
        assert meta["is_stale"] is True
        assert meta["days_old"] >= 30

    def test_schema_migration(self, test_db):
        # Verify migration runs cleanly
        migrate_db(test_db)
        # Verify new columns exist
        conn = get_db(test_db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(macro_score)").fetchall()]
        assert "overall_v2" in cols
        assert "score_velocity" in cols

    def test_event_upsert_idempotent(self, test_db):
        events = [{"event_date": "2024-03-20", "event_type": "FOMC", ...}]
        upsert_events(events, db_path=test_db)
        upsert_events(events, db_path=test_db)  # second time = no-op
        count = get_db(test_db).execute("SELECT COUNT(*) FROM macro_events").fetchone()[0]
        assert count == 1
```

---

### FIX-T4: Historical Validation Tests

**File**: `tests/test_compass_historical_validation.py`

These tests validate COMPASS against known historical events where the "correct" answer is observable in hindsight. They form the regression test suite that must pass before any COMPASS update is considered production-ready.

```python
GROUND_TRUTH = {
    # (snapshot_date, expected_behavior)
    "2020-03-20": {
        "risk_appetite": (0, 20),       # extreme fear — COVID bottom
        "overall_v2": (30, 55),         # deeply fearful macro
        "rrg_xli_xlf_blocked": True,    # industrials + financials both lagging
        "size_multiplier": (1.15, 1.30) # large fear premium
    },
    "2020-02-21": {
        "risk_appetite": (65, 85),      # complacency before COVID crash
        "overall_v2": (62, 74),         # appeared healthy — the danger sign
        "rrg_xli_xlf_blocked": False,   # markets still bullish
        "size_multiplier": (0.85, 1.00) # mild reduction or neutral
    },
    "2021-06-18": {
        "risk_appetite": (55, 80),      # post-vaccine bull market complacency
        "overall_v2": (64, 78),         # strong macro
        "rrg_xli_xlf_blocked": False,   # broad market leadership
        "size_multiplier": (0.85, 0.95) # complacency reduction active
    },
    "2022-10-14": {
        "risk_appetite": (25, 50),      # bear market stress
        "overall_v2": (42, 58),         # weak macro
        "rrg_xli_xlf_blocked": True,    # cyclicals lagging in bear
        "size_multiplier": (1.05, 1.20) # fear premium
    },
    "2025-04-04": {
        "risk_appetite": (35, 60),      # tariff shock fear
        "overall_v2": (50, 65),         # moderate macro stress
        "size_multiplier": (1.05, 1.20) # fear sizing boost
    },
}

@pytest.mark.parametrize("date_str,expected", GROUND_TRUTH.items())
def test_historical_validation(date_str, expected):
    snap = load_snapshot_from_db(date_str)
    assert expected["risk_appetite"][0] <= snap["risk_appetite"] <= expected["risk_appetite"][1]
    ...
```

These 5 validation checkpoints cover the critical regimes: COVID crash, pre-crash complacency, 2021 bull, 2022 bear, and 2025 tariff shock. Any change to the scoring curves must not cause these to fail.

---

### FIX-T5: API Layer Tests

**File**: `tests/test_macro_api.py`

```python
class TestCompassAPI:
    def test_get_score_returns_freshness_metadata(self, client):
        resp = client.get("/compass/score", headers={"x-api-key": TEST_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert "days_old" in data
        assert "is_stale" in data

    def test_stale_data_returns_503(self, client, stale_db):
        # DB with 20-day-old snapshot
        resp = client.get("/compass/score", headers={"x-api-key": TEST_KEY})
        assert resp.status_code == 503

    def test_rate_limiting(self, client):
        # Make 101 requests quickly
        for _ in range(100):
            client.get("/compass/score", headers={"x-api-key": TEST_KEY})
        resp = client.get("/compass/score", headers={"x-api-key": TEST_KEY})
        assert resp.status_code == 429

    def test_health_check_no_auth(self, client):
        # Health check should not require auth
        resp = client.get("/compass/health")
        assert resp.status_code in (200, 503)

    def test_invalid_api_key_returns_401(self, client):
        resp = client.get("/compass/score", headers={"x-api-key": "bad_key"})
        assert resp.status_code == 401
```

---

## 7. Fix Track 6 — Documentation

### FIX-DOC1: Methodology Document

**File**: `docs/COMPASS_METHODOLOGY.md`

This document explains every signal, threshold, and calibration choice to an institutional standard. A quant at a hedge fund reviewing this should understand exactly what was measured, how, and why.

Required sections:
1. **System Overview** — what COMPASS is, what it is not, how it relates to the backtester
2. **Data Sources** — FRED series (IDs, lag_days, known revisions), Polygon (ETF universe, refresh cadence), BLS event calendar
3. **Scoring Methodology** — each dimension (growth, inflation, fed_policy, risk_appetite) with:
   - The raw indicators used
   - The piecewise-linear scoring curves (xp/fp breakpoints) with rationale for each breakpoint
   - Historical validation examples
   - Known weaknesses (e.g., CFNAI lag, VIX mean-reversion)
4. **Composite Score** — equal-weight v1 vs risk_appetite-weighted v2, with empirical justification table
5. **Adaptive Thresholds** — rolling 3-year P20/P80 approach, transition from expanding to rolling window
6. **RRG Signal** — what RRG quadrants mean, why cross-sectional normalization makes breadth filters invalid, why XLI+XLF dual-lagging was chosen
7. **Event Gate** — FOMC/CPI/NFP logic, scaling factors with empirical context, emergency meeting handling
8. **Score Velocity** — week-over-week delta, cliff alert thresholds
9. **Known Limitations** — weekly granularity, FRED revision risk, VIX/HY correlation overlap, 2021 complacency trap, unscheduled events

---

### FIX-DOC2: Data Dictionary

**File**: `docs/COMPASS_DATA_DICTIONARY.md`

Every column in every table, with type, units, source, nullable status, and known caveats.

Example format:
```
Table: macro_score
Column: risk_appetite
Type: REAL (0-100)
Source: VIXCLS × 0.60 + BAMLH0A0HYM2 × 0.40, both piecewise-normalized
Null handling: Returns 50.0 (neutral) if FRED data unavailable — logged as WARNING
Lag: 1 trading day (daily FRED series)
Caveats: VIX and HY OAS are positively correlated; effective independent signal content
         is less than two separate signals. VIX measures equity vol; HY OAS measures
         credit spread — together they capture risk-off from two market angles.
```

---

### FIX-DOC3: Operational Runbook

**File**: `docs/COMPASS_RUNBOOK.md`

For production operations. Covers:

1. **Normal weekly snapshot job** — when it runs, what it does, how to verify success
2. **Normal daily event check job** — timing, verification
3. **Data freshness SLA** — alert if snapshot > 10 days old (weekly job failed)
4. **FOMC date maintenance** — add 2027 dates by Dec 2026; checklist
5. **Emergency FOMC handling** — when the Fed calls an emergency meeting:
   - Add to `FOMC_EMERGENCY_DATES` in `macro_event_gate.py`
   - Run `run_daily_event_check(as_of=announcement_date)`
   - Verify scaling_factor is stored in `macro_state.event_scaling_factor`
6. **Schema migration procedure** — when to run `migrate_db()`, rollback procedure
7. **Snapshot backfill procedure** — if weekly job misses multiple weeks, how to run `generate_snapshot()` for each missed Friday
8. **Troubleshooting**: common errors (FRED rate limit, Polygon quota exceeded, SQLite WAL checkpoint failure) with remediation steps
9. **Backup procedure** — how to backup macro_state.db before migrations

---

## 8. Implementation Roadmap

### Sequencing rationale
The fixes are ordered to unblock backtesting first (the most immediate value), then production hardening, then API/docs.

### Sprint 1 — Unblock Backtesting (1–2 days)

| Fix | Task | File(s) | Effort |
|-----|------|---------|--------|
| FIX-D1 | Backfill macro_events table | `scripts/backfill_macro_events.py` (new) | 2h |
| FIX-S1 | Add `overall_v2` with revised weights | `macro_snapshot_engine.py` | 1h |
| FIX-S2 | Add percentile threshold computation + storage | `macro_snapshot_engine.py`, `macro_state_db.py` | 2h |
| FIX-S3 | Redesign RRG filter to XLI+XLF dual-lagging | `macro_state_db.py` (new getter), `backtester.py` | 2h |
| FIX-D4 | Schema versioning + migration for new columns | `macro_state_db.py` | 1h |
| FIX-S5 | Add `score_velocity` computation and storage | `macro_snapshot_engine.py` | 1h |
| — | Recompute `overall_v2` and `score_velocity` for all 323 historical snapshots | `scripts/recompute_scores.py` (new) | 1h |

**Gate**: Run exp_102 A/B backtest with revised COMPASS. Confirm avg return ≥ exp_090 baseline before proceeding.

### Sprint 2 — Engine Hardening (2–3 days)

| Fix | Task | File(s) | Effort |
|-----|------|---------|--------|
| FIX-E1 | Thread-safe DB connections | `macro_snapshot_engine.py` | 2h |
| FIX-E2 | Snapshot validation function | `macro_snapshot_engine.py` | 2h |
| FIX-E3 | Retry logic for FRED + Polygon fetches | `macro_snapshot_engine.py` | 2h |
| FIX-E4 | Emergency FOMC handling + post-event buffer | `macro_event_gate.py` | 2h |
| FIX-E5 | Fix FRED monthly lag calculation | `macro_snapshot_engine.py` | 1h |
| FIX-E6 | Structured logging throughout | all 3 shared modules | 2h |
| FIX-D2 | Snapshot validation audit script | `scripts/audit_compass_snapshots.py` (new) | 2h |
| FIX-D3 | Staleness detection in all read functions | `macro_state_db.py` | 1h |

### Sprint 3 — Testing (2 days)

| Fix | Task | File(s) | Effort |
|-----|------|---------|--------|
| FIX-T1 | Unit tests for scoring logic | `tests/test_macro_scoring.py` | 3h |
| FIX-T2 | Event gate tests | `tests/test_macro_event_gate.py` | 2h |
| FIX-T3 | DB integration tests | `tests/test_macro_state_db.py` | 2h |
| FIX-T4 | Historical validation tests (5 ground-truth checkpoints) | `tests/test_compass_historical_validation.py` | 2h |

### Sprint 4 — API + Documentation (1–2 days)

| Fix | Task | File(s) | Effort |
|-----|------|---------|--------|
| FIX-A1 | Create macro_api.py (4 endpoints) | `scripts/macro_api.py` (new) | 4h |
| FIX-A2 | Input validation on all public functions | all 3 shared modules | 1h |
| FIX-T5 | API tests | `tests/test_macro_api.py` | 2h |
| FIX-DOC1 | Methodology document | `docs/COMPASS_METHODOLOGY.md` | 3h |
| FIX-DOC2 | Data dictionary | `docs/COMPASS_DATA_DICTIONARY.md` | 2h |
| FIX-DOC3 | Operational runbook | `docs/COMPASS_RUNBOOK.md` | 2h |

---

## 9. Success Criteria — Definition of "Institutional Grade"

COMPASS is considered institutional grade when ALL of the following are true:

### Signal Quality Gates
- [ ] `overall_v2` correlation with 4w forward SPY returns: |r| ≥ 0.15 (vs current 0.106)
- [ ] Adaptive thresholds fire in fear regime 18–22% of weeks (vs current 5%)
- [ ] Adaptive thresholds fire in complacency regime 18–22% of weeks
- [ ] XLI+XLF block rate: 15–25% of 2020–2025 weeks
- [ ] exp_102 A/B backtest: avg return ≥ exp_090 baseline (COMPASS does not hurt)
- [ ] All 5 historical validation checkpoints pass (FIX-T4)

### Data Completeness Gates
- [ ] `macro_events` table has ≥ 192 rows covering 2020–2025 (all FOMC/CPI/NFP)
- [ ] Emergency FOMC dates (March 3 and March 15, 2020) present in table
- [ ] Snapshot audit completes with 0 CRITICAL anomalies

### Engineering Quality Gates
- [ ] All unit tests pass (target ≥ 90% coverage of scoring functions)
- [ ] All integration tests pass
- [ ] Staleness detection: `get_current_macro_score()` returns `is_stale=True` when snapshot > 10 days old
- [ ] Schema migration runs cleanly from v1 → v2 on existing `macro_state.db`
- [ ] No thread-safety issues under concurrent reads (validate with pytest-xdist parallel runs)

### Documentation Gates
- [ ] `COMPASS_METHODOLOGY.md` reviewed and approved by Carlos
- [ ] `COMPASS_DATA_DICTIONARY.md` complete with all table columns
- [ ] `COMPASS_RUNBOOK.md` includes emergency FOMC procedure

### API Gates
- [ ] `GET /compass/health` returns 200 with correct `days_since_snapshot`
- [ ] `GET /compass/sizing-signal` returns correct `combined_multiplier` for at least 3 test snapshots
- [ ] Rate limiting prevents > 100 req/min per API key

---

## Appendix A: Complete FOMC Date Reference (2020–2026)

```
2020: Jan 29*, Mar 3 (EMERGENCY), Mar 15 (EMERGENCY), Apr 29, Jun 10, Jul 29, Sep 16, Nov 5, Dec 16
2021: Jan 27, Mar 17, Apr 28, Jun 16, Jul 28, Sep 22, Nov 3, Dec 15
2022: Jan 26, Mar 16, May 4, Jun 15, Jul 27, Sep 21, Nov 2, Dec 14
2023: Feb 1, Mar 22, May 3, Jun 14, Jul 26, Sep 20, Nov 1, Dec 13
2024: Jan 31, Mar 20, May 1, Jun 12, Jul 31, Sep 18, Nov 7, Dec 18
2025: Jan 29, Mar 19, May 7, Jun 18, Jul 30, Sep 17, Nov 5, Dec 10 (already in macro_event_gate.py)
2026: Jan 29, Mar 19, May 7, Jun 18, Jul 30, Sep 17, Nov 5, Dec 17 (already in macro_event_gate.py)

* The scheduled March 17-18, 2020 meeting was superseded by the March 15 emergency action.
```

---

## Appendix B: Empirical Evidence Summary

All findings are derived from `output/historical_snapshots/summary.csv` (323 weeks, 2020-2025) and `data/macro_state.db`.

| Finding | Value | Source |
|---------|-------|--------|
| macro_overall correlation with 4w fwd SPY | r = -0.106 | summary.csv correlation analysis |
| macro_risk_appetite correlation with 4w fwd SPY | r = -0.250 | summary.csv |
| Fear (score < 45) avg forward 4w return | +3.66% | summary.csv |
| Complacency (score > 70) avg forward 4w return | +1.77% (beats average!) | summary.csv |
| Q1 risk_appetite (lowest) avg 4w return | +2.92% | summary.csv quartile |
| Q4 risk_appetite (highest) avg 4w return | +0.58% | summary.csv quartile |
| Current RRG filter block rate (all 15 sectors, 50% threshold) | 54.0% | macro_state.db |
| RRG filter block rate (7 liquid sectors, 50% threshold) | 48.6% | macro_state.db |
| P20 of macro_overall (2020-2025) | 53.0 | summary.csv |
| P80 of macro_overall (2020-2025) | 69.5 | summary.csv |
| 2020-02-21 risk_appetite (pre-crash) | 76.0 → -31.4% next 4w | historical snapshots |
| 2020-03-20 risk_appetite (COVID bottom) | 7.3 → +25.3% next 4w | historical snapshots |

---

_This document is PLAN ONLY. No code has been changed. All fixes require Carlos approval before implementation begins._

_References_: `shared/macro_snapshot_engine.py`, `shared/macro_state_db.py`, `shared/macro_event_gate.py`, `output/compass_integration_analysis.md`, `output/compass_backtest_results.md`
