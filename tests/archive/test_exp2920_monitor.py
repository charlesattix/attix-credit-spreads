"""
Tests for EXP-2920 paper-trading monitoring core.

Covers the 7 monitoring dimensions and the 4 MASTERPLAN abort triggers.
All tests are pure-function / deterministic — no broker calls.
"""

from __future__ import annotations

import numpy as np
import pytest

from compass.exp2920_monitor_core import (
    AbortCode,
    AbortSeverity,
    AbortTriggerConfig,
    AbortTriggerEvaluator,
    AggregatorConfig,
    FillQuality,
    MetricAggregator,
    StreamPnl,
    correlation_matrix,
    rolling_sharpe,
    running_max_drawdown,
    vix_ladder_state,
)


# ─────────────────────────────────────────────────────────────────────────────
# VIX ladder
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("vix, mult, zone", [
    (15.0, 1.00, "calm"),
    (22.0, 0.90, "normal"),
    (28.0, 0.75, "elevated"),
    (33.0, 0.60, "caution"),
    (38.0, 0.50, "stress"),
    (45.0, 0.35, "acute_stress"),
    (55.0, 0.25, "crisis"),
    (65.0, 0.15, "panic"),
    (80.0, 0.00, "flat"),
])
def test_vix_ladder_zones(vix, mult, zone):
    m, z = vix_ladder_state(vix)
    assert m == pytest.approx(mult)
    assert z == zone


# ─────────────────────────────────────────────────────────────────────────────
# Rolling sharpe
# ─────────────────────────────────────────────────────────────────────────────
def test_rolling_sharpe_warmup_returns_none():
    assert rolling_sharpe([0.01, 0.02], window=30) is None


def test_rolling_sharpe_positive_series():
    rng = np.random.default_rng(2920)
    series = rng.normal(loc=0.001, scale=0.005, size=60).tolist()
    s = rolling_sharpe(series, window=30)
    assert s is not None and s > 0


def test_rolling_sharpe_zero_vol_returns_zero():
    assert rolling_sharpe([0.0] * 30, window=30) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Running max drawdown
# ─────────────────────────────────────────────────────────────────────────────
def test_running_max_drawdown_monotone_up_is_zero():
    assert running_max_drawdown([1.0, 1.1, 1.2, 1.3]) == 0.0


def test_running_max_drawdown_captures_trough():
    dd = running_max_drawdown([1.0, 1.1, 1.2, 1.0, 0.96, 1.15])
    # peak 1.2 → trough 0.96 → dd = 0.2/1.2 = 0.2
    assert dd == pytest.approx(0.2, abs=1e-6)


# ─────────────────────────────────────────────────────────────────────────────
# Correlation matrix
# ─────────────────────────────────────────────────────────────────────────────
def test_correlation_matrix_shape_and_values():
    rng = np.random.default_rng(2920)
    n = 60
    a = rng.normal(size=n).tolist()
    b = (np.array(a) * 0.8 + rng.normal(scale=0.5, size=n)).tolist()
    snap = correlation_matrix({"A": a, "B": b}, window=60)
    assert "A" in snap.matrix and "B" in snap.matrix["A"]
    assert snap.matrix["A"]["B"] == snap.matrix["B"]["A"]
    assert 0.5 < snap.matrix["A"]["B"] < 1.0
    assert snap.max_abs_offdiag > 0.5


def test_correlation_matrix_warmup_returns_empty():
    snap = correlation_matrix({"A": [0.01, 0.02], "B": [0.01, 0.03]}, window=60)
    assert snap.matrix == {}


# ─────────────────────────────────────────────────────────────────────────────
# Abort trigger 1 — 12% drawdown
# ─────────────────────────────────────────────────────────────────────────────
def _ev() -> AbortTriggerEvaluator:
    return AbortTriggerEvaluator()


def _find(verdicts, code):
    for v in verdicts:
        if v.code == code:
            return v
    raise AssertionError(f"missing verdict for {code}")


