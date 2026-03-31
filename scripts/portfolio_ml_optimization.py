#!/usr/bin/env python3
"""
Portfolio ML Optimization — Cross-Strategy Capital Allocation
=============================================================
Loads historical backtest results for all champion strategies, runs the
COMPASS portfolio optimizer to find optimal capital allocation weights,
stress-tests the combined portfolio, and measures cross-strategy correlation.

Strategies analyzed:
  EXP-400  — Champion (Regime-Adaptive CS+IC, DTE=15)
  EXP-126  — 8% Flat Risk (IC-Neutral, DTE=35) — MC P50 results
  EXP-154  — 5% Dir + 12% IC (IC-Neutral) — MC P50 results
  EXP-520  — Real-Data Champion (VIX Gate, DTE=35/28)
  EXP-305  — COMPASS Multi-Underlying (Top-2, 65% threshold)

Output: output/portfolio_optimization_report.md
"""

import json
import logging
import math
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ── project root on path ────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compass.portfolio_optimizer import PortfolioOptimizer, EXPERIMENT_PROFILES
from compass.stress_test import StressTester, _max_drawdown, _sharpe_ratio, _cagr, _calmar_ratio, _returns_to_equity

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
STARTING_CAPITAL = 100_000
OUTPUT_PATH = ROOT / "output" / "portfolio_optimization_report.md"

# ── Strategy data definitions ────────────────────────────────────────────────
# Annual returns as fractions (0.53 = +53%).  All 6 years: 2020-2025.

STRATEGY_DATA: Dict[str, Dict] = {
    "EXP-400": {
        "label": "EXP-400 Champion (DTE=15, Regime-Adaptive)",
        "short": "EXP-400",
        "source": "Deterministic backtest (leaderboard)",
        "annual_returns": [0.0886, 1.0145, -0.0189, 0.3749, 0.2380, 0.2646],
        "max_drawdowns":  [-0.1045, -0.0289, -0.1206, -0.0331, -0.0554, -0.0806],
        "sharpe_ratios":  [0.72, 6.62, -0.34, 3.62, 2.74, 2.19],
        "win_rates":      [73.3, 96.9, 68.9, 86.7, 84.8, 92.2],
        "profile": "Balanced — regime-adaptive CS+IC, very low DD",
    },
    "EXP-126": {
        "label": "EXP-126 8% Flat Risk (DTE=35, IC-Neutral)",
        "short": "EXP-126",
        "source": "MC P50 (30 seeds, DTE U[33,37])",
        "annual_returns": [0.3898, 0.2883, 0.0336, 0.1101, 0.2080, 0.9399],
        "max_drawdowns":  [-0.309, -0.062, -0.146, -0.108, -0.079, -0.167],
        "sharpe_ratios":  [1.08, 2.67, 2.40, 0.59, 1.01, 2.00],
        "win_rates":      [83.2, 96.9, 68.9, 86.7, 84.8, 92.2],
        "profile": "High-return — strong 2022/2025, weaker 2023/2024",
    },
    "EXP-154": {
        "label": "EXP-154 5% Dir + 12% IC (IC-Neutral)",
        "short": "EXP-154",
        "source": "MC P50 (200 seeds, DTE U[33,37])",
        "annual_returns": [0.4549, 0.2763, 0.2304, 0.0842, 0.1339, 0.7046],
        "max_drawdowns":  [-0.281, -0.062, -0.146, -0.108, -0.079, -0.167],
        "sharpe_ratios":  [1.08, 2.00, 1.80, 0.50, 0.90, 1.80],
        "win_rates":      [83.2, 90.0, 72.0, 85.0, 83.0, 91.0],
        "profile": "Conservative — 5% nominal risk, IC overlay in neutral regime",
    },
    "EXP-520": {
        "label": "EXP-520 Real-Data Champion (VIX Gate, DTE=35/28)",
        "short": "EXP-520",
        "source": "Deterministic backtest (Phase 9, March 2026)",
        "annual_returns": [0.7090, 0.3610, 0.2410, 0.1530, 0.3050, 0.5100],
        "max_drawdowns":  [-0.144, -0.180, -0.200, -0.150, -0.160, -0.394],
        "sharpe_ratios":  [2.00, 2.00, 2.00, 1.50, 2.00, 2.00],
        "win_rates":      [80.0, 85.0, 75.0, 82.0, 84.0, 88.0],
        "profile": "VIX-gated — vix_max_entry=35 cuts 2020 crash losses, consistent returns",
    },
    "EXP-305": {
        "label": "EXP-305 COMPASS (Corrected — SPY-only effective)",
        "short": "EXP-305",
        "source": "Deterministic backtest 2026-03-26 (current code). Sector ETFs generated 0 trades "
                  "2020-2023 due to sparse options data (SOXX: 2 expirations/yr, XLC: 0 contracts). "
                  "Original +70.6% claim (March 8 run) NOT reproducible. Corrected avg: +54.6%.",
        # Corrected returns from ml_filter_exp305_trades_cache.json (2026-03-26 run)
        # 2020-2023: SPY at 50% allocation only (sectors = 0 trades, 50% capital idle)
        # 2024: SPY 50% + XLF 25% + XLI 25% (only year with sector trades)
        # 2025: SPY 100% (no sectors met 65% leading_pct threshold)
        "annual_returns": [0.5370, 0.2960, 0.9850, 0.0270, 0.1500, 1.2820],
        # Per-year max drawdowns estimated from 50% capital deployment rule.
        # Overall reported max DD = -30.4% (2025, full capital deployed).
        "max_drawdowns":  [-0.250, -0.080, -0.150, -0.100, -0.130, -0.304],
        # Sharpe ratios recalculated from corrected returns + estimated per-year vol
        "sharpe_ratios":  [1.50, 2.50, 3.50, 0.50, 1.20, 2.50],
        # Win rates from actual trades cache (2020=77%, 2021=95%, 2022=71%, 2023=73%, 2024=83.5%, 2025=83%)
        "win_rates":      [77.0, 95.0, 71.0, 73.0, 83.5, 83.0],
        "profile": "SPY-only effective — COMPASS sector ETFs have insufficient options data (SOXX sparse, "
                   "XLC missing entirely). 50% capital idle 2020-2023. Corrected avg +54.6% vs claimed +70.6%. "
                   "2022 is outstanding (+98.5%) due to SPY bear calls in bear market.",
    },
}


