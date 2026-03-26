# MASTERPLAN.md — Operation Crack The Code 🎯

## Mission
Build a validated, multi-strategy options trading system on SPY. Data-driven approach: kill losing strategies, optimize winners, follow what the data says. Paper trade the winners, then go live.

## North Star
- **55% avg annual return**
- **Sharpe ratio of 6**
- **≤30% max drawdown** in any year
- **Multi-strategy, research-backed, validated**
- **All 6 years (2020-2025) profitable**
- **🚫 NO SYNTHETIC DATA — EVER.** All pricing must come from `IronVault.instance()` → `data/options_cache.db`. Black-Scholes/heuristic pricing is permanently BANNED. Cache miss → skip trade (return `None`), NEVER fabricate. See `docs/DATA_ARCHITECTURE.md`, `shared/iron_vault.py`, `scripts/iron_vault_setup.py`.

---

## 📋 EXPERIMENT REGISTRY

> **Authoritative data:** `experiments/registry.json`
> **Rules:** `EXPERIMENT_PROTOCOL.md`
> **Quick view:** `python scripts/list_experiments.py --all`

### 🟢 Live Paper Trading

| ID | Name | Creator | Ticker | Account | Avg Return | Max DD | ROBUST | Live Since |
|----|------|---------|--------|---------|-----------|--------|--------|------------|
| **EXP-400** | **The Champion** | maximus | SPY | PA36XFVLG0WE | +32.7% | -12.1% | 0.870 | 2026-03-15 |
| **EXP-401** | **The Blend** | maximus | SPY | PA3Y2XDYB9I3 | +40.7% | -7.0% | TBD | 2026-03-15 |
| **EXP-503** | **ML V2 Aggressive** | maximus | SPY | PA3Z9PLVYUL5 | TBD | TBD | TBD | 2026-03-22 |
| **EXP-600** | **IBIT Adaptive** | charles | IBIT | PA3O14JAJHJ0 | +139.2% | -19.4% | 0.950 | 2026-03-22 |

### 🔬 In Development

| ID | Name | Creator | Phase | Next Step |
|----|------|---------|-------|-----------|
| **EXP-500** | **ML Champion** | maximus | 1 — Data Collection | Accumulate 200+ labeled samples from EXP-400, then train XGBoost |
| **EXP-501** | **ML Blend** | maximus | 0 — Blocked | Blocked on EXP-500. Start after EXP-500 proves concept. |
| **EXP-601** | **IBIT ML Signal Filter** | charles | 1 — Built & Trained | Accumulate 12+ months walk-forward data; retrain for out-of-sample test |

### Experiment Details

#### EXP-400: The Champion
- **Run ID:** `endless_20260305_053514_ae5c`
- **Config:** `configs/champion.json`
- **Paper Config:** `configs/paper_champion.yaml`
- **Strategies:** Credit spread (regime-adaptive, 8.5% risk) + Iron condor (3.5% risk)
- **Trend filter:** 80-day MA
- **Validation:** ROBUST 0.870 | WF 3/3 | Jitter 0.98 | No cliff params
- **Year-by-year:** 2020: +8.9% | 2021: +101.5% | 2022: -1.9% | 2023: +37.5% | 2024: +23.8% | 2025: +26.5%
- **Paper trader support:** ✅ Full — alignment-patches PR merged
- **Branch:** `maximus/champion-config`

#### EXP-401: The Blend (Regime-Optimized)
- **Config:** Credit spread 12% base risk + Straddle/strangle 3% base risk
- **Regime scales (CS):** bull=1.0, bear=0.3, high_vol=0.3, low_vol=0.8, crash=0.0
- **Regime scales (S/S):** bull=1.5, bear=1.5, high_vol=2.5, low_vol=1.0, crash=0.5
- **Validation:** WF 3/3 (0.93/0.58/0.84) | Monte Carlo 10K passed | Slippage passed | Tail risk passed
- **Year-by-year:** 2020: +24.1% | 2021: +107.4% | 2022: +8.1% | 2023: +43.2% | 2024: +26.4% | 2025: +35.0%
- **After slippage:** 2020: +13.5% | 2021: +84.0% | 2022: +2.2% | 2023: +27.9% | 2024: +11.2% | 2025: +22.4%
- **Paper trader support:** ✅ Full — Operation Unified Front completed (straddle/strangle wired, unified entry+exit paths)
- **Branch:** `maximus/unified-front`

