# EXP-1590-max: Production Monitoring Dashboard

## Hypothesis
A comprehensive real-time monitoring dashboard tracking open positions, P&L,
drawdown, strategy attribution, risk budget, and VaR utilization — with
Telegram alert integration — enables faster detection and response to
production risk events.

## Approach
- Build `compass/production_monitor.py` with:
  - Position tracking with per-strategy breakdown and Greeks
  - Real-time unrealized + realized P&L
  - Drawdown monitoring with peak-to-trough tracking
  - Strategy-level attribution (P&L, win rate, capital at risk)
  - Risk budget utilization tracking
  - VaR utilization vs limits
  - Strategy correlation monitoring
- Auto-refreshing HTML dashboard (self-contained, no dependencies)
- Telegram alerts for: DD breach, daily loss limit, correlation spike,
  position limit breach
- Comprehensive test coverage

## North Star Metrics
- Dashboard renders all 7 tracking dimensions
- All 4 Telegram alert types fire correctly with cooldowns
- 100% test coverage on alert thresholds
- HTML auto-refreshes and is fully self-contained

## Status
COMPLETE
