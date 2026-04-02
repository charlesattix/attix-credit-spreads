# EXP-1450-max: Universal Portfolio Strategy

## Hypothesis
Cover's Universal Portfolio (via Exponential Gradient) as a meta-allocator achieves log-optimality guarantees with O(√T log N) regret vs the best constant-rebalanced portfolio in hindsight.

## Module
`compass/universal_portfolio.py` — 35/35 tests passing

## Method
- EG update: w_{t+1,i} = w_{t,i} × exp(η × ∇_i) / Z
- η = √(8 ln N / T) from theory
- Regret tracked vs best CRP (grid search for N≤4, random for N>4)
- Compare: EG vs equal weight vs risk parity vs Thompson sampling
