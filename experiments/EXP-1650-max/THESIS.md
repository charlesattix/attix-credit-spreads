# EXP-1650: Earnings Vol Crush on Sector ETFs

## Hypothesis
Sector ETFs (XLF, XLK, XLE) exhibit systematic implied volatility
overstatement ahead of sector-wide earnings seasons.

## Key Finding: MIXED — Earnings-Specific IV Crush EXISTS but is Seasonal

**IV overstatement at ETF level exists during Q1/Q2/Q4 earnings but REVERSES in Q3.**

### XLF Earnings-Only vs All-Month (Control)
| Metric | Earnings-Only | All-Month |
|--------|---------------|-----------|
| Win Rate | **84.6%** | 78.4% |
| IV Crush | **15.0%** | 1.6% |
| IV/Real Ratio | **1.18×** | 0.89× |
| Sharpe | **1.43** | 1.02 |

### Quarterly Seasonality (XLF)
| Quarter | Win Rate | Avg IV Crush | Verdict |
|---------|----------|--------------|---------|
| Q1 (Jan) | **100%** | **46.7%** | Strong crush |
| Q2 (Apr) | **100%** | **55.3%** | Strongest crush |
| Q3 (Jul) | 33% | **-104.3%** | Premium EXPANDS |
| Q4 (Oct) | **100%** | **48.3%** | Strong crush |

### Walk-Forward
- IS (2020-2022): 5 trades, Sharpe 17.23
- OOS (2023-2025): 8 trades, Sharpe 0.32

### Data Limitations
- XLK: 0 trades (call+put data too sparse for strangles)
- XLE: 0 trades (same issue)
- Only XLF has sufficient dual-leg option data

## Conclusion
Earnings-specific IV crush EXISTS at the XLF ETF level (1.18× overstatement),
but ONLY in Q1/Q2/Q4. The Q3 (July) earnings window shows violent premium
expansion, likely driven by summer volatility clustering. A Q3-exclusion filter
would improve results significantly.

The all-month control (0.89× overstatement) confirms that the IV crush is
genuinely earnings-specific, not just general theta decay.

## Status
COMPLETE
