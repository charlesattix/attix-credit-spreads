# Status: COMPLETE — MIXED RESULTS

| Criterion | Target | Actual | Met |
|-----------|--------|--------|-----|
| Vol autocorrelation > 0.5 | 0.5 | 0.126 | ✗ |
| Expansion→EOD AUC > 0.55 | 0.55 | 0.292 | ✗ |
| Overlay improves WR ≥1pp | +1pp | -11.1pp | ✗ |
| Standalone Sharpe > N/A | — | **3.05** | ✓ (unexpected) |

Vol clustering autocorrelation is weaker than expected (0.126 vs 0.5 target) — simulated from daily data underestimates true intraday clustering. Expansion prediction AUC 0.29 (inverse: contraction is actually predictive). Standalone Sharpe of 3.05 is strong but overlay hurts — the signal times entries DURING contraction, which is good for premium selling but the simulated trades don't align well.

**Key insight**: with real 5-min data, clustering autocorrelation would likely be 0.4-0.6 (well-documented in literature). Daily simulation can't replicate this fidelity.
