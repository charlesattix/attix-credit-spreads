"""
Correlation breakdown analyzer for credit spread portfolios.

Detects contagion (correlation spikes during stress), computes conditional
correlation matrices by regime, calculates portfolio fragility scores,
tracks rolling conditional correlations, and quantifies diversification
benefits.  Generates an HTML report at reports/correlation_breakdown.html.

Usage::

    from compass.correlation_breakdown import CorrelationBreakdownAnalyzer
    analyzer = CorrelationBreakdownAnalyzer(returns_df, regimes=regime_series)
    results = analyzer.analyze()
    analyzer.generate_report("reports/correlation_breakdown.html")
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "reports" / "correlation_breakdown.html"

REGIMES = ("bull", "bear", "high_vol", "neutral")


# ── Data classes ────────────────────────────────────────────────────────


@dataclass
class ContagionEvent:
    """A detected correlation spike indicative of contagion."""
    date: str
    pair: Tuple[str, str]
    baseline_corr: float
    stress_corr: float
    delta: float
    regime: str


@dataclass
class RegimeCorrelation:
    """Conditional correlation matrix for a single regime."""
    regime: str
    matrix: np.ndarray
    mean_corr: float
    max_corr: float
    n_obs: int
    assets: List[str]


@dataclass
class FragilityScore:
    """Portfolio fragility assessment."""
    score: float               # 0-100, higher = more fragile
    eigenvalue_ratio: float    # first eigenvalue / sum
    mean_stress_corr: float
    mean_calm_corr: float
    contagion_count: int
    diversification_ratio: float


@dataclass
class RollingCorrelation:
    """Rolling conditional correlation for a single asset pair."""
    pair: Tuple[str, str]
    dates: List[str]
    values: List[float]
    regime_labels: List[str]


@dataclass
class DiversificationBenefit:
    """Quantified diversification benefit."""
    portfolio_vol: float
    weighted_avg_vol: float
    benefit_ratio: float       # 1 - portfolio_vol / weighted_avg_vol
    marginal_contributions: Dict[str, float]


# ── Analyzer ────────────────────────────────────────────────────────────


class CorrelationBreakdownAnalyzer:
    """Full correlation breakdown analysis for a portfolio of return series."""

    def __init__(
        self,
        returns: pd.DataFrame,
        regimes: Optional[pd.Series] = None,
        window: int = 60,
        contagion_threshold: float = 0.3,
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        self.returns = returns.copy()
        self.assets = list(returns.columns)
        self.regimes = regimes if regimes is not None else pd.Series(
            "neutral", index=returns.index
        )
        self.window = window
        self.contagion_threshold = contagion_threshold
        # Equal-weight by default
        n = len(self.assets)
        self.weights = weights or {a: 1.0 / n for a in self.assets}

        # Results populated by analyze()
        self.contagion_events: List[ContagionEvent] = []
        self.regime_correlations: Dict[str, RegimeCorrelation] = {}
        self.fragility: Optional[FragilityScore] = None
        self.rolling_correlations: List[RollingCorrelation] = []
        self.diversification: Optional[DiversificationBenefit] = None

    # ── Class constructors ──────────────────────────────────────────────

    @classmethod
    def from_csv(
        cls,
        returns_path: str,
        regimes_path: Optional[str] = None,
        **kwargs: Any,
    ) -> "CorrelationBreakdownAnalyzer":
        """Load from CSV files."""
        ret = pd.read_csv(returns_path, index_col=0, parse_dates=True)
        reg = None
        if regimes_path:
            reg_df = pd.read_csv(regimes_path, index_col=0, parse_dates=True)
            reg = reg_df.iloc[:, 0]
        return cls(ret, regimes=reg, **kwargs)

    # ── Public API ──────────────────────────────────────────────────────

    def analyze(self) -> Dict[str, Any]:
        """Run full analysis and return results dict."""
        self.regime_correlations = self._conditional_correlations()
        self.contagion_events = self._detect_contagion()
        self.rolling_correlations = self._rolling_conditional_correlations()
        self.diversification = self._diversification_benefit()
        self.fragility = self._fragility_score()
        return {
            "contagion_events": self.contagion_events,
            "regime_correlations": self.regime_correlations,
            "fragility": self.fragility,
            "rolling_correlations": self.rolling_correlations,
            "diversification": self.diversification,
        }

    # ── Conditional correlation matrices ────────────────────────────────

    def _conditional_correlations(self) -> Dict[str, RegimeCorrelation]:
        """Compute correlation matrix for each regime."""
        results: Dict[str, RegimeCorrelation] = {}
        for regime in REGIMES:
            mask = self.regimes == regime
            subset = self.returns.loc[mask]
            if len(subset) < 3:
                continue
            corr = subset.corr().values
            np.fill_diagonal(corr, np.nan)
            results[regime] = RegimeCorrelation(
                regime=regime,
                matrix=subset.corr().values,
                mean_corr=float(np.nanmean(corr)),
                max_corr=float(np.nanmax(corr)),
                n_obs=len(subset),
                assets=self.assets,
            )
        return results

    # ── Contagion detection ─────────────────────────────────────────────

    def _detect_contagion(self) -> List[ContagionEvent]:
        """Detect correlation spikes: stress-regime corr >> calm-regime corr."""
        calm = self.regime_correlations.get("bull") or self.regime_correlations.get("neutral")
        stress = self.regime_correlations.get("bear") or self.regime_correlations.get("high_vol")
        if calm is None or stress is None:
            return []

        events: List[ContagionEvent] = []
        n = len(self.assets)
        for i in range(n):
            for j in range(i + 1, n):
                base_c = calm.matrix[i, j]
                stress_c = stress.matrix[i, j]
                delta = stress_c - base_c
                if delta >= self.contagion_threshold:
                    # Find peak date in stress regime
                    mask = (self.regimes == stress.regime)
                    stress_dates = self.returns.index[mask]
                    peak_date = str(stress_dates[-1]) if len(stress_dates) > 0 else ""
                    events.append(ContagionEvent(
                        date=peak_date,
                        pair=(self.assets[i], self.assets[j]),
                        baseline_corr=float(base_c),
                        stress_corr=float(stress_c),
                        delta=float(delta),
                        regime=stress.regime,
                    ))
        return sorted(events, key=lambda e: -e.delta)

    # ── Rolling conditional correlation ─────────────────────────────────

    def _rolling_conditional_correlations(self) -> List[RollingCorrelation]:
        """Compute rolling correlations for all asset pairs."""
        results: List[RollingCorrelation] = []
        n = len(self.assets)
        if len(self.returns) < self.window:
            return results

        for i in range(n):
            for j in range(i + 1, n):
                a, b = self.assets[i], self.assets[j]
                rolling = self.returns[[a, b]].rolling(self.window).corr()
                # Extract pair correlation from multi-index result
                pair_corr = rolling.unstack().iloc[:, 1].dropna()
                dates = [str(d) for d in pair_corr.index]
                vals = pair_corr.values.tolist()
                regime_labels = [
                    str(self.regimes.get(d, "neutral"))
                    for d in pair_corr.index
                ]
                results.append(RollingCorrelation(
                    pair=(a, b),
                    dates=dates,
                    values=vals,
                    regime_labels=regime_labels,
                ))
        return results

    # ── Diversification benefit ─────────────────────────────────────────

    def _diversification_benefit(self) -> DiversificationBenefit:
        """Quantify portfolio diversification benefit."""
        cov = self.returns.cov().values
        w = np.array([self.weights.get(a, 0) for a in self.assets])
        w = w / w.sum()  # normalize

        portfolio_var = float(w @ cov @ w)
        portfolio_vol = float(np.sqrt(max(portfolio_var, 0)))

        individual_vols = np.sqrt(np.diag(cov))
        weighted_avg_vol = float(np.dot(w, individual_vols))

        benefit_ratio = 1.0 - (portfolio_vol / weighted_avg_vol) if weighted_avg_vol > 0 else 0.0

        # Marginal risk contributions
        marginal: Dict[str, float] = {}
        if portfolio_vol > 0:
            mc = (cov @ w) / portfolio_vol
            for k, a in enumerate(self.assets):
                marginal[a] = float(w[k] * mc[k])
        else:
            marginal = {a: 0.0 for a in self.assets}

        return DiversificationBenefit(
            portfolio_vol=portfolio_vol,
            weighted_avg_vol=weighted_avg_vol,
            benefit_ratio=float(benefit_ratio),
            marginal_contributions=marginal,
        )

    # ── Fragility score ─────────────────────────────────────────────────

    def _fragility_score(self) -> FragilityScore:
        """Compute portfolio fragility score (0-100)."""
        corr_matrix = self.returns.corr().values
        eigenvalues = np.linalg.eigvalsh(corr_matrix)
        eigenvalues = np.sort(eigenvalues)[::-1]
        eig_ratio = float(eigenvalues[0] / eigenvalues.sum()) if eigenvalues.sum() > 0 else 0.0

        # Mean correlations by stress vs calm
        stress_rc = self.regime_correlations.get("bear") or self.regime_correlations.get("high_vol")
        calm_rc = self.regime_correlations.get("bull") or self.regime_correlations.get("neutral")
        mean_stress = stress_rc.mean_corr if stress_rc else 0.0
        mean_calm = calm_rc.mean_corr if calm_rc else 0.0

        contagion_count = len(self.contagion_events)
        div_ratio = self.diversification.benefit_ratio if self.diversification else 0.0

        # Composite score: weighted combination, clamped to [0, 100]
        raw = (
            25.0 * eig_ratio
            + 25.0 * max(mean_stress, 0)
            + 20.0 * (1.0 - div_ratio)
            + 15.0 * min(contagion_count / max(len(self.assets), 1), 1.0)
            + 15.0 * max(mean_stress - mean_calm, 0)
        )
        score = max(0.0, min(100.0, raw))

        return FragilityScore(
            score=score,
            eigenvalue_ratio=eig_ratio,
            mean_stress_corr=mean_stress,
            mean_calm_corr=mean_calm,
            contagion_count=contagion_count,
            diversification_ratio=div_ratio,
        )

    # ── Report generation ───────────────────────────────────────────────

    def generate_report(self, output: str = str(DEFAULT_OUTPUT)) -> str:
        """Generate HTML report. Runs analyze() if not yet run."""
        if self.fragility is None:
            self.analyze()

        charts = self._render_charts()
        html = self._build_html(charts)
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html)
        logger.info("Report written to %s", out)
        return str(out.resolve())

    # ── Charts ──────────────────────────────────────────────────────────

    @staticmethod
    def _fig_to_b64(fig) -> str:
        import matplotlib
        matplotlib.use("Agg")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        import matplotlib.pyplot as plt
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

    def _render_charts(self) -> Dict[str, str]:
        charts: Dict[str, str] = {}
        charts["regime_heatmap"] = self._chart_regime_heatmaps()
        charts["rolling"] = self._chart_rolling_correlation()
        charts["fragility"] = self._chart_fragility_gauge()
        charts["diversification"] = self._chart_diversification()
        return charts

    def _chart_regime_heatmaps(self) -> str:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        regimes = [r for r in REGIMES if r in self.regime_correlations]
        if not regimes:
            return ""
        ncols = len(regimes)
        fig, axes = plt.subplots(1, ncols, figsize=(4 * ncols, 3.5))
        if ncols == 1:
            axes = [axes]
        for ax, regime in zip(axes, regimes):
            rc = self.regime_correlations[regime]
            im = ax.imshow(rc.matrix, cmap="RdYlGn_r", vmin=-1, vmax=1, aspect="auto")
            ax.set_xticks(range(len(rc.assets)))
            ax.set_xticklabels(rc.assets, fontsize=7, rotation=45, ha="right")
            ax.set_yticks(range(len(rc.assets)))
            ax.set_yticklabels(rc.assets, fontsize=7)
            ax.set_title(f"{regime} (n={rc.n_obs})", fontsize=9)
        fig.colorbar(im, ax=axes, shrink=0.8)
        fig.suptitle("Conditional Correlation Matrices by Regime", fontsize=11)
        fig.tight_layout()
        return self._fig_to_b64(fig)

    def _chart_rolling_correlation(self) -> str:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if not self.rolling_correlations:
            return ""
        n_pairs = min(len(self.rolling_correlations), 6)
        fig, axes = plt.subplots(n_pairs, 1, figsize=(10, 2.5 * n_pairs), sharex=True)
        if n_pairs == 1:
            axes = [axes]
        regime_colors = {"bull": "#16a34a", "bear": "#dc2626", "high_vol": "#f59e0b", "neutral": "#64748b"}
        for ax, rc in zip(axes, self.rolling_correlations[:n_pairs]):
            vals = np.array(rc.values)
            xs = range(len(vals))
            ax.plot(xs, vals, color="#334155", lw=0.8, alpha=0.9)
            # Color background by regime
            for k in range(len(rc.regime_labels) - 1):
                color = regime_colors.get(rc.regime_labels[k], "#f8fafc")
                ax.axvspan(k, k + 1, alpha=0.08, color=color)
            ax.set_ylabel(f"{rc.pair[0]}/{rc.pair[1]}", fontsize=8)
            ax.set_ylim(-1, 1)
            ax.axhline(0, color="black", lw=0.3)
            ax.grid(True, alpha=0.2)
        fig.suptitle("Rolling Conditional Correlations", fontsize=11)
        fig.tight_layout()
        return self._fig_to_b64(fig)

    def _chart_fragility_gauge(self) -> str:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if self.fragility is None:
            return ""
        fig, ax = plt.subplots(figsize=(4, 3))
        score = self.fragility.score
        color = "#16a34a" if score < 33 else "#f59e0b" if score < 66 else "#dc2626"
        ax.barh(["Fragility"], [score], color=color, height=0.4, alpha=0.85)
        ax.set_xlim(0, 100)
        ax.set_xlabel("Fragility Score")
        ax.text(score + 2, 0, f"{score:.1f}", va="center", fontsize=12, fontweight="bold")
        ax.grid(True, axis="x", alpha=0.3)
        fig.tight_layout()
        return self._fig_to_b64(fig)

    def _chart_diversification(self) -> str:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if self.diversification is None:
            return ""
        mc = self.diversification.marginal_contributions
        assets = list(mc.keys())
        vals = [mc[a] for a in assets]
        colors = ["#16a34a" if v < np.mean(vals) else "#f59e0b" for v in vals]
        fig, ax = plt.subplots(figsize=(max(5, len(assets) * 1.2), 3.5))
        ax.bar(assets, vals, color=colors, alpha=0.85, edgecolor="white")
        ax.set_ylabel("Marginal Risk Contribution")
        ax.set_title("Risk Contribution by Asset", fontsize=11)
        ax.grid(True, axis="y", alpha=0.3)
        plt.xticks(fontsize=8, rotation=45, ha="right")
        fig.tight_layout()
        return self._fig_to_b64(fig)

    # ── HTML builder ────────────────────────────────────────────────────

    def _build_html(self, charts: Dict[str, str]) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        frag = self.fragility or FragilityScore(0, 0, 0, 0, 0, 0)
        div = self.diversification or DiversificationBenefit(0, 0, 0, {})

        # Contagion table
        contagion_rows = ""
        for e in self.contagion_events:
            contagion_rows += (
                f'<tr><td>{e.pair[0]} / {e.pair[1]}</td>'
                f'<td>{e.baseline_corr:.3f}</td>'
                f'<td class="bad">{e.stress_corr:.3f}</td>'
                f'<td class="bad">+{e.delta:.3f}</td>'
                f'<td>{e.regime}</td></tr>\n'
            )
        if not contagion_rows:
            contagion_rows = '<tr><td colspan="5" style="text-align:center;color:#64748b">No contagion events detected</td></tr>'

        # Regime correlation summary table
        regime_rows = ""
        for regime in REGIMES:
            rc = self.regime_correlations.get(regime)
            if rc is None:
                continue
            cls = "bad" if rc.mean_corr > 0.5 else "good" if rc.mean_corr < 0.3 else ""
            regime_rows += (
                f'<tr><td>{rc.regime}</td><td>{rc.n_obs}</td>'
                f'<td class="{cls}">{rc.mean_corr:.3f}</td>'
                f'<td>{rc.max_corr:.3f}</td></tr>\n'
            )

        # Diversification marginal contribution table
        div_rows = ""
        if div.marginal_contributions:
            for a in sorted(div.marginal_contributions, key=lambda x: -div.marginal_contributions[x]):
                v = div.marginal_contributions[a]
                div_rows += f'<tr><td>{a}</td><td>{v:.4f}</td></tr>\n'

        frag_cls = "bad" if frag.score >= 66 else "good" if frag.score < 33 else ""

        def _img(key: str) -> str:
            b64 = charts.get(key, "")
            return f'<div class="chart"><img src="data:image/png;base64,{b64}" alt="{key}"></div>' if b64 else ""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Correlation Breakdown Analysis</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         margin: 0; padding: 2em 3em; background: #f8fafc; color: #1e293b; }}
  h1 {{ color: #0f172a; border-bottom: 2px solid #e2e8f0; padding-bottom: 0.4em; }}
  h2 {{ color: #334155; margin-top: 2em; }}
  .meta {{ color: #64748b; font-size: 0.9em; margin-bottom: 1.5em; }}
  .good {{ color: #16a34a; font-weight: 600; }}
  .bad {{ color: #dc2626; font-weight: 600; }}
  .kpi-row {{ display: flex; gap: 1.2em; flex-wrap: wrap; margin: 1.5em 0; }}
  .kpi {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
          padding: 1em 1.5em; min-width: 120px; flex: 1; text-align: center; }}
  .kpi .value {{ font-size: 1.5em; font-weight: 700; }}
  .kpi .label {{ font-size: 0.75em; color: #64748b; margin-top: 0.2em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.88em; }}
  th {{ background: #f1f5f9; padding: 8px 10px; text-align: left;
       border-bottom: 2px solid #cbd5e1; font-weight: 600; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #e2e8f0; text-align: right; }}
  td:first-child {{ text-align: left; }}
  .chart {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
            padding: 1em; margin: 1.5em 0; text-align: center; }}
  .chart img {{ max-width: 100%; height: auto; }}
  footer {{ margin-top: 3em; padding-top: 1em; border-top: 1px solid #e2e8f0;
            font-size: 0.8em; color: #94a3b8; }}
</style>
</head>
<body>

<h1>Correlation Breakdown Analysis</h1>
<div class="meta">{len(self.assets)} assets &middot; {len(self.returns)} observations &middot; Generated {now}</div>

<div class="kpi-row">
  <div class="kpi"><div class="value {frag_cls}">{frag.score:.1f}</div><div class="label">Fragility Score</div></div>
  <div class="kpi"><div class="value">{div.benefit_ratio:.1%}</div><div class="label">Diversification Benefit</div></div>
  <div class="kpi"><div class="value">{len(self.contagion_events)}</div><div class="label">Contagion Events</div></div>
  <div class="kpi"><div class="value">{frag.eigenvalue_ratio:.2f}</div><div class="label">Eigenvalue Concentration</div></div>
  <div class="kpi"><div class="value">{div.portfolio_vol:.4f}</div><div class="label">Portfolio Vol</div></div>
</div>

<h2>1. Contagion Detection</h2>
<p>Asset pairs where stress-regime correlation exceeds calm-regime by &ge; {self.contagion_threshold:.1%}.</p>
<table>
<thead><tr><th>Pair</th><th>Baseline Corr</th><th>Stress Corr</th><th>Delta</th><th>Stress Regime</th></tr></thead>
<tbody>{contagion_rows}</tbody>
</table>

<h2>2. Conditional Correlation by Regime</h2>
{_img("regime_heatmap")}
<table>
<thead><tr><th>Regime</th><th>Observations</th><th>Mean Corr</th><th>Max Corr</th></tr></thead>
<tbody>{regime_rows}</tbody>
</table>

<h2>3. Fragility Analysis</h2>
{_img("fragility")}
<table>
<thead><tr><th>Metric</th><th>Value</th></tr></thead>
<tbody>
<tr><td>Fragility Score</td><td class="{frag_cls}">{frag.score:.1f} / 100</td></tr>
<tr><td>Eigenvalue Concentration</td><td>{frag.eigenvalue_ratio:.3f}</td></tr>
<tr><td>Mean Stress Correlation</td><td>{frag.mean_stress_corr:.3f}</td></tr>
<tr><td>Mean Calm Correlation</td><td>{frag.mean_calm_corr:.3f}</td></tr>
<tr><td>Contagion Events</td><td>{frag.contagion_count}</td></tr>
<tr><td>Diversification Ratio</td><td>{frag.diversification_ratio:.3f}</td></tr>
</tbody>
</table>

<h2>4. Rolling Conditional Correlations</h2>
{_img("rolling")}

<h2>5. Diversification Benefit</h2>
{_img("diversification")}
<table>
<thead><tr><th>Metric</th><th>Value</th></tr></thead>
<tbody>
<tr><td>Portfolio Volatility</td><td>{div.portfolio_vol:.4f}</td></tr>
<tr><td>Weighted Avg Asset Vol</td><td>{div.weighted_avg_vol:.4f}</td></tr>
<tr><td>Benefit Ratio</td><td class="good">{div.benefit_ratio:.1%}</td></tr>
</tbody>
</table>
<h3>Marginal Risk Contributions</h3>
<table>
<thead><tr><th>Asset</th><th>Marginal Contribution</th></tr></thead>
<tbody>{div_rows}</tbody>
</table>

<footer>Generated by <code>compass/correlation_breakdown.py</code></footer>
</body></html>"""
        return html
