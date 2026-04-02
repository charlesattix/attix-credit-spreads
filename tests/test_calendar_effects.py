"""Tests for compass.calendar_effects — 34 tests."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from compass.calendar_effects import (
    CalendarEffects, CalendarScore, EffectStats,
    CalendarBacktestResult, FOMC_DATES,
)


def _dates(n=756):
    return pd.bdate_range("2023-01-02", periods=n)


def _returns(n=756, seed=42):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.0004, 0.01, n), index=_dates(n))


# ===========================================================================
# Individual effects
# ===========================================================================

class TestTurnOfMonth:
    def test_first_days(self):
        assert CalendarEffects.is_turn_of_month(pd.Timestamp("2024-01-02")) == 1.0

    def test_last_days(self):
        assert CalendarEffects.is_turn_of_month(pd.Timestamp("2024-01-30")) == 1.0

    def test_mid_month(self):
        assert CalendarEffects.is_turn_of_month(pd.Timestamp("2024-01-15")) == 0.0


class TestOpEx:
    def test_opex_week(self):
        # Third Friday of Jan 2024 is Jan 19
        score = CalendarEffects.is_opex_week(pd.Timestamp("2024-01-18"))
        assert score < 0  # bearish around opex

    def test_not_opex(self):
        assert CalendarEffects.is_opex_week(pd.Timestamp("2024-01-08")) == 0.0


class TestFOMC:
    def test_day_before_fomc(self):
        # 2024-01-31 is FOMC, so 2024-01-30 is day before
        assert CalendarEffects.is_fomc_drift(pd.Timestamp("2024-01-30")) == 1.0

    def test_fomc_day(self):
        assert CalendarEffects.is_fomc_drift(pd.Timestamp("2024-01-31")) == 0.5

    def test_normal_day(self):
        assert CalendarEffects.is_fomc_drift(pd.Timestamp("2024-01-15")) == 0.0

    def test_fomc_dates_populated(self):
        assert len(FOMC_DATES) > 50


class TestQuadWitching:
    def test_quad_friday(self):
        # Third Friday of Mar 2024 is Mar 22 (Mar 1 is Friday, +7=8, +14=22)
        score = CalendarEffects.is_quad_witching(pd.Timestamp("2024-03-22"))
        assert score < 0

    def test_non_quad_month(self):
        assert CalendarEffects.is_quad_witching(pd.Timestamp("2024-02-15")) == 0.0


class TestSantaRally:
    def test_late_december(self):
        assert CalendarEffects.is_santa_rally(pd.Timestamp("2024-12-26")) == 1.0

    def test_early_january(self):
        assert CalendarEffects.is_santa_rally(pd.Timestamp("2025-01-02")) == 0.8

    def test_normal(self):
        assert CalendarEffects.is_santa_rally(pd.Timestamp("2024-06-15")) == 0.0


class TestSellInMay:
    def test_summer(self):
        assert CalendarEffects.is_sell_in_may(pd.Timestamp("2024-07-15")) == -0.3

    def test_winter(self):
        assert CalendarEffects.is_sell_in_may(pd.Timestamp("2024-01-15")) == 0.3


class TestMondayEffect:
    def test_monday(self):
        assert CalendarEffects.is_monday_effect(pd.Timestamp("2024-01-08")) == -0.4  # Monday

    def test_friday(self):
        assert CalendarEffects.is_monday_effect(pd.Timestamp("2024-01-12")) == 0.2  # Friday

    def test_wednesday(self):
        assert CalendarEffects.is_monday_effect(pd.Timestamp("2024-01-10")) == 0.0


class TestMonthEnd:
    def test_last_days(self):
        assert CalendarEffects.is_month_end(pd.Timestamp("2024-01-30")) > 0

    def test_mid_month(self):
        assert CalendarEffects.is_month_end(pd.Timestamp("2024-01-15")) == 0.0


# ===========================================================================
# Composite score
# ===========================================================================

class TestComposite:
    def test_score_day(self):
        ce = CalendarEffects()
        s = ce.score_day(pd.Timestamp("2024-01-02"))
        assert isinstance(s, CalendarScore)
        assert -1 <= s.composite <= 1

    def test_score_series(self):
        ce = CalendarEffects()
        df = ce.score_series(_dates(100))
        assert len(df) == 100
        assert "composite" in df.columns
        assert df["composite"].min() >= -1
        assert df["composite"].max() <= 1

    def test_signal_series(self):
        ce = CalendarEffects()
        sig = ce.signal_series(_dates(200))
        assert set(sig.dropna().unique()).issubset({-1.0, 0.0, 1.0})

    def test_custom_weights(self):
        ce = CalendarEffects(weights={"tom": 1.0, "opex": 0, "fomc": 0,
                                        "quad_witch": 0, "santa": 0,
                                        "sell_may": 0, "monday": 0, "month_end": 0})
        s = ce.score_day(pd.Timestamp("2024-01-02"))  # day 2 = ToM
        assert s.composite == 1.0


# ===========================================================================
# Significance testing
# ===========================================================================

class TestEffectSignificance:
    def test_produces_stats(self):
        ce = CalendarEffects()
        stats = ce.test_effects(_dates(500), _returns(500))
        assert len(stats) == 8
        assert all(isinstance(s, EffectStats) for s in stats)

    def test_stats_structure(self):
        ce = CalendarEffects()
        stats = ce.test_effects(_dates(500), _returns(500))
        for s in stats:
            assert s.n_days >= 0
            assert 0 <= s.p_value <= 1


# ===========================================================================
# Backtest
# ===========================================================================

class TestBacktest:
    def test_basic(self):
        ce = CalendarEffects()
        result = ce.backtest(_dates(500), _returns(500))
        assert isinstance(result, CalendarBacktestResult)
        assert result.n_total_days == 500
        assert result.n_active_days > 0

    def test_sharpe_finite(self):
        ce = CalendarEffects()
        result = ce.backtest(_dates(500), _returns(500))
        assert np.isfinite(result.sharpe)

    def test_effect_stats_included(self):
        ce = CalendarEffects()
        result = ce.backtest(_dates(500), _returns(500))
        assert len(result.effect_stats) == 8


# ===========================================================================
# Overlay filter
# ===========================================================================

class TestOverlay:
    def test_blocks_bad_days(self):
        ce = CalendarEffects()
        dates = _dates(500)  # need enough days to hit negatives
        base = pd.Series(1.0, index=dates)
        filtered = ce.overlay_filter(base, dates, block_threshold=-0.05)
        assert (filtered == 0).sum() > 0  # some days should be blocked

    def test_preserves_good_days(self):
        ce = CalendarEffects()
        dates = _dates(200)
        base = pd.Series(1.0, index=dates)
        filtered = ce.overlay_filter(base, dates, block_threshold=-0.1)
        assert (filtered == 1.0).sum() > (filtered == 0).sum()  # most days preserved


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        ce = CalendarEffects()
        dates = _dates(500)
        result = ce.backtest(dates, _returns(500))
        scores = ce.score_series(dates)
        out = tmp_path / "cal.html"
        path = ce.generate_report(result, scores, str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Calendar Effects" in html
        assert "<svg" in html
        assert "Significance" in html
