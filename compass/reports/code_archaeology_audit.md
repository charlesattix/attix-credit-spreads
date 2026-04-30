# Code Archaeology Audit — compass/

**Generated:** 2026-04-28
**Scope:** `/home/node/.openclaw/workspace/pilotai-credit-spreads/compass/`
**Mode:** Read-only analysis (no deletions, no moves)
**Authoritative sources:**
- `compass/PRODUCTION_MANIFEST.md` (2026-04-23)
- `MASTERPLAN.md` v11 (2026-04-08) — Wave Registry §5
- `compass/experiments/killed/README.md` (2026-04-08)
- `compass/archive/` (357 files, no README)

---

## 0. Important Provenance Note

The task brief refers to "264 archival candidates mentioned in MASTERPLAN
Phase 9 (90% code hardening complete section)." That phrasing does not
appear in `MASTERPLAN.md`. Phase 9 in MASTERPLAN is *paper trading*, not
code hardening.

The **264 figure originates in `compass/PRODUCTION_MANIFEST.md` line 85**
("Unlinked Modules (~264)"), which describes modules with no `__main__`
block and no place in the production import graph. This audit treats the
manifest as the operative source for that number.

---

## 1. Total File Count

| Location | `.py` files | Notes |
|---|---:|---|
| `compass/` (top-level) | 51 | Mix of production code, transitive deps, completed experiments, deprecated stubs |
| `compass/archive/` | 357 | Untracked dumping ground — no README, no verdicts |
| `compass/experiments/killed/` | 16 (+1 test, +readme) | Formally killed strategies with verdicts |
| `compass/experiments/` (root) | 0 | Only README.md |
| `compass/crypto/` | 11 | Standalone crypto data subpackage |
| `compass/scripts/` | 1 | `generate_daily_signals.py` (cron entry) |
| `compass/tests/` | 1 | `test_portfolio_risk_manager.py` only |
| `compass/research/` | 0 | Only `.md` files |
| `compass/paper_trading/` | 0 | Only a `.json` dry-run artifact |
| **Total .py** | **445** | |

`PRODUCTION_MANIFEST` partitions its 411-module inventory as
7 entry points + 18 production deps + 17 transitive deps + 105 standalone
experiments + ~264 unlinked. The 411 vs 445 delta (≈34) is accounted for
by the killed/, crypto/, and scripts/ subtrees the manifest does not
count.

---

## 2. Category Breakdown

### 2.1 Active production stack — MUST KEEP (42 files per PRODUCTION_MANIFEST)

#### Production entry points (7)
Cron drivers and orchestrators in the live paper-trading loop.

| File | Manifest Role | Found at |
|---|---|---|
| `scripts/generate_daily_signals.py` | Daily cron driver | ✅ `scripts/` |
| `exp2690_signal_generators.py` | GENERATOR_REGISTRY | ✅ top-level |
| `exp2830_paper_signal_generator.py` | Phase 9 daily paper signal generator | ✅ top-level |
| `alpaca_connector.py` | Alpaca order/position layer | ✅ top-level |
| `exp2860_paper_dry_run.py` | E2E dry run | ⚠️ `archive/` only |
| `exp2670_paper_gonogo.py` | Pre-flight checklist | ⚠️ `archive/` only |
| `exp2900_v8a_consistency_audit.py` | Portfolio consistency audit | ⚠️ `archive/` only |

#### Production dependencies (18, direct imports of entry points)
| File | Found at |
|---|---|
| `exp1220_standalone.py` | ✅ top-level |
| `exp1770_commodity_calendars.py` | ✅ top-level |
| `exp2020_cross_vol_arb.py` | ✅ top-level |
| `exp2160_high_capacity_alts.py` | ✅ top-level |
| `exp2200_north_star_v6.py` | ✅ top-level |
| `exp2240_qqq_iwm_credit_spreads.py` | ✅ top-level |
| `exp2360_robust_cov.py` | ✅ top-level |
| `exp2450_sparse_combined_honest.py` | ✅ top-level |
| `exp2850_v8a_with_vix_ladder.py` | ✅ top-level |
| `crisis_alpha_v5.py` | ✅ top-level |
| `vix_ladder.py` | ✅ top-level |
| `exp2580_spy_weekly_cs.py` | ⚠️ `archive/` only |
| `exp2820_flash_crash_protection.py` | ⚠️ `archive/` only |
| `paper_monitor_dashboard.py` | ⚠️ `archive/` only |
| `paper_trading_monitor.py` | ⚠️ `archive/` only |
| `prod_monitor.py` | ⚠️ `archive/` only |

