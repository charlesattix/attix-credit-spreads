"""Tests for compass/greeks_trade_sizer.py — Greeks-based trade sizing."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.greeks_trade_sizer import (
    BacktestResult, BacktestTrade, GreeksTradeSizer, PortfolioState,
    SizerConfig, SizingResult, TradeGreeks, estimate_trade_greeks,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _tg(**kw) -> TradeGreeks:
    defaults = dict(delta=5.0, gamma=0.5, theta=8.0, vega=3.0,
                    premium=150, dte=30, iv=0.22, underlying_price=430,
                    strike=420, spread_strike=410)
    defaults.update(kw)
    return TradeGreeks(**defaults)

def _port(**kw) -> PortfolioState:
    return PortfolioState(**kw)

def _sizer(**kw) -> GreeksTradeSizer:
    return GreeksTradeSizer(SizerConfig(**kw))

def _trades_df(n=100, seed=42):
    rng = np.random.RandomState(seed)
    return pd.DataFrame({
        "spy_price": 430 + rng.normal(0, 5, n),
        "short_strike": 420 + rng.normal(0, 3, n),
        "spread_width": 5.0,
        "dte_at_entry": rng.randint(7, 45, n),
        "iv_rank": rng.uniform(15, 80, n),
        "regime": rng.choice(["bull", "bear", "neutral", "high_vol"], n),
        "pnl": rng.normal(50, 200, n),
        "contracts": rng.randint(1, 5, n),
        "win": (rng.random(n) > 0.4).astype(int),
    })

# ── Trade Greeks estimation ──────────────────────────────────────────────

class TestEstimateGreeks:
    def test_returns_trade_greeks(self):
        tg = estimate_trade_greeks(430, 420, 30, 0.22, 410)
        assert isinstance(tg, TradeGreeks)
    def test_short_put_positive_theta(self):
        tg = estimate_trade_greeks(430, 420, 30, 0.22, 410, "put", "short")
        assert tg.theta > 0
    def test_spread_less_delta(self):
        naked = estimate_trade_greeks(430, 420, 30, 0.22, None, "put", "short")
        spread = estimate_trade_greeks(430, 420, 30, 0.22, 410, "put", "short")
        assert abs(spread.delta) < abs(naked.delta)
    def test_premium_positive(self):
        tg = estimate_trade_greeks(430, 420, 30, 0.22, 410)
        assert tg.premium > 0
    def test_higher_iv_more_premium(self):
        low = estimate_trade_greeks(430, 420, 30, 0.15, 410, "put", "short")
        high = estimate_trade_greeks(430, 420, 30, 0.35, 410, "put", "short")
        assert high.premium > low.premium

# ── Greeks sizing ────────────────────────────────────────────────────────

class TestGreeksSizing:
    def test_returns_result(self):
        s = _sizer()
        r = s.size_trade(_tg(), _port())
        assert isinstance(r, SizingResult)
        assert r.method == "greeks"
    def test_positive_contracts(self):
        s = _sizer(target_theta_daily=200)
        r = s.size_trade(_tg(theta=8), _port())
        assert r.contracts >= 1
    def test_scales_with_theta_gap(self):
        """More theta needed → more contracts."""
        s = _sizer(target_theta_daily=200)
        r1 = s.size_trade(_tg(theta=8), _port(total_theta=0))
        r2 = s.size_trade(_tg(theta=8), _port(total_theta=150))
        assert r1.contracts >= r2.contracts
    def test_gamma_cap(self):
        s = _sizer(max_gamma=5, target_theta_daily=500)
        r = s.size_trade(_tg(gamma=2.0, theta=5), _port())
        assert r.contracts <= 3  # 5 / 2.0 ≈ 2-3
        assert r.capped_by == "gamma"
    def test_vega_cap(self):
        s = _sizer(max_vega=10, target_theta_daily=500)
        r = s.size_trade(_tg(vega=5.0, theta=5), _port())
        assert r.contracts <= 2
        assert r.capped_by == "vega"
    def test_delta_budget(self):
        s = _sizer(target_theta_daily=500, delta_budget={"bear": 5.0})
        r = s.size_trade(_tg(delta=3.0, theta=5), _port(regime="bear"))
        assert r.contracts <= 2  # 5 / 3 ≈ 1-2
    def test_max_contracts_cap(self):
        s = _sizer(max_contracts=5, target_theta_daily=10000)
        r = s.size_trade(_tg(theta=1), _port())
        assert r.contracts <= 5
    def test_zero_theta_trade(self):
        s = _sizer()
        r = s.size_trade(_tg(theta=0), _port())
        assert r.contracts == 0
    def test_theta_already_met(self):
        s = _sizer(target_theta_daily=100)
        r = s.size_trade(_tg(theta=5), _port(total_theta=120))
        assert r.contracts == 0
    def test_pct_of_target(self):
        s = _sizer(target_theta_daily=200)
        r = s.size_trade(_tg(theta=10), _port())
        assert 0 < r.pct_of_theta_target <= 1.5

# ── Fixed sizing ─────────────────────────────────────────────────────────

class TestFixedSizing:
    def test_returns_fixed(self):
        s = _sizer()
        assert s.size_fixed(3) == 3
    def test_default(self):
        s = _sizer()
        assert s.size_fixed() == 2

# ── Kelly sizing ─────────────────────────────────────────────────────────

class TestKellySizing:
    def test_returns_int(self):
        s = _sizer()
        n = s.size_kelly(0.65, 150, 100)
        assert isinstance(n, int)
    def test_higher_wr_more_contracts(self):
        s = _sizer()
        n_low = s.size_kelly(0.50, 100, 100)
        n_high = s.size_kelly(0.80, 100, 100)
        assert n_high >= n_low
    def test_capped_at_max(self):
        s = _sizer(max_contracts=5)
        n = s.size_kelly(0.95, 1000, 10)
        assert n <= 5
    def test_min_contracts(self):
        s = _sizer(min_contracts=1)
        n = s.size_kelly(0.30, 50, 200)
        assert n >= 1
    def test_zero_loss(self):
        s = _sizer()
        n = s.size_kelly(0.65, 100, 0)
        assert n >= 1

# ── Backtest ─────────────────────────────────────────────────────────────

class TestBacktest:
    def test_returns_result(self):
        s = _sizer()
        bt = s.backtest(_trades_df(50))
        assert isinstance(bt, BacktestResult)
    def test_has_trades(self):
        s = _sizer()
        bt = s.backtest(_trades_df(50))
        assert bt.n_trades == 50
    def test_greeks_fewer_breaches(self):
        """Greeks sizing should produce fewer gamma/vega breaches."""
        s = _sizer(max_gamma=20, max_vega=100)
        bt = s.backtest(_trades_df(100))
        assert bt.gamma_breaches_greeks <= bt.gamma_breaches_fixed
    def test_all_methods_have_pnl(self):
        s = _sizer()
        bt = s.backtest(_trades_df(50))
        assert isinstance(bt.greeks_pnl, float)
        assert isinstance(bt.fixed_pnl, float)
        assert isinstance(bt.kelly_pnl, float)
    def test_win_rate_range(self):
        s = _sizer()
        bt = s.backtest(_trades_df(50))
        assert 0 <= bt.greeks_win_rate <= 1
        assert 0 <= bt.fixed_win_rate <= 1
    def test_sharpe_finite(self):
        s = _sizer()
        bt = s.backtest(_trades_df(50))
        assert np.isfinite(bt.greeks_sharpe)
    def test_theta_stats(self):
        s = _sizer()
        bt = s.backtest(_trades_df(50))
        assert isinstance(bt.greeks_avg_theta, float)
        assert bt.greeks_theta_std >= 0
    def test_improvement_computed(self):
        s = _sizer()
        bt = s.backtest(_trades_df(50))
        assert isinstance(bt.sharpe_improvement_vs_fixed, float)
        assert isinstance(bt.dd_improvement_vs_fixed, float)
    def test_trade_fields(self):
        s = _sizer()
        bt = s.backtest(_trades_df(20))
        t = bt.trades[0]
        assert isinstance(t, BacktestTrade)
        assert t.contracts_greeks >= 0
    def test_empty_df(self):
        s = _sizer()
        cols = ["spy_price", "short_strike", "spread_width", "dte_at_entry",
                "iv_rank", "regime", "pnl", "contracts", "win"]
        bt = s.backtest(pd.DataFrame(columns=cols))
        assert bt.n_trades == 0

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_crash_regime_small_delta(self):
        s = _sizer(delta_budget={"crash": 0.01}, target_theta_daily=100)
        r = s.size_trade(_tg(delta=5, theta=5), _port(regime="crash"))
        # Crash has near-zero delta budget → capped at min
        assert r.contracts <= 1
    def test_very_small_theta(self):
        s = _sizer(target_theta_daily=200)
        r = s.size_trade(_tg(theta=0.001), _port())
        assert r.contracts <= s.config.max_contracts
    def test_negative_theta_trade(self):
        s = _sizer()
        r = s.size_trade(_tg(theta=-5), _port())
        assert r.contracts == 0
    def test_full_portfolio(self):
        """Portfolio already at limits."""
        s = _sizer(max_gamma=10, max_vega=50)
        r = s.size_trade(_tg(), _port(total_gamma=10, total_vega=50, total_theta=200))
        assert r.contracts == 0 or r.capped_by != ""
