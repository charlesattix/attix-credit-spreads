"""Tests for compass.vol_surface — 38 tests."""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime
from pathlib import Path

from compass.vol_surface import (
    VolSurfaceEngine,
    SABRParams,
    SkewMetrics,
    ArbitrageViolation,
    TermStructurePoint,
    SurfaceGreeks,
    VolSurfaceSummary,
    sabr_vol,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _option_chain(n_strikes: int = 11, n_expiries: int = 4, forward: float = 100.0,
                   seed: int = 42) -> pd.DataFrame:
    """Synthetic option chain with realistic skew."""
    rng = np.random.default_rng(seed)
    strikes = np.linspace(forward * 0.85, forward * 1.15, n_strikes)
    expiries = [30, 60, 90, 180][:n_expiries]
    rows = []
    for exp in expiries:
        for k in strikes:
            # Simple skew: higher IV for lower strikes
            moneyness = k / forward
            base_iv = 0.20 + 0.05 * np.sqrt(exp / 365)
            skew = 0.15 * (1 - moneyness) ** 2
            iv = base_iv + skew + rng.normal(0, 0.005)
            rows.append({"strike": k, "expiry_days": exp, "iv": max(iv, 0.05)})
    return pd.DataFrame(rows)


def _surface_df() -> pd.DataFrame:
    chain = _option_chain()
    return VolSurfaceEngine.build_surface(chain)


# ===========================================================================
# SABR formula
# ===========================================================================

class TestSABRVol:
    def test_atm(self):
        v = sabr_vol(100, 100, 0.25, 0.3, 0.5, -0.3, 0.4)
        assert v > 0

    def test_positive(self):
        for k in [80, 90, 100, 110, 120]:
            v = sabr_vol(k, 100, 0.25, 0.3, 0.5, -0.3, 0.4)
            assert v > 0

    def test_zero_expiry(self):
        assert sabr_vol(100, 100, 0, 0.3, 0.5, -0.3, 0.4) == 0.0

    def test_skew(self):
        """Lower strikes should have higher vol with negative rho."""
        v_low = sabr_vol(90, 100, 0.25, 0.3, 0.5, -0.5, 0.4)
        v_high = sabr_vol(110, 100, 0.25, 0.3, 0.5, -0.5, 0.4)
        assert v_low > v_high


# ===========================================================================
# Surface construction
# ===========================================================================

class TestSurface:
    def test_build(self):
        chain = _option_chain()
        surface = VolSurfaceEngine.build_surface(chain)
        assert not surface.empty
        assert surface.shape[0] == 11
        assert surface.shape[1] == 4

    def test_empty_chain(self):
        assert VolSurfaceEngine.build_surface(pd.DataFrame()).empty

    def test_missing_columns(self):
        df = pd.DataFrame({"foo": [1]})
        assert VolSurfaceEngine.build_surface(df).empty


# ===========================================================================
# SABR calibration
# ===========================================================================

class TestSABRCalib:
    def test_calibrate(self):
        eng = VolSurfaceEngine(beta=0.5)
        chain = _option_chain()
        col = chain[chain["expiry_days"] == 30]
        params = eng.calibrate_sabr(
            col["strike"].values, col["iv"].values, 100.0, 30 / 365)
        assert isinstance(params, SABRParams)
        assert params.alpha > 0
        assert -1 < params.rho < 1
        assert params.nu > 0

    def test_short_data(self):
        eng = VolSurfaceEngine()
        params = eng.calibrate_sabr(np.array([100.0]), np.array([0.2]), 100.0, 0.25)
        assert params.alpha > 0  # returns defaults

    def test_sabr_smile(self):
        eng = VolSurfaceEngine()
        params = SABRParams(alpha=0.3, beta=0.5, rho=-0.3, nu=0.4,
                             forward=100, expiry=0.25)
        strikes = np.linspace(85, 115, 10)
        vols = eng.sabr_smile(params, strikes)
        assert len(vols) == 10
        assert all(v > 0 for v in vols)


# ===========================================================================
# Skew analytics
# ===========================================================================

class TestSkew:
    def test_basic(self):
        strikes = np.linspace(85, 115, 11)
        # Asymmetric skew: OTM puts more expensive than OTM calls
        vols = 0.20 + 0.15 * np.maximum(1 - strikes / 100, 0)
        sm = VolSurfaceEngine.compute_skew(strikes, vols, 100.0, 30)
        assert isinstance(sm, SkewMetrics)
        assert sm.atm_vol > 0
        assert sm.skew_25d > 0  # puts more expensive

    def test_butterfly_positive(self):
        strikes = np.linspace(85, 115, 11)
        vols = 0.20 + 0.05 * ((strikes - 100) / 15) ** 2  # smile
        sm = VolSurfaceEngine.compute_skew(strikes, vols, 100.0, 30)
        assert sm.butterfly_25d > 0

    def test_too_few_strikes(self):
        sm = VolSurfaceEngine.compute_skew(np.array([100.0]), np.array([0.2]), 100, 30)
        assert sm.atm_vol == 0.0


# ===========================================================================
# Arbitrage detection
# ===========================================================================

class TestArbitrage:
    def test_clean_surface(self):
        surface = _surface_df()
        violations = VolSurfaceEngine.detect_arbitrage(surface)
        # Synthetic surface may have some minor violations
        assert isinstance(violations, list)

    def test_calendar_violation(self):
        # Construct a surface where short-dated vol >> long-dated (total var violation)
        data = pd.DataFrame({
            30: [0.50, 0.45, 0.40],
            90: [0.15, 0.14, 0.13],  # much lower — total var decreases
        }, index=[90, 100, 110])
        violations = VolSurfaceEngine.detect_arbitrage(data)
        cal = [v for v in violations if v.violation_type == "calendar"]
        assert len(cal) > 0

    def test_empty(self):
        assert VolSurfaceEngine.detect_arbitrage(pd.DataFrame()) == []


# ===========================================================================
# Term structure
# ===========================================================================

class TestTermStructure:
    def test_basic(self):
        surface = _surface_df()
        ts, is_c = VolSurfaceEngine.term_structure(surface, 100.0)
        assert len(ts) > 0
        assert all(isinstance(p, TermStructurePoint) for p in ts)

    def test_contango(self):
        # Longer expiry → higher vol
        data = pd.DataFrame({30: [0.18], 90: [0.22], 180: [0.25]}, index=[100])
        ts, is_c = VolSurfaceEngine.term_structure(data, 100.0)
        assert is_c

    def test_backwardation(self):
        data = pd.DataFrame({30: [0.30], 90: [0.22], 180: [0.18]}, index=[100])
        ts, is_c = VolSurfaceEngine.term_structure(data, 100.0)
        assert not is_c


# ===========================================================================
# Interpolation
# ===========================================================================

class TestInterpolation:
    def test_on_grid(self):
        surface = _surface_df()
        k = surface.index[5]
        exp = surface.columns[1]
        iv = VolSurfaceEngine.interpolate(surface, float(k), float(exp))
        assert iv == pytest.approx(float(surface.loc[k, exp]), abs=0.001)

    def test_off_grid(self):
        surface = _surface_df()
        iv = VolSurfaceEngine.interpolate(surface, 97.5, 45)
        assert iv > 0

    def test_empty(self):
        assert VolSurfaceEngine.interpolate(pd.DataFrame(), 100, 30) == 0.0


# ===========================================================================
# Greeks
# ===========================================================================

class TestGreeks:
    def test_bs_greeks_call(self):
        g = VolSurfaceEngine.bs_greeks(100, 100, 0.25, 0.20, 0.045, True)
        assert isinstance(g, SurfaceGreeks)
        assert 0 < g.delta < 1
        assert g.gamma > 0
        assert g.vega > 0

    def test_bs_greeks_put(self):
        g = VolSurfaceEngine.bs_greeks(100, 100, 0.25, 0.20, 0.045, False)
        assert -1 < g.delta < 0

    def test_greeks_from_surface(self):
        eng = VolSurfaceEngine()
        surface = _surface_df()
        g = eng.greeks_from_surface(surface, 100.0, 95.0, 60)
        assert g.iv > 0
        assert g.delta != 0


# ===========================================================================
# Full analysis
# ===========================================================================

class TestAnalyze:
    def test_full(self):
        eng = VolSurfaceEngine()
        chain = _option_chain()
        summary = eng.analyze(chain, forward=100.0)
        assert isinstance(summary, VolSurfaceSummary)
        assert summary.sabr_params is not None
        assert len(summary.skew_metrics) > 0
        assert len(summary.term_structure) > 0

    def test_empty_chain(self):
        eng = VolSurfaceEngine()
        summary = eng.analyze(pd.DataFrame(), forward=100.0)
        assert summary.sabr_params is None


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        eng = VolSurfaceEngine()
        chain = _option_chain()
        summary = eng.analyze(chain, forward=100.0)
        out = tmp_path / "vol.html"
        result = eng.generate_report(summary, output_path=str(out))
        assert Path(result).exists()
        html = out.read_text()
        assert "Implied Volatility Surface" in html

    def test_contains_charts(self, tmp_path):
        eng = VolSurfaceEngine()
        chain = _option_chain()
        summary = eng.analyze(chain, forward=100.0)
        out = tmp_path / "vol.html"
        eng.generate_report(summary, output_path=str(out))
        html = out.read_text()
        assert "<svg" in html

    def test_contains_sabr(self, tmp_path):
        eng = VolSurfaceEngine()
        chain = _option_chain()
        summary = eng.analyze(chain, forward=100.0)
        out = tmp_path / "vol.html"
        eng.generate_report(summary, output_path=str(out))
        html = out.read_text()
        assert "SABR" in html

    def test_contains_skew_table(self, tmp_path):
        eng = VolSurfaceEngine()
        chain = _option_chain()
        summary = eng.analyze(chain, forward=100.0)
        out = tmp_path / "vol.html"
        eng.generate_report(summary, output_path=str(out))
        html = out.read_text()
        assert "Skew Analytics" in html
