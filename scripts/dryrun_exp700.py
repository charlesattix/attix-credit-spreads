#!/usr/bin/env python3
"""
dryrun_exp700.py — EXP-700 ML-Champion offline validation.

Verifies entirely offline (no Alpaca/Polygon calls):
  1.  Config file loads with correct EXP-400 params
  2.  Ensemble model loads from ml/models/ensemble_model_20260324.joblib
  3.  Feature stats schema matches the 35-feature training spec
  4.  Feature vector builds correctly (shape, no NaNs, correct OHE encoding)
  5.  ML prediction runs end-to-end and returns probability in [0,1]
  6.  Regime detector returns one of {bull, bear, neutral}
  7.  Strike computation is consistent with OTM % and spread width
  8.  Expiration selection returns a valid future Friday date
  9.  ExecutionEngine instantiates without Alpaca (dry-run mode)
  10. Scanner instantiates with correct params from config

Usage:
    python3 scripts/dryrun_exp700.py
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")

PASS = "\033[32m✓ PASS\033[0m"
FAIL = "\033[31m✗ FAIL\033[0m"
_failures = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {PASS if cond else FAIL}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        _failures.append(label)
    return cond


EXPECTED_FEATURES = [
    "dte_at_entry", "hold_days", "day_of_week", "days_since_last_trade",
    "rsi_14", "momentum_5d_pct", "momentum_10d_pct",
    "vix", "vix_percentile_20d", "vix_percentile_50d", "vix_percentile_100d",
    "iv_rank", "spy_price",
    "dist_from_ma20_pct", "dist_from_ma50_pct", "dist_from_ma80_pct", "dist_from_ma200_pct",
    "ma20_slope_ann_pct", "ma50_slope_ann_pct",
    "realized_vol_atr20", "realized_vol_5d", "realized_vol_10d", "realized_vol_20d",
    "net_credit", "spread_width", "max_loss_per_unit", "otm_pct", "contracts",
    "regime_neutral",
    "strategy_type_bear_call_spread", "strategy_type_bull_put_spread", "strategy_type_iron_condor",
    "spread_type_call", "spread_type_ic", "spread_type_put",
]


def test_config():
    print("\n[1] paper_exp700.yaml — config file")
    cfg_path = ROOT / "configs" / "paper_exp700.yaml"
    check("config file exists", cfg_path.exists())
    if not cfg_path.exists():
        return {}
    try:
        import yaml
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
    except Exception as exc:
        check("config parses", False, str(exc)); return {}

    check("experiment_id = EXP-700",     cfg.get("experiment_id") == "EXP-700")
    check("paper_mode = true",           cfg.get("paper_mode") is True)
    check("db_path contains exp700",     "exp700" in cfg.get("db_path", ""))

    s, r, ml = cfg.get("strategy", {}), cfg.get("risk", {}), cfg.get("ml_filter", {})
    check("target_dte = 15",             s.get("target_dte") == 15,            f"got {s.get('target_dte')}")
    check("otm_pct = 0.02",             abs(s.get("otm_pct", 0) - 0.02) < 1e-5, f"got {s.get('otm_pct')}")
    check("spread_width = 12",          s.get("spread_width") == 12,           f"got {s.get('spread_width')}")
    check("max_risk_per_trade = 8.5",   abs(r.get("max_risk_per_trade", 0) - 8.5) < 1e-5)
    check("profit_target = 55",         r.get("profit_target") == 55)
    check("stop_loss_multiplier = 1.25",abs(r.get("stop_loss_multiplier", 0) - 1.25) < 1e-5)
    check("ml_filter.enabled = true",   ml.get("enabled") is True)
    check("ml threshold = 0.65",        abs(ml.get("probability_threshold", 0) - 0.65) < 1e-5)
    return cfg


def test_model_load():
    print("\n[2] Ensemble model load")
    model_path = ROOT / "ml" / "models" / "ensemble_model_20260324.joblib"
    stats_path = ROOT / "ml" / "models" / "ensemble_model_20260324.feature_stats.json"
    check("model file exists", model_path.exists())
    check("stats file exists", stats_path.exists())
    if not model_path.exists():
        return None, []
    try:
        from compass.ensemble_signal_model import EnsembleSignalModel
        m = EnsembleSignalModel(model_dir=str(model_path.parent))
        loaded = m.load(model_path.name)
        check("model.load() True",           loaded)
        check("model.trained = True",        m.trained)
        check("3 calibrated models",         len(m.calibrated_models) == 3, f"got {len(m.calibrated_models)}")
        check("35 feature names",            len(m.feature_names) == 35,    f"got {len(m.feature_names)}")
        return m, list(m.feature_names)
    except Exception as exc:
        check("ensemble imports + loads", False, str(exc))
        return None, []


def test_feature_schema(feature_names: list):
    print("\n[3] Feature schema validation")
    stats_path = ROOT / "ml" / "models" / "ensemble_model_20260324.feature_stats.json"
    if not stats_path.exists():
        check("stats file exists", False); return [], []
    with open(stats_path) as f:
        stats = json.load(f)
    names  = stats.get("feature_names", [])
    means  = stats.get("feature_means", [])
    stds   = stats.get("feature_stds",  [])
    check("feature count = 35",        len(names) == 35,        f"got {len(names)}")
    check("means length matches",      len(means) == len(names))
    check("stds length matches",       len(stds)  == len(names))
    missing = [f for f in EXPECTED_FEATURES if f not in names]
    check("all 35 expected features",  not missing, f"missing: {missing}")
    name_to_std = dict(zip(names, stds))
    for key in ["dte_at_entry", "hold_days", "net_credit", "contracts"]:
        check(f"{key} std > 0",        name_to_std.get(key, 0) > 0, f"std={name_to_std.get(key, 0):.4f}")
    return names, means


def test_feature_vector(feature_names: list, feature_means: list):
    print("\n[4] Feature vector construction")
    import numpy as np
    from scripts.exp700_ml_scanner import _build_feature_vector

    spy_feats = {"rsi_14": 52.3, "momentum_5d_pct": 0.8, "momentum_10d_pct": 1.5,
                 "spy_price": 510.0, "dist_from_ma20_pct": 0.5, "dist_from_ma50_pct": 1.2,
                 "dist_from_ma80_pct": 2.0, "dist_from_ma200_pct": 4.5,
                 "ma20_slope_ann_pct": 12.0, "ma50_slope_ann_pct": 8.0,
                 "realized_vol_atr20": 0.9, "realized_vol_5d": 14.2,
                 "realized_vol_10d": 13.8, "realized_vol_20d": 15.1}
    vix_feats = {"vix": 18.5, "vix_percentile_20d": 42.0, "vix_percentile_50d": 38.0, "vix_percentile_100d": 35.0}

    vec = _build_feature_vector(
        dte=15, hold_days=15, day_of_week=1, days_since_last_trade=5,
        spy_feats=spy_feats, vix_feats=vix_feats, iv_rank=25.0,
        net_credit_dollars=85.0, spread_width=12.0, otm_pct=0.02,
        contracts=10, spread_type="bull_put", regime="bull",
        feature_names=feature_names, feature_means=feature_means,
    )
    check("shape = (35,)",                  vec.shape == (35,),     f"got {vec.shape}")
    check("no NaN values",                  not np.any(np.isnan(vec)))
    check("no Inf values",                  not np.any(np.isinf(vec)))
    fv = dict(zip(feature_names, vec.tolist()))
    check("dte_at_entry = 15",              abs(fv.get("dte_at_entry", -1) - 15) < 1e-6)
    check("strategy_type_bull_put = 1",     fv.get("strategy_type_bull_put_spread") == 1.0)
    check("strategy_type_bear_call = 0",    fv.get("strategy_type_bear_call_spread") == 0.0)
    check("regime_neutral = 0 (bull)",      fv.get("regime_neutral") == 0.0)
    check("spread_type_put = 1",            fv.get("spread_type_put") == 1.0)
    check("spread_type_call = 0",           fv.get("spread_type_call") == 0.0)
    return vec


def test_ml_prediction(model, vec):
    print("\n[5] ML prediction round-trip")
    if model is None:
        check("model available (skip)", False); return
    import numpy as np, pandas as pd
    names = list(model.feature_names)
    probs = model.predict_batch(pd.DataFrame(vec.reshape(1, -1), columns=names))
    check("predict_batch returns array", isinstance(probs, np.ndarray))
    check("probability in [0, 1]",      0.0 <= float(probs[0]) <= 1.0, f"got {float(probs[0]):.4f}")
    pred = model.predict(dict(zip(names, vec.tolist())))
    p = pred.get("probability", -1)
    check("predict() has probability",  isinstance(pred, dict) and "probability" in pred)
    check("probability field in [0,1]", 0.0 <= p <= 1.0, f"got {p}")
    print(f"       probability={p:.4f}  confidence={pred.get('confidence', '?')}")


def test_regime():
    print("\n[6] Regime detection")
    import numpy as np, pandas as pd
    from scripts.exp700_ml_scanner import _detect_regime

    n = 250
    dates  = pd.date_range("2024-01-01", periods=n)
    prices = pd.Series(np.linspace(400, 520, n), index=dates)
    spy_df = pd.DataFrame({"Close": prices, "High": prices * 1.01, "Low": prices * 0.99})
    vix_bull   = {"vix": 16.0, "vix_percentile_20d": 30.0, "vix_percentile_50d": 25.0, "vix_percentile_100d": 20.0}
    vix_crash  = {"vix": 45.0, "vix_percentile_20d": 95.0, "vix_percentile_50d": 90.0, "vix_percentile_100d": 85.0}
    rc = {"vix_extreme": 40.0, "vix_extreme_regime": "neutral", "ma_slow_period": 80,
          "ma200_neutral_band_pct": 0.5, "rsi_period": 14, "rsi_bull_threshold": 50.0,
          "rsi_bear_threshold": 45.0, "vix_structure_bull": 0.95, "vix_structure_bear": 1.05,
          "bear_requires_unanimous": True}

    check("synthetic bull → bull",  _detect_regime(spy_df, vix_bull, rc) == "bull",
          f"got {_detect_regime(spy_df, vix_bull, rc)}")
    check("VIX > 40 → neutral",     _detect_regime(spy_df, vix_crash, rc) == "neutral",
          f"got {_detect_regime(spy_df, vix_crash, rc)}")


def test_strikes():
    print("\n[7] Strike computation")
    from scripts.exp700_ml_scanner import _round_strike
    price, otm, width = 500.0, 0.02, 12.0
    short_put = _round_strike(price * (1.0 - otm))
    long_put  = _round_strike(short_put - width)
    check("bull_put short < price",  short_put < price)
    check("bull_put long < short",   long_put < short_put)
    check("bull_put width = $12",    abs(short_put - long_put - width) < 1.0, f"spread={short_put - long_put:.1f}")
    check("bull_put short ≈ 490",    abs(short_put - 490) <= 2.0, f"got {short_put}")
    short_call = _round_strike(price * (1.0 + otm))
    long_call  = _round_strike(short_call + width)
    check("bear_call short > price", short_call > price)
    check("bear_call long > short",  long_call > short_call)
    check("bear_call width = $12",   abs(long_call - short_call - width) < 1.0)


def test_expiration():
    print("\n[8] Expiration selection")
    from datetime import date
    from scripts.exp700_ml_scanner import _find_target_expiration
    exp_str  = _find_target_expiration(15)
    exp_date = date.fromisoformat(exp_str)
    dte      = (exp_date - date.today()).days
    check("valid future date",   exp_date > date.today(), f"got {exp_str}")
    check("is a Friday",         exp_date.weekday() == 4, f"weekday={exp_date.weekday()}")
    check("DTE >= 14",           dte >= 14,               f"DTE={dte}")
    check("DTE <= 21",           dte <= 21,               f"DTE={dte}")


def test_execution_engine():
    print("\n[9] ExecutionEngine — dry-run (no Alpaca)")
    try:
        from execution.execution_engine import ExecutionEngine
        engine = ExecutionEngine(alpaca_provider=None, db_path=":memory:")
        check("instantiates",         True)
        check("alpaca is None",       engine.alpaca is None)
    except Exception as exc:
        check("import + init",        False, str(exc))


def test_scanner_params():
    print("\n[10] EXP700Scanner — param validation")
    try:
        import yaml
        cfg_path = ROOT / "configs" / "paper_exp700.yaml"
        with open(cfg_path) as f:
            config = yaml.safe_load(f)
        from scripts.exp700_ml_scanner import EXP700Scanner
        s = EXP700Scanner(config=config, dry_run=True)
        check("target_dte = 15",    s.target_dte == 15)
        check("otm_pct = 0.02",     abs(s.otm_pct - 0.02) < 1e-6)
        check("spread_width = 12",  s.spread_width == 12.0)
        check("max_risk_pct = 8.5", abs(s.max_risk_pct - 8.5) < 1e-6)
        check("ml_filter present",  s.ml_filter is not None)
        check("ml threshold = 0.65",abs(s.ml_filter.threshold - 0.65) < 1e-6)
        check("model loaded or fail_open", s.ml_filter.fail_open or s.ml_filter._loaded)
        check("ic_enabled = True",  s.ic_enabled is True)
    except Exception as exc:
        check("scanner init", False, str(exc))


if __name__ == "__main__":
    print("=" * 65)
    print("  EXP-700 ML Champion — Deployment Dry-Run Verification")
    print("=" * 65)

    cfg                = test_config()
    model, fnames      = test_model_load()
    fnames, fmeans     = test_feature_schema(fnames)
    vec                = test_feature_vector(fnames, fmeans) if fnames else None
    if model is not None and vec is not None:
        test_ml_prediction(model, vec)
    else:
        print("\n[5] ML prediction round-trip  [SKIP — model or vector unavailable]")
    test_regime()
    test_strikes()
    test_expiration()
    test_execution_engine()
    test_scanner_params()

    print("\n" + "=" * 65)
    n_fail = len(_failures)
    if n_fail == 0:
        print("  \033[32mALL CHECKS PASSED — EXP-700 ready for paper trading\033[0m")
    else:
        print(f"  \033[31m{n_fail} FAILED: {', '.join(_failures)}\033[0m")
    print("=" * 65)
    sys.exit(0 if n_fail == 0 else 1)
