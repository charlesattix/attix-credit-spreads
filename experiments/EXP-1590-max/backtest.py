"""
EXP-1590-max — Production Monitoring Dashboard demo/validation.

Demonstrates the ProductionMonitorDashboard with simulated position data
and generates the HTML dashboard + state file.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from compass.production_monitor import (
    MonitorDashboardConfig,
    PositionRecord,
    ProductionMonitorDashboard,
)

OUTPUT_DIR = Path(__file__).resolve().parent / "results"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    config = MonitorDashboardConfig(
        initial_capital=100_000,
        dd_warning_pct=0.05,
        dd_critical_pct=0.10,
        daily_loss_warning=2_000,
        daily_loss_critical=5_000,
        max_positions=20,
        max_positions_per_strategy=8,
        max_capital_at_risk_pct=0.40,
        var_limit=10_000,
        correlation_spike_threshold=0.80,
        telegram_enabled=False,
        auto_refresh_seconds=30,
    )

    alerts_sent: list[str] = []
    monitor = ProductionMonitorDashboard(
        config=config,
        send_fn=lambda msg: (alerts_sent.append(msg), True)[1],
    )

    # Simulate positions
    positions = [
        PositionRecord(
            position_id="P001", strategy="iron_condor", ticker="SPY",
            direction="bull_put", contracts=2, entry_credit=1.50,
            max_loss=850.0, current_value=1.20, unrealized_pnl=60.0,
            entry_date="2026-03-28", expiration_date="2026-04-18", dte=20,
            delta=-0.12, gamma=0.02, theta=0.08, vega=-0.15,
            margin_required=1700.0,
        ),
        PositionRecord(
            position_id="P002", strategy="iron_condor", ticker="QQQ",
            direction="bear_call", contracts=3, entry_credit=1.80,
            max_loss=1020.0, current_value=1.50, unrealized_pnl=90.0,
            entry_date="2026-03-25", expiration_date="2026-04-18", dte=24,
            delta=0.08, gamma=0.01, theta=0.12, vega=-0.10,
            margin_required=3060.0,
        ),
        PositionRecord(
            position_id="P003", strategy="put_spread", ticker="IWM",
            direction="bull_put", contracts=5, entry_credit=0.90,
            max_loss=2050.0, current_value=0.60, unrealized_pnl=150.0,
            entry_date="2026-03-20", expiration_date="2026-04-11", dte=13,
            delta=-0.18, gamma=0.03, theta=0.15, vega=-0.22,
            margin_required=10250.0,
        ),
        PositionRecord(
            position_id="P004", strategy="put_spread", ticker="SPY",
            direction="bull_put", contracts=1, entry_credit=2.10,
            max_loss=790.0, current_value=1.80, unrealized_pnl=30.0,
            entry_date="2026-04-01", expiration_date="2026-04-25", dte=22,
            delta=-0.10, gamma=0.015, theta=0.06, vega=-0.08,
            margin_required=790.0,
        ),
    ]

    for pos in positions:
        monitor.record_position(pos)

    # Simulate some closed trades
    monitor.remove_position("P001", realized_pnl=120.0, won=True)
    # Re-add P001 as a new position (simulating position refresh)
    monitor.record_position(positions[0])

    # Update VaR
    monitor.update_var(var_95=6_500.0, var_99=9_200.0)

    # Update correlations
    monitor.update_correlation({
        "iron_condor|put_spread": 0.62,
        "iron_condor|call_spread": 0.35,
        "put_spread|call_spread": 0.48,
    })

    # Record daily return
    monitor.record_daily_return(330.0)
    monitor.update_pnl(daily=330.0, unrealized=330.0)

    # Generate snapshot and dashboard
    snap = monitor.snapshot()
    dashboard_path = ProductionMonitorDashboard.generate_dashboard(
        snap, OUTPUT_DIR / "production_dashboard.html"
    )
    monitor.save_state(OUTPUT_DIR / "production_monitor_state.json")

    print(f"Dashboard: {dashboard_path}")
    print(f"Health score: {snap.health_score:.0f}/100")
    print(f"Open positions: {snap.n_open_positions}")
    print(f"Total P&L: ${snap.total_pnl:+,.2f}")
    print(f"Drawdown: {snap.drawdown.drawdown_pct:.1%}")
    print(f"Risk budget util: {snap.risk_budget.utilization_pct:.1%}")
    print(f"VaR util: {snap.var_state.utilization_pct:.1%}")
    print(f"Alerts: {len(snap.alert_history)}")
    print(f"Strategies: {list(snap.strategy_attribution.keys())}")


if __name__ == "__main__":
    main()
