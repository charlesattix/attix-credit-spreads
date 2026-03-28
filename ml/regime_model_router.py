"""
ml/regime_model_router.py — Regime-aware position size multiplier router.

Maps market regime strings (bull/bear/neutral/high_vol/low_vol/crash) to
risk multipliers for ML V2 Aggressive sizing.

"Aggressive" profile:
  bull     → max_mult (default 1.50) — lean in hard
  neutral  → 1.00                    — normal sizing
  low_vol  → 1.20                    — calm markets: slightly more
  high_vol → min_mult (default 0.10) — very defensive
  bear     → min_mult (default 0.10) — nearly flat
  crash    → 0.00                    — fully out

Optionally loads the pre-trained signal model to blend ML confidence
into the multiplier.  When no model is available it degrades gracefully
to pure regime-table lookup.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL_PATH = ROOT / "ml" / "models" / "signal_model_20260217.joblib"


class RegimeModelRouter:
    """
    Maps market regime → position-size multiplier.

    Config keys (all optional):
        min_mult        float  0.10  — floor multiplier (bear/high_vol regimes)
        max_mult        float  1.50  — ceiling multiplier (bull regime)
        neutral_mult    float  1.00  — neutral regime multiplier
        low_vol_mult    float  1.20  — low-vol regime multiplier
        crash_mult      float  0.00  — crash regime multiplier
        use_signal_model bool True   — blend pre-trained ML model confidence
        model_path      str   None   — path to .joblib model (default: latest)
        ml_blend_weight float  0.25  — weight of ML confidence adjustment
                                        (0 = pure regime table, 1 = pure ML)
        shadow_ensemble bool  False  — wrap primary model with ShadowEnsemble;
                                        loads latest ensemble_model_*.joblib as
                                        shadow alongside the primary signal model.
                                        Primary still controls all live decisions;
                                        shadow predictions are logged only.
    """

    # Maps regime label (from MarketSnapshot.regime or ComboRegimeDetector) to
    # the config key that controls its multiplier.
    _REGIME_KEY_MAP = {
        "bull":     "max_mult",
        "neutral":  "neutral_mult",
        "low_vol":  "low_vol_mult",
        "high_vol": "min_mult",
        "bear":     "min_mult",
        "crash":    "crash_mult",
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self._min_mult     = float(cfg.get("min_mult",      0.10))
        self._max_mult     = float(cfg.get("max_mult",      1.50))
        self._neutral_mult = float(cfg.get("neutral_mult",  1.00))
        self._low_vol_mult = float(cfg.get("low_vol_mult",  1.20))
        self._crash_mult   = float(cfg.get("crash_mult",    0.00))
        self._blend_weight    = float(cfg.get("ml_blend_weight", 0.25))
        self._use_model       = bool(cfg.get("use_signal_model", True))
        self._shadow_ensemble = bool(cfg.get("shadow_ensemble", False))

        # Resolved multiplier table
        self._mult_table: Dict[str, float] = {
            "bull":     self._max_mult,
            "neutral":  self._neutral_mult,
            "low_vol":  self._low_vol_mult,
            "high_vol": self._min_mult,
            "bear":     self._min_mult,
            "crash":    self._crash_mult,
        }

        # Defensive regime set — used for logging and hard gate
        self._defensive_regimes = {"high_vol", "bear", "crash"}

        # Optional ML model
        self._model: Any = None
        if self._use_model:
            model_path = Path(cfg.get("model_path", str(_DEFAULT_MODEL_PATH)))
            primary = self._load_model(model_path)
            if primary is not None and self._shadow_ensemble:
                self._model = self._wrap_shadow(primary, model_path.parent)
            else:
                self._model = primary

        log.info(
            "RegimeModelRouter: min=%.2f neutral=%.2f max=%.2f "
            "low_vol=%.2f crash=%.2f model=%s",
            self._min_mult, self._neutral_mult, self._max_mult,
            self._low_vol_mult, self._crash_mult,
            "loaded" if self._model else "none",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def get_multiplier(self, regime: Optional[str]) -> float:
        """Return the risk multiplier for the given regime string.

        Args:
            regime: regime label from MarketSnapshot.regime or
                    ComboRegimeDetector (bull/bear/neutral/high_vol/low_vol/crash).
                    None is treated as "neutral".

        Returns:
            float in [0.0, max_mult]
        """
        r = (regime or "neutral").lower().strip()
        mult = self._mult_table.get(r, self._neutral_mult)
        return mult

    def get_multiplier_with_metadata(
        self, regime: Optional[str], ml_features: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Return multiplier plus audit metadata for logging/Telegram.

        Args:
            regime:      regime label string
            ml_features: optional feature dict for ML model scoring

        Returns:
            {
              "multiplier": float,
              "regime":     str,
              "is_defensive": bool,
              "ml_confidence": float | None,
            }
        """
        r = (regime or "neutral").lower().strip()
        base_mult = self._mult_table.get(r, self._neutral_mult)

        ml_confidence: Optional[float] = None
        if self._model is not None and ml_features:
            try:
                ml_confidence = self._score_features(ml_features)
                # Blend: mult = base_mult × (1 + blend_weight × (confidence - 0.5) × 2)
                # When confidence=1.0: mult boosted by blend_weight
                # When confidence=0.0: mult reduced by blend_weight
                adj = self._blend_weight * (ml_confidence - 0.5) * 2.0
                blended = base_mult * (1.0 + adj)
                blended = max(0.0, min(self._max_mult, blended))
                mult = blended
            except Exception as exc:
                log.debug("ML model scoring failed: %s — using table only", exc)
                mult = base_mult
        else:
            mult = base_mult

        return {
            "multiplier":    round(mult, 4),
            "regime":        r,
            "is_defensive":  r in self._defensive_regimes,
            "ml_confidence": round(ml_confidence, 3) if ml_confidence is not None else None,
        }

    def is_defensive(self, regime: Optional[str]) -> bool:
        """True if current regime is bear, high_vol, or crash."""
        r = (regime or "neutral").lower().strip()
        return r in self._defensive_regimes

    def describe(self) -> str:
        lines = ["RegimeModelRouter (ML V2 Aggressive):"]
        for regime, mult in sorted(self._mult_table.items()):
            tag = " ← DEFENSIVE" if regime in self._defensive_regimes else ""
            lines.append(f"  {regime:10s} → {mult:.2f}x{tag}")
        if self._model:
            lines.append(f"  ML model: loaded (blend={self._blend_weight:.2f})")
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _load_model(self, path: Path) -> Any:
        """Load a model from *path*; return None on failure (graceful degradation).

        Files named ``ensemble_model_*.joblib`` are loaded via
        EnsembleSignalModel so the ensemble dict payload is handled
        correctly (GAP-7).  Legacy ``signal_model_*.joblib`` files
        (raw XGBClassifier payloads) are loaded with joblib directly.
        """
        if not path.exists():
            log.warning("RegimeModelRouter: model not found at %s — running without ML", path)
            return None
        try:
            if path.name.startswith("ensemble_model_"):
                # Delegate to EnsembleSignalModel — its payload is a dict,
                # not a raw sklearn classifier, and calling predict_proba()
                # on it directly would fail (GAP-7).
                from compass.ensemble_signal_model import EnsembleSignalModel
                model = EnsembleSignalModel(model_dir=str(path.parent))
                if model.load(path.name):
                    log.info("RegimeModelRouter: loaded EnsembleSignalModel from %s", path.name)
                    return model
                log.warning(
                    "RegimeModelRouter: EnsembleSignalModel.load() failed for %s", path.name
                )
                return None
            else:
                # Legacy raw sklearn/XGBoost classifier payload.
                import joblib
                model = joblib.load(str(path))
                log.info("RegimeModelRouter: loaded legacy model from %s", path.name)
                return model
        except Exception as exc:
            log.warning("RegimeModelRouter: failed to load model (%s) — running without ML", exc)
            return None

    def _wrap_shadow(self, primary: Any, model_dir: Path) -> Any:
        """Attempt to load the latest ensemble_model_*.joblib and wrap primary
        in a ShadowEnsemble.  Returns primary unchanged if no ensemble model is
        found or if loading fails.
        """
        # Find the most-recently-modified ensemble model in the same dir.
        candidates = sorted(
            model_dir.glob("ensemble_model_*.joblib"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            log.warning(
                "ShadowEnsemble requested but no ensemble_model_*.joblib found "
                "in %s — running primary only",
                model_dir,
            )
            return primary

        ensemble_path = candidates[0]
        try:
            from compass.ensemble_signal_model import EnsembleSignalModel
            shadow_model = EnsembleSignalModel(model_dir=str(model_dir))
            if not shadow_model.load(ensemble_path.name):
                log.warning(
                    "ShadowEnsemble: EnsembleSignalModel.load() failed for %s "
                    "— running primary only",
                    ensemble_path.name,
                )
                return primary

            from compass.shadow_ensemble import ShadowEnsemble
            wrapped = ShadowEnsemble(primary, shadow_model)
            log.info(
                "ShadowEnsemble activated: primary=%s  shadow=%s",
                type(primary).__name__,
                ensemble_path.name,
            )
            return wrapped
        except Exception as exc:
            log.warning(
                "ShadowEnsemble: failed to load shadow model (%s) — running primary only",
                exc,
            )
            return primary

    def _score_features(self, features: Dict) -> float:
        """Run features through the loaded ML model, return probability in [0, 1].

        Handles two model types (GAP-7):
          * EnsembleSignalModel / SignalModel — call predict(features_dict),
            read the "probability" key from the returned PredictionResult.
          * Raw sklearn-compatible classifier (legacy signal_model_*.joblib) —
            build a sorted feature vector and call predict_proba().
        """
        # Our model classes (SignalModel, EnsembleSignalModel) expose a
        # predict(Dict) → PredictionResult interface and set self.trained.
        # Raw sklearn classifiers don't have 'trained'.
        if hasattr(self._model, "trained"):
            result = self._model.predict(features)
            return float(result.get("probability", 0.5))

        # Legacy raw sklearn-compatible classifier path.
        import numpy as np
        keys = sorted(features.keys())
        vec = np.array([[features[k] for k in keys]], dtype=float)
        if hasattr(self._model, "predict_proba"):
            probs = self._model.predict_proba(vec)[0]
            return float(probs[1]) if len(probs) > 1 else float(probs[0])
        pred = self._model.predict(vec)[0]
        return float(bool(pred))