def test_trigger1_fires_at_12pct():
    v = _ev().evaluate(
        trailing_dd_pct=12.5,
        rolling_sharpe_4w=3.0,
        consecutive_sharpe_breach_days=0,
        fill_quality=FillQuality(n_fills_today=10),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.DRAWDOWN_12PCT).severity == AbortSeverity.CRITICAL


def test_trigger1_ok_below_threshold():
    v = _ev().evaluate(
        trailing_dd_pct=11.9,
        rolling_sharpe_4w=3.0,
        consecutive_sharpe_breach_days=0,
        fill_quality=FillQuality(n_fills_today=10),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.DRAWDOWN_12PCT).severity == AbortSeverity.OK


# ─────────────────────────────────────────────────────────────────────────────
# Abort trigger 2 — rolling sharpe < 2 for 5 days
# ─────────────────────────────────────────────────────────────────────────────
def test_trigger2_warmup_returns_ok():
    v = _ev().evaluate(
        trailing_dd_pct=0,
        rolling_sharpe_4w=None,
        consecutive_sharpe_breach_days=0,
        fill_quality=FillQuality(n_fills_today=5),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.SHARPE_BELOW_2_5DAYS).severity == AbortSeverity.OK


def test_trigger2_warning_before_5_consecutive_days():
    v = _ev().evaluate(
        trailing_dd_pct=0,
        rolling_sharpe_4w=1.5,
        consecutive_sharpe_breach_days=3,
        fill_quality=FillQuality(n_fills_today=5),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.SHARPE_BELOW_2_5DAYS).severity == AbortSeverity.WARNING


def test_trigger2_critical_at_5_consecutive_days():
    v = _ev().evaluate(
        trailing_dd_pct=0,
        rolling_sharpe_4w=1.5,
        consecutive_sharpe_breach_days=5,
        fill_quality=FillQuality(n_fills_today=5),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.SHARPE_BELOW_2_5DAYS).severity == AbortSeverity.CRITICAL


def test_trigger2_resets_when_sharpe_recovers():
    v = _ev().evaluate(
        trailing_dd_pct=0,
        rolling_sharpe_4w=3.0,
        consecutive_sharpe_breach_days=4,   # stale counter — should not trip
        fill_quality=FillQuality(n_fills_today=5),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.SHARPE_BELOW_2_5DAYS).severity == AbortSeverity.OK


# ─────────────────────────────────────────────────────────────────────────────
# Abort trigger 3 — fill deviation
# ─────────────────────────────────────────────────────────────────────────────
def test_trigger3_fires_when_over_20pct_deviate():
    v = _ev().evaluate(
        trailing_dd_pct=0,
        rolling_sharpe_4w=3.0,
        consecutive_sharpe_breach_days=0,
        fill_quality=FillQuality(n_fills_today=50, frac_over_5c=0.25),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.FILL_DEVIATION_20PCT).severity == AbortSeverity.CRITICAL


def test_trigger3_ok_under_20pct():
    v = _ev().evaluate(
        trailing_dd_pct=0,
        rolling_sharpe_4w=3.0,
        consecutive_sharpe_breach_days=0,
        fill_quality=FillQuality(n_fills_today=50, frac_over_5c=0.18),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.FILL_DEVIATION_20PCT).severity == AbortSeverity.OK


def test_trigger3_ok_when_zero_fills_today():
    v = _ev().evaluate(
        trailing_dd_pct=0,
        rolling_sharpe_4w=3.0,
        consecutive_sharpe_breach_days=0,
        fill_quality=FillQuality(n_fills_today=0, frac_over_5c=0.0),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.FILL_DEVIATION_20PCT).severity == AbortSeverity.OK


# ─────────────────────────────────────────────────────────────────────────────
# Abort trigger 4 — Rule Zero zero tolerance
# ─────────────────────────────────────────────────────────────────────────────
def test_trigger4_fires_on_single_violation():
    v = _ev().evaluate(
        trailing_dd_pct=0,
        rolling_sharpe_4w=3.0,
        consecutive_sharpe_breach_days=0,
        fill_quality=FillQuality(n_fills_today=5),
        rule_zero_violations_24h=1,
    )
    assert _find(v, AbortCode.RULE_ZERO_VIOLATION).severity == AbortSeverity.CRITICAL