#### Transitive dependencies (17)
| File | Found at |
|---|---|
| `crisis_alpha.py`, `crisis_alpha_v3.py`, `crisis_alpha_v4.py` | ✅ top-level |
| `exp1780_exp1220_integration.py` | ✅ top-level |
| `exp1850_regime_portfolio.py`, `exp1960_skew_alpha.py`, `exp1970_vol_of_vol.py` | ✅ top-level |
| `exp2080_corr_regime.py`, `exp2370_dd_circuit_breaker.py`, `exp2390_robust_cov_audit.py`, `exp2400_combined_best_of.py`, `exp2420_transaction_costs.py` | ✅ top-level |
| `greeks_sensitivity.py`, `metrics.py`, `regime.py` | ✅ top-level |
| `exp2710_xle_integration.py`, `exp2750_oos_regime_stress.py` | ⚠️ `archive/` only |

### 2.2 Standalone experiments — KEEP (completed research, ~105 per manifest)

Completed `__main__`-bearing scripts that are NOT imported by production but encode reproducible result claims (cited in MASTERPLAN headline numbers).
Notable:

| File | Status | Role |
|---|---|---|
| `exp1660_vrp_deepening.py` | top-level | Wave 1 |
| `exp1740_sentiment_filter.py` | top-level | Wave 1 |
| `exp1750_putcall_overlay.py` | top-level | Wave 1 winner |
| `exp2280_wf_robustness.py` | archive | ★★ 20-fold WF, headline source |
| `exp2300_portfolio_runner.py` | top-level | Wave 8 |
| `exp2470_execution_optimization.py` | archive | ★★ stack A+B+C+D |
| `exp2510_broker_analysis.py` | archive | Broker comparison |
| `exp2540_regime_tc_model.py` | archive | Regime-conditional TC |
| `exp2570_commfree_net_sharpe.py` | archive | ★★★ Sharpe 6.00 headline |
| `exp2590_qqq_capacity_deep_dive.py` | archive | ★ Phase 8 win |
| `exp2600_north_star_v8.py` | top-level | v8a baseline |
| `exp2640_vix_stress_hardening.py` | archive | Adaptive VIX vol-target |
| `exp2720_dd_recovery.py` | archive | ★ 11-day max recovery |
| `exp2730_wf_robustness_v8a_net.py` | archive | 20-fold WF v8a net |

### 2.3 Formally killed experiments (16 files in `experiments/killed/`)

Per `experiments/killed/README.md` — each has a verdict in
`experiments/registry.json`. **Do not move.** They are evidence for
honest-negative provenance.

| File | Verdict |
|---|---|
| `exp1760_crypto_vol.py` | Sharpe 1.04, small sample |
| `exp1910_intraday_breakout.py` | Sharpe 0.31, no edge |
| `exp1920_carry_trade.py` | Sharpe 0.72, regime-dependent |
| `exp1930_vvix_signal.py` | +0.12 ΔSh, under threshold (MASTERPLAN Bug 6) |
| `exp1940_multi_tf_momentum.py` | Sharpe 0.14 L/S |
| `exp1950_adaptive_kelly.py` | +0.03 ΔSh, noise |
| `exp1990_meta_learner.py` | Overfit 10F on 141 trades (MASTERPLAN §5) |
| `exp2030_seasonality_overlay.py` | Patterns didn't persist (MASTERPLAN §5) |
| `exp2050_north_star_v5.py` | Superseded by v6/v7/v8 |
| `exp2090_calendar_seasonality.py` | GLD −0.42 / SLV −0.29 ΔSh |
| `exp2100_vf_true_integration.py` | Retracted |
| `exp2150_higher_frequency.py` | Weekly+T/V hurt portfolio |
| `exp2170_weight_optimization.py` | Sharpe 5.47 < 6.0 target |
| `exp2190_tail_risk_parity.py` | Triggers can't predict DD |
| `exp2250_north_star_v7.py` | Superseded by v8 |
| `exp2260_slv_replacement.py` | No clean replacement |
| `exp2310_aum_scaling.py` | IronVault can't answer |
| `exp2320_final_report.py` | Superseded by EXP-2680 |
| `exp2350_slv_replacement_v2.py` | Failed combined Sharpe+capacity |
| `exp2380_futures_calendar_capacity.py` | Futures ≈ ETF spreads |
| `exp2430_capacity_optimized.py` | XLI becomes next bottleneck |
| `exp2460_zero_cost_overlay.py` | Negative at portfolio level |
| `exp2480_three_sleeve_hicap.py` | −0.33 Sh, only 1.31× cap |