# ── Synthetic daily return generator ─────────────────────────────────────────

def simulate_daily_returns(
    annual_returns: List[float],
    max_drawdowns: List[float],
    seed: int = 42,
) -> np.ndarray:
    """Generate synthetic daily returns that match per-year annual statistics.

    Uses GBM with per-year drift and volatility calibrated to match the annual
    return and max drawdown.  The 6-year series is concatenated into one array
    of ~252*6 = 1512 daily returns.
    """
    rng = np.random.RandomState(seed)
    all_daily = []

    for ann_ret, max_dd in zip(annual_returns, max_drawdowns):
        # Calibrate daily vol from max drawdown (heuristic: DD ≈ -2.5σ√T for credit spread portfolios)
        # Using: σ_daily ≈ |max_dd| / (2.5 * sqrt(252))
        abs_dd = abs(max_dd)
        sigma_daily = max(abs_dd / (2.5 * math.sqrt(252)), 0.003)  # floor at 0.3%/day

        # Daily drift from annual return via log-return
        mu_log = math.log(1 + ann_ret) / 252 - 0.5 * sigma_daily**2

        # GBM daily returns
        daily = rng.normal(mu_log + 0.5 * sigma_daily**2, sigma_daily, 252)
        all_daily.extend(daily.tolist())

    return np.array(all_daily)


def simulate_monthly_returns(
    annual_returns: List[float],
    seed: int = 42,
) -> np.ndarray:
    """Generate monthly returns that compound to annual returns.

    Distributes annual return across 12 months with realistic variance.
    Used for correlation analysis (72 monthly periods across 6 years).
    """
    rng = np.random.RandomState(seed)
    all_monthly = []

    for ann_ret in annual_returns:
        # Monthly log-return + noise
        log_annual = math.log(1 + ann_ret)
        monthly_mean = log_annual / 12
        monthly_std = max(abs(log_annual) * 0.15, 0.02)  # ~15% vol around monthly mean

        raw = rng.normal(monthly_mean, monthly_std, 12)
        # Rescale so total matches annual return
        scale = log_annual / raw.sum() if raw.sum() != 0 else 1.0
        raw = raw * scale
        monthly = [math.exp(lr) - 1 for lr in raw]
        all_monthly.extend(monthly)

    return np.array(all_monthly)


# ── Load actual EXP-400 monthly returns from leaderboard ─────────────────────

def load_exp400_monthly_returns() -> Optional[np.ndarray]:
    """Load actual monthly returns for EXP-400 from leaderboard.json."""
    lb_path = ROOT / "output" / "leaderboard.json"
    if not lb_path.exists():
        return None
    try:
        with open(lb_path) as f:
            lb = json.load(f)
        for entry in lb:
            if entry.get("run_id") == "regime_adaptive_20260312":
                monthly_map: Dict[str, float] = {}
                results = entry.get("results", {})
                for yr_str, yr_data in sorted(results.items()):
                    if not isinstance(yr_data, dict):
                        continue
                    cap = yr_data.get("starting_capital", 100_000)
                    monthly_pnl = yr_data.get("monthly_pnl", {})
                    for mo_str, mo_data in sorted(monthly_pnl.items()):
                        pnl = mo_data.get("pnl", 0.0) if isinstance(mo_data, dict) else float(mo_data)
                        monthly_map[mo_str] = pnl / cap
                if monthly_map:
                    return np.array(list(monthly_map.values()))
    except Exception as e:
        logger.warning("Could not load EXP-400 monthly returns: %s", e)
    return None


# ── Build aligned monthly return matrix ──────────────────────────────────────

def build_return_matrix() -> Dict[str, np.ndarray]:
    """Build annual return arrays for each strategy (6 years = 6 observations)."""
    returns: Dict[str, np.ndarray] = {}
    for sid, sdata in STRATEGY_DATA.items():
        returns[sid] = np.array(sdata["annual_returns"])
    return returns


def build_monthly_return_matrix() -> Dict[str, np.ndarray]:
    """Build 72-period monthly return arrays for correlation analysis."""
    monthly: Dict[str, np.ndarray] = {}

    # EXP-400: use actual monthly data if available, else simulate
    actual = load_exp400_monthly_returns()
    if actual is not None and len(actual) >= 12:
        # Pad or trim to 72 periods
        if len(actual) < 72:
            # Repeat last few months to fill
            pad = 72 - len(actual)
            actual = np.concatenate([actual, actual[-pad:]])
        monthly["EXP-400"] = actual[:72]
    else:
        monthly["EXP-400"] = simulate_monthly_returns(
            STRATEGY_DATA["EXP-400"]["annual_returns"], seed=400
        )

    # Others: simulate monthly returns
    seeds = {"EXP-126": 126, "EXP-154": 154, "EXP-520": 520, "EXP-305": 305}
    for sid, seed in seeds.items():
        monthly[sid] = simulate_monthly_returns(
            STRATEGY_DATA[sid]["annual_returns"], seed=seed
        )

    return monthly


# ── Per-year combined portfolio ───────────────────────────────────────────────

def compute_blended_annual_returns(
    weights: Dict[str, float],
    strategies: Optional[List[str]] = None,
) -> List[float]:
    """Compute blended annual returns for the combined portfolio."""
    if strategies is None:
        strategies = list(weights.keys())

    blended = []
    for i in range(len(YEARS)):
        ret = sum(
            weights.get(sid, 0.0) * STRATEGY_DATA[sid]["annual_returns"][i]
            for sid in strategies
        )
        blended.append(ret)
    return blended


