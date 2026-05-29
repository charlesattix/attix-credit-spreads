"""Tests for compass/live/vrp_risk_parity.py (PR-C: LW risk-parity sizing).

Covers: numerical correctness vs known LW / ERC closed forms, vol-target
convergence + leverage clamping, cold-start prior fallback, and NaN/degenerate
robustness. Pure + fast — no network, no DB.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compass.live.vrp_risk_parity import (  # noqa: E402
    MAX_SCALE,
    MIN_SCALE,
    TRADING_DAYS,
    VRP_STREAMS,
    cold_start_covariance,
    compute_weights,
    ledoit_wolf_covariance,
    load_prior_covariance,
    risk_parity_weights,
    scale_to_vol_target,
)


def _returns(n_days, n_streams, sigma=0.01, seed=0, cols=None):
    rng = np.random.default_rng(seed)
    cols = cols or [f"s{i}" for i in range(n_streams)]
    idx = pd.bdate_range("2026-01-01", periods=n_days)
    data = rng.normal(0.0, sigma, size=(n_days, len(cols)))
    return pd.DataFrame(data, index=idx, columns=cols)


# ── Numerical correctness: Ledoit-Wolf ──────────────────────────────────────

def test_ledoit_wolf_matches_sklearn_and_is_psd():
    from sklearn.covariance import LedoitWolf
    R = _returns(200, 5, seed=1).to_numpy()
    cov = ledoit_wolf_covariance(R)
    expected = LedoitWolf().fit(R).covariance_
    assert np.allclose(cov, expected)
    # symmetric + PSD
    assert np.allclose(cov, cov.T)
    assert np.linalg.eigvalsh(cov).min() >= -1e-12


# ── Numerical correctness: ERC closed forms ─────────────────────────────────

def test_erc_diagonal_closed_form_inverse_vol():
    # For a diagonal covariance Σ = diag(σ_i²), ERC weights are w_i ∝ 1/σ_i.
    sigmas = np.array([1.0, 2.0, 3.0])
    cov = np.diag(sigmas ** 2)
    w = risk_parity_weights(cov)
    expected = (1.0 / sigmas) / (1.0 / sigmas).sum()
    assert np.allclose(w, expected, atol=1e-6)


def test_erc_equal_risk_contribution_property():
    # On a general PSD covariance, every asset's risk contribution must be equal.
    R = _returns(300, 4, seed=2).to_numpy()
    cov = ledoit_wolf_covariance(R)
    w = risk_parity_weights(cov)
    rc = w * (cov @ w)                 # per-asset risk contribution
    assert np.allclose(rc, rc.mean(), rtol=1e-3, atol=1e-12)
    assert np.all(w >= 0) and abs(w.sum() - 1.0) < 1e-9


def test_erc_identity_is_equal_weight():
    w = risk_parity_weights(np.eye(6))
    assert np.allclose(w, np.ones(6) / 6, atol=1e-6)


def test_risk_parity_edge_dims():
    assert risk_parity_weights(np.zeros((0, 0))).size == 0
    assert np.allclose(risk_parity_weights(np.array([[0.04]])), [1.0])


# ── compute_weights contract ────────────────────────────────────────────────

def test_compute_weights_sums_to_one_and_keys_match():
    df = _returns(120, 8, seed=3, cols=list(VRP_STREAMS))
    w = compute_weights(df)
    assert set(w.keys()) == set(VRP_STREAMS)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert all(v >= 0 for v in w.values())


def test_compute_weights_single_stream():
    df = _returns(80, 1, cols=["only"])
    assert compute_weights(df) == {"only": 1.0}


def test_compute_weights_empty_returns_empty():
    assert compute_weights(pd.DataFrame()) == {}


def test_compute_weights_rejects_nonpositive_vol_target():
    df = _returns(80, 3)
    with pytest.raises(ValueError):
        compute_weights(df, vol_target=0.0)


def test_compute_weights_scaled_is_erc_times_scale():
    df = _returns(120, 4, sigma=0.01, seed=4)
    base = compute_weights(df, scaled=False)
    scaled = compute_weights(df, scaled=True, vol_target=0.12)
    ratios = [scaled[k] / base[k] for k in base if base[k] > 1e-9]
    # All streams scaled by the same scalar.
    assert max(ratios) - min(ratios) < 1e-9
    assert MIN_SCALE - 1e-9 <= ratios[0] <= MAX_SCALE + 1e-9


# ── Vol-target convergence + clamping ───────────────────────────────────────

def test_scale_to_vol_target_converges():
    # With scale un-clamped, the scaled portfolio's realized vol == vol_target exactly.
    df = _returns(300, 3, sigma=0.01, seed=5)
    w = compute_weights(df)
    scaled = scale_to_vol_target(w, df, vol_target=0.12)
    cols = list(w.keys())
    port = df[cols].fillna(0.0).to_numpy() @ np.array([scaled[c] for c in cols])
    realized = float(np.std(port, ddof=1)) * np.sqrt(TRADING_DAYS)
    assert abs(realized - 0.12) < 1e-6


def test_scale_clamped_at_max_for_tiny_vol():
    df = _returns(200, 2, sigma=1e-4, seed=6)   # ~0.16%/yr ⇒ scale wants ≫ MAX
    w = {c: 0.5 for c in df.columns}
    scaled = scale_to_vol_target(w, df, vol_target=0.12)
    assert all(abs(v - 0.5 * MAX_SCALE) < 1e-9 for v in scaled.values())


def test_scale_clamped_at_min_for_huge_vol():
    df = _returns(200, 2, sigma=0.5, seed=7)    # ~790%/yr ⇒ scale wants ≪ MIN
    w = {c: 0.5 for c in df.columns}
    scaled = scale_to_vol_target(w, df, vol_target=0.12)
    assert all(abs(v - 0.5 * MIN_SCALE) < 1e-9 for v in scaled.values())


def test_scale_no_data_defaults_to_unity():
    w = {"a": 0.5, "b": 0.5}
    assert scale_to_vol_target(w, pd.DataFrame(), 0.12) == w
    assert scale_to_vol_target({}, _returns(10, 2)) == {}


# ── Cold-start ──────────────────────────────────────────────────────────────

def test_cold_start_pure_prior_when_no_history():
    df = pd.DataFrame(columns=list(VRP_STREAMS))   # 0 rows
    cov, source, lam = cold_start_covariance(df, list(VRP_STREAMS), min_live_days=60)
    assert source == "prior" and lam == 0.0
    assert cov.shape == (8, 8)
    # compute_weights still produces a valid allocation from the prior alone.
    w = compute_weights(df)
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_cold_start_blends_with_partial_history():
    df = _returns(15, 8, seed=8, cols=list(VRP_STREAMS))   # 15 < 60 days
    cov, source, lam = cold_start_covariance(df, list(VRP_STREAMS), min_live_days=60)
    assert source == "blend"
    assert abs(lam - 15 / 60) < 1e-9


def test_cold_start_pure_live_when_enough_history():
    df = _returns(80, 8, seed=9, cols=list(VRP_STREAMS))   # 80 >= 60 days
    cov, source, lam = cold_start_covariance(df, list(VRP_STREAMS), min_live_days=60)
    assert source == "live" and lam == 1.0


def test_load_prior_covariance_from_file_aligns_streams(tmp_path):
    streams = ["a", "b", "c"]
    base = np.array([[4.0, 1.0, 0.0], [1.0, 9.0, 0.5], [0.0, 0.5, 16.0]])
    p = tmp_path / "prior.json"
    p.write_text(json.dumps({"streams": streams, "cov": base.tolist()}))
    # Request a reordered subset + one unknown stream → unknown gets a diagonal entry.
    prior = load_prior_covariance(["c", "a", "zzz"], path=p)
    assert prior.shape == (3, 3)
    assert prior[0, 0] == 16.0 and prior[1, 1] == 4.0    # c, a diagonals preserved
    assert prior[0, 1] == 0.0                            # cov(c,a) from file
    assert prior[2, 0] == 0.0 and prior[2, 2] > 0        # unknown stream uncorrelated, finite var


def test_load_prior_missing_file_returns_none(tmp_path):
    assert load_prior_covariance(["a", "b"], path=tmp_path / "nope.json") is None


def test_explicit_prior_cov_is_used():
    df = pd.DataFrame(columns=["a", "b", "c"])           # no history → prior path
    prior = np.diag([1.0, 4.0, 9.0])                     # σ = 1,2,3 ⇒ w ∝ 1/σ
    w = compute_weights(df, prior_cov=prior)
    expected = (1.0 / np.array([1.0, 2.0, 3.0]))
    expected = expected / expected.sum()
    got = np.array([w["a"], w["b"], w["c"]])
    assert np.allclose(got, expected, atol=1e-6)


# ── Robustness: NaN / inf / degenerate ──────────────────────────────────────

def test_nan_and_inf_are_handled():
    df = _returns(100, 4, seed=10)
    df.iloc[3, 1] = np.nan
    df.iloc[7, 2] = np.inf
    df.iloc[9, 0] = -np.inf
    df.iloc[20] = np.nan                                 # whole all-NaN row
    w = compute_weights(df)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert all(np.isfinite(v) for v in w.values())


def test_all_zero_returns_does_not_crash():
    df = pd.DataFrame(0.0, index=pd.bdate_range("2026-01-01", periods=90),
                      columns=list("abcd"))
    w = compute_weights(df)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    # zero-variance streams ⇒ roughly equal weight, all finite
    assert all(np.isfinite(v) for v in w.values())


def test_one_dead_stream_still_allocates_all():
    df = _returns(120, 4, seed=11, cols=["a", "b", "c", "dead"])
    df["dead"] = 0.0                                     # never trades
    w = compute_weights(df)
    assert set(w) == {"a", "b", "c", "dead"}
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert all(v >= 0 for v in w.values())
