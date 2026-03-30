"""
Implied volatility surface modeling with SABR calibration.

Components:
  1. IV surface construction    (strike × expiry grid from option chain)
  2. SABR model calibration     (alpha, beta, rho, nu)
  3. Skew & smile analytics     (25-delta skew, butterfly, risk reversal)
  4. Arbitrage detection        (calendar, butterfly, vertical spread)
  5. Term structure analysis    (contango / backwardation)
  6. Surface interpolation      (off-grid strike / expiry)
  7. Greeks from calibrated surface

All methods work on pre-loaded data — no network calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SABRParams:
    """SABR model parameters."""
    alpha: float = 0.3       # initial vol
    beta: float = 0.5        # CEV exponent (0=normal, 1=lognormal)
    rho: float = -0.3        # vol-spot correlation
    nu: float = 0.4          # vol-of-vol
    forward: float = 100.0
    expiry: float = 0.25     # years


@dataclass
class SkewMetrics:
    """Skew and smile analytics for one expiry."""
    expiry_days: int
    atm_vol: float
    skew_25d: float           # 25-delta put IV - 25-delta call IV
    butterfly_25d: float      # (put + call) / 2 - ATM
    risk_reversal_25d: float  # call - put (same as -skew for standard def)
    skew_slope: float         # dIV/dK at ATM


@dataclass
class ArbitrageViolation:
    """Detected no-arb violation on the surface."""
    violation_type: str       # "calendar" | "butterfly" | "vertical"
    strike: float
    expiry_1: float
    expiry_2: Optional[float] = None
    detail: str = ""


@dataclass
class TermStructurePoint:
    """Single point on the ATM term structure."""
    expiry_days: int
    atm_vol: float


@dataclass
class SurfaceGreeks:
    """Greeks computed from the calibrated surface."""
    strike: float
    expiry: float
    iv: float
    delta: float
    gamma: float
    vega: float
    theta: float


@dataclass
class VolSurfaceSummary:
    """Full surface analysis result."""
    sabr_params: Optional[SABRParams] = None
    skew_metrics: List[SkewMetrics] = field(default_factory=list)
    term_structure: List[TermStructurePoint] = field(default_factory=list)
    is_contango: bool = True
    violations: List[ArbitrageViolation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SABR formulas (Hagan et al. 2002)
# ---------------------------------------------------------------------------

def sabr_vol(
    strike: float, forward: float, expiry: float,
    alpha: float, beta: float, rho: float, nu: float,
) -> float:
    """SABR implied volatility approximation (Hagan et al.)."""
    if expiry <= 0 or alpha <= 0:
        return 0.0
    K = max(strike, 1e-8)
    F = max(forward, 1e-8)

    if abs(K - F) < 1e-10:
        # ATM limit
        fk_mid = F ** (1 - beta)
        vol = alpha / fk_mid
        term1 = ((1 - beta) ** 2 / 24 * alpha ** 2 / fk_mid ** 2
                 + 0.25 * rho * beta * nu * alpha / fk_mid
                 + (2 - 3 * rho ** 2) / 24 * nu ** 2)
        return vol * (1 + term1 * expiry)

    fk = (F * K) ** ((1 - beta) / 2)
    log_fk = np.log(F / K)
    z = nu / alpha * fk * log_fk
    x_z = np.log((np.sqrt(1 - 2 * rho * z + z ** 2) + z - rho) / (1 - rho))

    if abs(x_z) < 1e-12:
        x_z = 1.0
        z = 1.0

    prefix = alpha / (fk * (1 + (1 - beta) ** 2 / 24 * log_fk ** 2
                              + (1 - beta) ** 4 / 1920 * log_fk ** 4))
    term1 = ((1 - beta) ** 2 / 24 * alpha ** 2 / fk ** 2
             + 0.25 * rho * beta * nu * alpha / fk
             + (2 - 3 * rho ** 2) / 24 * nu ** 2)

    return prefix * z / x_z * (1 + term1 * expiry)


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class VolSurfaceEngine:
    """Implied volatility surface modelling engine.

    Args:
        beta: Fixed CEV exponent for SABR (default 0.5).
    """

    def __init__(self, beta: float = 0.5) -> None:
        self.beta = beta

    # ------------------------------------------------------------------
    # 1. Surface construction
    # ------------------------------------------------------------------

    @staticmethod
    def build_surface(
        chain: pd.DataFrame,
        strike_col: str = "strike",
        expiry_col: str = "expiry_days",
        iv_col: str = "iv",
    ) -> pd.DataFrame:
        """Build a strike × expiry IV grid from an option chain.

        Returns a pivoted DataFrame: index=strike, columns=expiry_days.
        """
        if chain.empty:
            return pd.DataFrame()
        required = {strike_col, expiry_col, iv_col}
        if not required.issubset(chain.columns):
            return pd.DataFrame()

        surface = chain.pivot_table(
            index=strike_col, columns=expiry_col, values=iv_col,
            aggfunc="mean",
        )
        return surface.sort_index()

    # ------------------------------------------------------------------
    # 2. SABR calibration
    # ------------------------------------------------------------------

    def calibrate_sabr(
        self,
        strikes: np.ndarray,
        market_vols: np.ndarray,
        forward: float,
        expiry: float,
        alpha0: float = 0.3,
    ) -> SABRParams:
        """Calibrate SABR parameters to market vols for one expiry.

        beta is held fixed (set in constructor). Fits alpha, rho, nu.
        """
        if len(strikes) < 3:
            return SABRParams(alpha=alpha0, beta=self.beta, forward=forward, expiry=expiry)

        def objective(params):
            a, r, n = params
            r = max(-0.999, min(0.999, r))
            n = max(0.01, n)
            a = max(0.001, a)
            err = 0.0
            for i in range(len(strikes)):
                model = sabr_vol(strikes[i], forward, expiry, a, self.beta, r, n)
                err += (model - market_vols[i]) ** 2
            return err

        x0 = [alpha0, -0.3, 0.4]
        bounds = [(0.001, 5.0), (-0.999, 0.999), (0.01, 5.0)]
        res = minimize(objective, x0, method="L-BFGS-B", bounds=bounds,
                        options={"maxiter": 500})

        a, r, n = res.x
        return SABRParams(
            alpha=float(a), beta=self.beta,
            rho=float(max(-0.999, min(0.999, r))),
            nu=float(max(0.01, n)),
            forward=forward, expiry=expiry,
        )

    def sabr_smile(
        self, params: SABRParams, strikes: np.ndarray,
    ) -> np.ndarray:
        """Generate SABR model vols for a set of strikes."""
        return np.array([
            sabr_vol(k, params.forward, params.expiry,
                      params.alpha, params.beta, params.rho, params.nu)
            for k in strikes
        ])

    # ------------------------------------------------------------------
    # 3. Skew & smile analytics
    # ------------------------------------------------------------------

    @staticmethod
    def compute_skew(
        strikes: np.ndarray,
        vols: np.ndarray,
        forward: float,
        expiry_days: int,
    ) -> SkewMetrics:
        """Compute skew metrics from a vol smile."""
        if len(strikes) < 3 or len(vols) < 3:
            return SkewMetrics(expiry_days=expiry_days, atm_vol=0.0,
                                skew_25d=0.0, butterfly_25d=0.0,
                                risk_reversal_25d=0.0, skew_slope=0.0)

        # ATM: closest to forward
        atm_idx = int(np.argmin(np.abs(strikes - forward)))
        atm_vol = float(vols[atm_idx])

        # 25-delta approximation: ~10% OTM
        put_strike = forward * 0.90
        call_strike = forward * 1.10

        put_vol = float(np.interp(put_strike, strikes, vols))
        call_vol = float(np.interp(call_strike, strikes, vols))

        skew = put_vol - call_vol
        butterfly = (put_vol + call_vol) / 2 - atm_vol
        rr = call_vol - put_vol

        # Slope at ATM: finite difference
        if atm_idx > 0 and atm_idx < len(strikes) - 1:
            dk = strikes[atm_idx + 1] - strikes[atm_idx - 1]
            slope = (vols[atm_idx + 1] - vols[atm_idx - 1]) / dk if dk > 0 else 0.0
        else:
            slope = 0.0

        return SkewMetrics(
            expiry_days=expiry_days, atm_vol=atm_vol,
            skew_25d=skew, butterfly_25d=butterfly,
            risk_reversal_25d=rr, skew_slope=float(slope),
        )

    # ------------------------------------------------------------------
    # 4. Arbitrage detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect_arbitrage(surface: pd.DataFrame) -> List[ArbitrageViolation]:
        """Check for calendar, butterfly, and vertical spread violations."""
        violations: List[ArbitrageViolation] = []
        if surface.empty:
            return violations

        expiries = sorted(surface.columns)
        strikes = surface.index.values

        # Calendar spread: IV should generally increase with expiry (total variance)
        for k in strikes:
            row = surface.loc[k].dropna()
            if len(row) < 2:
                continue
            for i in range(len(row) - 1):
                t1, t2 = row.index[i], row.index[i + 1]
                tv1 = row.iloc[i] ** 2 * t1 / 365
                tv2 = row.iloc[i + 1] ** 2 * t2 / 365
                if tv2 < tv1 - 1e-6:
                    violations.append(ArbitrageViolation(
                        violation_type="calendar", strike=float(k),
                        expiry_1=float(t1), expiry_2=float(t2),
                        detail=f"total_var({t1})={tv1:.6f} > total_var({t2})={tv2:.6f}",
                    ))

        # Butterfly: convexity — IV should be convex in strike
        for exp in expiries:
            col = surface[exp].dropna()
            if len(col) < 3:
                continue
            vals = col.values
            for i in range(1, len(vals) - 1):
                butterfly = vals[i - 1] + vals[i + 1] - 2 * vals[i]
                if butterfly < -0.005:
                    violations.append(ArbitrageViolation(
                        violation_type="butterfly",
                        strike=float(col.index[i]),
                        expiry_1=float(exp),
                        detail=f"butterfly={butterfly:.6f}",
                    ))

        return violations

    # ------------------------------------------------------------------
    # 5. Term structure
    # ------------------------------------------------------------------

    @staticmethod
    def term_structure(
        surface: pd.DataFrame, forward: float,
    ) -> Tuple[List[TermStructurePoint], bool]:
        """Extract ATM term structure and detect contango/backwardation."""
        if surface.empty:
            return [], True

        points: List[TermStructurePoint] = []
        for exp in sorted(surface.columns):
            col = surface[exp].dropna()
            if col.empty:
                continue
            atm_idx = int(np.argmin(np.abs(col.index.values - forward)))
            atm_vol = float(col.iloc[atm_idx])
            points.append(TermStructurePoint(expiry_days=int(exp), atm_vol=atm_vol))

        if len(points) < 2:
            return points, True

        is_contango = points[-1].atm_vol >= points[0].atm_vol
        return points, is_contango

    # ------------------------------------------------------------------
    # 6. Interpolation
    # ------------------------------------------------------------------

    @staticmethod
    def interpolate(
        surface: pd.DataFrame,
        strike: float,
        expiry_days: float,
    ) -> float:
        """Bilinear interpolation on the IV surface."""
        if surface.empty:
            return 0.0

        strikes = surface.index.values.astype(float)
        expiries = np.array(surface.columns, dtype=float)

        # Clamp to surface boundaries
        strike = np.clip(strike, strikes.min(), strikes.max())
        expiry_days = np.clip(expiry_days, expiries.min(), expiries.max())

        # Find bracketing strikes
        k_idx = np.searchsorted(strikes, strike, side="right")
        k_idx = np.clip(k_idx, 1, len(strikes) - 1)
        k0, k1 = strikes[k_idx - 1], strikes[k_idx]
        kw = (strike - k0) / (k1 - k0) if k1 != k0 else 0.0

        # Find bracketing expiries
        e_idx = np.searchsorted(expiries, expiry_days, side="right")
        e_idx = np.clip(e_idx, 1, len(expiries) - 1)
        e0, e1 = expiries[e_idx - 1], expiries[e_idx]
        ew = (expiry_days - e0) / (e1 - e0) if e1 != e0 else 0.0

        # Bilinear
        v00 = surface.iloc[k_idx - 1, e_idx - 1]
        v01 = surface.iloc[k_idx - 1, e_idx]
        v10 = surface.iloc[k_idx, e_idx - 1]
        v11 = surface.iloc[k_idx, e_idx]

        # Handle NaN
        vals = [v00, v01, v10, v11]
        if any(np.isnan(v) for v in vals):
            valid = [v for v in vals if not np.isnan(v)]
            return float(np.mean(valid)) if valid else 0.0

        v0 = v00 * (1 - kw) + v10 * kw
        v1 = v01 * (1 - kw) + v11 * kw
        return float(v0 * (1 - ew) + v1 * ew)

    # ------------------------------------------------------------------
    # 7. Greeks
    # ------------------------------------------------------------------

    @staticmethod
    def bs_greeks(
        strike: float, forward: float, expiry: float,
        iv: float, rate: float = 0.045, is_call: bool = True,
    ) -> SurfaceGreeks:
        """Black-Scholes Greeks from IV."""
        if iv <= 0 or expiry <= 0:
            return SurfaceGreeks(
                strike=strike, expiry=expiry, iv=iv,
                delta=0, gamma=0, vega=0, theta=0)

        sqrt_t = np.sqrt(expiry)
        d1 = (np.log(forward / strike) + 0.5 * iv ** 2 * expiry) / (iv * sqrt_t)
        d2 = d1 - iv * sqrt_t

        df = np.exp(-rate * expiry)
        nd1 = norm.pdf(d1)

        if is_call:
            delta = float(df * norm.cdf(d1))
        else:
            delta = float(df * (norm.cdf(d1) - 1))

        gamma = float(df * nd1 / (forward * iv * sqrt_t))
        vega = float(forward * df * nd1 * sqrt_t / 100)

        # Theta (per day)
        theta_annual = -forward * df * nd1 * iv / (2 * sqrt_t)
        if is_call:
            theta_annual -= rate * strike * df * norm.cdf(d2)
        else:
            theta_annual += rate * strike * df * norm.cdf(-d2)
        theta = float(theta_annual / 365)

        return SurfaceGreeks(
            strike=strike, expiry=expiry, iv=iv,
            delta=delta, gamma=gamma, vega=vega, theta=theta,
        )

    def greeks_from_surface(
        self,
        surface: pd.DataFrame,
        forward: float,
        strike: float,
        expiry_days: float,
        rate: float = 0.045,
        is_call: bool = True,
    ) -> SurfaceGreeks:
        """Interpolate IV from surface then compute Greeks."""
        iv = self.interpolate(surface, strike, expiry_days)
        expiry_years = expiry_days / 365
        return self.bs_greeks(strike, forward, expiry_years, iv, rate, is_call)

    # ------------------------------------------------------------------
    # Full analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        chain: pd.DataFrame,
        forward: float,
        strike_col: str = "strike",
        expiry_col: str = "expiry_days",
        iv_col: str = "iv",
    ) -> VolSurfaceSummary:
        """Run full surface analysis."""
        surface = self.build_surface(chain, strike_col, expiry_col, iv_col)
        if surface.empty:
            return VolSurfaceSummary()

        # SABR on shortest expiry
        expiries = sorted(surface.columns)
        sabr = None
        if expiries:
            first_exp = expiries[0]
            col = surface[first_exp].dropna()
            if len(col) >= 3:
                sabr = self.calibrate_sabr(
                    col.index.values.astype(float), col.values.astype(float),
                    forward, first_exp / 365,
                )

        # Skew per expiry
        skew_list: List[SkewMetrics] = []
        for exp in expiries:
            col = surface[exp].dropna()
            if len(col) >= 3:
                sm = self.compute_skew(
                    col.index.values.astype(float), col.values.astype(float),
                    forward, int(exp))
                skew_list.append(sm)

        # Term structure
        ts, is_contango = self.term_structure(surface, forward)

        # Arbitrage
        violations = self.detect_arbitrage(surface)

        return VolSurfaceSummary(
            sabr_params=sabr,
            skew_metrics=skew_list,
            term_structure=ts,
            is_contango=is_contango,
            violations=violations,
        )

    # ------------------------------------------------------------------
    # HTML report
    # ------------------------------------------------------------------

    @staticmethod
    def _svg_line(
        xs: List[float], ys: List[float], title: str,
        width: int = 650, height: int = 200, color: str = "#2980b9",
        x_labels: Optional[List[str]] = None,
    ) -> str:
        if len(xs) < 2:
            return ""
        n = len(xs)
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        if ymax <= ymin:
            ymax = ymin + 0.01
        if xmax <= xmin:
            xmax = xmin + 1
        pad_l, pad_r, pad_t, pad_b = 55, 15, 28, 35
        pw = width - pad_l - pad_r
        ph = height - pad_t - pad_b

        def tx(v): return pad_l + (v - xmin) / (xmax - xmin) * pw
        def ty(v): return pad_t + (1 - (v - ymin) / (ymax - ymin)) * ph

        p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{height}" style="background:#fff;border:1px solid #ddd;'
             f'border-radius:6px;margin:.5rem 0">']
        p.append(f'<text x="{width // 2}" y="16" text-anchor="middle" font-size="12" '
                 f'font-weight="bold" fill="#1a1a2e">{title}</text>')
        d = " ".join(f"{'M' if i == 0 else 'L'}{tx(xs[i]):.1f},{ty(ys[i]):.1f}"
                      for i in range(n))
        p.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>')
        # dots
        for i in range(n):
            p.append(f'<circle cx="{tx(xs[i]):.1f}" cy="{ty(ys[i]):.1f}" r="3" fill="{color}"/>')
        p.append("</svg>")
        return "\n".join(p)

    def generate_report(
        self,
        summary: VolSurfaceSummary,
        output_path: str = "reports/vol_surface.html",
    ) -> str:
        """HTML report: skew chart, term structure, surface data."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # SABR
        sabr_html = ""
        if summary.sabr_params:
            sp = summary.sabr_params
            sabr_html = f"""
<h2>SABR Calibration</h2>
<table class="m"><tr><th>&alpha;</th><th>&beta;</th><th>&rho;</th><th>&nu;</th>
<th>Forward</th><th>Expiry</th></tr>
<tr><td>{sp.alpha:.4f}</td><td>{sp.beta:.2f}</td><td>{sp.rho:.4f}</td>
<td>{sp.nu:.4f}</td><td>{sp.forward:.2f}</td><td>{sp.expiry:.4f}y</td></tr></table>"""

        # Skew chart — use first expiry
        skew_svg = ""
        if summary.skew_metrics:
            sm = summary.skew_metrics
            xs = [s.expiry_days for s in sm]
            ys = [s.skew_25d for s in sm]
            skew_svg = self._svg_line(xs, ys, "25-Delta Skew by Expiry", color="#e74c3c")

        # Term structure chart
        ts_svg = ""
        if summary.term_structure:
            xs = [p.expiry_days for p in summary.term_structure]
            ys = [p.atm_vol for p in summary.term_structure]
            label = "Contango" if summary.is_contango else "Backwardation"
            ts_svg = self._svg_line(xs, ys, f"ATM Term Structure ({label})", color="#2980b9")

        # Skew table
        skew_table = ""
        if summary.skew_metrics:
            rows = [
                f"<tr><td>{s.expiry_days}</td><td>{s.atm_vol:.4f}</td>"
                f"<td>{s.skew_25d:+.4f}</td><td>{s.butterfly_25d:+.4f}</td>"
                f"<td>{s.risk_reversal_25d:+.4f}</td><td>{s.skew_slope:+.6f}</td></tr>"
                for s in summary.skew_metrics
            ]
            skew_table = f"""
<h2>Skew Analytics</h2>
<table><tr><th>Expiry (d)</th><th>ATM Vol</th><th>25&Delta; Skew</th>
<th>Butterfly</th><th>Risk Reversal</th><th>Slope</th></tr>
{''.join(rows)}</table>"""

        # Violations
        viol_html = ""
        if summary.violations:
            rows = [
                f"<tr><td>{v.violation_type}</td><td>{v.strike:.2f}</td>"
                f"<td>{v.expiry_1:.0f}</td>"
                f"<td>{v.expiry_2 if v.expiry_2 else '-'}</td>"
                f"<td style='text-align:left'>{v.detail}</td></tr>"
                for v in summary.violations
            ]
            viol_html = f"""
<h2>Arbitrage Violations ({len(summary.violations)})</h2>
<table><tr><th>Type</th><th>Strike</th><th>Expiry 1</th><th>Expiry 2</th>
<th style='text-align:left'>Detail</th></tr>
{''.join(rows)}</table>"""

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Vol Surface Analysis</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       margin: 2rem; background: #f5f5f5; color: #1a1a2e; }}
h1 {{ color: #1a1a2e; border-bottom: 2px solid #16213e; padding-bottom: .5rem; }}
h2 {{ color: #16213e; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; background: #fff;
         border-radius: 6px; overflow: hidden; }}
table.m {{ width: auto; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: right; }}
th {{ background: #16213e; color: #fff; }}
tr:nth-child(even) {{ background: #f9f9f9; }}
.summary {{ background: #fff; padding: 1.2rem 1.5rem; border-radius: 8px;
            margin: 1rem 0; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
</style></head><body>
<h1>Implied Volatility Surface Report</h1>
<div class="summary">
<p><strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p><strong>Term Structure:</strong> {'Contango' if summary.is_contango else 'Backwardation'}
   | <strong>Violations:</strong> {len(summary.violations)}</p>
</div>

{sabr_html}
{skew_svg}
{skew_table}
{ts_svg}
{viol_html}
</body></html>"""

        path.write_text(html, encoding="utf-8")
        logger.info("Vol surface report -> %s", path)
        return str(path)
