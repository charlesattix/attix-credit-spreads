"""EXP-3150 — Post-2020 Re-test of v8a (Defensive Audit).

Motivation
----------
Recent Chicago Fed paper (Dew-Becker & Giglio, 2025) claims VRP has
declined toward zero over the past 15 years. EXP-3150 stress-tests
whether the v8a portfolio edge from EXP-2600 survives when the
window is restricted to the most recent calendar window only.

IMPORTANT DATA LIMITATION
-------------------------
The EXP-2080 stream cache (load_streams) and the EXP-2250 cached
QQQ trade tape both start at 2020-01-01 and end at 2025-12-31. There
is NO pre-2020 history available in this repository, so the
"full-period" comparison here is 2020-01-01..2025-12-31 (~6 yrs)
versus the requested "post-2020" window 2020-01-01..2024-12-31 (5 yrs).
The only difference is whether 2025 trading days are included.

This audit therefore CANNOT directly test the Dew-Becker / Giglio
"15-year decline" claim — that would require pre-2010 IronVault/Yahoo
data we do not have. What this audit DOES answer is:

  Does the recent (2020-2024) sub-window's Sharpe materially differ
  from the full available window (2020-2025)?

A drop > 10% would still be a yellow flag worth escalating. A small
delta is consistent with — but not proof of — the strategy's
robustness to a shrinking VRP.

Methodology
-----------
  1. Reuse the v8a cube exactly as in EXP-2600 (8 streams).
  2. Re-run the same Ledoit-Wolf risk-parity walk-forward at the
     EXP-2600 winning target_vol = 0.18.
  3. Compute pooled gross + net (EXP-2570 890.3 bps drag) Sharpe,
     CAGR, DD on:
        full     2020-01-01 .. 2025-12-31
        post2020 2020-01-01 .. 2024-12-31  (drop 2025)
  4. Bootstrap 95% CI on pooled net Sharpe via stationary block
     bootstrap (mean block length = 5 days, 5000 resamples) on the
     pooled OOS daily returns.
  5. Flag if post-2020 net Sharpe < 0.90 × full-period net Sharpe.

Rule Zero: same real IronVault + Yahoo cube as EXP-2600. No
synthetic data, no parameter tuning — slicing only.

Outputs:
  compass/reports/exp3150_post2020_retest.json
  compass/reports/exp3150_post2020_retest.html
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
    walk_forward_lw,
    fold_metrics,
    apply_net_drag,
    NET_DRAG_BPS,
    NET_DRAG_PCT,
    TRADING_DAYS,
    TRAIN_DAYS,
    TEST_DAYS,
)

REPORT_JSON = ROOT / "compass" / "reports" / "exp3150_post2020_retest.json"
REPORT_HTML = ROOT / "compass" / "reports" / "exp3150_post2020_retest.html"

# EXP-2600 winner for v8a
V8A_TARGET_VOL = 0.18

# Post-2020 audit window (excluding 2025)
POST2020_START = pd.Timestamp("2020-01-01")
POST2020_END = pd.Timestamp("2024-12-31")

# Bootstrap config
BOOTSTRAP_N = 5000
BOOTSTRAP_BLOCK_MEAN = 5  # mean block length, days
RNG_SEED = 20260428


# ── Bootstrap helpers ────────────────────────────────────────────────


def _stationary_block_bootstrap_sharpe(
    r: np.ndarray, n_iter: int, mean_block: int, rng: np.random.Generator
) -> np.ndarray:
    """Politis-Romano stationary bootstrap of annualised Sharpe.

    Block lengths are i.i.d. Geometric(p) with p = 1/mean_block.
    """
    n = len(r)
    if n < 5:
        return np.array([])
    p = 1.0 / mean_block
    sharpes = np.empty(n_iter, dtype=float)
    for k in range(n_iter):
        out = np.empty(n, dtype=float)
        i = 0
        while i < n:
            start = int(rng.integers(0, n))
            block_len = int(rng.geometric(p))
            block_len = max(1, min(block_len, n - i))
            for j in range(block_len):
                out[i + j] = r[(start + j) % n]
            i += block_len
        mu = out.mean()
        sd = out.std(ddof=1)
        sharpes[k] = (mu / sd) * math.sqrt(TRADING_DAYS) if sd > 1e-12 else 0.0
    return sharpes


def bootstrap_ci(r: pd.Series) -> Dict[str, float]:
    rng = np.random.default_rng(RNG_SEED)
    arr = r.dropna().to_numpy(dtype=float)
    samples = _stationary_block_bootstrap_sharpe(
        arr, n_iter=BOOTSTRAP_N, mean_block=BOOTSTRAP_BLOCK_MEAN, rng=rng
    )
    if samples.size == 0:
        return {"mean": float("nan"), "lo95": float("nan"), "hi95": float("nan"), "n_iter": 0}
    return {
        "mean": float(np.mean(samples)),
        "lo95": float(np.quantile(samples, 0.025)),
        "hi95": float(np.quantile(samples, 0.975)),
        "std": float(np.std(samples, ddof=1)),
        "n_iter": int(BOOTSTRAP_N),
        "block_mean": int(BOOTSTRAP_BLOCK_MEAN),
    }


# ── Run windowed walk-forward ────────────────────────────────────────


def windowed_run(
    cube: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
    target_vol: float,
) -> Tuple[pd.Series, pd.Series, Dict]:
    """Slice cube to [start, end] then walk-forward.

    Returns (gross_pooled, net_pooled, summary_dict).
    """
    sub = cube.loc[(cube.index >= start) & (cube.index <= end)].copy()
    if len(sub) < TRAIN_DAYS + TEST_DAYS:
        raise ValueError(f"window {start}..{end} too short ({len(sub)} rows)")
    pooled, folds = walk_forward_lw(sub, target_vol=target_vol)
    net = apply_net_drag(pooled)
    gross_m = fold_metrics(pooled)
    net_m = fold_metrics(net)
    gross_ci = bootstrap_ci(pooled)
    net_ci = bootstrap_ci(net)
    summary = {
        "window_start": str(sub.index[0].date()),
        "window_end": str(sub.index[-1].date()),
        "n_obs_in_cube": int(len(sub)),
        "n_pooled_oos_days": int(len(pooled)),
        "n_folds": len(folds),
        "target_vol": target_vol,
        "gross": gross_m,
        "net": net_m,
        "gross_sharpe_ci95": gross_ci,
        "net_sharpe_ci95": net_ci,
    }
    return pooled, net, summary


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 72)
    print("EXP-3150 — Post-2020 Re-test of v8a (Defensive Audit)")
    print("=" * 72)

    print("\n[1/4] Building v8a cube (same as EXP-2600)…")
    cubes = build_cubes()
    v8a = cubes["v8a_add_qqq"]
    print(f"      v8a shape: {v8a.shape}")
    print(f"      data range: {v8a.index[0].date()} .. {v8a.index[-1].date()}")

    full_start = v8a.index[0]
    full_end = v8a.index[-1]

    print("\n[2/4] Full-period walk-forward (2020-01-01 .. 2025-12-31)…")
    full_pooled, full_net, full_summary = windowed_run(
        v8a, full_start, full_end, V8A_TARGET_VOL
    )
    print(
        f"      gross  CAGR {full_summary['gross']['cagr_pct']:+7.1f}%  "
        f"SR {full_summary['gross']['sharpe']:5.2f}  "
        f"DD {full_summary['gross']['max_dd_pct']:5.1f}%"
    )
    print(
        f"      net    CAGR {full_summary['net']['cagr_pct']:+7.1f}%  "
        f"SR {full_summary['net']['sharpe']:5.2f}  "
        f"DD {full_summary['net']['max_dd_pct']:5.1f}%"
    )
    nci = full_summary["net_sharpe_ci95"]
    print(
        f"      net SR 95% CI: [{nci['lo95']:.2f}, {nci['hi95']:.2f}]  "
        f"(mean {nci['mean']:.2f})"
    )

    print("\n[3/4] Post-2020 walk-forward (2020-01-01 .. 2024-12-31)…")
    p20_pooled, p20_net, p20_summary = windowed_run(
        v8a, POST2020_START, POST2020_END, V8A_TARGET_VOL
    )
    print(
        f"      gross  CAGR {p20_summary['gross']['cagr_pct']:+7.1f}%  "
        f"SR {p20_summary['gross']['sharpe']:5.2f}  "
        f"DD {p20_summary['gross']['max_dd_pct']:5.1f}%"
    )
    print(
        f"      net    CAGR {p20_summary['net']['cagr_pct']:+7.1f}%  "
        f"SR {p20_summary['net']['sharpe']:5.2f}  "
        f"DD {p20_summary['net']['max_dd_pct']:5.1f}%"
    )
    nci2 = p20_summary["net_sharpe_ci95"]
    print(
        f"      net SR 95% CI: [{nci2['lo95']:.2f}, {nci2['hi95']:.2f}]  "
        f"(mean {nci2['mean']:.2f})"
    )

    # ── Comparison + thesis-invalidation check ──
    full_net_sr = full_summary["net"]["sharpe"]
    p20_net_sr = p20_summary["net"]["sharpe"]
    ratio = p20_net_sr / full_net_sr if full_net_sr > 1e-9 else float("nan")
    survives = ratio >= 0.90  # gate from the EXP-3150 brief
    # Also check whether the two CIs overlap — non-overlap = stronger signal.
    ci_overlap = not (
        nci2["hi95"] < nci["lo95"] or nci["hi95"] < nci2["lo95"]
    )

    print("\n[4/4] Verdict")
    print("-" * 72)
    print(f"  full-period net Sharpe : {full_net_sr:.3f}  "
          f"95% CI [{nci['lo95']:.2f}, {nci['hi95']:.2f}]")
    print(f"  post-2020   net Sharpe : {p20_net_sr:.3f}  "
          f"95% CI [{nci2['lo95']:.2f}, {nci2['hi95']:.2f}]")
    print(f"  ratio post/full        : {ratio:.3f}  "
          f"(gate: ≥ 0.90)")
    print(f"  CIs overlap?           : {ci_overlap}")
    print(f"  thesis SURVIVES gate?  : {survives}")
    if not survives:
        print("  ⚠️  post-2020 Sharpe < 90% of full-period — escalate.")
    else:
        print("  ✓  post-2020 Sharpe within 10% of full-period.")

    print("\n  CAVEAT: cube only spans 2020-2025. We CANNOT test the")
    print("  Dew-Becker/Giglio 15-year VRP-decline claim with this data.")
    print("  This audit only checks 5y vs 6y stability, not long-run decay.")

    # Yearly decomposition (helpful for diagnosing where the 2025 delta lives)
    yearly = {}
    for label, series in [("full_net", full_net), ("post2020_net", p20_net)]:
        d: Dict[int, Dict] = {}
        for yr in sorted({d.year for d in series.index}):
            sub = series[series.index.year == yr]
            if len(sub) < 20:
                continue
            d[int(yr)] = fold_metrics(sub)
        yearly[label] = d

    print("\n  Yearly (full window, net):")
    for yr, m in yearly["full_net"].items():
        print(f"    {yr}  CAGR {m['cagr_pct']:+7.1f}%  "
              f"SR {m['sharpe']:5.2f}  DD {m['max_dd_pct']:5.1f}%")

    payload = {
        "experiment": "EXP-3150",
        "title": "Post-2020 Re-test of v8a — Defensive Audit",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "rule_zero": True,
        "data_range_caveat": (
            "Underlying cube only spans 2020-01-01..2025-12-31. "
            "Cannot directly test the Dew-Becker/Giglio 15-year VRP "
            "decline thesis from this data. This audit compares "
            "5y (2020-2024) vs 6y (2020-2025) windows only."
        ),
        "sources": {
            "cube_builder": "compass.exp2600_north_star_v8.build_cubes (v8a_add_qqq)",
            "stream_cache": "compass/cache/exp2080_streams.pkl (real IronVault + Yahoo)",
            "qqq_trades": "compass/cache/exp2250_qqq_trades.pkl (85 real IronVault QQQ chains)",
            "drag_rate": f"EXP-2570 {NET_DRAG_BPS:.1f} bps",
        },
        "config": {
            "target_vol": V8A_TARGET_VOL,
            "train_days": TRAIN_DAYS,
            "test_days": TEST_DAYS,
            "net_drag_pct": NET_DRAG_PCT,
            "bootstrap_n": BOOTSTRAP_N,
            "bootstrap_block_mean": BOOTSTRAP_BLOCK_MEAN,
            "rng_seed": RNG_SEED,
        },
        "windows": {
            "full": full_summary,
            "post_2020": p20_summary,
        },
        "comparison": {
            "full_net_sharpe": full_net_sr,
            "post2020_net_sharpe": p20_net_sr,
            "ratio_post_over_full": ratio,
            "gate_threshold": 0.90,
            "thesis_survives": bool(survives),
            "ci95_overlap": bool(ci_overlap),
        },
        "yearly": yearly,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\n[report] → {REPORT_JSON}")

    REPORT_HTML.write_text(build_html(payload), encoding="utf-8")
    print(f"[report] → {REPORT_HTML}")


# ── HTML ─────────────────────────────────────────────────────────────


def build_html(p: Dict) -> str:
    full = p["windows"]["full"]
    p20 = p["windows"]["post_2020"]
    cmp_ = p["comparison"]
    survives = cmp_["thesis_survives"]
    color = "#16a34a" if survives else "#dc2626"
    badge = "SURVIVES (≥ 90%)" if survives else "FAILS GATE (< 90%)"

    def fmt_window(w: Dict, label: str) -> str:
        g = w["gross"]
        n = w["net"]
        gci = w["gross_sharpe_ci95"]
        nci = w["net_sharpe_ci95"]
        return f"""
