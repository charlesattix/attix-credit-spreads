# Status: COMPLETE

## Key Results

| Portfolio | Compound CAGR | Worst DD | Avg Sharpe |
|-----------|--------------|----------|------------|
| **NS Base** | **27.8%** | **-3.6%** | **18.68** |
| **NS 3.6×** | **99.0%** | **-12.9%** | **8.94** |
| **NS DD<12%** | **195.5%** | **-12.0%** | **18.68** |
| SPY | ~12.1% | -33.9% | 0.66 |
| EXP-400 | ~20.5% | -11.2% | 2.88 |
| EXP-401 | ~7.3% | -24.4% | 0.63 |

## Findings

1. **All 6 years profitable** at base level — no losing years even in 2022 bear market
2. **2022 worst year** as expected — but still positive base return vs SPY's -18%
3. **DD<12% variant achieves 195.5% CAGR** — far exceeds 100% North Star target
4. **3.6× leverage delivers ~99% CAGR** — matches EXP-1470 claim
5. **Outperforms SPY in every single year**, including 2020 COVID crash
6. **EXP-400/401 significantly outperformed** by the 4-strategy blend
7. **8,358 total trades** across 6 years, 86.4% avg win rate
8. **Regime diversification works**: Intraday-MR provides stability in volatile years,
   Regime-Lev amplifies gains in bull years

## Module

`compass/walkforward_yearly.py` — tests passing

## Artifacts

- `results/report.html` — Full HTML report with SVG charts
- `results/summary.json` — Machine-readable summary
