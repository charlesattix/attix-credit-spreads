"""
Volatility forecaster — EWMA, GARCH(1,1), IV/RV spread analysis, regime classification.

Provides forward-looking volatility estimates and regime tags for the trading
pipeline.  All methods work on pre-loaded data (no network calls) so they can
run inside the backtester day loop.

Regime buckets (annualized vol):
  LOW      < 12%
  NORMAL   12-20%
  HIGH     20-35%
  EXTREME  > 35%
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
from scipy.optimize import minimize

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------

class VolRegime(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


VOL_REGIME_THRESHOLDS: Dict[VolRegime, Tuple[float, float]] = {
    VolRegime.LOW: (0.0, 0.12),
    VolRegime.NORMAL: (0.12, 0.20),
    VolRegime.HIGH: (0.20, 0.35),
    VolRegime.EXTREME: (0.35, float("inf")),
}


@dataclass
class VolForecast:
    """Point-in-time volatility forecast."""
    date: datetime
    ewma_vol: float
    garch_vol: float
    blended_vol: float
    regime: VolRegime
    iv: Optional[float] = None
    rv: float = 0.0
    iv_rv_spread: Optional[float] = None


@dataclass
class GARCHParams:
    """GARCH(1,1) parameters: sigma_t^2 = omega + alpha * r_{t-1}^2 + beta * sigma_{t-1}^2."""
    omega: float = 1e-6
    alpha: float = 0.10
    beta: float = 0.85

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    @property
    def long_run_var(self) -> float:
        denom = 1.0 - self.persistence
        if denom <= 0:
            return float("inf")
        return self.omega / denom


@dataclass
class ForecastAccuracy:
    """Tracks forecast-vs-realised accuracy."""
    date: datetime
    forecast_vol: float
    realised_vol: float
    error: float
    abs_error: float
    squared_error: float


@dataclass
class IVRVSpread:
    """Implied-vs-realised volatility spread snapshot."""
    date: datetime
    iv: float
    rv: float
    spread: float
    spread_percentile: float


@dataclass
class TermStructurePoint:
    """Single tenor on the vol term structure."""
    tenor_days: int
    tenor_label: str
    iv: float


@dataclass
class TermStructureSnapshot:
    """Full term structure at a point in time."""
    date: datetime
    points: List[TermStructurePoint]
    slope: float  # short-end minus long-end (positive = backwardation)
    curvature: float  # belly vs wings
    is_inverted: bool  # True when short > long (fear signal)


# ---------------------------------------------------------------------------
# Core forecaster
# ---------------------------------------------------------------------------

class VolForecaster:
    """EWMA + GARCH(1,1) volatility forecaster with regime classification.

    Args:
        ewma_span: Span for EWMA (days).
        garch_params: Initial GARCH(1,1) parameters (fitted on first call).
        blend_weight: Weight on EWMA in the blended forecast (1-w on GARCH).
        regime_thresholds: Override default regime boundaries.
    """

    def __init__(
        self,
        ewma_span: int = 30,
        garch_params: Optional[GARCHParams] = None,
        blend_weight: float = 0.5,
        regime_thresholds: Optional[Dict[VolRegime, Tuple[float, float]]] = None,
    ) -> None:
        self.ewma_span = ewma_span
        self.garch_params = garch_params or GARCHParams()
        self.blend_weight = blend_weight
        self.regime_thresholds = regime_thresholds or VOL_REGIME_THRESHOLDS
        self._accuracy_log: List[ForecastAccuracy] = []
        self._fitted = False

    # ------------------------------------------------------------------
    # EWMA
    # ------------------------------------------------------------------

    def ewma_vol(self, returns: pd.Series) -> pd.Series:
        """Exponentially-weighted annualised volatility series."""
        if len(returns) < 2:
            return pd.Series(dtype=float, index=returns.index)
        var = returns.ewm(span=self.ewma_span, min_periods=2).var()
        return np.sqrt(var * TRADING_DAYS)

    def ewma_forecast(self, returns: pd.Series) -> float:
        """Latest EWMA annualised vol."""
        s = self.ewma_vol(returns).dropna()
        if s.empty:
            return 0.0
        return float(s.iloc[-1])

    # ------------------------------------------------------------------
    # GARCH(1,1)
    # ------------------------------------------------------------------

    @staticmethod
    def _garch_loglik(params: np.ndarray, returns: np.ndarray) -> float:
        """Negative log-likelihood for GARCH(1,1)."""
        omega, alpha, beta = params
        T = len(returns)
        sigma2 = np.empty(T)
        sigma2[0] = np.var(returns)
        for t in range(1, T):
            sigma2[t] = omega + alpha * returns[t - 1] ** 2 + beta * sigma2[t - 1]
            if sigma2[t] <= 0:
                return 1e10
        ll = -0.5 * np.sum(np.log(sigma2) + returns ** 2 / sigma2)
        return -ll  # minimise neg-LL

    def fit_garch(self, returns: pd.Series, max_iter: int = 500) -> GARCHParams:
        """MLE fit of GARCH(1,1)."""
        r = returns.dropna().values
        if len(r) < 20:
            logger.warning("Too few returns for GARCH fit (%d); using defaults.", len(r))
            return self.garch_params

        x0 = np.array([self.garch_params.omega, self.garch_params.alpha, self.garch_params.beta])
        bounds = [(1e-8, 1e-2), (0.01, 0.50), (0.50, 0.999)]
        constraints = {"type": "ineq", "fun": lambda p: 0.9999 - p[1] - p[2]}

        res = minimize(
            self._garch_loglik,
            x0,
            args=(r,),
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": max_iter, "ftol": 1e-10},
        )

        if res.success:
            self.garch_params = GARCHParams(
                omega=res.x[0], alpha=res.x[1], beta=res.x[2],
            )
            self._fitted = True
            logger.info(
                "GARCH fit: omega=%.2e alpha=%.4f beta=%.4f persistence=%.4f",
                self.garch_params.omega,
                self.garch_params.alpha,
                self.garch_params.beta,
                self.garch_params.persistence,
            )
        else:
            logger.warning("GARCH optimiser did not converge: %s", res.message)

        return self.garch_params

    def garch_variance_series(self, returns: pd.Series) -> pd.Series:
        """Conditional variance series from GARCH(1,1)."""
        r = returns.dropna().values
        T = len(r)
        if T < 2:
            return pd.Series(dtype=float, index=returns.index)

        sigma2 = np.empty(T)
        sigma2[0] = np.var(r)
        omega = self.garch_params.omega
        alpha = self.garch_params.alpha
        beta = self.garch_params.beta
        for t in range(1, T):
            sigma2[t] = omega + alpha * r[t - 1] ** 2 + beta * sigma2[t - 1]

        idx = returns.dropna().index
        return pd.Series(sigma2, index=idx, name="garch_var")

    def garch_vol(self, returns: pd.Series) -> pd.Series:
        """Annualised GARCH volatility series."""
        return np.sqrt(self.garch_variance_series(returns) * TRADING_DAYS)

    def garch_forecast(self, returns: pd.Series, horizon: int = 1) -> float:
        """H-step ahead annualised GARCH vol forecast."""
        var_series = self.garch_variance_series(returns)
        if var_series.empty:
            return 0.0
        last_var = float(var_series.iloc[-1])
        lr_var = self.garch_params.long_run_var
        p = self.garch_params.persistence

        if p >= 1.0 or lr_var == float("inf"):
            return float(np.sqrt(last_var * TRADING_DAYS))

        # Multi-step GARCH forecast: E[sigma^2_{t+h}] = lr_var + p^h * (sigma^2_t - lr_var)
        forecast_var = lr_var + (p ** horizon) * (last_var - lr_var)
        return float(np.sqrt(forecast_var * TRADING_DAYS))

    # ------------------------------------------------------------------
    # Blended forecast
    # ------------------------------------------------------------------

    def blended_forecast(self, returns: pd.Series, horizon: int = 1) -> float:
        """Blend of EWMA and GARCH forecasts."""
        e = self.ewma_forecast(returns)
        g = self.garch_forecast(returns, horizon=horizon)
        return self.blend_weight * e + (1.0 - self.blend_weight) * g

    # ------------------------------------------------------------------
    # Regime classification
    # ------------------------------------------------------------------

    def classify_regime(self, annualised_vol: float) -> VolRegime:
        """Classify a single annualised vol reading into a regime."""
        for regime, (lo, hi) in self.regime_thresholds.items():
            if lo <= annualised_vol < hi:
                return regime
        return VolRegime.EXTREME

    def classify_series(self, vol_series: pd.Series) -> pd.Series:
        """Classify every point in an annualised vol series."""
        return vol_series.apply(self.classify_regime)

    # ------------------------------------------------------------------
    # Realised volatility
    # ------------------------------------------------------------------

    @staticmethod
    def realised_vol(returns: pd.Series, window: int = 21) -> pd.Series:
        """Rolling realised annualised volatility."""
        return returns.rolling(window).std() * np.sqrt(TRADING_DAYS)

    # ------------------------------------------------------------------
    # IV / RV spread analysis
    # ------------------------------------------------------------------

    def iv_rv_spread(
        self,
        iv_series: pd.Series,
        returns: pd.Series,
        rv_window: int = 21,
        lookback: int = 252,
    ) -> List[IVRVSpread]:
        """Compute IV-RV spread with percentile ranking."""
        rv = self.realised_vol(returns, window=rv_window)
        aligned = pd.DataFrame({"iv": iv_series, "rv": rv}).dropna()
        if aligned.empty:
            return []

        aligned["spread"] = aligned["iv"] - aligned["rv"]
        results: List[IVRVSpread] = []
        for i, (dt, row) in enumerate(aligned.iterrows()):
            start = max(0, i - lookback + 1)
            hist = aligned["spread"].iloc[start: i + 1]
            pctile = float((hist < row["spread"]).sum() / len(hist)) if len(hist) > 0 else 0.5
            results.append(
                IVRVSpread(
                    date=dt,
                    iv=float(row["iv"]),
                    rv=float(row["rv"]),
                    spread=float(row["spread"]),
                    spread_percentile=pctile,
                )
            )
        return results

    def iv_rv_signal(self, iv_rv_spreads: List[IVRVSpread]) -> Optional[str]:
        """Generate a signal from the latest IV/RV spread.

        Returns 'rich' if IV >> RV (good for selling premium),
        'cheap' if IV << RV, or None if neutral.
        """
        if not iv_rv_spreads:
            return None
        latest = iv_rv_spreads[-1]
        if latest.spread_percentile > 0.80:
            return "rich"
        if latest.spread_percentile < 0.20:
            return "cheap"
        return None

    # ------------------------------------------------------------------
    # Term structure analysis
    # ------------------------------------------------------------------

    @staticmethod
    def build_term_structure(
        iv_by_tenor: Dict[int, float],
        date: Optional[datetime] = None,
    ) -> TermStructureSnapshot:
        """Build a term structure snapshot from {tenor_days: iv} mapping.

        Args:
            iv_by_tenor: e.g. {7: 0.18, 30: 0.20, 60: 0.22, 90: 0.21}
            date: Observation date.
        """
        if not iv_by_tenor:
            return TermStructureSnapshot(
                date=date or datetime.now(), points=[], slope=0.0,
                curvature=0.0, is_inverted=False,
            )

        sorted_tenors = sorted(iv_by_tenor.items())
        labels = {7: "1W", 14: "2W", 21: "3W", 30: "1M", 45: "45D",
                  60: "2M", 90: "3M", 120: "4M", 180: "6M", 252: "1Y", 365: "1Y+"}

        points = []
        for t, iv in sorted_tenors:
            label = labels.get(t, f"{t}D")
            points.append(TermStructurePoint(tenor_days=t, tenor_label=label, iv=iv))

        # Slope: short minus long
        short_iv = sorted_tenors[0][1]
        long_iv = sorted_tenors[-1][1]
        slope = short_iv - long_iv
        is_inverted = slope > 0.005  # 50 bps threshold

        # Curvature: mid-tenor vs average of ends (positive = humped)
        curvature = 0.0
        if len(sorted_tenors) >= 3:
            mid_idx = len(sorted_tenors) // 2
            mid_iv = sorted_tenors[mid_idx][1]
            end_avg = (short_iv + long_iv) / 2.0
            curvature = mid_iv - end_avg

        return TermStructureSnapshot(
            date=date or datetime.now(),
            points=points,
            slope=slope,
            curvature=curvature,
            is_inverted=is_inverted,
        )

    @staticmethod
    def term_structure_series(
        iv_by_tenor_series: Dict[datetime, Dict[int, float]],
    ) -> List[TermStructureSnapshot]:
        """Build term structures across multiple dates."""
        results = []
        for dt, mapping in sorted(iv_by_tenor_series.items()):
            results.append(VolForecaster.build_term_structure(mapping, date=dt))
        return results

    # ------------------------------------------------------------------
    # Full forecast
    # ------------------------------------------------------------------

    def forecast(
        self,
        returns: pd.Series,
        iv_series: Optional[pd.Series] = None,
        horizon: int = 1,
        fit: bool = False,
    ) -> VolForecast:
        """Produce a single composite VolForecast.

        Args:
            returns: Daily log-return series.
            iv_series: Optional implied-vol series aligned to returns.
            horizon: Forecast horizon in days.
            fit: If True, re-fit GARCH before forecasting.
        """
        if fit and len(returns.dropna()) >= 20:
            self.fit_garch(returns)

        e = self.ewma_forecast(returns)
        g = self.garch_forecast(returns, horizon=horizon)
        blended = self.blend_weight * e + (1.0 - self.blend_weight) * g
        regime = self.classify_regime(blended)

        rv_val = float(self.realised_vol(returns).iloc[-1]) if len(returns) >= 22 else 0.0

        iv_val: Optional[float] = None
        iv_rv_spread: Optional[float] = None
        if iv_series is not None and not iv_series.empty:
            iv_val = float(iv_series.iloc[-1])
            iv_rv_spread = iv_val - rv_val if rv_val > 0 else None

        dt = returns.index[-1] if not returns.empty else datetime.now()

        return VolForecast(
            date=dt,
            ewma_vol=e,
            garch_vol=g,
            blended_vol=blended,
            regime=regime,
            iv=iv_val,
            rv=rv_val,
            iv_rv_spread=iv_rv_spread,
        )

    # ------------------------------------------------------------------
    # Forecast series
    # ------------------------------------------------------------------

    def forecast_series(
        self,
        returns: pd.Series,
        iv_series: Optional[pd.Series] = None,
        fit: bool = True,
    ) -> List[VolForecast]:
        """Generate a VolForecast for each date in the return series."""
        if fit and len(returns.dropna()) >= 20:
            self.fit_garch(returns)

        ewma = self.ewma_vol(returns)
        garch = self.garch_vol(returns)
        rv = self.realised_vol(returns)

        results: List[VolForecast] = []
        for dt in ewma.index:
            e = float(ewma.loc[dt]) if dt in ewma.index and pd.notna(ewma.loc[dt]) else 0.0
            g = float(garch.loc[dt]) if dt in garch.index and pd.notna(garch.loc[dt]) else 0.0
            blended = self.blend_weight * e + (1.0 - self.blend_weight) * g
            regime = self.classify_regime(blended)
            rv_val = float(rv.loc[dt]) if dt in rv.index and pd.notna(rv.loc[dt]) else 0.0

            iv_val: Optional[float] = None
            iv_rv_sp: Optional[float] = None
            if iv_series is not None and dt in iv_series.index and pd.notna(iv_series.get(dt)):
                iv_val = float(iv_series.loc[dt])
                iv_rv_sp = iv_val - rv_val if rv_val > 0 else None

            results.append(VolForecast(
                date=dt, ewma_vol=e, garch_vol=g, blended_vol=blended,
                regime=regime, iv=iv_val, rv=rv_val, iv_rv_spread=iv_rv_sp,
            ))
        return results

    # ------------------------------------------------------------------
    # Accuracy tracking
    # ------------------------------------------------------------------

    def log_accuracy(self, forecast_vol: float, realised_vol: float, date: datetime) -> ForecastAccuracy:
        """Record one forecast-vs-realised observation."""
        err = forecast_vol - realised_vol
        rec = ForecastAccuracy(
            date=date,
            forecast_vol=forecast_vol,
            realised_vol=realised_vol,
            error=err,
            abs_error=abs(err),
            squared_error=err ** 2,
        )
        self._accuracy_log.append(rec)
        return rec

    def accuracy_stats(self) -> Dict[str, float]:
        """Aggregate accuracy statistics."""
        if not self._accuracy_log:
            return {"mae": 0.0, "rmse": 0.0, "bias": 0.0, "n": 0}
        errs = [a.error for a in self._accuracy_log]
        abs_errs = [a.abs_error for a in self._accuracy_log]
        sq_errs = [a.squared_error for a in self._accuracy_log]
        return {
            "mae": float(np.mean(abs_errs)),
            "rmse": float(np.sqrt(np.mean(sq_errs))),
            "bias": float(np.mean(errs)),
            "n": len(self._accuracy_log),
        }

    def clear_accuracy(self) -> None:
        self._accuracy_log.clear()

    # ------------------------------------------------------------------
    # HTML report
    # ------------------------------------------------------------------

    def generate_report(
        self,
        forecasts: List[VolForecast],
        output_path: str = "reports/vol_forecast.html",
    ) -> str:
        """Write an HTML report summarising the vol forecasts."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        rows = []
        for f in forecasts:
            dt_str = f.date.strftime("%Y-%m-%d") if hasattr(f.date, "strftime") else str(f.date)
            rows.append(
                f"<tr><td>{dt_str}</td>"
                f"<td>{f.ewma_vol:.4f}</td>"
                f"<td>{f.garch_vol:.4f}</td>"
                f"<td>{f.blended_vol:.4f}</td>"
                f"<td>{f.rv:.4f}</td>"
                f"<td>{f.iv if f.iv is not None else '-'}</td>"
                f"<td>{f.iv_rv_spread if f.iv_rv_spread is not None else '-'}</td>"
                f"<td class='regime-{f.regime.value}'>{f.regime.value.upper()}</td></tr>"
            )

        # Regime summary
        regimes = [f.regime for f in forecasts]
        regime_counts = {r: regimes.count(r) for r in VolRegime if regimes.count(r) > 0}
        regime_summary = " | ".join(f"{r.value.upper()}: {c}" for r, c in regime_counts.items())

        # Accuracy
        acc = self.accuracy_stats()
        acc_html = ""
        if acc["n"] > 0:
            acc_html = f"""
            <h2>Forecast Accuracy</h2>
            <table><tr><th>MAE</th><th>RMSE</th><th>Bias</th><th>N</th></tr>
            <tr><td>{acc['mae']:.4f}</td><td>{acc['rmse']:.4f}</td>
            <td>{acc['bias']:.4f}</td><td>{int(acc['n'])}</td></tr></table>
            """

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Volatility Forecast Report</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 2rem; background: #f5f5f5; }}
h1 {{ color: #1a1a2e; }}
h2 {{ color: #16213e; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; background: #fff; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: right; }}
th {{ background: #16213e; color: #fff; }}
tr:nth-child(even) {{ background: #f9f9f9; }}
.regime-low {{ color: #27ae60; font-weight: bold; }}
.regime-normal {{ color: #2980b9; font-weight: bold; }}
.regime-high {{ color: #e67e22; font-weight: bold; }}
.regime-extreme {{ color: #e74c3c; font-weight: bold; }}
.summary {{ background: #fff; padding: 1rem; border-radius: 8px; margin: 1rem 0; }}
</style></head><body>
<h1>Volatility Forecast Report</h1>
<div class="summary">
<p><strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p><strong>Forecasts:</strong> {len(forecasts)}</p>
<p><strong>Regime Distribution:</strong> {regime_summary}</p>
<p><strong>GARCH Params:</strong> omega={self.garch_params.omega:.2e}
   alpha={self.garch_params.alpha:.4f} beta={self.garch_params.beta:.4f}
   persistence={self.garch_params.persistence:.4f}</p>
</div>
{acc_html}
<h2>Forecast Series</h2>
<table>
<tr><th>Date</th><th>EWMA</th><th>GARCH</th><th>Blended</th>
<th>RV</th><th>IV</th><th>IV-RV</th><th>Regime</th></tr>
{''.join(rows)}
</table>
</body></html>"""

        path.write_text(html, encoding="utf-8")
        logger.info("Vol forecast report written to %s", path)
        return str(path)