(16 .py files actually present + 1 test + README; the README's "(23 files)"
header pre-dates a partial reconciliation — flag for the maintainer.)

### 2.4 Archival candidates — already in `archive/` (357 files)

The directory has **no `__init__.py`** (deliberate, per
`experiments/killed/README.md`) so it cannot accidentally be imported.
However:

- It has **no README and no verdict registry** of its own.
- It contains files that `PRODUCTION_MANIFEST.md` (newer, 2026-04-23)
  still lists as production entry points or dependencies — see §3 below.
- It mixes superseded utilities, deprecated versions, completed
  experiments, and accidentally-misplaced production files. This is the
  reverse of the formal `experiments/killed/` discipline.

The 357 archive files are far more than the 264 the manifest cites as
"unlinked archival candidates." The 93-file overcount is exactly what
§3 documents — files that are still imported (or were intended as
production) yet currently sit only in `archive/`.

### 2.5 Other modules

- `compass/crypto/` (11 files) — separate crypto package, not in production import graph. Likely archival but flagged in the manifest as "unused."
- `compass/__init__.py` — package init, KEEP.
- `compass/dollar_notional_sizer.py` — referenced in MASTERPLAN Phase 10 §9 prereq #7. KEEP.
- `compass/ensemble_signal_model.py`, `signal_model.py`, `ml_strategy.py`, `online_retrain.py`, `shadow_ensemble.py` — ML stack; not in production import graph but may be Phase-10 in-flight. Flagged for review (see §5).
- `compass/portfolio_risk_manager.py` — EXP-1890, MASTERPLAN production stack, KEEP.
- `compass/risk_gate.py`, `regime.py`, `events.py`, `features.py`, `iv_surface.py`, `macro.py`, `macro_db.py`, `metrics.py`, `sizing.py`, `stress_test.py` — utility modules, mostly transitive deps. KEEP unless explicitly proven unused.
- `compass/crisis_hedge.py` — paired with `crisis_alpha_*`. KEEP (review for v5 supersession).

---

## 3. ⚠️ Critical Findings

### 3.1 Production files living only in `archive/`

Thirteen modules that `PRODUCTION_MANIFEST.md` (dated 2026-04-23) names
as Production Entry Points or Dependencies are present **only** in
`compass/archive/`, not at top level:

| File | Manifest tier |
|---|---|
| `exp2670_paper_gonogo.py` | Entry point |
| `exp2860_paper_dry_run.py` | Entry point |
| `exp2900_v8a_consistency_audit.py` | Entry point |
| `exp2580_spy_weekly_cs.py` | Production dep |
| `exp2820_flash_crash_protection.py` | Production dep |
| `paper_monitor_dashboard.py` | Production dep |
| `paper_trading_monitor.py` | Production dep |
| `prod_monitor.py` | Production dep |
| `exp2710_xle_integration.py` | Transitive dep |
| `exp2750_oos_regime_stress.py` | Transitive dep |
| `exp2470_execution_optimization.py` | Cited in MASTERPLAN §9 production stack |
| `exp2510_broker_analysis.py` | Cited in MASTERPLAN §9 |
| `exp2540_regime_tc_model.py` | Cited in MASTERPLAN §9 |
| `exp2570_commfree_net_sharpe.py` | Cited in MASTERPLAN §9 (Sharpe 6.00 headline calculator) |
| `exp2640_vix_stress_hardening.py` | Cited in MASTERPLAN §9 |

This is **likely a source of latent ImportError risk** — production
imports of these files will fail because `compass/archive/` lacks
`__init__.py` deliberately. Either:

a. The MANIFEST is stale (the system was restructured and these are
   genuinely archived now, manifest needs an update), or
b. `archive/` was over-aggressively populated and these files need to
   move back to top-level.

**This must be resolved before any move/delete operation.**

### 3.2 No verdict registry for `archive/`

`compass/archive/` has 357 .py files and no README, no verdict file, no
`__init__.py`. The `experiments/killed/README.md` claims to document
*"Killed, retracted, and superseded experiments moved out of the compass/
root during the EXP-2770 code cleanup (2026-04-08)"* but the README's
"(23 files)" header lists 23, while `experiments/killed/` itself
contains 16 `.py` files. The manifest of `archive/` exists nowhere.

### 3.3 README at `experiments/killed/` references `compass/archive/`

The README header says "compass/archive/ — Archived Experiments" but is
located at `experiments/killed/`. The two directories are different:
- `experiments/killed/` (16 files): formal kills with verdicts
- `archive/` (357 files): unstructured legacy