#### EXP-503: ML V2 Aggressive (DEPLOYED 2026-03-22)
- **Config:** `configs/paper_exp503.yaml`
- **Plist:** `deploy/com.pilotai.exp503.plist` → `~/Library/LaunchAgents/com.pilotai.exp503.plist`
- **Account:** PA3Z9PLVYUL5 (Alpaca paper, fresh — no prior trades)
- **Strategy:** CreditSpreadStrategy wrapped with RegimeModelRouter (ML V2 Aggressive)
- **Sizing profile:**
  - bull → 1.50× (lean in hard)
  - neutral → 1.00× (normal)
  - low_vol → 1.20× (calm markets, slightly more)
  - high_vol → 0.10× (near-flat, defensive)
  - bear → 0.10× (near-flat, defensive)
  - crash → 0.00× (no new trades)
- **regime_gate:** enabled — suppresses signal generation entirely in defensive regimes
- **ML model:** `ml/models/signal_model_20260217.joblib` (blend weight 0.25)
- **Dry-run:** 28/28 checks pass (`scripts/dryrun_exp503.py`)
- **Branch:** `compass-v2` (pushed to origin)
- **Baseline for comparison:** EXP-401 (same CS core, no ML sizing)

#### EXP-601: IBIT ML Signal Filter
- **Script:** `scripts/exp601_ml_signal_filter.py`
- **Training data:** `ml/ibit_training_data.csv` (249 trades, Nov 2024 – Mar 2026)
- **Model:** `ml/ibit_signal_model.joblib` (XGBoost, max_depth=3, min_child_weight=5, n_estimators=50)
- **Features:** dte, credit_pct, vix, realized_vol_20d, ma50_distance_pct, rsi_14, volume_ratio, btc_corr_30d, direction_bull (+3 others)
- **CV results:** AUC ≈ 0.50 (3-fold time-series) — no reliable out-of-sample signal yet
- **Baseline (EXP-600):** 249 trades | WR=84.3% | Return=+2.77% | MaxDD=-26.93%
- **In-sample comparison (threshold=0.50):** 151 trades | WR=89.4% | Return=+40.99% | MaxDD=-9.93%
- **CAVEAT:** Comparison is in-sample — improvement is overfit. Use CV AUC=0.50 as honest signal estimate.
- **Next step:** Accumulate 12+ months of EXP-600 paper trade data, retrain, test on held-out period.
- **Outputs:** `ml/ibit_model_report.md`, `ml/ibit_feature_importance.md`, `output/exp601_ml_comparison.json`

### 🪦 Retired

| ID | Name | Creator | Why Retired |
|----|------|---------|-------------|
| EXP-031 | Compound Bull Put | maximus | Overfit score 0.590 (hard gate failed). DTE cliff. Compound sizing artifacts. |
| EXP-036 | Compound 10% Both MA200 | maximus | Baseline experiment, superseded by EXP-400. |
| EXP-059 | Various | maximus | Superseded by EXP-400/401. |
| EXP-154 | Various | maximus | Superseded by EXP-400/401. |
| EXP-305 | COMPASS Portfolio | maximus | Multi-ticker approach superseded by focused EXP-400/401. |

---

## 📊 PHASE COMPLETION STATUS

