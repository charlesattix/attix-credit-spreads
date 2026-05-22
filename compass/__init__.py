"""compass — market regime, sizing, and risk analysis package."""

from compass.regime import Regime, RegimeClassifier, REGIME_INFO, ComboRegimeDetector
from compass.macro import MacroSnapshotEngine
from compass.macro_db import (
    init_db,
    get_db,
    get_current_macro_score,
    get_sector_rankings,
    get_event_scaling_factor,
    get_eligible_underlyings,
    save_snapshot,
    MACRO_DB_PATH,
    LIQUID_SECTOR_ETFS,
)
from compass.events import (
    get_upcoming_events,
    compute_composite_scaling,
    run_daily_event_check,
    ALL_FOMC_DATES,
)
from compass.risk_gate import RiskGate
from compass.sizing import calculate_dynamic_risk, get_contract_size, PositionSizer


def __getattr__(name):
    """Lazy-load heavy ML modules to avoid import-time crashes."""
    _ml_map = {
        "SignalModel": ("compass.signal_model", "SignalModel"),
        "EnsembleSignalModel": ("compass.ensemble_signal_model", "EnsembleSignalModel"),
        "FeatureEngine": ("compass.features", "FeatureEngine"),
        "IVAnalyzer": ("compass.iv_surface", "IVAnalyzer"),
        "MLEnhancedStrategy": ("compass.ml_strategy", "MLEnhancedStrategy"),
        "confidence_to_size_multiplier": ("compass.ml_strategy", "confidence_to_size_multiplier"),
        "RegimeModelRouter": ("compass.ml_strategy", "RegimeModelRouter"),
        "StressTester": ("compass.stress_test", "StressTester"),
        "CRISIS_SCENARIOS": ("compass.stress_test", "CRISIS_SCENARIOS"),
    }
    if name in _ml_map:
        import importlib
        module_path, attr = _ml_map[name]
        module = importlib.import_module(module_path)
        return getattr(module, attr)
    raise AttributeError(f"module 'compass' has no attribute {name!r}")

__all__ = [
    # regime
    "Regime",
    "RegimeClassifier",
    "REGIME_INFO",
    "ComboRegimeDetector",
    # macro
    "MacroSnapshotEngine",
    # macro_db
    "init_db",
    "get_db",
    "get_current_macro_score",
    "get_sector_rankings",
    "get_event_scaling_factor",
    "get_eligible_underlyings",
    "save_snapshot",
    "MACRO_DB_PATH",
    "LIQUID_SECTOR_ETFS",
    # events
    "get_upcoming_events",
    "compute_composite_scaling",
    "run_daily_event_check",
    "ALL_FOMC_DATES",
    # risk
    "RiskGate",
    # sizing
    "calculate_dynamic_risk",
    "get_contract_size",
    "PositionSizer",
    # ML (lazy-loaded via __getattr__ to avoid import-time crashes)
    "SignalModel",
    "EnsembleSignalModel",
    "FeatureEngine",
    "IVAnalyzer",
    "MLEnhancedStrategy",
    "confidence_to_size_multiplier",
    "RegimeModelRouter",
    "StressTester",
    "CRISIS_SCENARIOS",
]
