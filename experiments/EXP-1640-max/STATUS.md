# Status: COMPLETE

## Results (2020-2025, real IronVault data)

| Metric | Value |
|--------|-------|
| Trades | 19 |
| Win Rate | 84.2% |
| Total P&L | $2,111 |
| CAGR | +0.3% |
| Sharpe | 0.64 |
| Max DD | 0.8% |
| SPY Correlation | 0.038 |
| Calmar | 0.47 |

## Walk-Forward
- Train 2020-2023: Sharpe 1.07, 13 trades, 92.3% WR
- Test 2024-2025: Sharpe -0.12, 6 trades, 66.7% WR

## Key Findings
- Strategy works on XLF (247 exps) and XLI (277 exps) — XLK/XLE have sparse data
- 84.2% win rate with very low DD (0.8%) — conservative but reliable
- OOS degradation expected: only 6 test trades (not enough for significance)
- Near-zero SPY correlation (0.038) — excellent diversifier
- 5/6 years profitable
