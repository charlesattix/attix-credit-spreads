# Pipeline Audit — 2026-04-23

## 1. Cross-Vol Arb Wiring (Phase 9 blocker #7)

### Signal Flow Path

```
exp2020_cross_vol_arb.py::generate_today_signals(date)
  └→ delegates to exp2690_signal_generators.py::cross_vol_signals(date)
       └→ IV-RV ranking on SPY/QQQ/IWM/EEM (Yahoo + VIX proxy)
            └→ returns List[Dict] with unified signal schema
                 └→ consumed by scripts/generate_daily_signals.py
                      └→ writes compass/reports/daily_signals_YYYYMMDD.jsonl
```

### Duplication Assessment

**exp2020 → exp2690:** NO duplication. `generate_today_signals()` is a 4-line stub that imports and delegates to `cross_vol_signals()`. Clean architecture.

**exp2690 vs exp2830:** These are PARALLEL implementations, not duplicates.

| Aspect | exp2690 | exp2830 |
|---|---|---|
| Purpose | Central signal registry (8 streams) | Phase 9 paper trading |
| cross_vol logic | Multi-ticker IV-RV ranking (4 ETFs) | SPY-only VRP threshold (>2%) |
| Cadence | Monday-only | Daily (implied) |
| Output type | `List[Dict]` | `List[StreamSignal]` (dataclass) |
| Overlays | Minimal | VoV gate, term-structure, FOMC, regime |
| Sizing | None | Dollar-notional via `compute_notional_contracts()` |

**Design rationale:** exp2830 adds production risk gates and position sizing that exp2690 doesn't need (exp2690 is a signal audit/research tool). The two implementations are intentionally different.

### Naming Consistency

Resolved in the prior session (2026-04-21):
- GENERATOR_REGISTRY key: `cross_vol` (was `vol_arb`)
- Function: `cross_vol_signals()` with `vol_arb_signals` backward-compat alias
- All configs, audit, and pipeline code unified on `cross_vol`

### Smoke Tests Added

`tests/test_cross_vol_signal_pipeline.py` — **13 tests, all passing:**

| Test Class | Tests | Covers |
|---|---|---|
| `TestCrossVolSignalSchema` | 6 | Schema fields, stream name, action values, legs structure |
| `TestCrossVolCadence` | 2 | Monday-only enforcement |
| `TestExp2020Delegation` | 1 | exp2020 → exp2690 delegation identity |
| `TestRegistryIntegrity` | 4 | Registry keys, alias, 8-stream completeness |

### Remaining Test Gaps

- No integration test for full `generate_daily_signals.py` → JSONL output round-trip
- No test for exp2830's `_cross_vol_signal()` logic independently (only sizing is tested)
- No test for multi-ticker ranking correctness with controlled IV/RV inputs

---

## 2. Dead Code Inventory

Full manifest written to: `compass/PRODUCTION_MANIFEST.md`

### Summary

| Category | Count | Description |
|---|---|---|
| ENTRY | 7 | Production entry points |
| PRODUCTION_DEP | 18 | Directly imported by entry points |
| TRANSITIVE_DEP | 17 | Imported by production deps |
| STANDALONE | 105 | Completed experiments (have `__main__`) |
| UNKNOWN | ~264 | Utilities, libraries, dead code |
| **Total** | **~411** | All .py files in compass/ (excl. killed/, reports/) |

### Production Footprint

The entire production pipeline depends on **42 modules** (7 entry + 18 direct + 17 transitive). That's ~10% of the 411 compass/ modules. The remaining 90% are research experiments, utility libraries, and unlinked code.

### Key Findings

1. **No circular import issues** — stream modules (exp1220, exp2020, etc.) import exp2690 for their `generate_today_signals()` delegate, and exp2690 imports crisis_alpha for the v5 hedge. No problematic cycles.

2. **Dynamic loading is contained** — `importlib.import_module()` is used in 3 places: `generate_daily_signals.py`, `exp2670_paper_gonogo.py`, and `exp2900_v8a_consistency_audit.py`. All load known module paths from hardcoded lists.

3. **Crisis alpha version chain** — Production uses v5, which imports v3 and v4 transitively. v1 (`crisis_alpha.py`) and v2 are also in the chain. All 4 versions remain in the production graph.

4. **264 UNKNOWN modules** — These have no `__main__` block and are not in the production import graph. Many are likely utility libraries imported by STANDALONE experiments only. A full cross-reference (tracing imports from ALL 411 modules, not just production) would identify true dead code vs. experiment-only utilities.

### Archival Recommendations (DO NOT DELETE — inventory only)

**Safe to archive (never imported by anything):**
- `crypto/` subdirectory (8 files) — unused crypto strategy modules
- `ibit_*.py` (3 files) — IBIT-specific modules, no production path
- `rl_*.py` (3 files) — reinforcement learning experiments, never integrated
- `transformer_predictor.py` — ML experiment, not in any import chain

**Require further tracing before archiving:**
- `portfolio_risk_manager.py` — has tests but unclear if production uses it
- `execution_algo.py` — may be imported by Alpaca connector indirectly
- `walk_forward.py` — core framework used by many experiments

---

## Action Items

| Item | Priority | Status |
|---|---|---|
| cross_vol naming unified | Phase 9 blocker | DONE |
| Smoke tests for cross_vol pipeline | Phase 9 blocker | DONE (13 tests) |
| exp2020 → exp2690 delegation verified | Phase 9 blocker | DONE |
| Production manifest created | Post-Phase 9 | DONE |
| Full dead code sweep (all 411 modules) | Low | NOT STARTED |
| Archive crypto/, ibit_*, rl_* modules | Low | NOT STARTED |
