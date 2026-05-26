# ML & Infrastructure Production Readiness Evaluation Framework
## Attix Credit Spreads — March 19, 2026

---

## Philosophy

**We have a validated champion system (ROBUST 0.951, +40.7% avg, 6/6 years profitable).** Any new integration must PROVE it adds value without degrading what works. The burden of proof is on the new system, not on us to justify keeping the status quo.

**Principle: First, do no harm.**

---

## The 5-Gate Evaluation Framework

Every system must pass ALL 5 gates before production integration. Failure at any gate = STOP.

### Gate 1: Code Completeness & Quality (Pass/Fail)
| Criteria | How to Evaluate |
|----------|----------------|
| No TODO/FIXME/HACK in critical paths | `grep -rn "TODO\|FIXME\|HACK" <module>` |
| Error handling exists for all external calls | Manual review |
| No hardcoded paths, keys, or magic numbers | Manual review |
| Logging at appropriate levels | Manual review |
| Type hints on public interfaces | Manual review |
| Docstrings on classes and public methods | Manual review |
| No dead code or unused imports | `pylint` or manual review |

**Score: 1-10. Minimum to pass: 7**

### Gate 2: Test Coverage & Correctness (Pass/Fail)
| Criteria | How to Evaluate |
|----------|----------------|
| Unit tests exist for core logic | Check `tests/test_<module>.py` |
| Tests actually PASS | `pytest tests/test_<module>.py` |
| Edge cases covered (empty data, missing fields, NaN) | Review test file |
| Integration test with real data exists | Check for integration tests |
| No mocked-away critical logic | Review mocking strategy |

**Score: 1-10. Minimum to pass: 6**

### Gate 3: Data Dependency & Freshness (Pass/Fail)
| Criteria | How to Evaluate |
|----------|----------------|
| Required data sources identified | Document all inputs |
| Data currently available and accessible | Verify file/DB/API access |
| Data freshness acceptable (not stale) | Check timestamps |
| Training data sufficient (>500 samples for ML) | Count samples |
| No look-ahead bias in features | Review feature construction |
| Data pipeline can refresh automatically | Check scripts |

**Score: 1-10. Minimum to pass: 7**

### Gate 4: Backtester Integration Feasibility (Pass/Fail)
| Criteria | How to Evaluate |
|----------|----------------|
| Clear integration point exists | Map to PortfolioBacktester |
| Can run without live API calls (offline) | Review dependencies |
| Adds < 5x to backtest runtime | Benchmark |
| Compatible with existing config structure | Review champion.json |
| Doesn't require backtester rewrite | Estimate LOC changes |
| Can be A/B tested (toggle on/off) | Design review |

**Score: 1-10. Minimum to pass: 6**

### Gate 5: Impact Validation Protocol (CRITICAL)
| Step | Method |
|------|--------|
| **5a. Isolated backtest** | Run new system alone on same 2020-2025 data |
| **5b. A/B comparison** | Same trades, with vs without ML filter |
| **5c. Walk-forward test** | Train on 2020-2022, test on 2023-2025 |
| **5d. Degradation check** | Verify champion metrics don't worsen |
| **5e. Sensitivity test** | Perturb ML parameters ±20%, check stability |
| **5f. Combined ROBUST score** | Must be ≥ 0.90 (vs current 0.951) |

**This gate is non-negotiable. No shortcuts.**

---

## Decision Matrix Template

| System | Gate 1 | Gate 2 | Gate 3 | Gate 4 | Gate 5 | Verdict |
|--------|--------|--------|--------|--------|--------|---------|
| ML Signal Filter | ?/10 | ?/10 | ?/10 | ?/10 | TBD | ? |
| Combo Regime Detector | ?/10 | ?/10 | ?/10 | ?/10 | TBD | ? |
| ML Regime Detector | ?/10 | ?/10 | ?/10 | ?/10 | TBD | ? |
| Sector Expansion | ?/10 | ?/10 | ?/10 | ?/10 | TBD | ? |
| IV Analyzer | ?/10 | ?/10 | ?/10 | ?/10 | TBD | ? |
| ML Position Sizer | ?/10 | ?/10 | ?/10 | ?/10 | TBD | ? |

### Verdicts
- **GO** → Passes Gates 1-4 with scores ≥ threshold. Proceed to Gate 5 (backtest validation).
- **NEEDS WORK** → Passes 2-3 gates. Specific fixes required before re-evaluation.
- **NO-GO** → Fails 2+ gates. Not worth the integration risk. Shelf it.
- **REBUILD** → Concept is good but implementation is fundamentally flawed. Start over.

---

## Integration Playbook (For systems that pass Gates 1-4)

### Phase A: Shadow Mode (1 week)
- System runs alongside champion but does NOT affect trades
- Log predictions/signals to separate file
- Compare against actual outcomes
- Zero risk to production

### Phase B: Soft Filter (2 weeks)
- System can BLOCK trades (filter out bad ones) but never ADD trades
- Reduces exposure, can only help if model is good
- Compare filtered vs unfiltered results daily

### Phase C: Full Integration (4 weeks)
- System fully active including any new signals/trades
- Daily monitoring with automated deviation alerts
- Kill switch: revert to champion config if metrics degrade >10%

### Abort Criteria (Auto-revert at any phase)
- Win rate drops > 5pp below champion
- Max DD exceeds -10% (vs champion's -7%)
- 3 consecutive losing weeks
- ROBUST score drops below 0.85

---

## Anti-Patterns to Watch For

1. **Overfitting disguised as ML** — Model trained on same data it's tested on
2. **Feature leakage** — Using future information in features (e.g., close price of the same day)
3. **Survivorship bias** — Only modeling tickers that still exist
4. **Low sample size** — 18KB training file is suspicious. Need to verify trade count.
5. **Stale model** — Trained March 5, 2026. On what data? What was the validation score?
6. **Complexity for complexity's sake** — If simple regime detector gets 0.951 ROBUST, does ML actually add value?
7. **Sector diversification illusion** — More tickers ≠ more diversification if they're correlated

---

## Key Question to Answer

> "Does adding ML/advanced regime/sectors improve risk-adjusted returns WITHOUT degrading the proven system's robustness?"

If the answer isn't a clear YES backed by walk-forward evidence, we don't ship it.

---

*Framework designed by Maximus for Commander Carlos — March 19, 2026*
*"A good plan violently executed now is better than a perfect plan next week." — But we execute with discipline, not recklessness.*
