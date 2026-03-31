# EXP-890-max: Live Trading Blueprint — Analysis

## What We Built

`compass/live_trading_blueprint.py` — a production-grade integration layer with 7 components:

| Component | What It Does | Tests |
|-----------|-------------|-------|
| Signal → Order | Translates strategy signals to broker orders (limit/market) | 5 |
| Pre-Trade Risk | 6 gates: kill switch, position limit, strategy limit, DD, daily loss, notional, confidence | 8 |
| Order Management | Submit, fill, close, emergency liquidate | 7 |
| P&L Tracking | Mark-to-market, per-strategy attribution, alert thresholds | 3 |
| Kill Switch | Auto-trigger on DD or daily loss, manual halt, reset | 5 |
| Reconciliation | Paper vs live P&L comparison, position matching, drift detection | 3 |
| Audit Trail | Every signal, risk check, order, fill logged with timestamps | 2 |

**35 tests passing**, all components verified independently and in integration.

## Pre-Trade Risk Gates

Every signal must pass ALL 6 gates before an order is submitted:

| Gate | Check | Default Limit | Action on Fail |
|------|-------|--------------|----------------|
| 1. Kill Switch | System is ARMED | — | Reject all orders |
| 2. Position Limit | Total positions < max | 10 | Reject |
| 3. Strategy Limit | Per-strategy < max | 5 | Reject |
| 4. Drawdown | DD < threshold | 8% | Reject |
| 5. Daily Loss | Today's loss < limit | 3% | Reject |
| 6. Confidence | Signal P >= min | 0.50 | Reject |
| 7. Notional | Order size < max | $50K | Reject |

## Kill Switch Design

Three trigger modes:
1. **Auto: Drawdown** — triggers when portfolio DD exceeds 8%, liquidates all positions
2. **Auto: Daily Loss** — triggers when daily loss exceeds 3%, liquidates all
3. **Manual** — operator calls `manual_halt()`, blocks all new orders

Recovery: requires explicit `reset_kill_switch()` after review. Kill switch state is checked on every order submission AND every risk check.

---

# DEPLOYMENT CHECKLIST

## Before Going Live

### Infrastructure
- [ ] Alpaca paper trading account set up and tested
- [ ] API keys stored in environment variables (never in code)
- [ ] Logging pipeline to persistent storage (not just stdout)
- [ ] Monitoring dashboard accessible (Grafana/similar)
- [ ] Alert routing configured (email/Slack/SMS for CRITICAL)

### Strategy Validation
- [ ] All strategies backtested with realistic slippage ($5+ spreads)
- [ ] Walk-forward validation shows <20% OOS degradation
- [ ] Paper trading for minimum 30 days with positive results
- [ ] Paper vs backtest reconciliation within 10%

### Risk Limits Configured
- [ ] Max positions: 10 (portfolio), 5 (per strategy)
- [ ] Max drawdown kill switch: 8%
- [ ] Max daily loss: 3%
- [ ] Max notional per order: $50,000
- [ ] Min confidence: 0.50
- [ ] Margin utilisation cap: 80%

### Operational Readiness
- [ ] Kill switch tested (trigger and recovery)
- [ ] Emergency liquidation tested
- [ ] Reconciliation process verified
- [ ] Audit trail reviewed for completeness
- [ ] Runbook distributed to all operators
- [ ] Escalation contacts confirmed

### Go-Live Sequence
1. Deploy code to production server
2. Start with $10K allocation (10% of target)
3. First week: 1 strategy only (credit spreads, $5 widths)
4. Daily reconciliation: paper vs live
5. Week 2: add second strategy if drift < 5bps
6. Week 3: scale to 50% allocation if metrics hold
7. Week 4: full allocation if all checks pass

---

# RISK MANAGEMENT RUNBOOK

## Scenario: Normal Operations
- Monitor dashboard every 30 minutes during market hours
- Review P&L at 12:00 and 15:30 daily
- Run reconciliation after close
- Review audit trail daily

## Scenario: Drawdown Warning (DD > 5%)
1. Reduce new position sizes by 50%
2. Review all open positions — close any with negative expectancy
3. Check: is this broad market move or strategy-specific?
4. If strategy-specific: pause that strategy
5. If market-wide: tighten stops on all positions

## Scenario: Kill Switch Triggered (DD > 8% or daily loss > 3%)
1. **DO NOT PANIC** — system has already liquidated
2. Wait 15 minutes for all fills to settle
3. Run reconciliation to verify all positions closed
4. Review audit trail: what caused the loss?
5. Document the incident
6. Wait minimum 24 hours before restarting
7. Reset kill switch only after risk committee review

## Scenario: Anomalous Fill (price > 2σ from expected)
1. Immediately check if it's a data error or real fill
2. If real: close the position at market
3. Log as anomaly in audit trail
4. Contact broker support if fill seems erroneous
5. Review order routing — switch to limit orders

## Scenario: Broker Outage
1. Kill switch to MANUAL_HALT immediately
2. Note all open positions from last reconciliation
3. When broker reconnects: reconcile before resuming
4. If extended outage (>1 hour): assess hedging needs
5. Never trade during broker instability

## Scenario: Model Degradation (OOS Sharpe drops below 1.0)
1. Reduce allocation to degraded strategy by 50%
2. Increase monitoring frequency to 15 minutes
3. Run walk-forward validation on recent data
4. If confirmed degradation: pause strategy for re-training
5. Do not resume until OOS metrics recover above threshold

## Scenario: Scaling Up (adding AUM)
1. Verify execution analytics at new size (EXP-850-max)
2. Increase allocation in 25% increments
3. Monitor slippage per trade — should not increase >20%
4. If slippage increases: widen spreads or reduce trade frequency
5. Re-run capacity analysis at each increment
