"""
Strategy decay monitor — detects alpha decay and lifecycle phase transitions.

Components:
  - Rolling metrics:  Sharpe, hit-rate, avg P&L with configurable windows
  - CUSUM:            Cumulative-sum structural break detection
  - Lifecycle:        growth → maturity → decay → dead classification
  - Decay rate:       exponential half-life of alpha
  - Regime-conditioned decay:  per-regime Sharpe to see if decay is regime-specific
  - Kill signals:     composite score triggering strategy retirement

Lifecycle classification:
  GROWTH   — rising rolling Sharpe, positive alpha
  MATURITY — stable rolling Sharpe, alpha present but flat
  DECAY    — declining rolling Sharpe, alpha eroding
  DEAD     — negative rolling Sharpe sustained, alpha gone
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------

class LifecyclePhase(str, Enum):
    GROWTH = "growth"
    MATURITY = "maturity"
    DECAY = "decay"
    DEAD = "dead"


@dataclass
class RollingMetrics:
    """Point-in-time rolling performance snapshot."""
    date: datetime
    sharpe: float
    hit_rate: float
    avg_pnl: float
    cumulative_pnl: float


@dataclass
class CUSUMResult:
    """CUSUM structural break detection output."""
    break_detected: bool
    break_index: Optional[int] = None
    break_date: Optional[datetime] = None
    cusum_series: Optional[np.ndarray] = None
    threshold: float = 0.0
    max_cusum: float = 0.0


@dataclass
class DecayEstimate:
    """Exponential decay rate of alpha."""
    half_life_days: float
    decay_rate: float          # lambda in exp(-lambda * t)
    r_squared: float           # goodness of fit
    initial_alpha: float
    current_alpha: float


@dataclass
class RegimeDecay:
    """Decay characteristics within a specific regime."""
    regime: str
    n_days: int
    avg_sharpe: float
    sharpe_trend: float        # slope of rolling Sharpe within regime
    is_decaying: bool


@dataclass
class KillSignal:
    """Composite retirement recommendation."""
    strategy_name: str
    lifecycle: LifecyclePhase
    kill_score: float          # 0-1, higher = more urgent to retire
    reasons: List[str]
    confidence: float
    recommendation: str        # "keep", "monitor", "retire"


# ---------------------------------------------------------------------------
# Core monitor
# ---------------------------------------------------------------------------

class StrategyDecayMonitor:
    """Detects strategy alpha decay and manages lifecycle transitions.

    Args:
        rolling_window: Window for rolling Sharpe / metrics (days).
        cusum_threshold: Standard deviations for CUSUM break detection.
        decay_lookback: Days to estimate decay rate over.
        kill_sharpe_threshold: Rolling Sharpe below this contributes to kill score.
        kill_hit_rate_threshold: Hit rate below this contributes to kill score.
    """

    def __init__(
        self,
        rolling_window: int = 63,
        cusum_threshold: float = 3.0,
        decay_lookback: int = 126,
        kill_sharpe_threshold: float = 0.3,
        kill_hit_rate_threshold: float = 0.40,
    ) -> None:
        self.rolling_window = rolling_window
        self.cusum_threshold = cusum_threshold
        self.decay_lookback = decay_lookback
        self.kill_sharpe_threshold = kill_sharpe_threshold
        self.kill_hit_rate_threshold = kill_hit_rate_threshold

    # ------------------------------------------------------------------
    # Rolling metrics
    # ------------------------------------------------------------------

    def rolling_metrics(
        self, returns: pd.Series, window: Optional[int] = None,
    ) -> List[RollingMetrics]:
        """Compute rolling Sharpe, hit rate, avg P&L."""
        w = window or self.rolling_window
        if len(returns) < w:
            return []

        results: List[RollingMetrics] = []
        cum = returns.cumsum()
        for end in range(w, len(returns) + 1):
            chunk = returns.iloc[end - w: end]
            mu = float(chunk.mean())
            std = float(chunk.std())
            sharpe = mu / std * np.sqrt(TRADING_DAYS) if std > 1e-12 else 0.0
            hit = float((chunk > 0).sum() / len(chunk))
            results.append(RollingMetrics(
                date=chunk.index[-1],
                sharpe=sharpe,
                hit_rate=hit,
                avg_pnl=mu,
                cumulative_pnl=float(cum.iloc[end - 1]),
            ))
        return results

    # ------------------------------------------------------------------
    # CUSUM
    # ------------------------------------------------------------------

    def cusum_test(
        self, returns: pd.Series, threshold: Optional[float] = None,
    ) -> CUSUMResult:
        """CUSUM structural break detection.

        Detects downward mean shift (declining alpha).
        """
        th = threshold or self.cusum_threshold
        r = returns.dropna().values
        if len(r) < 10:
            return CUSUMResult(break_detected=False, threshold=th)

        mu = r.mean()
        sigma = r.std()
        if sigma < 1e-12:
            return CUSUMResult(break_detected=False, threshold=th)

        # Negative CUSUM: cumulative sum of deviations below mean
        s = np.zeros(len(r))
        for i in range(1, len(r)):
            s[i] = max(0.0, s[i - 1] + (mu - r[i]) / sigma)

        max_s = float(s.max())
        break_idx = int(s.argmax()) if max_s > th else None
        break_dt = (returns.dropna().index[break_idx]
                    if break_idx is not None else None)

        return CUSUMResult(
            break_detected=max_s > th,
            break_index=break_idx,
            break_date=break_dt,
            cusum_series=s,
            threshold=th,
            max_cusum=max_s,
        )

    # ------------------------------------------------------------------
    # Lifecycle classification
    # ------------------------------------------------------------------

    def classify_lifecycle(
        self, returns: pd.Series,
    ) -> LifecyclePhase:
        """Classify strategy into lifecycle phase based on rolling Sharpe trend."""
        metrics = self.rolling_metrics(returns)
        if len(metrics) < 5:
            return LifecyclePhase.GROWTH  # insufficient data → assume new

        sharpes = np.array([m.sharpe for m in metrics])

        # Recent Sharpe (last quarter of rolling window outputs)
        quarter = max(len(sharpes) // 4, 1)
        recent = sharpes[-quarter:]
        recent_mean = float(recent.mean())

        # Trend: linear regression slope of Sharpe over time
        x = np.arange(len(sharpes), dtype=float)
        slope, _, _, _, _ = sp_stats.linregress(x, sharpes)

        if recent_mean < 0 and slope < 0:
            return LifecyclePhase.DEAD
        if slope < -0.001 or recent_mean < self.kill_sharpe_threshold:
            return LifecyclePhase.DECAY
        if slope > 0.001 and recent_mean > 0.5:
            return LifecyclePhase.GROWTH
        return LifecyclePhase.MATURITY

    # ------------------------------------------------------------------
    # Decay rate estimation
    # ------------------------------------------------------------------

    def estimate_decay_rate(
        self, returns: pd.Series, lookback: Optional[int] = None,
    ) -> DecayEstimate:
        """Estimate exponential decay half-life of rolling alpha."""
        lb = lookback or self.decay_lookback
        metrics = self.rolling_metrics(returns)
        if len(metrics) < 10:
            return DecayEstimate(
                half_life_days=float("inf"), decay_rate=0.0,
                r_squared=0.0, initial_alpha=0.0, current_alpha=0.0,
            )

        # Use most recent `lb` rolling Sharpe values as alpha proxy
        sharpes = np.array([m.sharpe for m in metrics[-lb:]])
        n = len(sharpes)
        if n < 5:
            return DecayEstimate(
                half_life_days=float("inf"), decay_rate=0.0,
                r_squared=0.0, initial_alpha=float(sharpes[0]),
                current_alpha=float(sharpes[-1]),
            )

        # Fit: log(|sharpe|) = a - lambda*t  (for positive sharpes)
        positive = sharpes > 0.01
        if positive.sum() < 5:
            return DecayEstimate(
                half_life_days=float("inf"), decay_rate=0.0,
                r_squared=0.0, initial_alpha=float(sharpes[0]),
                current_alpha=float(sharpes[-1]),
            )

        idx_pos = np.where(positive)[0]
        log_s = np.log(sharpes[idx_pos])
        slope, intercept, r, _, _ = sp_stats.linregress(idx_pos.astype(float), log_s)

        decay_rate = max(-slope, 0.0)  # positive = decaying
        half_life = np.log(2) / decay_rate if decay_rate > 1e-8 else float("inf")

        return DecayEstimate(
            half_life_days=half_life,
            decay_rate=decay_rate,
            r_squared=r ** 2,
            initial_alpha=float(sharpes[0]),
            current_alpha=float(sharpes[-1]),
        )

    # ------------------------------------------------------------------
    # Regime-conditioned decay
    # ------------------------------------------------------------------

    @staticmethod
    def regime_conditioned_decay(
        returns: pd.Series,
        regimes: pd.Series,
        window: int = 63,
    ) -> List[RegimeDecay]:
        """Analyse decay characteristics per regime."""
        aligned = pd.concat([returns.rename("ret"), regimes.rename("reg")],
                            axis=1).dropna()
        results: List[RegimeDecay] = []

        for regime, grp in aligned.groupby("reg"):
            r = grp["ret"]
            n = len(r)
            if n < 10:
                results.append(RegimeDecay(
                    regime=str(regime), n_days=n, avg_sharpe=0.0,
                    sharpe_trend=0.0, is_decaying=False,
                ))
                continue

            mu = float(r.mean())
            std = float(r.std())
            sharpe = mu / std * np.sqrt(TRADING_DAYS) if std > 1e-12 else 0.0

            # Rolling Sharpe trend within this regime
            trend = 0.0
            if n >= window:
                roll_sharpe = []
                for end in range(window, n + 1):
                    ch = r.iloc[end - window: end]
                    m = float(ch.mean())
                    s = float(ch.std())
                    roll_sharpe.append(m / s * np.sqrt(TRADING_DAYS) if s > 1e-12 else 0.0)
                if len(roll_sharpe) >= 2:
                    x = np.arange(len(roll_sharpe), dtype=float)
                    trend = float(sp_stats.linregress(x, roll_sharpe).slope)

            results.append(RegimeDecay(
                regime=str(regime), n_days=n, avg_sharpe=sharpe,
                sharpe_trend=trend, is_decaying=trend < -0.001,
            ))

        return results

    # ------------------------------------------------------------------
    # Kill signal
    # ------------------------------------------------------------------

    def kill_signal(
        self,
        strategy_name: str,
        returns: pd.Series,
    ) -> KillSignal:
        """Composite kill recommendation."""
        lifecycle = self.classify_lifecycle(returns)
        cusum = self.cusum_test(returns)
        decay = self.estimate_decay_rate(returns)
        metrics = self.rolling_metrics(returns)

        reasons: List[str] = []
        score = 0.0

        # Lifecycle
        phase_scores = {
            LifecyclePhase.GROWTH: 0.0,
            LifecyclePhase.MATURITY: 0.1,
            LifecyclePhase.DECAY: 0.4,
            LifecyclePhase.DEAD: 0.8,
        }
        score += phase_scores.get(lifecycle, 0.0)
        if lifecycle in (LifecyclePhase.DECAY, LifecyclePhase.DEAD):
            reasons.append(f"lifecycle={lifecycle.value}")

        # CUSUM break
        if cusum.break_detected:
            score += 0.2
            reasons.append("structural_break_detected")

        # Recent Sharpe
        if metrics:
            recent_sharpe = metrics[-1].sharpe
            if recent_sharpe < 0:
                score += 0.2
                reasons.append(f"negative_sharpe({recent_sharpe:.2f})")
            elif recent_sharpe < self.kill_sharpe_threshold:
                score += 0.1
                reasons.append(f"low_sharpe({recent_sharpe:.2f})")

        # Hit rate
        if metrics:
            recent_hr = metrics[-1].hit_rate
            if recent_hr < self.kill_hit_rate_threshold:
                score += 0.1
                reasons.append(f"low_hit_rate({recent_hr:.1%})")

        # Decay rate
        if decay.half_life_days < 60:
            score += 0.1
            reasons.append(f"fast_decay(hl={decay.half_life_days:.0f}d)")

        score = min(score, 1.0)
        confidence = min(len(metrics) / 50, 1.0)  # more data = higher confidence

        if score >= 0.7:
            rec = "retire"
        elif score >= 0.4:
            rec = "monitor"
        else:
            rec = "keep"

        return KillSignal(
            strategy_name=strategy_name,
            lifecycle=lifecycle,
            kill_score=score,
            reasons=reasons,
            confidence=confidence,
            recommendation=rec,
        )

    # ------------------------------------------------------------------
    # HTML report
    # ------------------------------------------------------------------

    @staticmethod
    def _svg_line(
        values: List[float], dates: Optional[List] = None,
        width: int = 700, height: int = 200, title: str = "",
        threshold: Optional[float] = None, color: str = "#2980b9",
    ) -> str:
        """Generic SVG line chart."""
        if len(values) < 2:
            return ""
        n = len(values)
        y_min = min(min(values), 0)
        y_max = max(values) * 1.15 if max(values) > 0 else 0.1
        if y_max <= y_min:
            y_max = y_min + 0.1
        pad_l, pad_r, pad_t, pad_b = 55, 15, 28, 25
        pw = width - pad_l - pad_r
        ph = height - pad_t - pad_b

        def tx(i: int) -> float:
            return pad_l + i / max(n - 1, 1) * pw

        def ty(v: float) -> float:
            return pad_t + (1 - (v - y_min) / (y_max - y_min)) * ph

        p = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" style="background:#fff;border:1px solid #ddd;'
            f'border-radius:6px;margin:.5rem 0">'
        ]
        if title:
            p.append(
                f'<text x="{width // 2}" y="16" text-anchor="middle" '
                f'font-size="12" font-weight="bold" fill="#1a1a2e">{title}</text>'
            )
        # zero line
        if y_min < 0:
            zy = ty(0)
            p.append(
                f'<line x1="{pad_l}" y1="{zy:.0f}" x2="{width - pad_r}" '
                f'y2="{zy:.0f}" stroke="#ccc" stroke-dasharray="3,3"/>'
            )
        if threshold is not None:
            thy = ty(threshold)
            p.append(
                f'<line x1="{pad_l}" y1="{thy:.0f}" x2="{width - pad_r}" '
                f'y2="{thy:.0f}" stroke="#e74c3c" stroke-width="1" stroke-dasharray="4,3"/>'
            )
        d = " ".join(
            f"{'M' if i == 0 else 'L'}{tx(i):.1f},{ty(v):.1f}"
            for i, v in enumerate(values)
        )
        p.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>')
        p.append("</svg>")
        return "\n".join(p)

    @staticmethod
    def _svg_cusum(cusum: CUSUMResult, width: int = 700, height: int = 180) -> str:
        """SVG CUSUM chart with threshold and break marker."""
        if cusum.cusum_series is None or len(cusum.cusum_series) < 2:
            return ""
        vals = cusum.cusum_series.tolist()
        n = len(vals)
        y_max = max(max(vals), cusum.threshold) * 1.2
        if y_max <= 0:
            y_max = 1.0
        pad_l, pad_r, pad_t, pad_b = 55, 15, 28, 25
        pw = width - pad_l - pad_r
        ph = height - pad_t - pad_b

        def tx(i: int) -> float:
            return pad_l + i / max(n - 1, 1) * pw

        def ty(v: float) -> float:
            return pad_t + (1 - v / y_max) * ph

        p = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" style="background:#fff;border:1px solid #ddd;'
            f'border-radius:6px;margin:.5rem 0">'
        ]
        p.append(
            f'<text x="{width // 2}" y="16" text-anchor="middle" font-size="12" '
            f'font-weight="bold" fill="#1a1a2e">CUSUM Structural Break</text>'
        )
        thy = ty(cusum.threshold)
        p.append(
            f'<line x1="{pad_l}" y1="{thy:.0f}" x2="{width - pad_r}" '
            f'y2="{thy:.0f}" stroke="#e74c3c" stroke-dasharray="4,3"/>'
        )
        p.append(
            f'<text x="{width - pad_r + 2}" y="{thy + 4:.0f}" font-size="9" '
            f'fill="#e74c3c">threshold</text>'
        )
        d = " ".join(
            f"{'M' if i == 0 else 'L'}{tx(i):.1f},{ty(v):.1f}"
            for i, v in enumerate(vals)
        )
        p.append(f'<path d="{d}" fill="none" stroke="#e67e22" stroke-width="2"/>')
        if cusum.break_detected and cusum.break_index is not None:
            bx = tx(cusum.break_index)
            by = ty(vals[cusum.break_index])
            p.append(f'<circle cx="{bx:.0f}" cy="{by:.0f}" r="5" fill="#e74c3c"/>')
        p.append("</svg>")
        return "\n".join(p)

    def generate_report(
        self,
        strategy_name: str,
        returns: pd.Series,
        regimes: Optional[pd.Series] = None,
        output_path: str = "reports/strategy_decay.html",
    ) -> str:
        """HTML dashboard: rolling metrics, CUSUM, lifecycle, kill signal."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        metrics = self.rolling_metrics(returns)
        cusum = self.cusum_test(returns)
        decay = self.estimate_decay_rate(returns)
        lifecycle = self.classify_lifecycle(returns)
        kill = self.kill_signal(strategy_name, returns)

        # Charts
        sharpe_vals = [m.sharpe for m in metrics] if metrics else []
        sharpe_svg = self._svg_line(
            sharpe_vals, title="Rolling Sharpe Ratio",
            threshold=self.kill_sharpe_threshold, color="#2980b9",
        )
        cusum_svg = self._svg_cusum(cusum)

        hit_vals = [m.hit_rate for m in metrics] if metrics else []
        hit_svg = self._svg_line(
            hit_vals, title="Rolling Hit Rate",
            threshold=self.kill_hit_rate_threshold, color="#27ae60",
        )

        # Regime decay table
        regime_html = ""
        if regimes is not None:
            rd = self.regime_conditioned_decay(returns, regimes, self.rolling_window)
            if rd:
                rows = []
                for r in rd:
                    dec = "YES" if r.is_decaying else "no"
                    rows.append(
                        f"<tr><td>{r.regime}</td><td>{r.n_days}</td>"
                        f"<td>{r.avg_sharpe:.2f}</td>"
                        f"<td>{r.sharpe_trend:+.4f}</td>"
                        f"<td>{dec}</td></tr>"
                    )
                regime_html = f"""
<h2>Regime-Conditioned Decay</h2>
<table><tr><th>Regime</th><th>Days</th><th>Avg Sharpe</th>
<th>Sharpe Trend</th><th>Decaying?</th></tr>
{''.join(rows)}</table>"""

        # Lifecycle colors
        lc_colors = {
            LifecyclePhase.GROWTH: "#27ae60",
            LifecyclePhase.MATURITY: "#2980b9",
            LifecyclePhase.DECAY: "#e67e22",
            LifecyclePhase.DEAD: "#e74c3c",
        }
        lc_color = lc_colors.get(lifecycle, "#999")

        # Kill signal
        ks_color = {"keep": "#27ae60", "monitor": "#e67e22", "retire": "#e74c3c"}
        rec_color = ks_color.get(kill.recommendation, "#999")

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Strategy Decay: {strategy_name}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       margin: 2rem; background: #f5f5f5; color: #1a1a2e; }}
h1 {{ color: #1a1a2e; border-bottom: 2px solid #16213e; padding-bottom: .5rem; }}
h2 {{ color: #16213e; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; background: #fff;
         border-radius: 6px; overflow: hidden; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: right; }}
th {{ background: #16213e; color: #fff; }}
tr:nth-child(even) {{ background: #f9f9f9; }}
.summary {{ background: #fff; padding: 1.2rem 1.5rem; border-radius: 8px;
            margin: 1rem 0; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
.phase {{ display: inline-block; padding: 4px 12px; border-radius: 12px;
          color: #fff; font-weight: bold; }}
</style></head><body>
<h1>Strategy Decay Monitor: {strategy_name}</h1>
<div class="summary">
<p><strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p><strong>Lifecycle:</strong>
   <span class="phase" style="background:{lc_color}">{lifecycle.value.upper()}</span></p>
<p><strong>Kill Score:</strong> {kill.kill_score:.2f} / 1.00 &rarr;
   <span style="color:{rec_color};font-weight:bold">{kill.recommendation.upper()}</span>
   (confidence {kill.confidence:.0%})</p>
<p><strong>Half-Life:</strong> {decay.half_life_days:.0f} days
   (R&sup2;={decay.r_squared:.2f})</p>
<p><strong>CUSUM Break:</strong>
   {'YES at index ' + str(cusum.break_index) if cusum.break_detected else 'None detected'}
   (max={cusum.max_cusum:.2f}, threshold={cusum.threshold:.1f})</p>
{('<p><strong>Kill Reasons:</strong> ' + ', '.join(kill.reasons) + '</p>') if kill.reasons else ''}
</div>

<h2>Rolling Sharpe</h2>
{sharpe_svg}

<h2>CUSUM</h2>
{cusum_svg}

<h2>Rolling Hit Rate</h2>
{hit_svg}

{regime_html}
</body></html>"""

        path.write_text(html, encoding="utf-8")
        logger.info("Decay report -> %s", path)
        return str(path)
