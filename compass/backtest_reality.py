"""
Backtest reality checker — detect biases, unrealistic assumptions, and
over-fitting indicators in backtest results.

Look-ahead bias detection, survivorship bias, transaction cost realism,
fill assumption realism, capacity (trade size vs ADV), parameter
sensitivity (±10%), in-sample vs OOS degradation, free-params/data
ratio, complexity penalty, and composite credibility score (0-100).

Usage::

    from compass.backtest_reality import BacktestRealityChecker
    checker = BacktestRealityChecker(backtest_config)
    results = checker.check()
    checker.generate_report()
"""

from __future__ import annotations

import base64
import io
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "reports" / "backtest_reality.html"


# ── Data classes ────────────────────────────────────────────────────────


@dataclass
class BiasFlag:
    """A detected bias or unrealistic assumption."""
    name: str
    category: str           # "look_ahead", "survivorship", "cost", "fill",
                            # "capacity", "overfit", "complexity"
    severity: str           # "critical", "warning", "info"
    passed: bool
    score_penalty: float    # 0-30 points deducted
    detail: str


@dataclass
class SensitivityPoint:
    """Performance at one parameter perturbation."""
    param: str
    base_value: float
    perturbed_value: float
    base_sharpe: float
    perturbed_sharpe: float
    change_pct: float       # % change in Sharpe


@dataclass
class DegradationResult:
    """In-sample vs out-of-sample comparison."""
    is_sharpe: float
    oos_sharpe: float
    degradation_ratio: float   # oos / is (1.0 = no degradation)
    is_return: float
    oos_return: float
    is_dd: float
    oos_dd: float
    passed: bool               # degradation < threshold


@dataclass
class CapacityCheck:
    """Trade size vs market capacity."""
    avg_trade_size: float      # contracts or dollars
    avg_daily_volume: float
    participation_rate: float  # trade_size / ADV
    max_participation: float
    passed: bool
    impact_estimate_bps: float


@dataclass
class ComplexityMetrics:
    """Model complexity indicators."""
    n_free_params: int
    n_data_points: int
    params_per_point: float    # free_params / data_points
    n_rules: int
    degrees_of_freedom: int
    complexity_score: float    # 0-100 (lower = simpler = better)


@dataclass
class CredibilityScore:
    """Composite backtest credibility."""
    score: float               # 0-100
    grade: str                 # A-F
    n_critical: int
    n_warnings: int
    n_passed: int
    top_issues: List[str]


@dataclass
class BacktestConfig:
    """Configuration describing the backtest to check."""
    # Returns
    daily_returns: np.ndarray
    # Split point for IS/OOS
    is_fraction: float = 0.7
    # Cost assumptions
    commission_per_trade: float = 0.65
    assumed_slippage_bps: float = 2.0
    realistic_slippage_bps: float = 5.0
    # Fill assumptions
    assumed_fill_rate: float = 1.0
    realistic_fill_rate: float = 0.85
    # Capacity
    avg_trade_contracts: float = 5.0
    avg_daily_volume: float = 5000.0
    max_participation: float = 0.02
    # Complexity
    n_free_params: int = 10
    n_rules: int = 5
    # Lookback used in signal generation
    lookback_days: int = 60
    # Data
    n_assets_traded: int = 1
    n_assets_universe: int = 500
    # Timestamps
    uses_close_price_for_signal: bool = False
    signal_generated_before_trade: bool = True
    # Parameters for sensitivity
    param_values: Optional[Dict[str, float]] = None
    param_sensitivity_fn: Optional[Callable] = None


# ── Checker ─────────────────────────────────────────────────────────────


