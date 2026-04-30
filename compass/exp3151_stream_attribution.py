"""EXP-3151 — Per-stream Sharpe attribution (post-2020, equal-vega weights).

Motivation
----------
EXP-3150 confirmed v8a's net Sharpe is stable on the 2020-2024 sub-window
(post-2020 SR 6.40 vs full-period 6.39, ratio 1.003). The next defensive
question — flagged after Dew-Becker & Giglio (2025) — is whether the
post-2020 edge is concentrated in SPX/SPY (where they argue dealer GEX
flipped and VRP collapsed) or whether it actually lives in the sector
ETFs / calendars / cross-vol streams that should not depend on SPX VRP.

Methodology
-----------
1. Reuse v8a cube (same 8 streams as EXP-2600 / EXP-3150).
2. Slice to 2020-01-01 .. 2024-12-31.
3. Compute per-stream over the slice:
     - annualised mean, vol, Sharpe
     - 95% CI on Sharpe via stationary block bootstrap
       (mean block = 5 days, 5 000 resamples, fixed seed)
4. Build the equal-vega portfolio:
     - inverse-vol weights w_i = (1/sigma_i) / sum_j (1/sigma_j)
     - rescale weights so the portfolio's annualised vol equals
       EXP-2600's v8a target (18%) — this matches the production
       target_vol so contribution Sharpes are comparable to the
       EXP-3150 figures.
5. Decompose portfolio Sharpe:
     SR_p = sum_i  contrib_i
     contrib_i = w_i_scaled · mu_i · sqrt(252) / sigma_p_ann
     (Sums exactly to SR_p; standard linear-in-mean attribution.)
6. Also report risk contribution (component vol):
     risk_contrib_i = w_i_scaled · Cov(R_i, R_p) / sigma_p
     (Sums to sigma_p.)
7. Bootstrap the contribution Sharpe by resampling (stream returns
    jointly) with the same block-bootstrap scheme so the within-day
    cross-stream correlations are preserved.

Rule Zero: same v8a cube as EXP-2600 / EXP-3150. No synthetic data,
no parameter tuning beyond the rescale to EXP-2600's 18% target vol.

Outputs:
  compass/reports/exp3151_stream_attribution.json
  compass/reports/exp3151_stream_attribution.html
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compass.exp2600_north_star_v8 import (  # noqa: E402
    build_cubes,
    NET_DRAG_BPS,
    NET_DRAG_PCT,
    TRADING_DAYS,
)

REPORT_JSON = ROOT / "compass" / "reports" / "exp3151_stream_attribution.json"
REPORT_HTML = ROOT / "compass" / "reports" / "exp3151_stream_attribution.html"

WINDOW_START = pd.Timestamp("2020-01-01")
WINDOW_END = pd.Timestamp("2024-12-31")

# Match EXP-2600 v8a winning target vol so the contribution Sharpes
# are directly comparable to the production EXP-2600/EXP-3150 numbers.
TARGET_VOL_ANNUAL = 0.18

# Bootstrap config (same as EXP-3150 for consistency)
BOOTSTRAP_N = 5000
BOOTSTRAP_BLOCK_MEAN = 5
RNG_SEED = 20260428

STREAM_LABELS = {
    "exp1220":   "SPY put-credit (exp1220)",
    "v5_hedge":  "v5 hedge",
    "gld_cal":   "GLD calendar",
    "slv_cal":   "SLV calendar",
    "cross_vol": "Cross-vol",
    "xlf_cs":    "XLF credit spread",
    "xli_cs":    "XLI credit spread",
    "qqq_cs":    "QQQ credit spread",
}

# SPX-VRP-sensitive streams (per Dew-Becker / Giglio): pure index VRP
# harvesting. These should be the most exposed if their thesis is right.
SPX_LIKE = {"exp1220"}


# ── Bootstrap engine (joint, preserves cross-stream covariance) ──────


def _stationary_indices(n: int, mean_block: int, rng: np.random.Generator) -> np.ndarray:
    """Return n stationary-block-bootstrap row indices into a length-n array."""
    p = 1.0 / mean_block
    out = np.empty(n, dtype=np.int64)
    i = 0
    while i < n:
        start = int(rng.integers(0, n))
        block_len = int(rng.geometric(p))
        block_len = max(1, min(block_len, n - i))
        for j in range(block_len):
            out[i + j] = (start + j) % n
        i += block_len
    return out


def annualised_sharpe(r: np.ndarray) -> float:
    if len(r) < 2:
        return 0.0
    mu = float(np.mean(r))
    sd = float(np.std(r, ddof=1))
    return (mu / sd) * math.sqrt(TRADING_DAYS) if sd > 1e-12 else 0.0


def annualised_vol(r: np.ndarray) -> float:
    if len(r) < 2:
        return 0.0
    return float(np.std(r, ddof=1)) * math.sqrt(TRADING_DAYS)


def stream_sharpe_ci(
    r: np.ndarray, n_iter: int, mean_block: int, rng: np.random.Generator
) -> Dict[str, float]:
    samples = np.empty(n_iter, dtype=float)
    n = len(r)
    for k in range(n_iter):
        idx = _stationary_indices(n, mean_block, rng)
        samples[k] = annualised_sharpe(r[idx])
    return {
        "mean": float(np.mean(samples)),
        "lo95": float(np.quantile(samples, 0.025)),
        "hi95": float(np.quantile(samples, 0.975)),
        "std":  float(np.std(samples, ddof=1)),
        "n_iter": int(n_iter),
        "block_mean": int(mean_block),
    }


def joint_contribution_ci(
    cube_arr: np.ndarray, weights: np.ndarray,
    n_iter: int, mean_block: int, rng: np.random.Generator,
) -> Dict[str, Dict[str, float]]:
    """Bootstrap distribution of per-stream Sharpe contributions.

    For each bootstrap resample (stationary block on row indices,
    SAME indices applied to all streams to preserve cross-stream
    structure), recompute:
      contrib_i = w_i · mu_i · sqrt(252) / sigma_p_ann
    where sigma_p_ann is the resample's portfolio annualised vol.
    """
    n, m = cube_arr.shape
    contribs = np.empty((n_iter, m), dtype=float)
    sr_p = np.empty(n_iter, dtype=float)
    for k in range(n_iter):
        idx = _stationary_indices(n, mean_block, rng)
        boot = cube_arr[idx]
        port = boot @ weights
        sigma_p_ann = annualised_vol(port)
        if sigma_p_ann < 1e-12:
            contribs[k, :] = 0.0
            sr_p[k] = 0.0
            continue
        mu_ann = boot.mean(axis=0) * TRADING_DAYS
        contribs[k, :] = weights * mu_ann / sigma_p_ann
        sr_p[k] = float(port.mean() / port.std(ddof=1) * math.sqrt(TRADING_DAYS))
    out: Dict[str, Dict[str, float]] = {}
    for j in range(m):
        s = contribs[:, j]
        out[str(j)] = {
            "mean": float(np.mean(s)),
            "lo95": float(np.quantile(s, 0.025)),
            "hi95": float(np.quantile(s, 0.975)),
            "std":  float(np.std(s, ddof=1)),
        }
    out["__portfolio__"] = {
        "mean": float(np.mean(sr_p)),
        "lo95": float(np.quantile(sr_p, 0.025)),
        "hi95": float(np.quantile(sr_p, 0.975)),
        "std":  float(np.std(sr_p, ddof=1)),
    }
    return out


# ── Equal-vega weights + scaling ─────────────────────────────────────


def equal_vega_weights(vols: np.ndarray) -> np.ndarray:
    """Inverse-vol weights normalised to sum to 1."""
    inv = 1.0 / np.where(vols > 1e-12, vols, np.nan)
    inv = np.nan_to_num(inv, nan=0.0)
    s = inv.sum()
    if s <= 1e-12:
        return np.ones_like(vols) / len(vols)
    return inv / s


def rescale_to_target_vol(
    weights: np.ndarray, cube_arr: np.ndarray, target_vol_ann: float
) -> Tuple[np.ndarray, float]:
    """Multiply weights by a single scalar so portfolio realised
    annualised vol equals target_vol_ann."""
    port = cube_arr @ weights
    realised = annualised_vol(port)
    if realised < 1e-12:
        return weights.copy(), 1.0
    scale = target_vol_ann / realised
    return weights * scale, float(scale)


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 72)
    print("EXP-3151 — Per-stream Sharpe attribution (post-2020, eq-vega)")
    print("=" * 72)

    print("\n[1/5] Building v8a cube …")
    cubes = build_cubes()
    v8a = cubes["v8a_add_qqq"]
    sub = v8a.loc[(v8a.index >= WINDOW_START) & (v8a.index <= WINDOW_END)].copy()
    cols = list(sub.columns)
    print(f"      v8a slice: {sub.index[0].date()} .. {sub.index[-1].date()}  "
          f"shape {sub.shape}")
    print(f"      streams: {cols}")

    cube_arr = sub.to_numpy(dtype=float)
    n, m = cube_arr.shape

    # ── Per-stream standalone stats ──
    print("\n[2/5] Standalone per-stream stats (gross) …")
    rng = np.random.default_rng(RNG_SEED)
    standalone: Dict[str, Dict] = {}
    vols = np.empty(m, dtype=float)
    means_ann = np.empty(m, dtype=float)
    for j, c in enumerate(cols):
        r = cube_arr[:, j]
        nz = int((r != 0).sum())
        mu_ann = float(r.mean() * TRADING_DAYS)
        vol = annualised_vol(r)
        sr = annualised_sharpe(r)
        ci = stream_sharpe_ci(r, BOOTSTRAP_N, BOOTSTRAP_BLOCK_MEAN, rng)
        vols[j] = vol if vol > 1e-12 else 1e-12
        means_ann[j] = mu_ann
        standalone[c] = {
            "label": STREAM_LABELS.get(c, c),
            "spx_like": c in SPX_LIKE,
            "n_obs": n,
            "n_nonzero_days": nz,
            "mean_ann_pct": round(mu_ann * 100, 4),
            "vol_ann_pct": round(vol * 100, 4),
            "sharpe": round(sr, 4),
            "sharpe_ci95": {k: round(v, 4) if isinstance(v, float) else v
                            for k, v in ci.items()},
        }
        print(f"      {c:10s}  μ_ann {mu_ann*100:+7.2f}%  "
              f"σ_ann {vol*100:5.2f}%  SR {sr:6.3f}  "
              f"95% CI [{ci['lo95']:5.2f}, {ci['hi95']:5.2f}]  "
              f"nz_days={nz}")

    # ── Equal-vega weights ──
    print("\n[3/5] Equal-vega weights + scaling to target vol "
          f"({TARGET_VOL_ANNUAL*100:.0f}%) …")
    w_raw = equal_vega_weights(vols)
    w_scaled, scalar = rescale_to_target_vol(w_raw, cube_arr, TARGET_VOL_ANNUAL)
    port = cube_arr @ w_scaled
    sigma_p = annualised_vol(port)
    sr_p = annualised_sharpe(port)
    print(f"      vol-rescale scalar : {scalar:.4f}")
    print(f"      portfolio σ_ann    : {sigma_p*100:.2f}%")
    print(f"      portfolio gross SR : {sr_p:.3f}")

    # Net portfolio Sharpe (apply EXP-2570 daily drag)
    daily_drag = NET_DRAG_PCT / 100.0 / TRADING_DAYS
    port_net = port - daily_drag
    sigma_p_net = annualised_vol(port_net)
    sr_p_net = annualised_sharpe(port_net)
    print(f"      portfolio net SR   : {sr_p_net:.3f}  "
          f"(drag {NET_DRAG_BPS:.1f} bps/yr)")

    # ── Decompose portfolio Sharpe (point estimate) ──
    # contrib_i = w_i · mu_i_ann / sigma_p   (sums to SR_p exactly)
    contrib_sr = w_scaled * means_ann / sigma_p
    # Risk decomposition: contrib_vol_i = w_i · Cov(R_i, R_p) / sigma_p
    cov_with_port = (cube_arr.T @ port) / (n - 1) - cube_arr.mean(axis=0) * port.mean() * n / (n - 1)
    # cleaner: use np.cov on the joint matrix
    joint = np.column_stack([cube_arr, port])
    cov_full = np.cov(joint, rowvar=False, ddof=1)
    cov_with_port_clean = cov_full[:m, m]
    sigma_p_daily = float(np.sqrt(cov_full[m, m]))
    contrib_vol = w_scaled * cov_with_port_clean / sigma_p_daily
    contrib_vol_ann = contrib_vol * math.sqrt(TRADING_DAYS)

    # Sanity: contrib_sr should sum to sr_p
    sum_contrib_sr = float(contrib_sr.sum())
    sum_contrib_vol = float(contrib_vol_ann.sum())
    print(f"      Σ contrib_SR = {sum_contrib_sr:.4f}  (should equal {sr_p:.4f})")
    print(f"      Σ risk_contrib (ann) = {sum_contrib_vol*100:.3f}%  "
          f"(should equal {sigma_p*100:.3f}%)")

    # ── Bootstrap contribution CIs ──
    print(f"\n[4/5] Bootstrap contribution Sharpe ({BOOTSTRAP_N} resamples, "
          f"joint stationary block, mean block {BOOTSTRAP_BLOCK_MEAN}d) …")
    rng2 = np.random.default_rng(RNG_SEED + 1)
    contrib_ci = joint_contribution_ci(
        cube_arr, w_scaled, BOOTSTRAP_N, BOOTSTRAP_BLOCK_MEAN, rng2,
    )

    # ── Build per-stream attribution table ──
    attribution: Dict[str, Dict] = {}
    pct_total_sr = (
        contrib_sr / sr_p * 100.0 if abs(sr_p) > 1e-9 else np.zeros_like(contrib_sr)
    )
    pct_total_risk = (
        contrib_vol_ann / sigma_p * 100.0 if sigma_p > 1e-9 else np.zeros_like(contrib_vol_ann)
    )
    print("\n      Per-stream contribution to portfolio Sharpe (gross):")
    print(f"      {'stream':<10s}  {'w_eq_vega':>9s}  {'w_scaled':>9s}  "
          f"{'std_SR':>7s}  {'contrib_SR':>11s}  {'%SR':>6s}  "
          f"{'risk%':>6s}  {'95% CI':>16s}")
    for j, c in enumerate(cols):
        ci = contrib_ci[str(j)]
        attribution[c] = {
            "label": STREAM_LABELS.get(c, c),
            "spx_like": c in SPX_LIKE,
            "weight_eq_vega": round(float(w_raw[j]), 6),
            "weight_scaled": round(float(w_scaled[j]), 6),
            "standalone_sharpe": standalone[c]["sharpe"],
            "contrib_sharpe": round(float(contrib_sr[j]), 4),
            "contrib_sharpe_pct": round(float(pct_total_sr[j]), 2),
            "contrib_sharpe_ci95": {
                "lo95": round(ci["lo95"], 4),
                "hi95": round(ci["hi95"], 4),
                "mean": round(ci["mean"], 4),
                "std":  round(ci["std"],  4),
            },
            "risk_contrib_ann_pct": round(float(contrib_vol_ann[j]) * 100, 4),
            "risk_contrib_share_pct": round(float(pct_total_risk[j]), 2),
        }
        print(f"      {c:<10s}  {w_raw[j]:9.4f}  {w_scaled[j]:9.4f}  "
              f"{standalone[c]['sharpe']:7.2f}  {contrib_sr[j]:11.3f}  "
              f"{pct_total_sr[j]:5.1f}%  {pct_total_risk[j]:5.1f}%  "
              f"[{ci['lo95']:5.2f},{ci['hi95']:5.2f}]")

    # SPX-like vs non-SPX-like aggregate
    spx_idx = [j for j, c in enumerate(cols) if c in SPX_LIKE]
    non_idx = [j for j in range(m) if j not in spx_idx]
    spx_sr = float(contrib_sr[spx_idx].sum())
    non_sr = float(contrib_sr[non_idx].sum())
    spx_risk = float(contrib_vol_ann[spx_idx].sum())
    non_risk = float(contrib_vol_ann[non_idx].sum())

    print("\n      SPX-VRP-sensitive vs other streams:")
    print(f"        SPX-like (exp1220)  : SR contrib {spx_sr:.3f} "
          f"({spx_sr/sr_p*100:5.1f}%)   "
          f"risk contrib {spx_risk*100:.2f}% "
          f"({spx_risk/sigma_p*100:5.1f}%)")
    print(f"        Other 7 streams     : SR contrib {non_sr:.3f} "
          f"({non_sr/sr_p*100:5.1f}%)   "
          f"risk contrib {non_risk*100:.2f}% "
          f"({non_risk/sigma_p*100:5.1f}%)")

    # ── Verdict ──
    spx_share = spx_sr / sr_p * 100.0 if sr_p > 1e-9 else 0.0
    print("\n[5/5] Verdict")
    print("-" * 72)
    if spx_share < 20.0:
        verdict = "ROBUST_TO_SPX_VRP_DECLINE"
        print("  ✓ exp1220 (SPY) contributes < 20% of portfolio Sharpe.")
        print("    The post-2020 edge is dominated by sector ETFs / calendars /")
        print("    cross-vol — streams not directly tied to SPX VRP.")
    elif spx_share < 40.0:
        verdict = "MODEST_SPX_DEPENDENCE"
        print("  ◐ exp1220 contributes 20-40% of portfolio Sharpe — meaningful")
        print("    but not dominant. Worth monitoring SPY-specific erosion.")
    else:
        verdict = "SPX_DEPENDENT"
        print("  ✗ exp1220 contributes > 40% of portfolio Sharpe — Dew-Becker")
        print("    style SPX-VRP collapse would materially impair v8a.")

    payload = {
        "experiment": "EXP-3151",
        "title": "Per-stream Sharpe attribution — v8a, post-2020, equal-vega",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "rule_zero": True,
        "data_caveat": (
            "Cube spans 2020-01-01..2025-12-31; this attribution covers "
            "2020-01-01..2024-12-31 only. Cannot test 15-yr Dew-Becker "
            "VRP-decline thesis directly. Answers: within 2020-2024, "
            "is the v8a edge dominated by SPX-style streams or by "
            "sector / cross-vol streams?"
        ),
        "config": {
            "window_start": str(WINDOW_START.date()),
            "window_end": str(WINDOW_END.date()),
            "target_vol_annual": TARGET_VOL_ANNUAL,
            "weighting": "equal-vega (inverse-vol, normalised, then rescaled to target vol)",
            "bootstrap_n": BOOTSTRAP_N,
            "bootstrap_block_mean": BOOTSTRAP_BLOCK_MEAN,
            "rng_seed": RNG_SEED,
            "drag_bps": NET_DRAG_BPS,
        },
        "streams": cols,
        "standalone": standalone,
        "weights": {
            "raw_eq_vega": {c: float(w_raw[j]) for j, c in enumerate(cols)},
            "scaled_to_target_vol": {c: float(w_scaled[j]) for j, c in enumerate(cols)},
            "rescale_scalar": scalar,
        },
        "portfolio": {
            "vol_ann_pct": round(sigma_p * 100, 4),
            "gross_sharpe": round(sr_p, 4),
            "net_sharpe":   round(sr_p_net, 4),
            "gross_sharpe_ci95": {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in contrib_ci["__portfolio__"].items()
            },
            "sum_check_contrib_sr": sum_contrib_sr,
            "sum_check_risk_ann_pct": sum_contrib_vol * 100,
        },
        "attribution": attribution,
        "aggregate": {
            "spx_like_streams":    sorted(SPX_LIKE),
            "spx_contrib_sharpe":  round(spx_sr, 4),
            "spx_contrib_pct":     round(spx_share, 2),
            "spx_risk_contrib_ann_pct": round(spx_risk * 100, 4),
            "spx_risk_share_pct":  round(spx_risk / sigma_p * 100, 2)
                                   if sigma_p > 1e-9 else 0.0,
            "other_contrib_sharpe": round(non_sr, 4),
            "other_contrib_pct":   round(non_sr / sr_p * 100, 2)
                                   if sr_p > 1e-9 else 0.0,
            "other_risk_contrib_ann_pct": round(non_risk * 100, 4),
            "other_risk_share_pct": round(non_risk / sigma_p * 100, 2)
                                    if sigma_p > 1e-9 else 0.0,
            "verdict": verdict,
        },
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\n[report] → {REPORT_JSON}")

    REPORT_HTML.write_text(build_html(payload), encoding="utf-8")
    print(f"[report] → {REPORT_HTML}")


# ── HTML ─────────────────────────────────────────────────────────────


def build_html(p: Dict) -> str:
    cols = p["streams"]
    rows = ""
    for c in cols:
        a = p["attribution"][c]
        s = p["standalone"][c]
        ci_s = s["sharpe_ci95"]
        ci_c = a["contrib_sharpe_ci95"]
        spx_tag = " spx" if a["spx_like"] else ""
        rows += (
            f"<tr class='stream{spx_tag}'>"
            f"<td>{a['label']}</td>"
            f"<td>{s['n_nonzero_days']}</td>"
            f"<td>{s['vol_ann_pct']:.2f}%</td>"
            f"<td>{s['mean_ann_pct']:+.2f}%</td>"
            f"<td>{s['sharpe']:.2f}</td>"
            f"<td>[{ci_s['lo95']:.2f}, {ci_s['hi95']:.2f}]</td>"
            f"<td>{a['weight_scaled']:.3f}</td>"
            f"<td>{a['contrib_sharpe']:+.3f}</td>"
            f"<td>{a['contrib_sharpe_pct']:.1f}%</td>"
            f"<td>[{ci_c['lo95']:+.3f}, {ci_c['hi95']:+.3f}]</td>"
            f"<td>{a['risk_contrib_ann_pct']:.2f}%</td>"
            f"<td>{a['risk_contrib_share_pct']:.1f}%</td>"
            f"</tr>"
        )

    agg = p["aggregate"]
    port = p["portfolio"]
    cfg = p["config"]
    verdict = agg["verdict"]
    color = {
        "ROBUST_TO_SPX_VRP_DECLINE": "#16a34a",
        "MODEST_SPX_DEPENDENCE": "#f59e0b",
        "SPX_DEPENDENT": "#dc2626",
    }.get(verdict, "#64748b")
    headline = {
        "ROBUST_TO_SPX_VRP_DECLINE":
            "Robust — SPX-VRP-sensitive contribution &lt; 20%",
        "MODEST_SPX_DEPENDENCE":
            "Modest SPX dependence (20-40%)",
        "SPX_DEPENDENT":
            "Material SPX dependence (&ge; 40%) — Dew-Becker collapse would hurt",
    }.get(verdict, verdict)

    pci = port["gross_sharpe_ci95"]
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>EXP-3151 — Stream Sharpe attribution</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:1240px;margin:0 auto;padding:28px;background:#fff;color:#1e293b;}}
h1{{font-size:1.7em;color:#0f172a;}}
h2{{margin-top:2em;border-bottom:2px solid #e2e8f0;padding-bottom:8px;color:#334155;}}
.muted{{color:#64748b;font-size:0.85em;}}
.caveat{{background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:14px;margin:16px 0;font-size:0.9rem;line-height:1.55;}}
.sources{{background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:14px;font-size:0.84rem;line-height:1.6;}}
.verdict{{background:#fff;border:2px solid {color};border-radius:8px;padding:18px;margin:18px 0;}}
.verdict .badge{{display:inline-block;padding:5px 14px;border-radius:14px;color:#fff;background:{color};font-weight:700;font-size:0.86rem;}}
table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:0.84em;}}
th{{background:#f1f5f9;padding:8px 9px;text-align:right;border-bottom:2px solid #cbd5e1;font-size:0.7em;text-transform:uppercase;}}
th:first-child{{text-align:left;}}
td{{padding:7px 9px;text-align:right;border-bottom:1px solid #e2e8f0;}}
td:first-child{{text-align:left;font-weight:600;color:#475569;}}
tr.stream.spx{{background:#fef2f2;}}
.kv{{display:grid;grid-template-columns:repeat(2,1fr);gap:6px 18px;font-size:0.9em;margin:10px 0;}}
.kv b{{color:#475569;}}
</style></head><body>

<h1>EXP-3151 — Per-stream Sharpe attribution (v8a, post-2020)</h1>
<p class="muted">Equal-vega-weighted decomposition of the v8a portfolio
on {cfg['window_start']} .. {cfg['window_end']}, rescaled to {cfg['target_vol_annual']*100:.0f}% target vol.
Bootstrap CIs from {cfg['bootstrap_n']:,} stationary-block resamples
(mean block {cfg['bootstrap_block_mean']}d). {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="caveat">
<strong>⚠ Data-range caveat (carried from EXP-3150).</strong> Cube spans
2020-01-01..2025-12-31; this attribution slices to 2020-2024. We
<strong>cannot directly test the Dew-Becker / Giglio 15-year VRP-decline
thesis</strong> from this data. What we <em>can</em> answer: within the
post-2020 sample, is the v8a edge dominated by SPX-VRP-sensitive streams
(highlighted in red) or by sector / cross-vol / calendar streams that
should not depend on dealer GEX flipping?
</div>

<div class="sources">
<strong>Rule Zero.</strong> Same v8a cube as EXP-2600 / EXP-3150.
Streams: {", ".join(cols)}. Net SR uses EXP-2570 {NET_DRAG_BPS:.1f} bps drag.
Weights: equal-vega (inverse-vol) then rescaled by a single scalar so
realised portfolio σ matches {cfg['target_vol_annual']*100:.0f}%.
</div>

<div class="verdict">
<span class="badge">{headline}</span>
<div class="kv" style="margin-top:14px">
<div><b>Portfolio gross Sharpe</b></div><div>{port['gross_sharpe']:.3f}
&nbsp; 95% CI [{pci['lo95']:.2f}, {pci['hi95']:.2f}]</div>
<div><b>Portfolio net Sharpe</b></div><div>{port['net_sharpe']:.3f}</div>
<div><b>Portfolio σ_ann</b></div><div>{port['vol_ann_pct']:.2f}%</div>
<div><b>SPX-like (exp1220) contrib</b></div>
<div>SR <strong>{agg['spx_contrib_sharpe']:.3f}</strong>
({agg['spx_contrib_pct']:.1f}% of port SR);
risk {agg['spx_risk_contrib_ann_pct']:.2f}%
({agg['spx_risk_share_pct']:.1f}% of port σ)</div>
<div><b>Other 7 streams contrib</b></div>
<div>SR <strong>{agg['other_contrib_sharpe']:.3f}</strong>
({agg['other_contrib_pct']:.1f}% of port SR);
risk {agg['other_risk_contrib_ann_pct']:.2f}%
({agg['other_risk_share_pct']:.1f}% of port σ)</div>
<div><b>Sum check</b></div>
<div>Σ contrib_SR = {port['sum_check_contrib_sr']:.3f}
(should equal {port['gross_sharpe']:.3f})</div>
</div>
</div>

<h2>1. Per-stream attribution</h2>
<table>
<thead><tr>
<th>Stream</th>
<th>Nonzero days</th>
<th>σ_ann</th>
<th>μ_ann</th>
<th>Standalone SR</th>
<th>SR 95% CI</th>
<th>Weight (scaled)</th>
<th>Contrib SR</th>
<th>% of port SR</th>
<th>Contrib SR 95% CI</th>
<th>Risk contrib (ann)</th>
<th>% of port σ</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="muted">Rows highlighted in red are SPX-VRP-sensitive
(exp1220 = SPY put-credit spread). Contribution Sharpe is
<code>w_i · μ_i_ann / σ_p_ann</code>; sums to portfolio Sharpe.
Risk contribution is <code>w_i · Cov(R_i, R_p) / σ_p</code>; sums to σ_p.</p>

<p style="margin-top:3em;color:#94a3b8;font-size:0.78em;text-align:center">
compass/exp3151_stream_attribution.py · Rule Zero · real data only
</p>
</body></html>"""


if __name__ == "__main__":
    main()