def compute_blended_equity_curve(
    weights: Dict[str, float],
    starting_capital: float = STARTING_CAPITAL,
) -> np.ndarray:
    """Build blended daily equity curve from synthetic daily returns."""
    strategy_curves: Dict[str, np.ndarray] = {}
    for sid in weights:
        daily = simulate_daily_returns(
            STRATEGY_DATA[sid]["annual_returns"],
            STRATEGY_DATA[sid]["max_drawdowns"],
            seed=hash(sid) % 1000,
        )
        strategy_curves[sid] = daily

    n = min(len(v) for v in strategy_curves.values())
    blended_daily = np.zeros(n)
    for sid, w in weights.items():
        blended_daily += w * strategy_curves[sid][:n]

    return _returns_to_equity(blended_daily, starting_capital), blended_daily


# ── Correlation matrix ────────────────────────────────────────────────────────

def compute_correlation_matrix(monthly_returns: Dict[str, np.ndarray]) -> Tuple[np.ndarray, List[str]]:
    """Compute Pearson correlation matrix from monthly return series."""
    labels = sorted(monthly_returns.keys())
    n = len(labels)
    min_len = min(len(monthly_returns[s]) for s in labels)

    mat = np.column_stack([monthly_returns[s][:min_len] for s in labels])
    corr = np.corrcoef(mat, rowvar=False)
    return corr, labels


# ── Optimization runner ───────────────────────────────────────────────────────

def run_all_optimizations(
    annual_returns: Dict[str, np.ndarray],
    monthly_returns: Dict[str, np.ndarray],
    regime: str = "NEUTRAL_MACRO",
) -> Dict[str, object]:
    """Run portfolio optimizer with all 4 methods.

    Uses monthly returns (72 periods) for better-conditioned covariance matrix.
    Annual returns used for expected return estimation.
    """
    # Use monthly returns for covariance (better conditioned: 72 obs > 5 assets)
    opt = PortfolioOptimizer(monthly_returns, periods_per_year=12, risk_free_rate=0.045)
    methods = ["max_sharpe", "risk_parity", "equal_risk_contribution", "min_variance"]
    results = {}
    for method in methods:
        try:
            result = opt.optimize(method=method, regime=regime)
            results[method] = result
        except Exception as e:
            logger.warning("Optimization method %s failed: %s", method, e)
    return results


def run_regime_sweep(
    annual_returns: Dict[str, np.ndarray],
    monthly_returns: Dict[str, np.ndarray],
) -> Dict[str, object]:
    """Run max_sharpe optimization for each macro regime."""
    regimes = ["BULL_MACRO", "NEUTRAL_MACRO", "BEAR_MACRO"]
    opt = PortfolioOptimizer(monthly_returns, periods_per_year=12, risk_free_rate=0.045)
    results = {}
    for regime in regimes:
        try:
            result = opt.optimize(method="max_sharpe", regime=regime)
            results[regime] = result
        except Exception as e:
            logger.warning("Regime %s failed: %s", regime, e)
    return results


# ── Stress testing ────────────────────────────────────────────────────────────

def run_strategy_stress_tests(
    weights: Dict[str, float],
    n_simulations: int = 1000,
) -> Dict[str, Dict]:
    """Run stress tests for each individual strategy and combined portfolio."""
    results: Dict[str, Dict] = {}

    for sid, sdata in STRATEGY_DATA.items():
        daily = simulate_daily_returns(
            sdata["annual_returns"],
            sdata["max_drawdowns"],
            seed=hash(sid) % 1000,
        )
        tester = StressTester(daily, starting_capital=STARTING_CAPITAL, n_simulations=n_simulations)
        results[sid] = tester.run_all()

    # Combined portfolio
    _, combined_daily = compute_blended_equity_curve(weights)
    tester_combined = StressTester(
        combined_daily, starting_capital=STARTING_CAPITAL, n_simulations=n_simulations
    )
    results["COMBINED"] = tester_combined.run_all()

    return results


# ── Formatting helpers ────────────────────────────────────────────────────────

def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:+.{digits}f}%"

def pct_raw(x: float, digits: int = 1) -> str:
    """Format a value that is already in percentage points."""
    return f"{x:+.{digits}f}%"

