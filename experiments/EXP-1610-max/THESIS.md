# EXP-1610-max: Paper Trading Reconciler V2

## Hypothesis

A six-dimension reconciliation framework comparing live paper trading against
backtest predictions will identify systematic drift, fill quality degradation,
and regime misclassification **before** they cause material PnL divergence.

By tracking signal agreement, PnL deviation, fill quality, slippage, and regime
accuracy in a unified dashboard with automated alerting (>10% deviation threshold),
we can maintain confidence that paper trading results are representative of
backtest expectations — or catch divergence early.

## Strategy

**Module**: `compass/paper_reconciler.py`

Six reconciliation dimensions:
1. **Signal Agreement Rate** — Do backtest and paper agree on trade direction and confidence?
2. **PnL Deviation** — Per-trade and aggregate PnL comparison with daily breakdown
3. **Fill Quality** — Expected vs actual fill prices in basis points
4. **Slippage Analysis** — Decomposed by regime, direction, spread type with rolling trend
5. **Regime Classification Accuracy** — Confusion matrix of predicted vs observed regime
6. **Automated Alerting** — Critical/warning alerts when thresholds breached

## Success Criteria

| Metric | Target | Notes |
|--------|--------|-------|
| Signal agreement rate | ≥ 85% | Direction + confidence alignment |
| PnL deviation (aggregate) | < 10% | Alert triggers above this |
| Fill accuracy | ≥ 80% | Fills within tolerance |
| Regime accuracy | ≥ 80% | Correct regime classification |
| Reconciliation score | ≥ 70/100 | Composite across all dimensions |
| All tests passing | 100% | Comprehensive test coverage |

## Implementation

- `compass/paper_reconciler.py` — Core reconciler (PaperReconcilerV2 class)
- `tests/test_paper_reconciler.py` — 35+ test cases across all dimensions
- Self-contained HTML report with dark theme, SVG charts, alert panel
- Builds on patterns from `compass/backtest_reconciler.py` (V1)

## Risk Factors

- Regime data may not always be available in paper trades
- Matching by date fallback is less reliable than trade_id matching
- Alert thresholds may need tuning per-strategy

## Date

2026-04-03
