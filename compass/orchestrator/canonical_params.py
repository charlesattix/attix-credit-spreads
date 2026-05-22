"""compass/orchestrator/canonical_params.py — Per-sleeve frozen parameter registry.

This is the single source of truth for the parameters every sleeve was
backtested under. Both the entry_gate (gate 3 — param-drift check) and
the position_sizer consult this registry to detect drift between live
intent and the locked backtest configuration.

Usage
-----
    from compass.orchestrator.canonical_params import load_canonical_params

    registry = load_canonical_params()                # default YAML path
    params = registry.get("exp1220")                  # → CanonicalParams
    result = registry.validate_signal(signal_intent)  # → ValidationResult
    if not result.ok:
        for r in result.reasons:
            log.warning(r)

The registry is loaded once per orchestrator run; it is immutable
thereafter. Any change to canonical_params.yaml requires a PR.

See ORCHESTRATOR_PROPOSAL.md §4.1 gate 3 and §4.2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from compass.orchestrator.types import SignalIntent

LOG = logging.getLogger(__name__)

_DEFAULT_YAML_PATH = Path(__file__).resolve().parent / "canonical_params.yaml"

# Sleeve structure strings recognised by the orchestrator. Anything else
# in the YAML is rejected at load time so typos don't silently degrade
# downstream behaviour.
_KNOWN_STRUCTURES = frozenset({
    "put_credit_spread",
    "call_credit_spread",
    "iron_condor",
    "calendar_spread",
    "iv_rv_pair",
    "equity_etf",
})


# ───────────────────────────────────────────────────────────────────────────
# Dataclasses
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CanonicalParams:
    """Frozen per-sleeve backtest parameters.

    Optional fields (delta / dte / width / otm_pct) are None for sleeves
    where the parameter does not apply (e.g. equity baskets).
    """

    stream: str
    structure: str
    ticker: str

    # Strike / DTE / width targets
    delta: Optional[float]
    dte: Optional[int]
    width: Optional[float]
    otm_pct: Optional[float]

    # Position management
    profit_target: float
    stop_mult: float
    min_spacing_days: int
    risk_per_trade_pct: float
    max_contracts: int
    vix_block: float

    # Portfolio / tradability
    portfolio_weight: float
    tradable: bool
    untradeable_reason: Optional[str]
    liquidity_oi_floor: int

    # Drift tolerances (per-sleeve overrides of defaults)
    delta_tol: float
    dte_tol: int
    width_tol: float

    # Provenance
    source: str

    @property
    def is_credit_spread(self) -> bool:
        """True for the structures the position_sizer can size in v1."""
        return self.structure in {
            "put_credit_spread",
            "call_credit_spread",
            "iron_condor",
        }


@dataclass(frozen=True)
class PortfolioParams:
    """Portfolio-level invariants consumed by the position_sizer."""

    port_risk_cap_pct: float    # gross dollar-at-risk / equity
    corr_threshold: float       # apply haircut above this avg ρ
    corr_min_scale: float       # haircut floor (never reduce below this)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a signal-vs-canonical comparison."""

    ok: bool
    reasons: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # allow `if not result: ...`
        return self.ok


# ───────────────────────────────────────────────────────────────────────────
# Registry
# ───────────────────────────────────────────────────────────────────────────

class CanonicalRegistry:
    """Read-only registry of canonical params, keyed by sleeve id."""

    def __init__(
        self,
        sleeves: Dict[str, CanonicalParams],
        portfolio: PortfolioParams,
        version: int = 1,
    ):
        self._sleeves = dict(sleeves)
        self.portfolio = portfolio
        self.version = version

    # ── inspection ────────────────────────────────────────────────────

    def streams(self) -> List[str]:
        return sorted(self._sleeves)

    def has(self, stream: str) -> bool:
        return stream in self._sleeves

    def get(self, stream: str) -> CanonicalParams:
        if stream not in self._sleeves:
            raise KeyError(f"unknown sleeve: {stream!r}")
        return self._sleeves[stream]

    # ── validation ────────────────────────────────────────────────────

    def validate_signal(self, signal: SignalIntent) -> ValidationResult:
        """Check that a SignalIntent matches its canonical record.

        Compares (delta, dte, width) for options sleeves and verifies the
        sleeve is marked tradable. Each drift beyond tolerance becomes a
        line in `result.reasons`; result.ok is True only when reasons is
        empty.

        Calendar / iv_rv_pair / equity_etf sleeves skip the strike-level
        checks (their canonical delta/dte/width are null by design).
        """
        if signal.stream not in self._sleeves:
            return ValidationResult(False, [f"unknown sleeve: {signal.stream!r}"])

        p = self._sleeves[signal.stream]
        reasons: List[str] = []

        if not p.tradable:
            reasons.append(
                f"untradeable: {p.untradeable_reason or 'sleeve disabled in canonical_params.yaml'}"
            )

        if p.delta is not None and signal.delta is not None:
            d = abs(float(signal.delta) - p.delta)
            if d > p.delta_tol:
                reasons.append(
                    f"param_drift: signal.delta={signal.delta:.3f} "
                    f"≠ canonical delta={p.delta:.3f} (|Δ|={d:.3f} > tol={p.delta_tol})"
                )

        if p.dte is not None and signal.dte is not None:
            d = abs(int(signal.dte) - p.dte)
            if d > p.dte_tol:
                reasons.append(
                    f"param_drift: signal.dte={signal.dte} "
                    f"≠ canonical dte={p.dte} (|Δ|={d} > tol={p.dte_tol})"
                )

        if p.width is not None and signal.width is not None:
            d = abs(float(signal.width) - p.width)
            if d > p.width_tol:
                reasons.append(
                    f"param_drift: signal.width={signal.width} "
                    f"≠ canonical width={p.width} (|Δ|={d:.2f} > tol={p.width_tol})"
                )

        return ValidationResult(ok=not reasons, reasons=reasons)