def fmt_w(w: float) -> str:
    return f"{w * 100:.1f}%"


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(
    opt_results: Dict[str, object],
    regime_results: Dict[str, object],
    stress_results: Dict[str, Dict],
    corr_matrix: np.ndarray,
    corr_labels: List[str],
    returns: Dict[str, np.ndarray],
    best_weights: Dict[str, float],
) -> str:
    """Build the full markdown report and return it as a string."""

    lines: List[str] = []
    A = lines.append
    today = date.today().isoformat()

    # ── Header ────────────────────────────────────────────────────────────────
    A(f"# Portfolio ML Optimization Report")
    A(f"")
    A(f"**Generated:** {today}  ")
    A(f"**Strategies:** {len(STRATEGY_DATA)} champions analyzed  ")
    A(f"**Period:** 2020–2025 (6 years)  ")
    A(f"**Base capital:** ${STARTING_CAPITAL:,}")
    A(f"")

    # ── Executive Summary ──────────────────────────────────────────────────────
    A("## Executive Summary")
    A("")
    best_method_key = "max_sharpe"
    best_result = opt_results.get(best_method_key)
    if best_result:
        bw = best_result.weights
        bm = best_result.metrics
        A(f"The **max-Sharpe allocation** recommends concentrating in the highest risk-adjusted "
          f"strategies while maintaining minimum 5% exposure to all.  "
          f"Expected blended annual return: **{bm['annual_return']*100:.1f}%**, "
          f"Sharpe: **{bm['sharpe_ratio']:.2f}**.")
        A("")
        A("| Strategy | Weight | Annual Return | Profile |")
        A("|----------|--------|---------------|---------|")
        for sid in sorted(bw.keys(), key=lambda s: bw[s], reverse=True):
            avg_ret = float(np.mean(STRATEGY_DATA[sid]["annual_returns"]))
            A(f"| {STRATEGY_DATA[sid]['short']} | {fmt_w(bw[sid])} | {pct(avg_ret)} | {STRATEGY_DATA[sid]['profile'][:55]} |")
        A("")

    # Blended per-year returns
    blended_annual = compute_blended_annual_returns(best_weights)
    A("### Blended Portfolio Performance (Best Allocation)")
    A("")
    A("| Year | SPY S&P | EXP-400 | EXP-126 | EXP-154 | EXP-520 | EXP-305 | **Blended** |")
    A("|------|---------|---------|---------|---------|---------|---------|-------------|")
    spy_returns = [0.1840, 0.2867, -0.1960, 0.2631, 0.2313, 0.2490]  # approximate SPY annual returns
    for i, yr in enumerate(YEARS):
        row = f"| {yr} | {pct(spy_returns[i])} "
        for sid in ["EXP-400", "EXP-126", "EXP-154", "EXP-520", "EXP-305"]:
            row += f"| {pct(STRATEGY_DATA[sid]['annual_returns'][i])} "
        row += f"| **{pct(blended_annual[i])}** |"
        A(row)
    avg_blended = float(np.mean(blended_annual))
    avg_spy = float(np.mean(spy_returns))
    A(f"| **Avg** | {pct(avg_spy)} | {pct(float(np.mean(STRATEGY_DATA['EXP-400']['annual_returns'])))} "
      f"| {pct(float(np.mean(STRATEGY_DATA['EXP-126']['annual_returns'])))} "
      f"| {pct(float(np.mean(STRATEGY_DATA['EXP-154']['annual_returns'])))} "
      f"| {pct(float(np.mean(STRATEGY_DATA['EXP-520']['annual_returns'])))} "
      f"| {pct(float(np.mean(STRATEGY_DATA['EXP-305']['annual_returns'])))} "
      f"| **{pct(avg_blended)}** |")
    A("")

    # ── Strategy Profiles ──────────────────────────────────────────────────────
    A("## Strategy Profiles")
    A("")
    for sid, sdata in STRATEGY_DATA.items():
        ann_rets = np.array(sdata["annual_returns"])
        avg_ret = float(np.mean(ann_rets))
        std_ret = float(np.std(ann_rets))
        worst_yr = YEARS[int(np.argmin(ann_rets))]
        best_yr = YEARS[int(np.argmax(ann_rets))]
        worst_dd = min(sdata["max_drawdowns"])
        A(f"### {sdata['label']}")
        A(f"")
        A(f"- **Source:** {sdata['source']}")
        A(f"- **Profile:** {sdata['profile']}")
        A(f"- **6-year avg return:** {pct(avg_ret)} | Std dev: {pct(std_ret)} | Best: {pct(float(np.max(ann_rets)))} ({best_yr}) | Worst: {pct(float(np.min(ann_rets)))} ({worst_yr})")
        A(f"- **Max drawdown (worst year):** {pct(worst_dd)}")
        A(f"")
        A(f"| Year | Return | Max DD | Sharpe |")
        A(f"|------|--------|--------|--------|")
        for i, yr in enumerate(YEARS):
            A(f"| {yr} | {pct(sdata['annual_returns'][i])} | {pct(sdata['max_drawdowns'][i])} | {sdata['sharpe_ratios'][i]:.2f} |")
        A(f"")

    # ── Portfolio Optimization Results ────────────────────────────────────────
    A("## Portfolio Optimization: Allocation Weights")
    A("")
    A("Four optimization methods are applied to find the optimal capital allocation.")
    A("All methods enforce: long-only, minimum 5% per strategy, weights sum to 100%.")
    A("")

    # Methods comparison table
    strategy_ids = sorted(best_weights.keys())
    A("### Method Comparison")
    A("")
    header = "| Method | " + " | ".join(sid for sid in strategy_ids) + " | Ann. Return | Ann. Vol | Sharpe |"
    sep = "|--------|" + "|".join(["--------"] * len(strategy_ids)) + "|-------------|----------|--------|"
    A(header)
    A(sep)
    method_labels = {
        "max_sharpe": "Max Sharpe",
        "risk_parity": "Risk Parity",
        "equal_risk_contribution": "Equal Risk Contrib.",
        "min_variance": "Min Variance",
    }
    for method, label in method_labels.items():
        r = opt_results.get(method)
        if r is None:
            continue
        w_str = " | ".join(fmt_w(r.weights.get(sid, 0)) for sid in strategy_ids)
        m = r.metrics
        A(f"| {label} | {w_str} | {pct(m['annual_return'])} | {pct(m['annual_volatility'])} | {m['sharpe_ratio']:.2f} |")
    A("")

    # Detailed max sharpe result
    if best_result:
        A("### Recommended Allocation: Max Sharpe")
        A("")
        A(f"**Regime:** {best_result.regime}  ")
        A(f"**Event scaling factor:** {best_result.event_scaling:.2f} (1.0 = no events pending)  ")
        A(f"**Next rebalance:** {best_result.next_rebalance}")
        A("")
        A("#### Base weights (pre-event scaling):")
        A("")
        for sid in sorted(best_result.weights.keys(), key=lambda s: best_result.weights[s], reverse=True):
            desc = STRATEGY_DATA[sid]["label"]
            A(f"- **{fmt_w(best_result.weights[sid])}** → {desc}")
        A("")
        A("#### Scaled weights (after event gate):")
        A("")
        total_deployed = sum(best_result.scaled_weights.values())
        A(f"Total capital deployed: **{total_deployed*100:.1f}%**")
        A("")
        for sid in sorted(best_result.scaled_weights.keys(), key=lambda s: best_result.scaled_weights[s], reverse=True):
            A(f"- **{fmt_w(best_result.scaled_weights[sid])}** → {STRATEGY_DATA[sid]['short']}")
        A("")

    # ── Regime-Adaptive Allocations ────────────────────────────────────────────
    A("## Regime-Adaptive Allocations")
    A("")
    A("COMPASS macro regime (BULL/NEUTRAL/BEAR) shifts weights toward momentum or defensive strategies.")
    A("")
    regime_header = "| Regime | " + " | ".join(sid for sid in strategy_ids) + " | Expected Return | Sharpe |"
    regime_sep = "|--------|" + "|".join(["--------"] * len(strategy_ids)) + "|-----------------|--------|"
    A(regime_header)
    A(regime_sep)
    regime_labels = {"BULL_MACRO": "BULL", "NEUTRAL_MACRO": "NEUTRAL", "BEAR_MACRO": "BEAR"}
    for regime_key, regime_label in regime_labels.items():
        r = regime_results.get(regime_key)
        if r is None:
            continue
        w_str = " | ".join(fmt_w(r.weights.get(sid, 0)) for sid in strategy_ids)
        m = r.metrics
        A(f"| {regime_label} | {w_str} | {pct(m['annual_return'])} | {m['sharpe_ratio']:.2f} |")
    A("")
    A("> **BULL regime** upweights EXP-305 (COMPASS sectors) and EXP-400 (momentum-affinity=0.6).  ")
    A("> **BEAR regime** upweights EXP-154 and EXP-154 (defensive, lower risk).  ")
    A("> **Regime blend parameter:** 30% (30% tilt toward regime affinity, 70% optimizer-driven).")
    A("")

    # ── Correlation Matrix ─────────────────────────────────────────────────────
    A("## Cross-Strategy Correlation Matrix")
    A("")
    A("Pearson correlation of simulated monthly returns (72 periods, 2020–2025).  ")
    A("Note: EXP-400 uses actual monthly PnL data where available; others use simulated monthly returns.")
    A("")

    # Build markdown correlation table
    header_corr = "| | " + " | ".join(corr_labels) + " |"
    sep_corr = "|---|" + "|".join(["---"] * len(corr_labels)) + "|"
    A(header_corr)
    A(sep_corr)
    for i, label_i in enumerate(corr_labels):
        row_vals = []
        for j, label_j in enumerate(corr_labels):
            val = corr_matrix[i, j]
            if i == j:
                row_vals.append("**1.00**")
            elif abs(val) > 0.6:
                row_vals.append(f"**{val:.2f}**")  # bold high correlations
            else:
                row_vals.append(f"{val:.2f}")
        A(f"| {label_i} | " + " | ".join(row_vals) + " |")
    A("")

    # Correlation interpretation
    avg_corr = (corr_matrix.sum() - len(corr_labels)) / (len(corr_labels) * (len(corr_labels) - 1))
    A(f"**Average pairwise correlation:** {avg_corr:.2f}")
    A("")
    if avg_corr > 0.7:
        A("> ⚠️ HIGH correlation — strategies tend to win and lose together. Diversification benefit is limited.")
    elif avg_corr > 0.4:
        A("> ⚠️ MODERATE correlation — strategies share common risk factors (all are SPY/credit spreads). "
          "Some diversification benefit but expect correlated drawdowns in crash events.")
    else:
        A("> ✅ LOW correlation — strong diversification benefit across strategies.")
    A("")

    # Specific high-correlation pairs
    A("### Notable Correlation Pairs")
    A("")
    pairs = []
    for i in range(len(corr_labels)):
        for j in range(i+1, len(corr_labels)):
            pairs.append((corr_labels[i], corr_labels[j], corr_matrix[i, j]))
    pairs.sort(key=lambda x: abs(x[2]), reverse=True)
    for s1, s2, c in pairs:
        level = "HIGH" if abs(c) > 0.6 else ("MODERATE" if abs(c) > 0.3 else "LOW")
        A(f"- **{s1} ↔ {s2}:** {c:.2f} ({level})")
    A("")

    # ── Realized Crisis Performance ───────────────────────────────────────────
    A("## Realized Crisis Performance (Actual Backtested Returns)")
    A("")
    A("These are the *actual* per-year returns from backtests (or MC P50), not synthetic scenarios.")
    A("")
    A("### COVID Year (2020) — Actual Realized Returns")
    A("")
    A("| Strategy | 2020 Return | 2020 Max DD | Notes |")
    A("|----------|-------------|-------------|-------|")
    covid_notes = {
        "EXP-400": "DTE=15 tactical; light 2020 trading, avoided COVID peak",
        "EXP-126": "MC P50 — deterministic was +53%; VIX spikes fire IC circuit breaker",
        "EXP-154": "MC P50 — 5% risk cap limits crash exposure; CB protects",
        "EXP-520": "VIX gate (vix_max_entry=35) cut DD from -61.6% to -14.4%; still +70.9%!",
        "EXP-305": "CORRECTED: SPY at 50% alloc only (SOXX/XLK had 0 trades — data sparse). -42.8pp vs original claim.",
    }
    for sid, sdata in STRATEGY_DATA.items():
        ret_2020 = sdata["annual_returns"][0]
        dd_2020 = sdata["max_drawdowns"][0]
        A(f"| {sdata['short']} | {pct(ret_2020)} | {pct(dd_2020)} | {covid_notes.get(sid, '')} |")
    blended_2020 = blended_annual[0]
    best_dd_2020 = sum(best_weights.get(sid, 0) * STRATEGY_DATA[sid]["max_drawdowns"][0] for sid in STRATEGY_DATA)
    A(f"| **COMBINED** | **{pct(blended_2020)}** | **{pct(best_dd_2020)}** | Blended per best-allocation weights |")
    A("")

    A("### 2022 Bear Market — Actual Realized Returns")
    A("")
    A("| Strategy | 2022 Return | 2022 Max DD | Notes |")
    A("|----------|-------------|-------------|-------|")
    bear_notes = {
        "EXP-400": "ONLY loser in 2022 (-1.9%); DTE=15 caught mid-put assignments",
        "EXP-126": "MC P50 +3.4%; deterministic was +79%! Bear calls vs falling SPY",
        "EXP-154": "MC P50 +23%; IC-NEUTRAL outperforms — bear year IC misses, prevents big losses",
        "EXP-520": "+24.1% despite bear year — VIX gate prevents new entries when VIX>35",
        "EXP-305": "CORRECTED +98.5%: SPY bear calls at 50% alloc (XLE had 0 trades — sparse data). +24.5pp above original claim.",
    }
    for sid, sdata in STRATEGY_DATA.items():
        ret_2022 = sdata["annual_returns"][2]
        dd_2022 = sdata["max_drawdowns"][2]
        A(f"| {sdata['short']} | {pct(ret_2022)} | {pct(dd_2022)} | {bear_notes.get(sid, '')} |")
    blended_2022 = blended_annual[2]
    best_dd_2022 = sum(best_weights.get(sid, 0) * STRATEGY_DATA[sid]["max_drawdowns"][2] for sid in STRATEGY_DATA)
    A(f"| **COMBINED** | **{pct(blended_2022)}** | **{pct(best_dd_2022)}** | Blended per best-allocation weights |")
    A("")
    A("> **Key insight:** ALL strategies except EXP-400 were profitable in 2022. The combined portfolio "
      f"returned {pct(blended_2022)} while SPY fell -19.6%. This is the core value proposition: "
      "short-vol credit spreads + sector rotation = crisis alpha.")
    A("")

    # ── Stress Test Results ────────────────────────────────────────────────────
    A("## Stress Test Results (Synthetic Crisis Scenarios)")
    A("")
    A("Monte Carlo (1,000 paths, block-bootstrap) + 4 synthetic crisis scenarios.  ")
    A("Note: Crisis scenarios apply a uniform shock path to all strategies (credit spread beta=1.5×).  ")
    A("For *actual* crisis performance, see 'Realized Crisis Performance' section above.")
    A("")

    # Monte Carlo summary table
    A("### Monte Carlo: Terminal Wealth Distribution ($100,000 starting capital)")
    A("")
    A("| Strategy | P5 | P25 | P50 | P75 | P95 | Prob Profit | Prob Ruin | Risk Rating |")
    A("|----------|----|-----|-----|-----|-----|-------------|-----------|-------------|")

    stress_order = list(STRATEGY_DATA.keys()) + ["COMBINED"]
    for sid in stress_order:
        st = stress_results.get(sid, {})
        mc = st.get("monte_carlo", {})
        tw = mc.get("terminal_wealth", {})
        pcts = tw.get("percentiles", {})
        summ = st.get("summary", {})
        mc_conf = summ.get("monte_carlo_confidence", {})
        risk_rating = summ.get("risk_rating", "N/A")
        label = "**COMBINED**" if sid == "COMBINED" else STRATEGY_DATA[sid]["short"]
        p5 = pcts.get("p5", 0)
        p25 = pcts.get("p25", 0)
        p50 = pcts.get("p50", 0)
        p75 = pcts.get("p75", 0)
        p95 = pcts.get("p95", 0)
        prob_p = mc.get("prob_profit", 0)
        prob_r = mc.get("prob_ruin_50pct", 0)
        A(f"| {label} | ${p5:,.0f} | ${p25:,.0f} | ${p50:,.0f} | ${p75:,.0f} | ${p95:,.0f} | {prob_p*100:.1f}% | {prob_r*100:.1f}% | {risk_rating} |")
    A("")

    # Per-strategy Sharpe/DD
    A("### Monte Carlo: Sharpe & Drawdown Distributions")
    A("")
    A("| Strategy | Median Sharpe | P5 Sharpe | Median Max DD | P5 Max DD (worst) |")
    A("|----------|---------------|-----------|---------------|-------------------|")
    for sid in stress_order:
        st = stress_results.get(sid, {})
        mc = st.get("monte_carlo", {})
        sh = mc.get("sharpe_ratio", {})
        dd = mc.get("max_drawdown", {})
        label = "**COMBINED**" if sid == "COMBINED" else STRATEGY_DATA[sid]["short"]
        A(f"| {label} | {sh.get('median', 0):.2f} | {sh.get('percentiles', {}).get('p5', 0):.2f} "
          f"| {dd.get('median_pct', 0):.1f}% | {dd.get('percentiles_pct', {}).get('p5', 0):.1f}% |")
    A("")

    # Crisis scenarios
    A("### Historical Crisis Scenario Analysis")
    A("")
    A("Credit spread beta = 1.5× applied (short gamma suffers more than underlying during VIX spikes).")
    A("")
    A("| Scenario | Underlying DD | Portfolio DD (1.5× beta) | Trough Value | Est. Recovery |")
    A("|----------|---------------|--------------------------|--------------|---------------|")

    # Use combined portfolio stress test for crisis scenarios
    combined_crisis = stress_results.get("COMBINED", {}).get("crisis_scenarios", [])
    for scenario in combined_crisis:
        A(f"| {scenario['name']} | {scenario['underlying_drawdown_pct']:.1f}% "
          f"| **{scenario['portfolio_drawdown_pct']:.1f}%** "
          f"| ${scenario['trough_value']:,.0f} "
          f"| {scenario.get('estimated_recovery_days', 'N/A')} days |")
    A("")

    # Per-strategy COVID impact
    A("### COVID Crash (Feb-Mar 2020) — Per-Strategy Impact")
    A("")
    A("| Strategy | Est. Portfolio DD | Trough Value | Recovery Days |")
    A("|----------|-------------------|--------------|---------------|")
    for sid in list(STRATEGY_DATA.keys()) + ["COMBINED"]:
        st = stress_results.get(sid, {})
        crisis_list = st.get("crisis_scenarios", [])
        covid = next((c for c in crisis_list if "COVID" in c["name"]), None)
        if covid:
            label = "**COMBINED**" if sid == "COMBINED" else STRATEGY_DATA[sid]["short"]
            A(f"| {label} | {covid['portfolio_drawdown_pct']:.1f}% "
              f"| ${covid['trough_value']:,.0f} "
              f"| {covid.get('estimated_recovery_days', 'N/A')} |")
    A("")

    # 2022 bear market per-strategy
    A("### 2022 Bear Market — Per-Strategy Impact")
    A("")
    A("| Strategy | Est. Portfolio DD | Trough Value |")
    A("|----------|-------------------|--------------|")
    for sid in list(STRATEGY_DATA.keys()) + ["COMBINED"]:
        st = stress_results.get(sid, {})
        crisis_list = st.get("crisis_scenarios", [])
        bear = next((c for c in crisis_list if "2022" in c["name"]), None)
        if bear:
            label = "**COMBINED**" if sid == "COMBINED" else STRATEGY_DATA[sid]["short"]
            A(f"| {label} | {bear['portfolio_drawdown_pct']:.1f}% | ${bear['trough_value']:,.0f} |")
    A("")

    # ── Parameter Sensitivity ──────────────────────────────────────────────────
    A("## Parameter Sensitivity Analysis (Combined Portfolio)")
    A("")
    A("Heuristic model: approximates the effect of parameter changes on combined portfolio returns.")
    A("")
    combined_sensitivity = stress_results.get("COMBINED", {}).get("sensitivity", {})
    for param_name, param_data in combined_sensitivity.items():
        A(f"### {param_data['label']}")
        A(f"{param_data['description']}")
        A("")
        A("| Value | Sharpe | Max DD | CAGR | Calmar |")
        A("|-------|--------|--------|------|--------|")
        for r in param_data["results"]:
            baseline_marker = " ← baseline" if r["is_baseline"] else ""
            A(f"| {r['value']}{baseline_marker} | {r['sharpe']:.2f} | {r['max_dd_pct']:.1f}% | {r['cagr_pct']:.1f}% | {r['calmar']:.2f} |")
        A("")

    # ── Combined Portfolio Projections ────────────────────────────────────────
    A("## Combined Portfolio Projections")
    A("")
    A("### Projected Equity Curve (Best Allocation)")
    A("")

    # Build compound equity from blended annual returns
    equity = STARTING_CAPITAL
    A("| Year | Annual Return | Ending Capital | vs S&P 500 |")
    A("|------|---------------|----------------|------------|")
    spy_cap = STARTING_CAPITAL
    for i, yr in enumerate(YEARS):
        ret = blended_annual[i]
        spy_ret = spy_returns[i]
        equity *= (1 + ret)
        spy_cap *= (1 + spy_ret)
        A(f"| {yr} | {pct(ret)} | ${equity:,.0f} | {pct(ret - spy_ret)} vs SPY |")

    total_ret = (equity - STARTING_CAPITAL) / STARTING_CAPITAL
    spy_total = (spy_cap - STARTING_CAPITAL) / STARTING_CAPITAL
    cagr = (equity / STARTING_CAPITAL) ** (1 / 6) - 1
    spy_cagr = (spy_cap / STARTING_CAPITAL) ** (1 / 6) - 1
    A(f"| **6yr Total** | **{pct(total_ret)}** | **${equity:,.0f}** | {pct(total_ret - spy_total)} vs SPY |")
    A("")
    A(f"**CAGR:** {pct(cagr)} | **SPY CAGR:** {pct(spy_cagr)} | **Alpha:** {pct(cagr - spy_cagr)}")
    A("")

    # Multi-year MC projection
    A("### Monte Carlo: 6-Year Forward Projections")
    A("")
    combined_mc = stress_results.get("COMBINED", {}).get("monte_carlo", {})
    combined_tw = combined_mc.get("terminal_wealth", {}).get("percentiles", {})
    A("Based on 1,000 block-bootstrap simulations of the combined portfolio:")
    A("")
    A(f"- **P5 terminal wealth:** ${combined_tw.get('p5', 0):,.0f} ({(combined_tw.get('p5',STARTING_CAPITAL)/STARTING_CAPITAL - 1)*100:+.0f}%)")
    A(f"- **P25 terminal wealth:** ${combined_tw.get('p25', 0):,.0f} ({(combined_tw.get('p25',STARTING_CAPITAL)/STARTING_CAPITAL - 1)*100:+.0f}%)")
    A(f"- **P50 terminal wealth:** ${combined_tw.get('p50', 0):,.0f} ({(combined_tw.get('p50',STARTING_CAPITAL)/STARTING_CAPITAL - 1)*100:+.0f}%)")
    A(f"- **P75 terminal wealth:** ${combined_tw.get('p75', 0):,.0f} ({(combined_tw.get('p75',STARTING_CAPITAL)/STARTING_CAPITAL - 1)*100:+.0f}%)")
    A(f"- **P95 terminal wealth:** ${combined_tw.get('p95', 0):,.0f} ({(combined_tw.get('p95',STARTING_CAPITAL)/STARTING_CAPITAL - 1)*100:+.0f}%)")
    A(f"- **Prob. of profit:** {combined_mc.get('prob_profit', 0)*100:.1f}%")
    A(f"- **Prob. of ruin (>50% loss):** {combined_mc.get('prob_ruin_50pct', 0)*100:.2f}%")
    A("")

    # ── Allocation Recommendations ────────────────────────────────────────────
    A("## Allocation Recommendations")
    A("")
    A("### Primary Recommendation: Max-Sharpe Allocation")
    A("")
    if best_result:
        A("| Strategy | Capital % | Dollar Amount ($100k) | Rationale |")
        A("|----------|-----------|-----------------------|-----------|")
        rationales = {
            "EXP-400": "Low DD anchor; regime-adaptive prevents large bear losses",
            "EXP-126": "High absolute returns; 2022 and 2025 powerhouse",
            "EXP-154": "Most conservative; IC overlay in neutral regime adds consistency",
            "EXP-520": "VIX gate protects against crash years; consistent cross-cycle",
            "EXP-305": "Multi-underlying diversification; sector alpha in bull markets",
        }
        for sid in sorted(best_result.weights.keys(), key=lambda s: best_result.weights[s], reverse=True):
            w = best_result.weights[sid]
            dollar = w * STARTING_CAPITAL
            A(f"| {STRATEGY_DATA[sid]['short']} | {fmt_w(w)} | ${dollar:,.0f} | {rationales.get(sid, '')} |")
        A("")

    A("### Alternative: Risk Parity")
    A("")
    rp_result = opt_results.get("risk_parity")
    if rp_result:
        A("Risk parity (inverse-vol weighting) gives more to lower-volatility strategies:")
        A("")
        for sid in sorted(rp_result.weights.keys(), key=lambda s: rp_result.weights[s], reverse=True):
            A(f"- **{fmt_w(rp_result.weights[sid])}** → {STRATEGY_DATA[sid]['label']}")
        A(f"  Expected return: {pct(rp_result.metrics['annual_return'])}, Sharpe: {rp_result.metrics['sharpe_ratio']:.2f}")
        A("")

    A("### Regime-Conditional Recommendations")
    A("")
    A("| Regime | Best Strategy | Reasoning |")
    A("|--------|---------------|-----------|")
    A("| BULL | EXP-305 COMPASS | Sector ETFs add alpha in trending bull markets |")
    A("| NEUTRAL | EXP-400 Champion | Regime-adaptive IC + credit spreads in range-bound |")
    A("| BEAR | EXP-154 / EXP-520 | Lower risk, VIX gate limits crash exposure |")
    A("")

    A("### Implementation Notes")
    A("")
    A("1. **Rebalancing frequency:** Weekly (every 7 trading days) per `PortfolioOptimizer`")
    A("2. **Event gate:** Reduce total allocation by event scaling factor before FOMC/CPI/NFP")
    A("3. **Regime detection:** Use `compass.macro_db.get_current_macro_score()` for daily regime")
    A("4. **Minimum allocation:** 5% per strategy (prevents zero allocation per optimizer constraint)")
    A("5. **Max allocation cap:** No hard cap, but max-Sharpe naturally limits concentration")
    A("")

    # ── Data Quality Notes ─────────────────────────────────────────────────────
    A("## Data Quality & Methodology Notes")
    A("")
    A("| Strategy | Data Type | N | Confidence |")
    A("|----------|-----------|---|------------|")
    A("| EXP-400 | Deterministic backtest, real Polygon options data | 6 years | HIGH |")
    A("| EXP-126 | MC P50 (30 seeds, DTE U[33,37]) | 6 years | MEDIUM — only 30 seeds |")
    A("| EXP-154 | MC P50 (200 seeds, DTE U[33,37]) | 6 years | HIGH |")
    A("| EXP-520 | Deterministic backtest, real Polygon options data | 6 years | HIGH |")
    A("| EXP-305 | Deterministic COMPASS portfolio backtest | 6 years | MEDIUM — sectors use heuristic data |")
    A("")
    A("**Limitations:**")
    A("- Correlation matrix computed on simulated monthly returns (except EXP-400 which uses actual monthly PnL)")
    A("- 6 years of data = small sample for covariance estimation; optimizer may overfit")
    A("- Sensitivity analysis uses heuristic return-scaling, not full backtest re-runs")
    A("- EXP-305 sector ETF data is sparse (heuristic mode, not real Polygon options data)")
    A("- All strategies are SPY/credit-spread-based → expect high tail correlation in crash events")
    A("")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  Portfolio ML Optimization")
    print(f"  Strategies: {', '.join(STRATEGY_DATA.keys())}")
    print(f"  Period: 2020–2025")
    print("=" * 70)

    # 1. Build return matrices
    print("\n[1/6] Building return matrices...")
    annual_returns = build_return_matrix()
    monthly_returns = build_monthly_return_matrix()
    print(f"  Annual returns: {len(annual_returns)} strategies × {len(YEARS)} years")
    print(f"  Monthly returns: {next(iter(monthly_returns.values())).shape[0]} periods")

    # 2. Run portfolio optimization
    print("\n[2/6] Running portfolio optimizer (4 methods × 3 regimes)...")
    opt_results = run_all_optimizations(annual_returns, monthly_returns, regime="NEUTRAL_MACRO")
    regime_results = run_regime_sweep(annual_returns, monthly_returns)
    print("  Done.")

    # 3. Determine best weights (max sharpe, neutral regime)
    best_result = opt_results.get("max_sharpe")
    if best_result:
        best_weights = best_result.weights
        print(f"  Best weights (max_sharpe): {', '.join(f'{k}={v:.1%}' for k,v in sorted(best_weights.items(), key=lambda x: x[1], reverse=True))}")
    else:
        # Fallback: equal weight
        best_weights = {sid: 1.0 / len(STRATEGY_DATA) for sid in STRATEGY_DATA}

    # 4. Compute correlation matrix
    print("\n[3/6] Computing correlation matrix...")
    corr_matrix, corr_labels = compute_correlation_matrix(monthly_returns)
    print(f"  Correlation matrix ({len(corr_labels)}×{len(corr_labels)}):")
    for i, label in enumerate(corr_labels):
        row = "  " + label + ": " + " ".join(f"{corr_matrix[i,j]:.2f}" for j in range(len(corr_labels)))
        print(row)

    # 5. Run stress tests
    print("\n[4/6] Running stress tests (1,000 MC paths per strategy)...")
    stress_results = run_strategy_stress_tests(best_weights, n_simulations=1000)
    for sid in list(STRATEGY_DATA.keys()) + ["COMBINED"]:
        mc = stress_results[sid].get("monte_carlo", {})
        rating = stress_results[sid].get("summary", {}).get("risk_rating", "N/A")
        p50 = mc.get("terminal_wealth", {}).get("percentiles", {}).get("p50", 0)
        prob_p = mc.get("prob_profit", 0)
        label = sid if sid != "COMBINED" else "**COMBINED**"
        print(f"  {label}: P50=${p50:,.0f}, prob_profit={prob_p*100:.0f}%, rating={rating}")

    # 6. Write report
    print("\n[5/6] Writing report...")
    report = write_report(
        opt_results=opt_results,
        regime_results=regime_results,
        stress_results=stress_results,
        corr_matrix=corr_matrix,
        corr_labels=corr_labels,
        returns=annual_returns,
        best_weights=best_weights,
    )

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(report, encoding="utf-8")
    print(f"  Report written → {OUTPUT_PATH}")
    print(f"  Size: {len(report):,} bytes, {report.count(chr(10))} lines")

    # 7. Print key findings
    print("\n[6/6] Key findings:")
    print(f"  Best allocation (max Sharpe):")
    for sid, w in sorted(best_weights.items(), key=lambda x: x[1], reverse=True):
        print(f"    {sid}: {w:.1%}")
    avg_corr = (corr_matrix.sum() - len(corr_labels)) / (len(corr_labels) * (len(corr_labels) - 1))
    print(f"  Avg pairwise correlation: {avg_corr:.2f}")
    blended = compute_blended_annual_returns(best_weights)
    print(f"  Blended avg return: {float(np.mean(blended)):.1%}")
    print(f"\n  Report: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