<h3>{label}</h3>
<p class="muted">{w['window_start']} .. {w['window_end']} · {w['n_obs_in_cube']} cube rows ·
{w['n_pooled_oos_days']} pooled OOS days · {w['n_folds']} folds · target_vol = {w['target_vol']:.2f}</p>
<table>
<thead><tr><th></th><th>Gross</th><th>Net</th></tr></thead>
<tbody>
<tr><td>CAGR</td><td>{g['cagr_pct']:+.2f}%</td><td>{n['cagr_pct']:+.2f}%</td></tr>
<tr><td>Sharpe</td><td>{g['sharpe']:.3f}</td><td>{n['sharpe']:.3f}</td></tr>
<tr><td>Sharpe 95% CI</td><td>[{gci['lo95']:.2f}, {gci['hi95']:.2f}]</td><td>[{nci['lo95']:.2f}, {nci['hi95']:.2f}]</td></tr>
<tr><td>Max DD</td><td>{g['max_dd_pct']:.2f}%</td><td>{n['max_dd_pct']:.2f}%</td></tr>
<tr><td>Annualised vol</td><td>{g['vol_pct']:.2f}%</td><td>{n['vol_pct']:.2f}%</td></tr>
<tr><td>Calmar</td><td>{g['calmar']:.2f}</td><td>{n['calmar']:.2f}</td></tr>
</tbody>
</table>
"""

    yr_full = p["yearly"]["full_net"]
    yr_rows = ""
    for yr in sorted(yr_full.keys()):
        m = yr_full[yr]
        in_p20 = "—" if yr > 2024 else (
            f"{p['yearly']['post2020_net'].get(yr, {}).get('sharpe', 0):.2f}"
        )
        yr_rows += (
            f"<tr><td>{yr}</td>"
            f"<td>{m['cagr_pct']:+.1f}%</td>"
            f"<td>{m['sharpe']:.2f}</td>"
            f"<td>{m['max_dd_pct']:.1f}%</td>"
            f"<td>{in_p20}</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>EXP-3150 — Post-2020 Re-test</title>
<style>
body {{ font-family:-apple-system,sans-serif;max-width:1200px;margin:0 auto;padding:28px;background:#fff;color:#1e293b; }}
h1 {{ font-size:1.8em;color:#0f172a; }}
h2 {{ margin-top:2em;border-bottom:2px solid #e2e8f0;padding-bottom:8px;color:#334155; }}
h3 {{ color:#475569;margin-top:1em; }}
.muted {{ color:#64748b;font-size:0.85em; }}
.caveat {{ background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:14px;margin:16px 0;font-size:0.9rem;line-height:1.55; }}
.verdict {{ background:#fff;border:2px solid {color};border-radius:8px;padding:18px;margin:18px 0;font-size:1.0rem; }}
.verdict .badge {{ display:inline-block;padding:5px 14px;border-radius:14px;color:#fff;background:{color};font-weight:700;font-size:0.86rem; }}
table {{ width:100%;border-collapse:collapse;margin:12px 0;font-size:0.9em; }}
th {{ background:#f1f5f9;padding:9px 11px;text-align:right;border-bottom:2px solid #cbd5e1;font-size:0.74em;text-transform:uppercase; }}
th:first-child {{ text-align:left; }}
td {{ padding:8px 11px;text-align:right;border-bottom:1px solid #e2e8f0; }}
td:first-child {{ text-align:left;font-weight:600;color:#475569; }}
.sources {{ background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:14px;font-size:0.84rem;line-height:1.6; }}
</style></head><body>

<h1>EXP-3150 — Post-2020 Re-test of v8a (Defensive Audit)</h1>
<p class="muted">Triggered by Dew-Becker &amp; Giglio (2025) Chicago Fed paper claiming
15-yr VRP decline. Tests whether v8a Sharpe is stable when window is
restricted to 2020–2024 only. {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="caveat">
<strong>⚠ DATA-RANGE CAVEAT.</strong> The underlying stream cache and QQQ
trade tape only span <strong>2020-01-01 to 2025-12-31</strong>. There is
no pre-2020 history in this repository. This audit therefore compares a
5-year window (2020–2024) against a 6-year window (2020–2025) — the
delta is essentially calendar year 2025. <strong>It cannot directly
test the Dew-Becker/Giglio 15-year VRP-decline claim.</strong> A clean
test of that thesis requires pre-2010 IronVault chain data we do not
have.
</div>

<div class="sources">
<strong>Rule Zero.</strong> Same v8a cube as EXP-2600 (8 streams: exp1220,
v5_hedge, gld_cal, slv_cal, cross_vol, xlf_cs, xli_cs, qqq_cs).
Walk-forward and target-vol settings unchanged from the EXP-2600
winning config (target_vol = 0.18). Net = gross − {NET_DRAG_BPS:.1f}
bps drag (EXP-2570).
</div>

<div class="verdict">
<span class="badge">{badge}</span><br><br>
<strong>Full-period net Sharpe:</strong> {cmp_['full_net_sharpe']:.3f}<br>
<strong>Post-2020 net Sharpe:</strong> {cmp_['post2020_net_sharpe']:.3f}<br>
<strong>Ratio (post-2020 / full):</strong> {cmp_['ratio_post_over_full']:.3f}
&nbsp;&nbsp;<em>(gate ≥ 0.90)</em><br>
<strong>95% CI overlap:</strong> {cmp_['ci95_overlap']}<br>
</div>

<h2>1. Window comparison</h2>
{fmt_window(full, "Full window — 2020-01-01 .. 2025-12-31")}
{fmt_window(p20, "Post-2020 window — 2020-01-01 .. 2024-12-31")}

<h2>2. Yearly decomposition (net)</h2>
<table>
<thead><tr>
<th>Year</th><th>CAGR</th><th>Sharpe (full)</th><th>Max DD</th><th>Sharpe (post-2020)</th>
</tr></thead>
<tbody>{yr_rows}</tbody>
</table>

<p style="margin-top:3em;color:#94a3b8;font-size:0.78em;text-align:center">
compass/exp3150_post2020_retest.py · Rule Zero · real data only
</p>
</body></html>"""


if __name__ == "__main__":
    main()