class BacktestRealityChecker:
    """Check backtest for biases and unrealistic assumptions."""

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.returns = np.asarray(config.daily_returns, dtype=float)

        self.flags: List[BiasFlag] = []
        self.sensitivity: List[SensitivityPoint] = []
        self.degradation: Optional[DegradationResult] = None
        self.capacity: Optional[CapacityCheck] = None
        self.complexity: Optional[ComplexityMetrics] = None
        self.credibility: Optional[CredibilityScore] = None

    # ── Public API ──────────────────────────────────────────────────────

    def check(self) -> Dict[str, Any]:
        self.flags = []
        self._check_look_ahead()
        self._check_survivorship()
        self._check_transaction_costs()
        self._check_fill_realism()
        self.capacity = self._check_capacity()
        self.degradation = self._check_degradation()
        self.complexity = self._check_complexity()
        self.sensitivity = self._parameter_sensitivity()
        self._check_overfit_indicators()
        self.credibility = self._compute_credibility()
        return {
            "flags": self.flags,
            "sensitivity": self.sensitivity,
            "degradation": self.degradation,
            "capacity": self.capacity,
            "complexity": self.complexity,
            "credibility": self.credibility,
        }

    # ── Look-ahead bias ─────────────────────────────────────────────────

    def _check_look_ahead(self) -> None:
        c = self.config

        # Close-price signal generation
        if c.uses_close_price_for_signal:
            self.flags.append(BiasFlag(
                "close_price_signal", "look_ahead", "critical", False, 20,
                "Signal uses close price which is unavailable at decision time",
            ))
        else:
            self.flags.append(BiasFlag(
                "close_price_signal", "look_ahead", "info", True, 0,
                "Signal does not use close price for same-bar decisions",
            ))

        # Signal timing
        if not c.signal_generated_before_trade:
            self.flags.append(BiasFlag(
                "signal_timing", "look_ahead", "critical", False, 20,
                "Signal generated after trade execution — classic look-ahead",
            ))
        else:
            self.flags.append(BiasFlag(
                "signal_timing", "look_ahead", "info", True, 0,
                "Signal generated before trade execution",
            ))

        # Lookback sanity: if lookback > 50% of data, suspicious
        n = len(self.returns)
        if n > 0 and c.lookback_days > n * 0.5:
            self.flags.append(BiasFlag(
                "lookback_ratio", "look_ahead", "warning", False, 8,
                f"Lookback {c.lookback_days}d is >{n * 0.5:.0f}d (50% of data)",
            ))
        else:
            self.flags.append(BiasFlag(
                "lookback_ratio", "look_ahead", "info", True, 0,
                f"Lookback {c.lookback_days}d is reasonable for {n}d of data",
            ))

    # ── Survivorship bias ───────────────────────────────────────────────

    def _check_survivorship(self) -> None:
        c = self.config
        if c.n_assets_traded > 0 and c.n_assets_universe > 0:
            ratio = c.n_assets_traded / c.n_assets_universe
            if ratio < 0.1:
                self.flags.append(BiasFlag(
                    "survivorship_selection", "survivorship", "warning", False, 10,
                    f"Trading {c.n_assets_traded}/{c.n_assets_universe} assets "
                    f"({ratio:.0%}) — potential selection/survivorship bias",
                ))
            else:
                self.flags.append(BiasFlag(
                    "survivorship_selection", "survivorship", "info", True, 0,
                    f"Trading {c.n_assets_traded}/{c.n_assets_universe} assets — acceptable",
                ))

    # ── Transaction costs ───────────────────────────────────────────────

    def _check_transaction_costs(self) -> None:
        c = self.config
        if c.assumed_slippage_bps < c.realistic_slippage_bps * 0.5:
            self.flags.append(BiasFlag(
                "slippage_underestimate", "cost", "critical", False, 15,
                f"Assumed slippage {c.assumed_slippage_bps}bps < 50% of realistic "
                f"{c.realistic_slippage_bps}bps — costs understated",
            ))
        elif c.assumed_slippage_bps < c.realistic_slippage_bps:
            self.flags.append(BiasFlag(
                "slippage_underestimate", "cost", "warning", False, 5,
                f"Assumed slippage {c.assumed_slippage_bps}bps < realistic "
                f"{c.realistic_slippage_bps}bps",
            ))
        else:
            self.flags.append(BiasFlag(
                "slippage_realistic", "cost", "info", True, 0,
                f"Slippage assumption {c.assumed_slippage_bps}bps is realistic",
            ))

        # Commission check
        if c.commission_per_trade <= 0:
            self.flags.append(BiasFlag(
                "zero_commission", "cost", "warning", False, 5,
                "Zero commissions assumed — unrealistic for production",
            ))
        else:
            self.flags.append(BiasFlag(
                "commission_present", "cost", "info", True, 0,
                f"Commission ${c.commission_per_trade}/trade included",
            ))

    # ── Fill realism ────────────────────────────────────────────────────

    def _check_fill_realism(self) -> None:
        c = self.config
        if c.assumed_fill_rate > 0.99:
            self.flags.append(BiasFlag(
                "perfect_fills", "fill", "warning", False, 8,
                f"Assumed {c.assumed_fill_rate:.0%} fill rate — "
                f"realistic is ~{c.realistic_fill_rate:.0%}",
            ))
        else:
            self.flags.append(BiasFlag(
                "fill_rate_ok", "fill", "info", True, 0,
                f"Fill rate {c.assumed_fill_rate:.0%} is reasonable",
            ))

        # Impact of fill rate on returns
        if c.assumed_fill_rate > c.realistic_fill_rate:
            miss_pct = c.assumed_fill_rate - c.realistic_fill_rate
            self.flags.append(BiasFlag(
                "fill_rate_gap", "fill", "warning", False, 5,
                f"{miss_pct:.0%} of trades may not fill in production",
            ))

    # ── Capacity ────────────────────────────────────────────────────────

    def _check_capacity(self) -> CapacityCheck:
        c = self.config
        participation = c.avg_trade_contracts / c.avg_daily_volume if c.avg_daily_volume > 0 else 1.0
        impact = participation * 100  # rough bps
        passed = participation <= c.max_participation

        if not passed:
            self.flags.append(BiasFlag(
                "capacity_exceeded", "capacity", "critical", False, 12,
                f"Participation {participation:.1%} > limit {c.max_participation:.1%}",
            ))
        else:
            self.flags.append(BiasFlag(
                "capacity_ok", "capacity", "info", True, 0,
                f"Participation {participation:.1%} within limit",
            ))

        return CapacityCheck(
            c.avg_trade_contracts, c.avg_daily_volume,
            participation, c.max_participation, passed, impact,
        )

    # ── IS vs OOS degradation ───────────────────────────────────────────

    def _check_degradation(self) -> DegradationResult:
        n = len(self.returns)
        split = int(n * self.config.is_fraction)
        if split < 10 or n - split < 10:
            return DegradationResult(0, 0, 1.0, 0, 0, 0, 0, True)

        is_ret = self.returns[:split]
        oos_ret = self.returns[split:]

        is_sh = self._sharpe(is_ret)
        oos_sh = self._sharpe(oos_ret)
        ratio = oos_sh / is_sh if abs(is_sh) > 0.01 else 1.0

        is_eq = np.cumprod(1 + is_ret)
        oos_eq = np.cumprod(1 + oos_ret)
        is_dd = self._max_dd(is_eq)
        oos_dd = self._max_dd(oos_eq)

        is_total = float(np.prod(1 + is_ret) - 1)
        oos_total = float(np.prod(1 + oos_ret) - 1)

        passed = ratio > 0.5  # OOS Sharpe >= 50% of IS

        if ratio < 0.3:
            self.flags.append(BiasFlag(
                "severe_degradation", "overfit", "critical", False, 20,
                f"OOS/IS Sharpe ratio {ratio:.2f} — severe overfitting",
            ))
        elif ratio < 0.5:
            self.flags.append(BiasFlag(
                "moderate_degradation", "overfit", "warning", False, 10,
                f"OOS/IS Sharpe ratio {ratio:.2f} — moderate overfitting",
            ))
        else:
            self.flags.append(BiasFlag(
                "degradation_ok", "overfit", "info", True, 0,
                f"OOS/IS Sharpe ratio {ratio:.2f} — acceptable",
            ))

        return DegradationResult(
            is_sh, oos_sh, ratio, is_total, oos_total, is_dd, oos_dd, passed,
        )

    # ── Complexity ──────────────────────────────────────────────────────

    def _check_complexity(self) -> ComplexityMetrics:
        c = self.config
        n = len(self.returns)
        ratio = c.n_free_params / max(n, 1)
        dof = max(n - c.n_free_params, 0)

        # Complexity score: higher = worse
        comp_score = min(100, ratio * 5000 + c.n_rules * 3)

        if ratio > 0.05:
            self.flags.append(BiasFlag(
                "params_data_ratio", "complexity", "critical", False, 15,
                f"Free params/data = {ratio:.3f} (>{0.05}) — high overfit risk",
            ))
        elif ratio > 0.02:
            self.flags.append(BiasFlag(
                "params_data_ratio", "complexity", "warning", False, 5,
                f"Free params/data = {ratio:.3f} — moderate complexity",
            ))
        else:
            self.flags.append(BiasFlag(
                "params_data_ratio", "complexity", "info", True, 0,
                f"Free params/data = {ratio:.4f} — acceptable",
            ))

        return ComplexityMetrics(
            c.n_free_params, n, ratio, c.n_rules, dof, comp_score,
        )

    # ── Parameter sensitivity ───────────────────────────────────────────

    def _parameter_sensitivity(self) -> List[SensitivityPoint]:
        points: List[SensitivityPoint] = []
        c = self.config

        if c.param_values and c.param_sensitivity_fn:
            for param, base_val in c.param_values.items():
                base_sh = self._sharpe(self.returns)
                for delta in [-0.1, 0.1]:
                    perturbed = base_val * (1 + delta)
                    try:
                        pert_returns = c.param_sensitivity_fn(param, perturbed)
                        pert_sh = self._sharpe(pert_returns)
                    except Exception:
                        pert_sh = base_sh
                    change = (pert_sh - base_sh) / abs(base_sh) if abs(base_sh) > 0.01 else 0
                    points.append(SensitivityPoint(
                        param, base_val, perturbed, base_sh, pert_sh, change,
                    ))
        else:
            # Default: sensitivity to return scaling
            base_sh = self._sharpe(self.returns)
            for name, mult in [("return_scale_-10%", 0.9), ("return_scale_+10%", 1.1),
                               ("vol_scale_-10%", 0.9), ("vol_scale_+10%", 1.1)]:
                if "return" in name:
                    adj = self.returns * mult
                else:
                    adj = self.returns + (self.returns - self.returns.mean()) * (mult - 1)
                pert_sh = self._sharpe(adj)
                change = (pert_sh - base_sh) / abs(base_sh) if abs(base_sh) > 0.01 else 0
                points.append(SensitivityPoint(
                    name, 1.0, mult, base_sh, pert_sh, change,
                ))

        # Flag if any ±10% perturbation kills > 50% of Sharpe
        max_degradation = max((abs(p.change_pct) for p in points), default=0)
        if max_degradation > 0.5:
            self.flags.append(BiasFlag(
                "parameter_fragile", "overfit", "warning", False, 10,
                f"±10% parameter change degrades Sharpe by {max_degradation:.0%}",
            ))
        else:
            self.flags.append(BiasFlag(
                "parameter_robust", "overfit", "info", True, 0,
                f"Max Sharpe sensitivity to ±10% is {max_degradation:.0%}",
            ))

        return points

    # ── Overfit indicators ──────────────────────────────────────────────

    def _check_overfit_indicators(self) -> None:
        n = len(self.returns)
        if n < 50:
            self.flags.append(BiasFlag(
                "insufficient_data", "overfit", "critical", False, 15,
                f"Only {n} data points — insufficient for reliable backtest",
            ))

        # Suspiciously high Sharpe
        sh = self._sharpe(self.returns)
        if sh > 4.0:
            self.flags.append(BiasFlag(
                "suspicious_sharpe", "overfit", "warning", False, 8,
                f"Sharpe {sh:.2f} > 4.0 — uncommon in production; verify not overfit",
            ))

        # Suspiciously smooth equity curve
        if n > 20:
            eq = np.cumprod(1 + self.returns)
            daily_changes = np.diff(eq)
            win_rate = float((daily_changes > 0).mean())
            if win_rate > 0.70:
                self.flags.append(BiasFlag(
                    "smooth_equity", "overfit", "warning", False, 5,
                    f"Daily win rate {win_rate:.0%} — suspiciously smooth",
                ))

    # ── Credibility score ───────────────────────────────────────────────

    def _compute_credibility(self) -> CredibilityScore:
        base = 100.0
        total_penalty = sum(f.score_penalty for f in self.flags if not f.passed)
        score = max(0, base - total_penalty)

        n_crit = sum(1 for f in self.flags if f.severity == "critical" and not f.passed)
        n_warn = sum(1 for f in self.flags if f.severity == "warning" and not f.passed)
        n_pass = sum(1 for f in self.flags if f.passed)

        if score >= 85:
            grade = "A"
        elif score >= 70:
            grade = "B"
        elif score >= 55:
            grade = "C"
        elif score >= 40:
            grade = "D"
        else:
            grade = "F"

        issues = [f.detail for f in self.flags if not f.passed]
        issues.sort(key=lambda x: -len(x))  # longest descriptions first

        return CredibilityScore(score, grade, n_crit, n_warn, n_pass, issues[:5])

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _sharpe(returns: np.ndarray) -> float:
        if len(returns) < 2 or np.std(returns) == 0:
            return 0.0
        return float(np.mean(returns) / np.std(returns) * np.sqrt(252))

    @staticmethod
    def _max_dd(equity: np.ndarray) -> float:
        if len(equity) < 2:
            return 0.0
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / np.where(peak > 0, peak, 1)
        return float(np.min(dd))

    # ── Report ──────────────────────────────────────────────────────────

    def generate_report(self, output: str = str(DEFAULT_OUTPUT)) -> str:
        if self.credibility is None:
            self.check()
        charts = self._render_charts()
        html = self._build_html(charts)
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html)
        return str(out.resolve())

    @staticmethod
    def _fig_to_b64(fig) -> str:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
        plt.close(fig); buf.seek(0)
        return base64.b64encode(buf.read()).decode("ascii")

    def _render_charts(self) -> Dict[str, str]:
        return {
            "tornado": self._chart_tornado(),
            "degradation": self._chart_degradation(),
            "flags": self._chart_flags(),
        }

    def _chart_tornado(self) -> str:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        if not self.sensitivity:
            return ""
        fig, ax = plt.subplots(figsize=(8, max(3, len(self.sensitivity) * 0.4)))
        names = [f"{s.param}" for s in self.sensitivity]
        changes = [s.change_pct * 100 for s in self.sensitivity]
        colors = ["#dc2626" if abs(c) > 30 else "#f59e0b" if abs(c) > 15 else "#16a34a" for c in changes]
        ax.barh(names, changes, color=colors, alpha=0.85)
        ax.axvline(0, color="black", lw=0.5)
        ax.set_xlabel("Sharpe Change (%)"); ax.set_title("Parameter Sensitivity Tornado", fontsize=11)
        ax.grid(True, axis="x", alpha=0.3); fig.tight_layout()
        return self._fig_to_b64(fig)

    def _chart_degradation(self) -> str:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        d = self.degradation
        if d is None:
            return ""
        fig, ax = plt.subplots(figsize=(5, 3))
        labels = ["In-Sample", "Out-of-Sample"]
        sharpes = [d.is_sharpe, d.oos_sharpe]
        colors = ["#3b82f6", "#f59e0b"]
        ax.bar(labels, sharpes, color=colors, alpha=0.85)
        ax.set_ylabel("Sharpe Ratio"); ax.set_title(f"IS vs OOS (ratio: {d.degradation_ratio:.2f})", fontsize=11)
        ax.grid(True, axis="y", alpha=0.3); fig.tight_layout()
        return self._fig_to_b64(fig)

    def _chart_flags(self) -> str:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        if not self.flags:
            return ""
        categories = sorted(set(f.category for f in self.flags))
        cat_scores = {}
        for cat in categories:
            cat_flags = [f for f in self.flags if f.category == cat]
            penalty = sum(f.score_penalty for f in cat_flags if not f.passed)
            cat_scores[cat] = penalty
        names = list(cat_scores.keys())
        vals = list(cat_scores.values())
        colors = ["#dc2626" if v > 10 else "#f59e0b" if v > 0 else "#16a34a" for v in vals]
        fig, ax = plt.subplots(figsize=(7, max(3, len(names) * 0.4)))
        ax.barh(names, vals, color=colors, alpha=0.85)
        ax.set_xlabel("Penalty Points"); ax.set_title("Bias Category Penalties", fontsize=11)
        ax.grid(True, axis="x", alpha=0.3); fig.tight_layout()
        return self._fig_to_b64(fig)

    def _build_html(self, charts: Dict[str, str]) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cr = self.credibility or CredibilityScore(0, "F", 0, 0, 0, [])
        d = self.degradation or DegradationResult(0, 0, 1, 0, 0, 0, 0, True)
        cap = self.capacity or CapacityCheck(0, 0, 0, 0, True, 0)
        cx = self.complexity or ComplexityMetrics(0, 0, 0, 0, 0, 0)

        grade_colors = {"A": "#16a34a", "B": "#22c55e", "C": "#f59e0b", "D": "#f97316", "F": "#dc2626"}
        gc = grade_colors.get(cr.grade, "#64748b")

        # Flags table
        flag_rows = ""
        for f in sorted(self.flags, key=lambda x: (x.passed, -x.score_penalty)):
            sev_cls = "bad" if f.severity == "critical" else "warn" if f.severity == "warning" else "good"
            status = "PASS" if f.passed else "FAIL"
            st_cls = "good" if f.passed else "bad"
            flag_rows += (f'<tr><td>{f.name}</td><td>{f.category}</td>'
                         f'<td class="{sev_cls}">{f.severity}</td>'
                         f'<td class="{st_cls}">{status}</td>'
                         f'<td>{f.score_penalty:.0f}</td>'
                         f'<td style="text-align:left">{f.detail}</td></tr>\n')

        # Sensitivity table
        sens_rows = ""
        for s in self.sensitivity:
            cls = "bad" if abs(s.change_pct) > 0.3 else ""
            sens_rows += (f'<tr><td>{s.param}</td><td>{s.base_value:.3f}</td>'
                         f'<td>{s.perturbed_value:.3f}</td>'
                         f'<td>{s.base_sharpe:.2f}</td><td>{s.perturbed_sharpe:.2f}</td>'
                         f'<td class="{cls}">{s.change_pct:+.1%}</td></tr>\n')

        issue_list = "".join(f"<li>{i}</li>" for i in cr.top_issues) or "<li>No issues</li>"

        def _img(k):
            b = charts.get(k, "")
            return f'<div class="chart"><img src="data:image/png;base64,{b}" alt="{k}"></div>' if b else ""

        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Backtest Reality Check</title>