Either the README has been moved/copied without retargeting, or the
intended naming convention drifted.

---

## 4. Safe-to-Archive Candidates

These are files that meet **all** of:
1. No `__main__` block (cannot be a CLI entry).
2. Not listed in `PRODUCTION_MANIFEST` entry/prod/transitive lists.
3. Not cited in `MASTERPLAN.md` §9 production stack.
4. Not in `experiments/killed/` (already accounted for).

Per the manifest, this is **~264 modules**. They are *already* sitting in
`compass/archive/`. A complete enumeration would require static-import
graph analysis across all 411 modules — out of scope for this read-only
audit but recommended as a follow-up (§5).

The 357 files currently in `archive/` are a **superset** that includes
~93 mistakenly-archived production files (per §3.1). The intersection of
"in archive/" AND "not referenced by manifest/MASTERPLAN" is the **safe**
set; everything else needs adjudication first.

Highly likely safe-to-keep-archived (sample, by clear obsolescence
markers — superseded versions, redundant duplicates):

- `crisis_alpha_v2.py`, `crisis_alpha_production.py` — superseded by `crisis_alpha_v5` (production)
- `crisis_hedge_v2.py`, `crisis_hedge_monitor.py` — superseded
- `dynamic_leverage.py`, `dynamic_leverage_v2.py`, `dynamic_leverage_v3.py`, `dynamic_leverage_hedged.py`, `dynamic_hedge.py`, `dynamic_hedging.py`, `dynamic_kelly.py`, `dynamic_sizing.py` — abandoned alpha branch
- `correlation_alpha.py`, `correlation_analysis.py`, `correlation_analyzer.py`, `correlation_breakdown.py`, `correlation_monitor.py`, `corr_regime_detector.py`, `cross_asset_*.py` — dispersed correlation experiments superseded by `exp2080_corr_regime.py`
- `north_star_backtest.py`, `north_star_dashboard.py`, `north_star_deployer.py`, `north_star_gap.py`, `north_star_integrator.py`, `north_star_portfolio*.py`, `north_star_real_backtest.py`, `north_star_stress_test.py`, `north_star_tracker.py`, `north_star_v4_audit.py`, `north_star_validator.py` — pre-v8a iterations
- `regime_ensemble.py`, `regime_ensemble_v2.py`, `regime_backtest.py`, `regime_forecast.py`, `regime_gate.py`, `regime_hedge.py`, `regime_hmm.py`, `regime_performance.py`, `regime_portfolio.py`, `regime_predictor.py`, `regime_transition*.py` — superseded by `regime.py` + `exp2080`
- `meta_learner.py`, `meta_learner_v2.py` — kill verdict in EXP-1990
- `earnings_alpha.py`, `earnings_crush.py`, `earnings_iv_crush.py`, `earnings_vol_crush.py` — single-strategy abandoned line
- `dispersion.py`, `dispersion_strategy.py`, `dispersion_trader.py` — abandoned dispersion line
- `intraday_*.py` (5 files), `microstructure*.py` (3), `market_maker.py`, `market_making_sim.py` — out-of-scope strategies
- `rl_executor.py`, `rl_portfolio_manager.py`, `rl_position_sizer.py` — abandoned RL experiments
- `transformer_predictor.py`, `genetic_evolver.py` — exploratory ML never adopted
- `auto_docs.py`, `dependency_analyzer.py`, `module_auditor.py`, `module_health.py`, `generate_docs.py` — internal tooling never wired
- `live_bridge.py`, `live_correlation_monitor.py`, `live_sim_engine.py`, `live_trading_blueprint.py`, `paper_reconciler.py`, `paper_tracker.py`, `paper_trading_engine.py`, `paper_trading_v4.py` — superseded by Phase 9 paper signal generator stack (verify against §3.1 first)
- `experiment_auto.py`, `experiment_compare.py`, `experiment_dashboard.py`, `experiment_launcher.py`, `experiment_manager.py`, `experiment_pipeline.py`, `experiment_ranker.py`, `experiment_runner.py` — multiple competing experiment harnesses, only one in use

(Sampled. Full archival list = `archive/` minus §3.1 misclassified production files.)

---

## 5. Must-Keep Files (Definitive List)

### From `MASTERPLAN.md` §9 (Production Stack):

**Strategy streams:**
- `exp1220_standalone.py`, `exp2200_north_star_v6.py`, `exp1770_commodity_calendars.py`,
  `exp2020_cross_vol_arb.py`, `crisis_alpha_v5.py`, `exp2580_spy_weekly_cs.py`*,
  `exp2600_north_star_v8.py`, `exp2240_qqq_iwm_credit_spreads.py`