def test_trigger4_ok_at_zero():
    v = _ev().evaluate(
        trailing_dd_pct=0,
        rolling_sharpe_4w=3.0,
        consecutive_sharpe_breach_days=0,
        fill_quality=FillQuality(n_fills_today=5),
        rule_zero_violations_24h=0,
    )
    assert _find(v, AbortCode.RULE_ZERO_VIOLATION).severity == AbortSeverity.OK


# ─────────────────────────────────────────────────────────────────────────────
# Integration: MetricAggregator.build_tick
# ─────────────────────────────────────────────────────────────────────────────
def test_aggregator_builds_full_tick(tmp_path):
    cfg = AggregatorConfig(state_file=tmp_path / "state.json")
    agg = MetricAggregator(cfg)
    rng = np.random.default_rng(2920)
    daily = rng.normal(loc=0.0015, scale=0.003, size=100).tolist()
    per_stream_hist = {
        "exp1220_spy": rng.normal(size=60).tolist(),
        "xlf_cs":      rng.normal(size=60).tolist(),
        "xli_cs":      rng.normal(size=60).tolist(),
    }
    per_stream_pnl = {
        "exp1220_spy": StreamPnl("exp1220_spy", pnl_today=120.0, weight=0.2),
        "xlf_cs":      StreamPnl("xlf_cs",      pnl_today= 40.0, weight=0.15),
        "xli_cs":      StreamPnl("xli_cs",      pnl_today=-15.0, weight=0.15),
    }
    tick = agg.build_tick(
        equity=102_500,
        vix=17.3,
        leverage=2.8,
        per_stream_pnl=per_stream_pnl,
        daily_returns=daily,
        per_stream_return_hist=per_stream_hist,
        fill_quality=FillQuality(n_fills_today=12, mean_deviation_cents=2.1, frac_over_5c=0.08),
        rule_zero_violations_24h=0,
        portfolio_return_today=0.0015,
    )
    assert tick.vix_ladder_zone == "calm"
    assert tick.rolling_sharpe_30d is not None
    assert tick.rolling_sharpe_60d is not None
    assert tick.rolling_sharpe_90d is not None
    assert len(tick.per_stream) == 3
    assert tick.correlation.matrix  # populated
    # All 4 abort triggers evaluated
    assert len(tick.abort_verdicts) == 4
    assert all(v.severity == AbortSeverity.OK for v in tick.abort_verdicts)


def test_aggregator_persists_rolling_peak(tmp_path):
    cfg = AggregatorConfig(state_file=tmp_path / "state.json")
    agg = MetricAggregator(cfg)
    # first tick
    t1 = agg.build_tick(
        equity=100_000, vix=18, leverage=1.0,
        per_stream_pnl={}, daily_returns=[],
        per_stream_return_hist={}, fill_quality=FillQuality(),
        rule_zero_violations_24h=0, portfolio_return_today=0,
    )
    assert t1.rolling_peak_equity == 100_000
    # higher equity — peak advances
    t2 = agg.build_tick(
        equity=110_000, vix=18, leverage=1.0,
        per_stream_pnl={}, daily_returns=[],
        per_stream_return_hist={}, fill_quality=FillQuality(),
        rule_zero_violations_24h=0, portfolio_return_today=0,
    )
    assert t2.rolling_peak_equity == 110_000
    assert t2.trailing_dd_pct == 0
    # drop 8% — peak holds, dd tracks
    t3 = agg.build_tick(
        equity=101_200, vix=18, leverage=1.0,
        per_stream_pnl={}, daily_returns=[],
        per_stream_return_hist={}, fill_quality=FillQuality(),
        rule_zero_violations_24h=0, portfolio_return_today=0,
    )
    assert t3.rolling_peak_equity == 110_000
    assert 7.9 < t3.trailing_dd_pct < 8.1
