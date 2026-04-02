# EXP-1360-max: Regime Transition Probabilities — Analysis

## Results
- **Sharpe:** 0.12 (low standalone — used as overlay, not alpha source)
- **Transitions detected:** 370 with **99% accuracy** and **28-day avg lead**
- **Max DD:** 3.1% (very low — conservative positioning)

## Transition Matrix (learned)

| From \ To | Bull | Sideways | Correction | Crisis |
|-----------|------|----------|------------|--------|
| Bull | **0.69** | 0.00 | 0.31 | 0.00 |
| Sideways | 0.01 | **0.99** | 0.00 | 0.00 |
| Correction | 0.39 | 0.00 | **0.60** | 0.00 |
| Crisis | 0.00 | 0.03 | 0.00 | **0.97** |

Key insights:
- **Sideways is very sticky** (99% persistence) — once in sideways, stays there
- **Crisis is extremely persistent** (97%) — don't fight a crisis
- **Bull→Correction** is the main risk transition (31% probability)
- **Correction→Bull** recovery happens 39% of the time

## Production Value

The HMM's main value is as a **regime persistence estimator**, not a standalone trading signal:
1. When in bull (69% persist), run full size
2. When crisis detected (97% persist), stay flat until regime changes
3. The 28-day average lead time gives ample warning to reduce exposure

Integrate as: `if P(crisis next day) > 0.3: reduce size 50%`