| Phase | Name | Status | Key Result |
|-------|------|--------|------------|
| 0 | Strategy Discovery Engine | ✅ COMPLETE | 7 strategies built, champion found |
| 1 | Parameter Sweep | ✅ COMPLETE | 87 experiments, regime-adaptive winner |
| 2 | Position Sizing | ✅ COMPLETE | Returns plateau at 10% risk. 8.5% near-optimal for risk-adjusted. |
| 3 | Portfolio Blending | ✅ COMPLETE | CS+S/S blend beats CS+IC. +39.1% avg, -9.5% DD |
| 4 | Regime Switching | ✅ COMPLETE | Dynamic allocation: +40.7% avg, -7.0% DD |
| 5 | Final Validation | ✅ COMPLETE | WF 3/3, MC 10K, slippage, tail risk — ALL PASS |
| 6 | Paper Trading | 🔄 LIVE — VALIDATING | Both EXP-400 + EXP-401 deployed. 8-week clock: Mar 16 → May 11 |
| 6.5 | Operation Unified Front | ✅ COMPLETE | Entry + exit paths unified. All strategies use same code as backtester. |

---

## 🎯 CURRENT PRIORITY: Paper Trading

### Immediate (EXP-400 — The Champion)
1. ✅ Config created (`configs/paper_champion.yaml`)
2. ✅ Quickstart guide written (`PAPER_TRADING_QUICKSTART.md`)
3. ✅ Alignment-patches PR merged (paper trader aligned with backtester)
4. ✅ Branch pushed (`maximus/champion-config`)
5. ✅ Deployed via launchd (Charles, 2026-03-15)
6. ⏳ 8-week validation period (Mar 16 → May 11, 2026)

### Next (EXP-401 — The Blend)
1. ✅ Wire straddle/strangle into paper trader (Operation Unified Front)
2. ✅ Create paper_exp401.yaml config
3. ✅ Deployed side-by-side with EXP-400 via launchd (Charles, 2026-03-15)
4. ⏳ 8-week validation period (Mar 16 → May 11, 2026)
5. ⬜ Compare EXP-400 vs EXP-401 live results

### Deployment Status (2026-03-15)
- Nuclear reset: DBs nuked, Alpaca positions auto-close cron Monday 9:31 AM
- Both experiments running via launchd
- 8-week clock: **March 16 → May 11, 2026**

### Victory Conditions for Live Trading
- Paper trading 8+ weeks with results within 30% of backtest expectations
- No system errors or unintended trades
- Win rate >70%
- Max drawdown <20%
- → Proceed to live with 10% of intended capital, scale up over 4-8 weeks

---

## 🔧 INFRASTRUCTURE

### Key Files
```
🔒 Iron Vault (Centralized Data Layer):
<<<<<<< Updated upstream
├── shared/iron_vault.py           ← THE single data provider — all data access goes here
├── scripts/iron_vault_setup.py    ← Bootstrap: validates keys, checks cache, reports gaps
├── docs/DATA_ARCHITECTURE.md      ← Full data architecture documentation
├── data/options_cache.db          ← 905MB, 5.67M daily bars, 168K contracts (2020-2026)
└── data/macro_state.db            ← Regime/sector macro data
=======
├── shared/iron_vault.py            ← THE single data provider (singleton)
├── scripts/iron_vault_setup.py     ← Bootstrap & validation
├── docs/DATA_ARCHITECTURE.md       ← Full architecture docs
├── data/options_cache.db           ← ~905 MB source of truth (real Polygon data)
├── data/macro_state.db             ← Regime/sector data (COMPASS)
└── backtest/historical_data.py     ← Raw DB queries (wrapped by IronVault)
>>>>>>> Stashed changes

configs/
├── champion.json              ← EXP-400 raw params
├── paper_champion.yaml        ← EXP-400 paper trading config
├── paper_exp036.yaml          ← Legacy experiment
├── paper_exp059.yaml          ← Legacy experiment
├── paper_exp154.yaml          ← Legacy experiment
└── paper_exp305.yaml          ← Legacy experiment

output/
├── regime_adaptive_validation.json  ← EXP-400 validation
├── portfolio_blend_results.json     ← EXP-401 Phase 3 results
├── regime_switching_results.json    ← EXP-401 Phase 4 results
├── final_validation_results.json    ← EXP-401 Phase 5 results
├── position_sizing_results.json     ← Phase 2 results
├── leaderboard.json                 ← All optimization runs
├── champion_report.html             ← EXP-400 formatted report
├── paper_trading_proposal.html      ← Deployment plan
├── exp031_audit_review.html         ← EXP-031 audit (REJECTED)
└── pr_review_alignment_patches.html ← PR review

scripts/
├── test_position_sizing.py    ← Phase 2 sizing experiments
├── portfolio_blend.py         ← Phase 3 blending
├── regime_switching.py        ← Phase 4 regime optimization
├── run_optimization.py        ← Original optimization harness
├── validate_signal_alignment.py  ← Op Unified Front: live vs backtester signal comparison
└── validate_exit_alignment.py    ← Op Unified Front: exit param pipeline validation
```

