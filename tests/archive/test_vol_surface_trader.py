"""Tests for compass/vol_surface_trader.py — vol surface trading."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.vol_surface_trader import (
    BacktestResult, ButterflySpread, IVPoint, IVSurface, SkewScore,
    SurfaceSignal, TermStructureSignal, VolSurfaceTrader,
    build_surface, generate_butterflies, score_skew, term_structure_signal,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _chain(n_strikes=10, n_dtes=2, price=430, base_iv=0.20, skew=0.03):
    """Generate synthetic options chain."""
    rows = []
    strikes = np.linspace(price * 0.90, price * 1.10, n_strikes)
    dtes = [30, 60] if n_dtes >= 2 else [30]
    for dte in dtes[:n_dtes]:
        for s in strikes:
            moneyness = s / price
            # Put IV: higher for lower strikes (skew)
            put_iv = base_iv + skew * (1 - moneyness)
            call_iv = base_iv - skew * (moneyness - 1) * 0.5
            delta_put = -0.5 * (1 - moneyness) / 0.1 if moneyness < 1 else -0.05
            delta_call = 0.5 * moneyness / 1.1 if moneyness > 1 else 0.05
            rows.append({"strike": s, "dte": dte, "iv": put_iv,
                         "option_type": "put", "delta": delta_put})
            rows.append({"strike": s, "dte": dte, "iv": call_iv,
                         "option_type": "call", "delta": delta_call})
    return pd.DataFrame(rows)

def _surface(**kw):
    chain = _chain(**{k: v for k, v in kw.items() if k in ("n_strikes", "n_dtes", "price", "base_iv", "skew")})
    price = kw.get("price", 430)
    return build_surface(chain, price)

def _trades(n=100, seed=42):
    rng = np.random.RandomState(seed)
    return pd.DataFrame({
        "pnl": rng.normal(30, 150, n),
        "vix": rng.uniform(12, 35, n),
        "iv_rank": rng.uniform(10, 90, n),
        "regime": rng.choice(["bull", "bear", "neutral", "high_vol"], n),
        "win": (rng.random(n) > 0.4).astype(int),
    })

# ── Surface builder ──────────────────────────────────────────────────────

class TestBuildSurface:
    def test_returns_surface(self):
        s = _surface()
        assert isinstance(s, IVSurface)
    def test_points_populated(self):
        s = _surface(n_strikes=8, n_dtes=2)
        assert len(s.points) == 8 * 2 * 2  # strikes × dtes × put/call
    def test_atm_iv_positive(self):
        s = _surface()
        assert s.atm_iv > 0
    def test_expirations(self):
        s = _surface(n_dtes=2)
        assert len(s.expirations) == 2
    def test_skew_positive_with_put_skew(self):
        s = _surface(skew=0.05)
        assert s.skew_25d > 0  # puts more expensive than calls
    def test_term_slope(self):
        s = _surface(n_dtes=2)
        assert isinstance(s.term_slope, float)
    def test_curvature(self):
        s = _surface()
        assert isinstance(s.smile_curvature, float)
    def test_empty_chain(self):
        s = build_surface(pd.DataFrame(), 430)
        assert s.atm_iv == 0
        assert len(s.points) == 0

# ── Skew scorer ──────────────────────────────────────────────────────────

class TestSkewScore:
    def test_returns_score(self):
        s = _surface(skew=0.04)
        score = score_skew(s)
        assert isinstance(score, SkewScore)
    def test_score_range(self):
        s = _surface(skew=0.04)
        score = score_skew(s)
        assert -1 <= score.score <= 1
    def test_high_skew_positive_score(self):
        """Higher skew param → higher skew_25d → more positive score."""
        s_high = _surface(skew=0.08)
        s_low = _surface(skew=0.01)
        sc_high = score_skew(s_high)
        sc_low = score_skew(s_low)
        assert sc_high.score > sc_low.score
    def test_signal_is_valid(self):
        s = _surface(skew=0.04)
        score = score_skew(s)
        assert score.signal in ("sell_puts", "sell_calls", "neutral")
    def test_historical_percentile(self):
        s = _surface(skew=0.05)
        hist = np.random.RandomState(42).normal(0.03, 0.02, 200)
        score = score_skew(s, historical_skews=hist)
        assert 0 <= score.percentile <= 1
    def test_confidence_range(self):
        s = _surface(skew=0.06)
        score = score_skew(s)
        assert 0 <= score.confidence <= 1

# ── Term structure signal ────────────────────────────────────────────────

class TestTermStructure:
    def test_returns_signal(self):
        s = _surface(n_dtes=2)
        sig = term_structure_signal(s)
        assert isinstance(sig, TermStructureSignal)
    def test_contango_detected(self):
        s = IVSurface(430, "", [], [30, 60], [], 0.20, 0, 0, 0.03, 0)
        sig = term_structure_signal(s, contango_threshold=0.10)
        assert sig.regime in ("steep_contango", "mild_contango")
    def test_backwardation_detected(self):
        s = IVSurface(430, "", [], [30, 60], [], 0.20, 0, 0, -0.05, 0)
        sig = term_structure_signal(s, contango_threshold=0.10)
        assert sig.regime == "backwardation"
        assert sig.signal == "hedge"
    def test_flat_neutral(self):
        s = IVSurface(430, "", [], [30], [], 0.20, 0, 0, 0.001, 0)
        sig = term_structure_signal(s, contango_threshold=0.10)
        assert sig.regime == "flat"
    def test_slope_pct(self):
        s = IVSurface(430, "", [], [30, 60], [], 0.20, 0, 0, 0.04, 0)
        sig = term_structure_signal(s)
        assert sig.slope_pct == pytest.approx(0.04 / 0.20, abs=0.01)

# ── Butterfly generator ──────────────────────────────────────────────────

class TestButterflies:
    def test_generates_butterflies(self):
        s = _surface(n_strikes=20, skew=0.05)
        bfs = generate_butterflies(s, wing_widths=[5.0])
        assert isinstance(bfs, list)
    def test_butterfly_fields(self):
        s = _surface(n_strikes=20, skew=0.05)
        bfs = generate_butterflies(s, wing_widths=[5.0], min_edge=0.001)
        if bfs:
            b = bfs[0]
            assert b.lower_strike < b.center_strike < b.upper_strike
            assert b.wing_width == 5.0
    def test_sorted_by_edge(self):
        s = _surface(n_strikes=20, skew=0.06)
        bfs = generate_butterflies(s, wing_widths=[5.0, 10.0], min_edge=0.001)
        if len(bfs) >= 2:
            assert abs(bfs[0].iv_edge) >= abs(bfs[1].iv_edge)
    def test_direction(self):
        s = _surface(n_strikes=20, skew=0.06)
        bfs = generate_butterflies(s, min_edge=0.001)
        for b in bfs:
            assert b.direction in ("sell_butterfly", "buy_butterfly")
    def test_no_butterflies_flat_surface(self):
        s = _surface(skew=0.0)
        bfs = generate_butterflies(s, min_edge=0.05)
        assert len(bfs) == 0

# ── VolSurfaceTrader ─────────────────────────────────────────────────────

class TestVolSurfaceTrader:
    def test_analyze_returns_signal(self):
        chain = _chain()
        trader = VolSurfaceTrader(chain, 430)
        sig = trader.analyze()
        assert isinstance(sig, SurfaceSignal)
    def test_action_valid(self):
        trader = VolSurfaceTrader(_chain(), 430)
        sig = trader.analyze()
        assert sig.action in ("aggressive_sell", "normal_sell", "flat", "reduce", "hedge")
    def test_composite_range(self):
        trader = VolSurfaceTrader(_chain(), 430)
        sig = trader.analyze()
        assert -1 <= sig.composite_score <= 1
    def test_regime_overlay_crash(self):
        trader = VolSurfaceTrader(_chain(skew=0.06), 430, regime="crash")
        sig = trader.analyze()
        # Crash should push composite negative
        trader2 = VolSurfaceTrader(_chain(skew=0.06), 430, regime="bull")
        sig2 = trader2.analyze()
        assert sig.composite_score < sig2.composite_score
    def test_high_skew_aggressive(self):
        trader = VolSurfaceTrader(_chain(skew=0.08, base_iv=0.25), 430, regime="bull")
        sig = trader.analyze()
        assert sig.composite_score > 0
    def test_surface_stored(self):
        trader = VolSurfaceTrader(_chain(), 430)
        trader.analyze()
        assert trader.surface is not None
    def test_signal_has_components(self):
        trader = VolSurfaceTrader(_chain(), 430)
        sig = trader.analyze()
        assert sig.skew is not None
        assert sig.term is not None
        assert isinstance(sig.butterflies, list)

# ── Backtest ─────────────────────────────────────────────────────────────

class TestBacktest:
    def test_returns_result(self):
        trader = VolSurfaceTrader(_chain(), 430)
        bt = trader.backtest(_trades())
        assert isinstance(bt, BacktestResult)
    def test_trade_counts(self):
        trader = VolSurfaceTrader(_chain(), 430)
        bt = trader.backtest(_trades(100))
        assert bt.n_trades == 100
        assert 0 < bt.n_signal_trades <= 100
    def test_win_rates_range(self):
        trader = VolSurfaceTrader(_chain(), 430)
        bt = trader.backtest(_trades())
        assert 0 <= bt.win_rate_all <= 1
        assert 0 <= bt.win_rate_signal <= 1
    def test_signal_filters_crisis(self):
        """Signal should exclude high_vol/crash trades."""
        df = _trades(100)
        df["regime"] = "crash"  # all crash
        df["vix"] = 40
        trader = VolSurfaceTrader(_chain(), 430)
        bt = trader.backtest(df)
        assert bt.n_signal_trades == 0
    def test_empty_trades(self):
        trader = VolSurfaceTrader(_chain(), 430)
        bt = trader.backtest(pd.DataFrame(columns=["pnl", "vix", "iv_rank", "regime"]))
        assert bt.n_trades == 0

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_expiry(self):
        s = _surface(n_dtes=1)
        assert s.term_slope == 0  # can't compute with one DTE
    def test_two_strikes(self):
        s = _surface(n_strikes=2)
        assert s.atm_iv > 0
    def test_zero_iv(self):
        chain = pd.DataFrame({"strike": [430], "dte": [30], "iv": [0],
                               "option_type": ["put"], "delta": [-0.5]})
        s = build_surface(chain, 430)
        assert s.atm_iv == 0
