"""
EXP-1610-max: Paper Trading Reconciler V2 — validation script.

Generates synthetic paired backtest/paper data, runs full reconciliation,
and outputs HTML report + summary JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from compass.paper_reconciler import PaperReconcilerV2, ReconcilerConfig

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def generate_test_data(n: int = 50, seed: int = 42):
    """Generate realistic paired backtest/paper trade data."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2026-01-02", periods=n)
    exit_dates = dates + pd.Timedelta(days=3)

    bt_entry = rng.uniform(2.5, 7.5, n)
    bt_exit = bt_entry + rng.normal(0.6, 0.8, n)
    bt_pnl = (bt_exit - bt_entry) * 100

    # Paper trades: realistic noise
    pp_entry = bt_entry + rng.normal(0, 0.03, n)
    pp_exit = bt_exit + rng.normal(0, 0.03, n)
    pp_pnl = bt_pnl + rng.normal(0, 12.0, n)

    time_offsets = pd.to_timedelta(rng.normal(0, 0.3, n), unit="h")

    regimes = ["bull", "sideways", "bear"]
    bt_regimes = [regimes[i % 3] for i in range(n)]
    pp_regimes = [
        bt_regimes[i] if rng.random() < 0.85 else regimes[(i + 1) % 3]
        for i in range(n)
    ]

    directions = ["short", "long"]
    bt_dirs = [directions[i % 2] for i in range(n)]
    pp_dirs = list(bt_dirs)

    bt = pd.DataFrame({
        "trade_id": [f"T-{i:04d}" for i in range(n)],
        "entry_price": bt_entry,
        "exit_price": bt_exit,
        "pnl": bt_pnl,
        "entry_date": dates,
        "exit_date": exit_dates,
        "regime": bt_regimes,
        "direction": bt_dirs,
        "confidence": rng.uniform(0.5, 0.9, n),
        "spread_type": ["bull_put" if i % 2 == 0 else "bear_call" for i in range(n)],
    })

    pp = pd.DataFrame({
        "trade_id": [f"T-{i:04d}" for i in range(n)],
        "entry_price": pp_entry,
        "exit_price": pp_exit,
        "pnl": pp_pnl,
        "entry_date": dates + time_offsets.round("min"),
        "exit_date": exit_dates + time_offsets.round("min"),
        "regime": pp_regimes,
        "direction": pp_dirs,
        "confidence": np.clip(rng.uniform(0.5, 0.9, n) + rng.normal(0, 0.03, n), 0, 1),
        "spread_type": ["bull_put" if i % 2 == 0 else "bear_call" for i in range(n)],
    })

    return bt, pp


def main():
    RESULTS.mkdir(parents=True, exist_ok=True)

    bt, pp = generate_test_data(n=50)
    config = ReconcilerConfig(deviation_alert_pct=10.0)
    rec = PaperReconcilerV2(bt, pp, config=config)
    result = rec.reconcile()

    # Generate HTML report
    report_path = RESULTS / "reconciliation_v2.html"
    PaperReconcilerV2.generate_report(result, report_path)
    print(f"Report: {report_path}")

    # Save summary JSON
    summary = {
        "experiment": "EXP-1610-max",
        "description": "Paper Trading Reconciler V2 — 6-dimension backtest/paper comparison",
        "n_backtest_trades": result.n_backtest_trades,
        "n_paper_trades": result.n_paper_trades,
        "n_matched": result.n_matched,
        "reconciliation_score": result.reconciliation_score,
        "score_breakdown": result.score_breakdown,
        "signal_agreement_rate": result.signal_agreement.agreement_rate,
        "pnl_deviation_pct": result.pnl_deviation.aggregate_deviation_pct,
        "fill_accuracy_pct": result.fill_quality.fill_accuracy_pct,
        "regime_accuracy": result.regime_accuracy.accuracy,
        "n_alerts": len(result.alerts),
        "alerts": [
            {"severity": a.severity, "category": a.category, "message": a.message}
            for a in result.alerts
        ],
        "success_criteria": {
            "signal_agreement_ge_85": {
                "target": 0.85,
                "actual": result.signal_agreement.agreement_rate,
                "met": result.signal_agreement.agreement_rate >= 0.85,
            },
            "pnl_deviation_lt_10": {
                "target": 10.0,
                "actual": abs(result.pnl_deviation.aggregate_deviation_pct),
                "met": abs(result.pnl_deviation.aggregate_deviation_pct) < 10.0,
            },
            "fill_accuracy_ge_80": {
                "target": 80.0,
                "actual": result.fill_quality.fill_accuracy_pct,
                "met": result.fill_quality.fill_accuracy_pct >= 80.0,
            },
            "regime_accuracy_ge_80": {
                "target": 0.80,
                "actual": result.regime_accuracy.accuracy,
                "met": result.regime_accuracy.accuracy >= 0.80,
            },
            "score_ge_70": {
                "target": 70.0,
                "actual": result.reconciliation_score,
                "met": result.reconciliation_score >= 70.0,
            },
        },
        "all_criteria_met": all([
            result.signal_agreement.agreement_rate >= 0.85,
            abs(result.pnl_deviation.aggregate_deviation_pct) < 10.0,
            result.fill_quality.fill_accuracy_pct >= 80.0,
            result.regime_accuracy.accuracy >= 0.80,
            result.reconciliation_score >= 70.0,
        ]),
    }

    summary_path = RESULTS / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Summary: {summary_path}")
    print(f"Score: {result.reconciliation_score}/100")
    print(f"All criteria met: {summary['all_criteria_met']}")


if __name__ == "__main__":
    main()
