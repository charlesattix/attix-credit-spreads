# EXP-1210-max: Bayesian Strategy Selection

## Hypothesis
Thompson Sampling with Normal-Inverse-Gamma posteriors dynamically allocates capital to the best-performing strategies, outperforming static equal-weight, risk parity, and Markowitz allocations.

## Module
`compass/bayesian_selector.py` — 43/43 tests passing

## Method
- Each strategy is a bandit arm with NIG(μ₀, κ₀, α₀, β₀) prior
- Daily returns update posteriors via conjugate Bayesian update
- Thompson Sampling: sample μ from marginal posterior, allocate via softmax
- Regret tracked vs oracle (best single strategy in hindsight)