# ───────────────────────────────────────────────────────────────────────────
# Loader
# ───────────────────────────────────────────────────────────────────────────

def load_canonical_params(path: Optional[Path] = None) -> CanonicalRegistry:
    """Load and validate canonical_params.yaml.

    Raises
    ------
    FileNotFoundError: if the YAML file is missing.
    ValueError:        if a sleeve declares an unknown `structure` or
                       has a malformed entry.
    """
    yaml_path = Path(path) if path else _DEFAULT_YAML_PATH
    if not yaml_path.exists():
        raise FileNotFoundError(f"canonical_params.yaml not found at {yaml_path}")

    with yaml_path.open("r") as fh:
        raw = yaml.safe_load(fh) or {}

    defaults = raw.get("defaults") or {}
    portfolio_raw = raw.get("portfolio") or {}
    sleeves_raw = raw.get("sleeves") or {}

    portfolio = PortfolioParams(
        port_risk_cap_pct=float(portfolio_raw.get("port_risk_cap_pct", 0.20)),
        corr_threshold=float(portfolio_raw.get("corr_threshold", 0.50)),
        corr_min_scale=float(portfolio_raw.get("corr_min_scale", 0.50)),
    )

    sleeves: Dict[str, CanonicalParams] = {}
    for stream, body in sleeves_raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"sleeve {stream!r}: expected a mapping, got {type(body).__name__}")

        structure = body.get("structure")
        if structure not in _KNOWN_STRUCTURES:
            raise ValueError(
                f"sleeve {stream!r}: unknown structure {structure!r}; "
                f"valid: {sorted(_KNOWN_STRUCTURES)}"
            )

        sleeves[stream] = CanonicalParams(
            stream=stream,
            structure=structure,
            ticker=str(body.get("ticker", "")),
            delta=_opt_float(body, "delta"),
            dte=_opt_int(body, "dte"),
            width=_opt_float(body, "width"),
            otm_pct=_opt_float(body, "otm_pct"),
            profit_target=_with_default(body, defaults, "profit_target", 0.50, float),
            stop_mult=_with_default(body, defaults, "stop_mult", 2.0, float),
            min_spacing_days=_with_default(body, defaults, "min_spacing_days", 7, int),
            risk_per_trade_pct=_with_default(body, defaults, "risk_per_trade_pct", 0.02, float),
            max_contracts=_with_default(body, defaults, "max_contracts", 5, int),
            vix_block=_with_default(body, defaults, "vix_block", 40.0, float),
            portfolio_weight=float(body.get("portfolio_weight", 0.0)),
            tradable=bool(body.get("tradable", True)),
            untradeable_reason=body.get("untradeable_reason"),
            liquidity_oi_floor=_with_default(body, defaults, "liquidity_oi_floor", 50, int),
            delta_tol=_with_default(body, defaults, "delta_tol", 0.05, float),
            dte_tol=_with_default(body, defaults, "dte_tol", 4, int),
            width_tol=_with_default(body, defaults, "width_tol", 1.0, float),
            source=str(body.get("source", "")),
        )

    LOG.info(
        "loaded canonical params: %d sleeves, port_risk_cap=%.0f%%",
        len(sleeves), portfolio.port_risk_cap_pct * 100,
    )
    return CanonicalRegistry(
        sleeves=sleeves,
        portfolio=portfolio,
        version=int(raw.get("version", 1)),
    )


# ───────────────────────────────────────────────────────────────────────────
# Internal coercion helpers
# ───────────────────────────────────────────────────────────────────────────

def _opt_float(body: Dict, key: str) -> Optional[float]:
    v = body.get(key)
    return None if v is None else float(v)


def _opt_int(body: Dict, key: str) -> Optional[int]:
    v = body.get(key)
    return None if v is None else int(v)


def _with_default(body: Dict, defaults: Dict, key: str, fallback, caster):
    """Read body[key], else defaults[key], else fallback; cast through `caster`."""
    if key in body and body[key] is not None:
        return caster(body[key])
    if key in defaults and defaults[key] is not None:
        return caster(defaults[key])
    return caster(fallback)
