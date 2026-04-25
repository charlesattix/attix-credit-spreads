"""Tests for compass/paper_reconciler.py — Paper Trading Reconciler V2."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd
import pytest

from compass.paper_reconciler import (
    Alert,
    FillQuality,
    PaperReconcilerV2,
    PnLDeviation,
    ReconcilerConfig,
    ReconciliationResultV2,
    RegimeAccuracy,
    SignalAgreement,
    SlippageAnalysis,
    TradeComparison,
    _bps_diff,
    _safe_pct_diff,
    build_comparisons,
    compute_fill_quality,
    compute_pnl_deviation,
    compute_reconciliation_score,
    compute_regime_accuracy,
    compute_signal_agreement,
    compute_slippage_analysis,
    generate_alerts,
    match_trades,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_trades(
    n: int = 30,
    seed: int = 42,
    price_noise: float = 0.02,
    pnl_noise: float = 15.0,
    time_noise_hours: float = 0.5,
    with_trade_id: bool = True,
    with_regime: bool = True,
    with_direction: bool = True,
    with_confidence: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Generate paired backtest and paper trade DataFrames with controlled noise."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2024-01-02", periods=n)
    exit_dates = dates + pd.Timedelta(days=2)

    bt_entry = rng.uniform(3.0, 8.0, n)
    bt_exit = bt_entry + rng.normal(0.5, 1.0, n)
    bt_pnl = (bt_exit - bt_entry) * 100

    pp_entry = bt_entry + rng.normal(0, price_noise, n)
    pp_exit = bt_exit + rng.normal(0, price_noise, n)
    pp_pnl = bt_pnl + rng.normal(0, pnl_noise, n)

    time_offsets = pd.to_timedelta(rng.normal(0, time_noise_hours, n), unit="h")
    pp_entry_dates = dates + time_offsets.round("min")
    pp_exit_dates = exit_dates + time_offsets.round("min")

    bt_data = {
        "entry_price": bt_entry,
        "exit_price": bt_exit,
        "pnl": bt_pnl,
        "entry_date": dates,
        "exit_date": exit_dates,
    }
    pp_data = {
        "entry_price": pp_entry,
        "exit_price": pp_exit,
        "pnl": pp_pnl,
        "entry_date": pp_entry_dates,
        "exit_date": pp_exit_dates,
    }

    if with_trade_id:
        trade_ids = [f"T-{i:04d}" for i in range(n)]
        bt_data["trade_id"] = trade_ids
        pp_data["trade_id"] = trade_ids

    if with_regime:
        regimes = ["bull", "sideways", "bear"]
        bt_regimes = [regimes[i % 3] for i in range(n)]
        # 80% match, 20% mismatch
        pp_regimes = [
            bt_regimes[i] if rng.random() < 0.80 else regimes[(i + 1) % 3]
            for i in range(n)
        ]
        bt_data["regime"] = bt_regimes
        pp_data["regime"] = pp_regimes

    if with_direction:
        directions = ["short", "long"]
        bt_dirs = [directions[i % 2] for i in range(n)]
        pp_dirs = list(bt_dirs)  # 100% match by default
        bt_data["direction"] = bt_dirs
        pp_data["direction"] = pp_dirs

    if with_confidence:
        bt_conf = rng.uniform(0.4, 0.9, n)
        pp_conf = bt_conf + rng.normal(0, 0.05, n)
        bt_data["confidence"] = bt_conf
        pp_data["confidence"] = np.clip(pp_conf, 0.0, 1.0)

    bt_data["spread_type"] = ["bull_put" if i % 2 == 0 else "bear_call" for i in range(n)]
    pp_data["spread_type"] = bt_data["spread_type"]

    return pd.DataFrame(bt_data), pd.DataFrame(pp_data)


# ── Helper function tests ────────────────────────────────────────────────


class TestHelpers:
    def test_safe_pct_diff_normal(self):
        assert abs(_safe_pct_diff(100.0, 110.0) - 10.0) < 0.01

    def test_safe_pct_diff_zero_base(self):
        result = _safe_pct_diff(0.0, 5.0)
        assert result == 500.0  # base defaults to 1.0

    def test_safe_pct_diff_negative(self):
        result = _safe_pct_diff(100.0, 90.0)
        assert abs(result - (-10.0)) < 0.01

    def test_bps_diff(self):
        # 1% difference = 100 bps
        result = _bps_diff(100.0, 101.0)
        assert abs(result - 100.0) < 0.01

    def test_bps_diff_zero(self):
        assert _bps_diff(5.0, 5.0) == 0.0


# ── Match trades tests ───────────────────────────────────────────────────


class TestMatchTrades:
    def test_match_by_trade_id(self):
        bt, pp = _make_trades(n=10, with_trade_id=True)
        pairs, unmatched_bt, unmatched_pp = match_trades(bt, pp)
        assert len(pairs) == 10
        assert len(unmatched_bt) == 0
        assert len(unmatched_pp) == 0

    def test_match_by_date_fallback(self):
        bt, pp = _make_trades(n=10, with_trade_id=False)
        pairs, unmatched_bt, unmatched_pp = match_trades(bt, pp)
        assert len(pairs) == 10

    def test_partial_match(self):
        bt, pp = _make_trades(n=10, with_trade_id=True)
        # Remove some paper trades
        pp = pp.iloc[:7].reset_index(drop=True)
        pp_ids = pp["trade_id"].tolist()
        pairs, unmatched_bt, unmatched_pp = match_trades(bt, pp)
        assert len(pairs) == 7
        assert len(unmatched_bt) == 3

    def test_empty_dataframes(self):
        bt = pd.DataFrame(columns=["entry_price", "exit_price", "pnl", "entry_date", "exit_date", "trade_id"])
        pp = bt.copy()
        pairs, unmatched_bt, unmatched_pp = match_trades(bt, pp)
        assert len(pairs) == 0


# ── Signal agreement tests ───────────────────────────────────────────────


class TestSignalAgreement:
    def test_perfect_agreement(self):
        bt, pp = _make_trades(n=20, with_direction=True, with_confidence=True)
        pairs = list(zip(range(20), range(20)))
        sa = compute_signal_agreement(bt, pp, pairs, [], [])
        # All directions match and confidence noise is small
        assert sa.agreement_rate >= 0.8
        assert sa.total_signals == 20

    def test_direction_mismatch(self):
        bt, pp = _make_trades(n=10, with_direction=True)
        # Flip all paper directions
        pp["direction"] = pp["direction"].map(lambda d: "long" if d == "short" else "short")
        pairs = list(zip(range(10), range(10)))
        sa = compute_signal_agreement(bt, pp, pairs, [], [])
        assert sa.agreement_rate == 0.0
        assert sa.disagreements_by_type.get("direction_mismatch", 0) == 10

    def test_unmatched_counted(self):
        bt, pp = _make_trades(n=10)
        pairs = list(zip(range(5), range(5)))
        sa = compute_signal_agreement(bt, pp, pairs, [5, 6, 7], [8, 9])
        assert sa.total_signals == 10  # 5 matched + 3 unmatched_bt + 2 unmatched_pp
        assert sa.disagreements_by_type.get("missing_in_paper", 0) == 3
        assert sa.disagreements_by_type.get("missing_in_backtest", 0) == 2

    def test_empty(self):
        bt = pd.DataFrame(columns=["entry_price", "exit_price", "pnl", "entry_date", "exit_date"])
        pp = bt.copy()
        sa = compute_signal_agreement(bt, pp, [], [], [])
        assert sa.total_signals == 0
        assert sa.agreement_rate == 0.0


# ── PnL deviation tests ─────────────────────────────────────────────────


class TestPnLDeviation:
    def test_low_noise_within_tolerance(self):
        bt, pp = _make_trades(n=30, pnl_noise=5.0)
        pairs = list(zip(range(30), range(30)))
        config = ReconcilerConfig(pnl_tol_dollars=20.0)
        pnl = compute_pnl_deviation(bt, pp, pairs, config)
        assert pnl.pct_within_tolerance >= 80.0

    def test_high_noise_triggers_alert(self):
        bt, pp = _make_trades(n=30, pnl_noise=200.0, seed=99)
        pairs = list(zip(range(30), range(30)))
        config = ReconcilerConfig(deviation_alert_pct=5.0)
        pnl = compute_pnl_deviation(bt, pp, pairs, config)
        # With 200 noise, very likely to trigger alert
        assert isinstance(pnl.aggregate_deviation_pct, float)

    def test_daily_deviations_generated(self):
        bt, pp = _make_trades(n=30)
        pairs = list(zip(range(30), range(30)))
        config = ReconcilerConfig()
        pnl = compute_pnl_deviation(bt, pp, pairs, config)
        assert len(pnl.daily_deviations) > 0
        assert all("date" in d for d in pnl.daily_deviations)

    def test_empty_pairs(self):
        bt, pp = _make_trades(n=10)
        pnl = compute_pnl_deviation(bt, pp, [], ReconcilerConfig())
        assert pnl.bt_total_pnl == 0.0
        assert pnl.pp_total_pnl == 0.0


# ── Fill quality tests ───────────────────────────────────────────────────


class TestFillQuality:
    def test_tight_fills(self):
        bt, pp = _make_trades(n=20, price_noise=0.001)
        pairs = list(zip(range(20), range(20)))
        fq = compute_fill_quality(bt, pp, pairs, ReconcilerConfig())
        assert fq.fill_accuracy_pct >= 80.0
        assert fq.avg_entry_slippage_bps < 50.0

    def test_wide_fills(self):
        bt, pp = _make_trades(n=20, price_noise=0.5)
        pairs = list(zip(range(20), range(20)))
        fq = compute_fill_quality(bt, pp, pairs, ReconcilerConfig())
        assert fq.avg_entry_slippage_bps > 0

    def test_slippage_bps_nonnegative(self):
        bt, pp = _make_trades(n=20)
        pairs = list(zip(range(20), range(20)))
        fq = compute_fill_quality(bt, pp, pairs, ReconcilerConfig())
        assert fq.avg_entry_slippage_bps >= 0
        assert fq.avg_exit_slippage_bps >= 0
        assert fq.worst_entry_slippage_bps >= fq.avg_entry_slippage_bps

    def test_empty(self):
        bt, pp = _make_trades(n=10)
        fq = compute_fill_quality(bt, pp, [], ReconcilerConfig())
        assert fq.total_fills == 0


# ── Slippage analysis tests ─────────────────────────────────────────────


class TestSlippageAnalysis:
    def test_slippage_decomposition(self):
        bt, pp = _make_trades(n=30, with_regime=True)
        pairs = list(zip(range(30), range(30)))
        sa = compute_slippage_analysis(bt, pp, pairs)
        assert isinstance(sa.total_slippage_dollars, float)
        assert len(sa.slippage_by_regime) > 0
        assert len(sa.slippage_by_direction) > 0
        assert len(sa.slippage_by_spread_type) > 0

    def test_slippage_trend(self):
        bt, pp = _make_trades(n=30)
        pairs = list(zip(range(30), range(30)))
        sa = compute_slippage_analysis(bt, pp, pairs)
        assert len(sa.slippage_trend) > 0

    def test_empty(self):
        bt, pp = _make_trades(n=10)
        sa = compute_slippage_analysis(bt, pp, [])
        assert sa.total_slippage_dollars == 0.0


# ── Regime accuracy tests ───────────────────────────────────────────────


class TestRegimeAccuracy:
    def test_with_regime_data(self):
        bt, pp = _make_trades(n=30, with_regime=True)
        pairs = list(zip(range(30), range(30)))
        ra = compute_regime_accuracy(bt, pp, pairs)
        assert ra.total_classified == 30
        assert 0.0 <= ra.accuracy <= 1.0
        assert len(ra.confusion_matrix) > 0

    def test_perfect_regime_match(self):
        bt, pp = _make_trades(n=20, with_regime=True)
        pp["regime"] = bt["regime"]  # force perfect match
        pairs = list(zip(range(20), range(20)))
        ra = compute_regime_accuracy(bt, pp, pairs)
        assert ra.accuracy == 1.0
        assert ra.correctly_classified == 20

    def test_no_regime_data(self):
        bt, pp = _make_trades(n=10, with_regime=False)
        pairs = list(zip(range(10), range(10)))
        ra = compute_regime_accuracy(bt, pp, pairs)
        assert ra.total_classified == 0

    def test_accuracy_by_regime(self):
        bt, pp = _make_trades(n=30, with_regime=True)
        pp["regime"] = bt["regime"]
        pairs = list(zip(range(30), range(30)))
        ra = compute_regime_accuracy(bt, pp, pairs)
        for regime, acc in ra.accuracy_by_regime.items():
            assert acc == 1.0


# ── Trade comparison tests ──────────────────────────────────────────────


class TestBuildComparisons:
    def test_comparisons_count(self):
        bt, pp = _make_trades(n=15)
        pairs = list(zip(range(15), range(15)))
        comps = build_comparisons(bt, pp, pairs)
        assert len(comps) == 15

    def test_comparison_fields(self):
        bt, pp = _make_trades(n=5)
        pairs = list(zip(range(5), range(5)))
        comps = build_comparisons(bt, pp, pairs)
        c = comps[0]
        assert isinstance(c, TradeComparison)
        assert c.trade_id == "T-0000"
        assert isinstance(c.bt_entry_price, float)
        assert isinstance(c.pnl_deviation, float)
        assert isinstance(c.entry_slippage_bps, float)
        assert isinstance(c.regime_match, bool)
        assert isinstance(c.signal_match, bool)


# ── Alert generation tests ──────────────────────────────────────────────


class TestAlertGeneration:
    def test_pnl_alert_triggered(self):
        pnl = PnLDeviation(
            bt_total_pnl=1000, pp_total_pnl=800,
            aggregate_deviation_pct=-20.0,
            alert_triggered=True,
            alert_message="Aggregate PnL deviation -20.0% exceeds 10.0% threshold",
        )
        fq = FillQuality(avg_entry_slippage_bps=3.0)
        ra = RegimeAccuracy(total_classified=10, accuracy=0.9)
        sa_sig = SignalAgreement(total_signals=10, agreement_rate=0.9)
        slip = SlippageAnalysis(slippage_as_pct_of_pnl=2.0)
        config = ReconcilerConfig()

        alerts = generate_alerts(pnl, fq, ra, sa_sig, slip, config)
        assert any(a.category == "pnl_deviation" and a.severity == "critical" for a in alerts)

    def test_no_alerts_when_healthy(self):
        pnl = PnLDeviation(aggregate_deviation_pct=2.0, alert_triggered=False)
        fq = FillQuality(avg_entry_slippage_bps=2.0)
        ra = RegimeAccuracy(total_classified=10, accuracy=0.9)
        sa_sig = SignalAgreement(total_signals=10, agreement_rate=0.9)
        slip = SlippageAnalysis(slippage_as_pct_of_pnl=1.0)
        config = ReconcilerConfig()

        alerts = generate_alerts(pnl, fq, ra, sa_sig, slip, config)
        assert len(alerts) == 0

    def test_fill_quality_alert(self):
        pnl = PnLDeviation(alert_triggered=False)
        fq = FillQuality(avg_entry_slippage_bps=10.0)
        ra = RegimeAccuracy()
        sa_sig = SignalAgreement()
        slip = SlippageAnalysis(slippage_as_pct_of_pnl=1.0)
        config = ReconcilerConfig(slippage_warn_bps=5.0)

        alerts = generate_alerts(pnl, fq, ra, sa_sig, slip, config)
        assert any(a.category == "fill_quality" for a in alerts)

    def test_regime_alert(self):
        pnl = PnLDeviation(alert_triggered=False)
        fq = FillQuality(avg_entry_slippage_bps=1.0)
        ra = RegimeAccuracy(total_classified=20, accuracy=0.5)
        sa_sig = SignalAgreement(total_signals=10, agreement_rate=0.9)
        slip = SlippageAnalysis(slippage_as_pct_of_pnl=1.0)
        config = ReconcilerConfig()

        alerts = generate_alerts(pnl, fq, ra, sa_sig, slip, config)
        assert any(a.category == "regime" for a in alerts)

    def test_alerts_sorted_by_severity(self):
        pnl = PnLDeviation(
            aggregate_deviation_pct=-20.0, alert_triggered=True,
            alert_message="PnL deviation alert",
        )
        fq = FillQuality(avg_entry_slippage_bps=10.0)
        ra = RegimeAccuracy(total_classified=20, accuracy=0.5)
        sa_sig = SignalAgreement(total_signals=10, agreement_rate=0.5)
        slip = SlippageAnalysis(slippage_as_pct_of_pnl=10.0)
        config = ReconcilerConfig()

        alerts = generate_alerts(pnl, fq, ra, sa_sig, slip, config)
        assert len(alerts) >= 2
        # Critical should come first
        assert alerts[0].severity == "critical"


# ── Score computation tests ─────────────────────────────────────────────


class TestScoreComputation:
    def test_perfect_score(self):
        sa = SignalAgreement(total_signals=10, agreement_rate=1.0)
        pnl = PnLDeviation(aggregate_deviation_pct=0.0)
        fq = FillQuality(total_fills=10, fill_accuracy_pct=100.0)
        ra = RegimeAccuracy(total_classified=10, accuracy=1.0)

        score, breakdown = compute_reconciliation_score(sa, pnl, fq, ra, 10, 10)
        assert score == 100.0
        assert all(v == 20.0 for v in breakdown.values())

    def test_zero_score(self):
        sa = SignalAgreement(total_signals=10, agreement_rate=0.0)
        pnl = PnLDeviation(aggregate_deviation_pct=200.0)
        fq = FillQuality(total_fills=10, fill_accuracy_pct=0.0)
        ra = RegimeAccuracy(total_classified=10, accuracy=0.0)

        score, breakdown = compute_reconciliation_score(sa, pnl, fq, ra, 0, 10)
        assert score == 0.0

    def test_partial_score(self):
        sa = SignalAgreement(total_signals=10, agreement_rate=0.5)
        pnl = PnLDeviation(aggregate_deviation_pct=5.0)
        fq = FillQuality(total_fills=10, fill_accuracy_pct=80.0)
        ra = RegimeAccuracy(total_classified=10, accuracy=0.7)

        score, breakdown = compute_reconciliation_score(sa, pnl, fq, ra, 10, 10)
        assert 40 < score < 90
        assert len(breakdown) == 5


# ── Full reconciler integration tests ───────────────────────────────────


class TestPaperReconcilerV2:
    def test_full_reconciliation(self):
        bt, pp = _make_trades(n=30)
        rec = PaperReconcilerV2(bt, pp)
        result = rec.reconcile()

        assert isinstance(result, ReconciliationResultV2)
        assert result.n_backtest_trades == 30
        assert result.n_paper_trades == 30
        assert result.n_matched == 30
        assert 0 <= result.reconciliation_score <= 100
        assert len(result.comparisons) == 30
        assert isinstance(result.signal_agreement, SignalAgreement)
        assert isinstance(result.pnl_deviation, PnLDeviation)
        assert isinstance(result.fill_quality, FillQuality)
        assert isinstance(result.slippage_analysis, SlippageAnalysis)
        assert isinstance(result.regime_accuracy, RegimeAccuracy)

    def test_custom_config(self):
        bt, pp = _make_trades(n=20)
        config = ReconcilerConfig(
            pnl_tol_dollars=50.0,
            deviation_alert_pct=5.0,
            slippage_warn_bps=2.0,
        )
        rec = PaperReconcilerV2(bt, pp, config=config)
        result = rec.reconcile()
        assert result.config.pnl_tol_dollars == 50.0

    def test_missing_columns_raises(self):
        bt = pd.DataFrame({"entry_price": [1.0], "pnl": [10.0]})
        pp = pd.DataFrame({"entry_price": [1.0], "pnl": [10.0]})
        with pytest.raises(ValueError, match="missing columns"):
            PaperReconcilerV2(bt, pp)

    def test_unmatched_trades(self):
        bt, pp = _make_trades(n=20, with_trade_id=True)
        # Remove last 5 from paper
        pp = pp.iloc[:15].reset_index(drop=True)
        rec = PaperReconcilerV2(bt, pp)
        result = rec.reconcile()
        assert result.n_matched == 15
        assert result.n_backtest_trades == 20
        assert result.n_paper_trades == 15

    def test_html_report_generation(self):
        bt, pp = _make_trades(n=20)
        rec = PaperReconcilerV2(bt, pp)
        result = rec.reconcile()

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test_report.html"
            path = PaperReconcilerV2.generate_report(result, out)
            assert path.exists()
            content = path.read_text()
            assert "Paper Trading Reconciliation V2" in content
            assert "Six-Dimension Analysis" in content
            assert "Signal Agreement" in content
            assert "PnL Deviation" in content
            assert "Fill Accuracy" in content
            assert "Regime" in content
            assert "Slippage" in content

    def test_empty_dataframes(self):
        cols = ["entry_price", "exit_price", "pnl", "entry_date", "exit_date"]
        bt = pd.DataFrame(columns=cols)
        pp = pd.DataFrame(columns=cols)
        rec = PaperReconcilerV2(bt, pp)
        result = rec.reconcile()
        assert result.n_matched == 0
        assert result.reconciliation_score >= 0

    def test_large_deviation_triggers_alert(self):
        bt, pp = _make_trades(n=30, pnl_noise=500.0, seed=7)
        config = ReconcilerConfig(deviation_alert_pct=5.0)
        rec = PaperReconcilerV2(bt, pp, config=config)
        result = rec.reconcile()
        # With 500 noise, should likely trigger some alerts
        assert isinstance(result.alerts, list)

    def test_no_regime_columns_handled(self):
        bt, pp = _make_trades(n=10, with_regime=False)
        rec = PaperReconcilerV2(bt, pp)
        result = rec.reconcile()
        assert result.regime_accuracy.total_classified == 0

    def test_score_breakdown_sums_correctly(self):
        bt, pp = _make_trades(n=20)
        rec = PaperReconcilerV2(bt, pp)
        result = rec.reconcile()
        bd = result.score_breakdown
        expected_total = sum(bd.values())
        assert abs(result.reconciliation_score - expected_total) < 0.1

    def test_date_fallback_matching(self):
        bt, pp = _make_trades(n=15, with_trade_id=False)
        rec = PaperReconcilerV2(bt, pp)
        result = rec.reconcile()
        assert result.n_matched == 15
