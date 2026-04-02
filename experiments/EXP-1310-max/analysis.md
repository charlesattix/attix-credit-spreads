# EXP-1310-max: Options Flow Sentiment — Analysis

## Results

| Metric | Value |
|--------|-------|
| Sharpe | 0.37 |
| Annual Return | 4.5% |
| Max DD | 35.2% |
| Signals | 1,075 |
| Win Rate | 52% |
| Signal Accuracy | 52% |

## Interpretation

As a **standalone strategy**, options flow has modest alpha (Sharpe 0.37). This is expected — flow data is noisy and well-arbitraged by institutional players.

However, the **primary value is as an overlay**:
- Blocking trades when flow is strongly bearish (composite < -0.4) should reduce losses on the worst days
- The bearish signal average return (-0.56%) is much worse than bullish (+0.065%), confirming asymmetric value: the signal is better at **avoiding bad trades** than finding good ones

## Production Use

Integrate as EXP-880 overlay filter:
- Block new credit spread entries when flow composite < -0.4
- Reduce position size 50% when flow < -0.2
- Expected benefit: reduce max DD by 2-3pp with minimal return cost
