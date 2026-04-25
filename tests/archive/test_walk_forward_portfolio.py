"""Tests for compass/walk_forward_portfolio.py — walk-forward validation."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.walk_forward_portfolio import (
    FoldResult, ValidationResult, WFConfig, WalkForwardValidator,
    _cagr, _max_dd, _sharpe,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _trades(n=300, seed=42):
    rng = np.random.RandomState(seed)
    years = np.concatenate([
        np.full(50, 2020), np.full(70, 2021), np.full(50, 2022),
        np.full(50, 2023), np.full(50, 2024), np.full(30, 2025),
    ])[:n]
    dates = pd.bdate_range("2020-01-02", periods=n)
    return pd.DataFrame({
        "entry_date": dates,
        "exit_date": dates + pd.Timedelta(days=7),
        "year": years.astype(int),
        "pnl": rng.normal(50, 200, n),
        "win": (rng.random(n) > 0.4).astype(int),
        "vix": rng.uniform(12, 35, n),
        "rsi_14": rng.uniform(20, 80, n),
        "iv_rank": rng.uniform(10, 90, n),
        "dte_at_entry": rng.randint(7, 45, n),
        "momentum_5d_pct": rng.normal(0, 2, n),
        "momentum_10d_pct": rng.normal(0, 2, n),
        "vix_percentile_20d": rng.uniform(10, 90, n),
        "vix_percentile_50d": rng.uniform(10, 90, n),
        "vix_percentile_100d": rng.uniform(10, 90, n),
        "spy_price": 400 + rng.normal(0, 20, n),
        "dist_from_ma20_pct": rng.normal(0, 2, n),
        "dist_from_ma50_pct": rng.normal(0, 3, n),
        "dist_from_ma80_pct": rng.normal(0, 4, n),
        "dist_from_ma200_pct": rng.normal(0, 5, n),
        "ma20_slope_ann_pct": rng.normal(10, 20, n),
        "ma50_slope_ann_pct": rng.normal(10, 20, n),
        "realized_vol_atr20": rng.uniform(8, 25, n),
        "realized_vol_5d": rng.uniform(5, 30, n),
        "realized_vol_10d": rng.uniform(5, 25, n),
        "realized_vol_20d": rng.uniform(5, 20, n),
        "net_credit": rng.uniform(0.5, 3, n),
        "spread_width": 5.0,
        "max_loss_per_unit": rng.uniform(2, 5, n),
        "regime": rng.choice(["bull", "bear", "neutral", "high_vol"], n),
        "contracts": rng.randint(1, 5, n),
        "hold_days": rng.randint(3, 20, n),
        "day_of_week": rng.randint(0, 5, n),
        "days_since_last_trade": rng.uniform(1, 20, n),
    })

def _validator(n=300, seed=42, **kw):
    return WalkForwardValidator(_trades(n, seed), WFConfig(**kw))

# ── Core metric tests ────────────────────────────────────────────────────

class TestMetrics:
    def test_sharpe_nonzero(self):
        rets = np.random.RandomState(42).normal(0.001, 0.01, 100)
        assert _sharpe(rets) != 0  # nonzero with drift + noise
    def test_sharpe_zero_std(self):
        assert _sharpe(np.ones(10)) == 0.0
    def test_max_dd_negative(self):
        eq = np.array([100, 105, 95, 90, 100])
        assert _max_dd(eq) < 0
    def test_cagr_positive(self):
        eq = np.array([100, 110, 120])
        assert _cagr(eq, 365) > 0
    def test_cagr_zero_days(self):
        assert _cagr(np.array([100, 110]), 0) == 0.0

# ── Fold structure ───────────────────────────────────────────────────────

class TestFolds:
    def test_validate_returns_result(self):
        v = _validator()
        r = v.validate()
        assert isinstance(r, ValidationResult)
    def test_multiple_folds(self):
        v = _validator()
        r = v.validate()
        assert r.n_folds >= 3  # 2021-2025 = 5 folds (minus first year)
    def test_fold_has_train_years(self):
        v = _validator()
        r = v.validate()
        for f in r.folds:
            assert len(f.train_years) >= 1
            assert f.test_year not in f.train_years
    def test_fold_expanding_window(self):
        """Each fold trains on more data than the previous."""
        v = _validator()
        r = v.validate()
        for i in range(1, len(r.folds)):
            assert r.folds[i].n_train >= r.folds[i-1].n_train
    def test_no_look_ahead(self):
        """Test year is always after all training years."""
        v = _validator()
        r = v.validate()
        for f in r.folds:
            assert f.test_year > max(f.train_years)

# ── Per-fold metrics ─────────────────────────────────────────────────────

class TestFoldMetrics:
    def test_is_metrics_populated(self):
        v = _validator()
        r = v.validate()
        for f in r.folds:
            assert isinstance(f.is_sharpe, float)
            assert isinstance(f.is_pnl, float)
    def test_oos_metrics_populated(self):
        v = _validator()
        r = v.validate()
        for f in r.folds:
            assert isinstance(f.oos_sharpe, float)
            assert isinstance(f.oos_pnl, float)
    def test_sharpe_ratio_computed(self):
        v = _validator()
        r = v.validate()
        for f in r.folds:
            assert isinstance(f.sharpe_ratio, float)
    def test_auc_range(self):
        v = _validator()
        r = v.validate()
        for f in r.folds:
            assert 0 <= f.auc <= 1
    def test_dd_limit_checked(self):
        v = _validator()
        r = v.validate()
        for f in r.folds:
            assert isinstance(f.dd_within_limit, bool)
    def test_ml_filter_reduces(self):
        v = _validator()
        r = v.validate()
        for f in r.folds:
            assert f.ml_filtered_n <= f.n_test

# ── Aggregate results ────────────────────────────────────────────────────

class TestAggregates:
    def test_combined_oos_sharpe(self):
        v = _validator()
        r = v.validate()
        assert isinstance(r.combined_oos_sharpe, float)
    def test_combined_oos_pnl(self):
        v = _validator()
        r = v.validate()
        assert isinstance(r.combined_oos_pnl, float)
    def test_avg_ratios(self):
        v = _validator()
        r = v.validate()
        assert isinstance(r.avg_sharpe_ratio, float)
        assert isinstance(r.avg_cagr_ratio, float)
    def test_worst_fold_identified(self):
        v = _validator()
        r = v.validate()
        assert r.worst_fold_year in [f.test_year for f in r.folds]
    def test_verdict_string(self):
        v = _validator()
        r = v.validate()
        assert len(r.verdict) > 0
        assert "✓" in r.verdict or "✗" in r.verdict
    def test_passed_is_bool(self):
        v = _validator()
        r = v.validate()
        assert isinstance(r.passed, bool)

# ── Year attribution ─────────────────────────────────────────────────────

class TestYearAttribution:
    def test_attribution_per_year(self):
        v = _validator()
        r = v.validate()
        assert len(r.year_attribution) >= 1
    def test_attribution_has_metrics(self):
        v = _validator()
        r = v.validate()
        for year, data in r.year_attribution.items():
            assert "oos_sharpe" in data
            assert "oos_pnl" in data
            assert "auc" in data
    def test_attribution_years_match_folds(self):
        v = _validator()
        r = v.validate()
        fold_years = {f.test_year for f in r.folds}
        attr_years = set(r.year_attribution.keys())
        assert fold_years == attr_years

# ── Degradation checks ──────────────────────────────────────────────────

class TestDegradation:
    def test_sharpe_ratio_range(self):
        v = _validator()
        r = v.validate()
        # Could be negative (OOS worse) but should be finite
        for f in r.folds:
            assert np.isfinite(f.sharpe_ratio)
    def test_dd_within_limit_flag(self):
        v = _validator()
        r = v.validate()
        assert isinstance(r.all_folds_dd_ok, bool)

# ── from_csv ─────────────────────────────────────────────────────────────

class TestFromCSV:
    def test_from_csv(self, tmp_path):
        df = _trades(100)
        csv = tmp_path / "trades.csv"
        df.to_csv(csv, index=False)
        v = WalkForwardValidator.from_csv(str(csv))
        r = v.validate()
        assert isinstance(r, ValidationResult)

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_two_years(self):
        df = _trades(100)
        df["year"] = np.where(np.arange(100) < 50, 2023, 2024)
        v = WalkForwardValidator(df)
        r = v.validate()
        assert r.n_folds >= 1
    def test_single_year(self):
        df = _trades(50)
        df["year"] = 2024
        v = WalkForwardValidator(df)
        r = v.validate()
        assert r.n_folds == 0
    def test_no_year_column(self):
        df = _trades(100)
        df = df.drop(columns=["year"])
        v = WalkForwardValidator(df)
        # Should auto-derive year from entry_date
        r = v.validate()
        assert isinstance(r, ValidationResult)
