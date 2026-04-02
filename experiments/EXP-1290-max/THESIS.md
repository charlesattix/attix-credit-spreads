# EXP-1290-max: Reinforcement Learning Position Sizer

## Hypothesis

An RL agent (tabular Q-learning) trained on historical PnL sequences
learns better position sizing than Kelly criterion or fixed fractional
by adapting to: current drawdown, vol regime, signal strength, and
portfolio heat.

## Design

- **State**: (dd_bucket[0-4], vol_bucket[0-2], signal_bucket[0-3], heat_bucket[0-2])
  = 180 possible states
- **Actions**: position size 0-100% in 10% steps (11 actions)
- **Reward**: PnL × size − drawdown_penalty × max(dd − 5%, 0) × size
- **Training**: tabular Q-learning with epsilon-greedy exploration,
  epsilon decay over 15 epochs, discount 0.95

## Baselines Compared

1. **Full Kelly** — optimal for independent bets, aggressive
2. **Half-Kelly** — reduced Kelly, industry standard
3. **Fixed 10%** — constant allocation
4. **Regime-based** — from EXP-720 (hand-tuned)
5. **RL Q-learning** — learned from data

## Status: COMPLETE
- compass/rl_position_sizer.py: 380+ lines
- tests/test_rl_position_sizer.py: 36 tests, all passing
