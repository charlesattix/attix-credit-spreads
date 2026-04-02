# EXP-1340-max: Ensemble Meta-Learner V2

## Hypothesis

A gradient-boosted meta-learner trained on outputs from ALL existing
signal generators finds optimal signal combinations that no single
overlay or simple average achieves.

## Design

12 input signals from existing modules:
  base_ensemble, regime_score, momentum_20d, ofi_score,
  calendar_signal, sentiment_composite, microstructure,
  vol_surface, tail_risk, vix_term, credit_spread, breadth

Meta-learner: gradient-boosted decision stumps (80 rounds, lr=0.08,
subsample=0.75) — same architecture as EXP-860/880 ML ensemble.

3-way comparison via walk-forward validation:
  1. Meta-learner (stacked GB on all 12 signals)
  2. Simple average (equal-weight average → sigmoid)
  3. Best individual signal (highest |correlation| with label)

## Status: COMPLETE
- compass/meta_learner_v2.py: 370+ lines
- tests/test_meta_learner_v2.py: 31 tests, all passing