<style>
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; margin:0; padding:2em 3em; background:#f8fafc; color:#1e293b; }}
  h1 {{ color:#0f172a; border-bottom:2px solid #e2e8f0; padding-bottom:0.4em; }} h2 {{ color:#334155; margin-top:2em; }}
  .meta {{ color:#64748b; font-size:0.9em; margin-bottom:1.5em; }}
  .good {{ color:#16a34a; font-weight:600; }} .bad {{ color:#dc2626; font-weight:600; }} .warn {{ color:#f59e0b; font-weight:600; }}
  .kpi-row {{ display:flex; gap:1.2em; flex-wrap:wrap; margin:1.5em 0; }}
  .kpi {{ background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:1em 1.5em; min-width:120px; flex:1; text-align:center; }}
  .kpi .value {{ font-size:1.5em; font-weight:700; }} .kpi .label {{ font-size:0.75em; color:#64748b; margin-top:0.2em; }}
  .risk-badge {{ display:inline-block; padding:0.3em 0.8em; border-radius:4px; color:white; font-weight:700; }}
  table {{ border-collapse:collapse; width:100%; margin:1em 0; font-size:0.85em; }}
  th {{ background:#f1f5f9; padding:8px 10px; text-align:left; border-bottom:2px solid #cbd5e1; font-weight:600; }}
  td {{ padding:6px 10px; border-bottom:1px solid #e2e8f0; text-align:right; }} td:first-child {{ text-align:left; }}
  .chart {{ background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:1em; margin:1.5em 0; text-align:center; }}
  .chart img {{ max-width:100%; height:auto; }}
  ul {{ margin:0.5em 0; padding-left:1.5em; }}
  footer {{ margin-top:3em; padding-top:1em; border-top:1px solid #e2e8f0; font-size:0.8em; color:#94a3b8; }}
</style></head><body>
<h1>Backtest Reality Check</h1>
<div class="meta">{len(self.returns)} daily returns &middot; {len(self.flags)} checks &middot; Generated {now}</div>
<div class="kpi-row">
  <div class="kpi"><div class="value"><span class="risk-badge" style="background:{gc}">{cr.grade}</span></div><div class="label">Grade</div></div>
  <div class="kpi"><div class="value">{cr.score:.0f}</div><div class="label">Credibility Score</div></div>
  <div class="kpi"><div class="value bad">{cr.n_critical}</div><div class="label">Critical</div></div>
  <div class="kpi"><div class="value warn">{cr.n_warnings}</div><div class="label">Warnings</div></div>
  <div class="kpi"><div class="value good">{cr.n_passed}</div><div class="label">Passed</div></div>
  <div class="kpi"><div class="value">{d.degradation_ratio:.2f}</div><div class="label">OOS/IS Ratio</div></div>
</div>
<h2>1. Top Issues</h2><ul>{issue_list}</ul>
<h2>2. Bias Flags</h2>{_img("flags")}
<table><thead><tr><th>Check</th><th>Category</th><th>Severity</th><th>Status</th><th>Penalty</th><th>Detail</th></tr></thead>
<tbody>{flag_rows}</tbody></table>
<h2>3. IS vs OOS Degradation</h2>{_img("degradation")}
<table><thead><tr><th>Metric</th><th>In-Sample</th><th>Out-of-Sample</th></tr></thead><tbody>
<tr><td>Sharpe</td><td>{d.is_sharpe:.2f}</td><td>{d.oos_sharpe:.2f}</td></tr>
<tr><td>Return</td><td>{d.is_return:+.1%}</td><td>{d.oos_return:+.1%}</td></tr>
<tr><td>Max DD</td><td>{d.is_dd:.1%}</td><td>{d.oos_dd:.1%}</td></tr>
<tr><td>Ratio</td><td colspan="2" class="{"good" if d.passed else "bad"}">{d.degradation_ratio:.2f}</td></tr>
</tbody></table>
<h2>4. Parameter Sensitivity</h2>{_img("tornado")}
<table><thead><tr><th>Parameter</th><th>Base</th><th>Perturbed</th><th>Base Sharpe</th><th>Pert Sharpe</th><th>Change</th></tr></thead>
<tbody>{sens_rows}</tbody></table>
<h2>5. Complexity</h2>
<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>
<tr><td>Free Parameters</td><td>{cx.n_free_params}</td></tr>
<tr><td>Data Points</td><td>{cx.n_data_points}</td></tr>
<tr><td>Params/Data Ratio</td><td>{cx.params_per_point:.4f}</td></tr>
<tr><td>Rules</td><td>{cx.n_rules}</td></tr>
<tr><td>Degrees of Freedom</td><td>{cx.degrees_of_freedom}</td></tr>
</tbody></table>
<h2>6. Capacity</h2>
<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>
<tr><td>Avg Trade Size</td><td>{cap.avg_trade_size:.0f}</td></tr>
<tr><td>Avg Daily Volume</td><td>{cap.avg_daily_volume:,.0f}</td></tr>
<tr><td>Participation Rate</td><td class="{"good" if cap.passed else "bad"}">{cap.participation_rate:.2%}</td></tr>
<tr><td>Est. Impact</td><td>{cap.impact_estimate_bps:.1f}bps</td></tr>
</tbody></table>
<footer>Generated by <code>compass/backtest_reality.py</code></footer>
</body></html>"""
        return html