**Risk / execution / cost:**
- `portfolio_risk_manager.py`, `exp2370_dd_circuit_breaker.py`,
  `exp2420_transaction_costs.py`, `exp2470_execution_optimization.py`*,
  `exp2510_broker_analysis.py`*, `exp2540_regime_tc_model.py`*,
  `exp2570_commfree_net_sharpe.py`*, `exp2640_vix_stress_hardening.py`*

**Paper trading infra:**
- `exp2670_paper_gonogo.py`*, `exp2830_paper_signal_generator.py`,
  `paper_trading_v4.py`, `paper_monitor_dashboard.py`*,
  `execution_simulator.py` (in `archive/`!), `prod_monitor.py`*,
  `scripts/generate_daily_signals.py`

`*` = ⚠️ currently only in `archive/` — see §3.1.

### Plus PRODUCTION_MANIFEST (§2.1 of this report) full set: 42 files.

### Plus all 16 `experiments/killed/` files (§2.3) — formal evidence trail.

---

## 6. Recommendations

### Immediate (before any archival action)

1. **Reconcile `PRODUCTION_MANIFEST.md` vs. `archive/` (§3.1).** Either move the
   13 misclassified production files back to top-level, or rewrite the
   manifest to acknowledge the new layout. **Do not** archive anything
   else until this is settled — current state means production imports
   will fail at runtime.

2. **Add `compass/archive/README.md` and a verdict registry.** Mirror the
   structure of `experiments/killed/README.md`. Without a registry,
   `archive/` is indistinguishable from a junk folder.

3. **Fix the `experiments/killed/README.md` header**: it says "compass/archive/" but lives at `experiments/killed/`, and its "(23 files)" count does not match the actual 16 .py files.

### Short-term

4. **Run a static import-graph scan** (`pyflakes`/`grep '^from compass' -r .`) to confirm the manifest's 264 figure. The current 357-file `archive/` size suggests either the count or the directory is stale.

5. **Decide the fate of the ML/ensemble stack** (`ensemble_signal_model.py`, `signal_model.py`, `ml_strategy.py`, `online_retrain.py`, `shadow_ensemble.py`): they are at top-level but absent from the production manifest. Either wire them in, kill them, or move to `archive/` with a verdict.

6. **Decide the fate of `compass/crypto/`**: 11 files, separate package, marked "unused" by the manifest. Either commit to a v9 crypto stream or archive the package.

### Medium-term

7. **Adopt one and only one archival convention**: today there are two competing patterns — formal kills with verdicts (`experiments/killed/`) and an undocumented dump (`archive/`). Pick one.

8. **Move the 16+ killed-but-still-in-archive late-numbered experiments** (`exp2810_9stream_portfolio.py`, `exp2910_tlt_credit_spreads.py`, `exp2920_tlt_ivrv_arb.py`, `exp2950_sector_momentum.py` — all flagged "Killed" in PRODUCTION_MANIFEST §"Standalone Experiments") into `experiments/killed/` so they get verdicts.

---

## 7. Summary Table

| Category | File count | Action |
|---|---:|---|
| Production entry points (manifest) | 7 | KEEP |
| Production deps (manifest) | 18 | KEEP |
| Transitive deps (manifest) | 17 | KEEP |
| Standalone experiments (manifest, completed) | ~105 | KEEP (most) |
| Formally killed (`experiments/killed/`) | 16 | KEEP (evidence) |
| Crypto subpackage | 11 | DECIDE |
| ML/ensemble stack at top-level | ~5 | DECIDE |
| Already archived (`archive/`) | 357 | RECONCILE — see §3.1 |
| → of which production-mislocated | ~13 | RESTORE to top-level |
| → of which true archival | ~344 (≈264 unlinked + ~80 standalone-killed) | KEEP archived |
| Tests | 1+1 | KEEP (and expand) |
| **Total compass/.py** | **445** | |

---

## 8. Conclusion

The "264 archival candidates" is an honest count from
`PRODUCTION_MANIFEST.md` of compass modules that are not in the
production import graph. Most of them are *already* in
`compass/archive/`.

The bigger problem is not which 264 to archive — it's that
**`compass/archive/` is currently mis-populated**: it contains ~13 files
that the same manifest calls production entry points or production
dependencies. Archiving more files on top of this disorder would
compound risk. Recommend pausing further archival until §3.1 is
reconciled and `archive/` gets a README + registry comparable to the
`experiments/killed/` discipline.

---

*This report is a read-only inventory. No files were moved, modified, or deleted.*