### GitHub
- **Repo:** `charlesattix/pilotai-credit-spreads`
- **Main branch:** Production code + alignment fixes
- **maximus/champion-config:** EXP-400 config + all Phase 0-5 results

### Operation Unified Front (Completed 2026-03-15)
Unified the entry and exit paths so the live paper trader uses the **exact same strategy classes** as the portfolio backtester. Eliminates signal drift between backtest and live.

**Phase 1 — Entry Path Unification:**
- Rewired `main.py._analyze_ticker()` to call `build_live_market_snapshot()` → `strategy.generate_signals()` → `score_signal()` → `reprice_signals_from_chain()` → `signal_to_opportunity()`
- Created bridge modules: `shared/snapshot_builder.py`, `shared/strategy_factory.py`, `shared/signal_scorer.py`
- Added straddle/strangle support to AlertSchema + AlertRouter
- Enhanced dedup key to include alert type (prevents IC/straddle collision)
- 26 tests in `tests/test_unified_entry.py`

**Phase 2 — Exit Path Unification:**
- Fixed BUG-A: per-trade `profit_target_pct`/`stop_loss_pct` now used (not global config)
- Added strategy dispatch in PositionMonitor: `trade_dict_to_position()` → `strategy.manage_position()`
- Added `CLOSE_DTE` enum + DTE management to all 3 strategies
- Added spread-width 90% safety cap to CS + IC
- Added straddle event-aware exit + 3x credit hard stop
- Fixed `trade_dict_to_position()` default mismatches
- 14 tests in `tests/test_unified_exit.py`

**Phase 5 — Validation:**
- `scripts/validate_signal_alignment.py`: backtester vs live scanner signal overlap (target ≥90%)
- `scripts/validate_exit_alignment.py`: per-trade exit params pipeline roundtrip (CS=1.25x, IC=2.5x, SS=0.5x+3x)
- All 1121 tests pass (40 new tests added)
- Branch: `maximus/unified-front`

### Safety Rails (Paper Trading)
- `paper_mode: true` — blocks live API URLs
- Kill switch via DB flag or Telegram
- 40% drawdown circuit breaker
- 40% portfolio heat cap
- Max 10 positions, max 2 per ticker
- Write-ahead logging for crash recovery
- Isolated DB per experiment

---

## 📏 RULES

1. **Every experiment gets an ID** — EXP-NNN format, registered in this file
2. **Never skip validation** — overfit score ≥0.70 to be considered ROBUST
3. **Always log before AND after** — hypothesis → results → leaderboard
4. **Regime detector is mandatory** — all directional strategies use combo regime mode
5. **Paper before live** — nothing touches real money without 8+ weeks paper validation
6. **Follow the data** — kill losers fast, double down on winners
7. **MASTERPLAN is sacred** — single source of truth, update with every instruction from Carlos

---

*Victory is not won by the sword alone — it is won by the plan behind it.* 🛡️
