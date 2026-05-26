#!/usr/bin/env python3
"""Gate 3 — Champion equity-curve equivalence (Polygon ↔ Yahoo).

Runs the EXP-400 champion strategy twice over 2024-01-01..2025-12-31:

  A) "Yahoo arm" — backtester routed through the legacy
     ``_yf_history_safe`` / ``_yf_download_safe`` helpers (split+dividend
     adjusted prices, no holiday filter). This is the pre-migration path.
  B) "Polygon arm" — current ``load_market_history`` (Polygon stocks +
     hybrid indices, splits-only adjustment, NYSE calendar filter).

Both arms share the same IronVault option pricing layer — only the OHLCV
source differs. We assert:

  * Trade count match within ±5%
  * Equity-curve daily correlation ≥ 0.99
  * Final PnL within ±2%

Per BACKTEST_MIGRATION_PROPOSAL.md the Gate-3 ratio target is 0.99 —
this is the migration's "no-fly zone" gate.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import backtest.backtester as bt_mod  # noqa: E402
from backtest.backtester import Backtester, _yf_history_safe  # noqa: E402
from backtest.market_history import load_market_history as _polygon_load_market_history  # noqa: E402
# Reuse the canonical config builder so we exactly match production wiring.
from scripts.run_optimization import _build_config  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gate3")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Yahoo-arm shim: route load_market_history through the legacy curl helpers.
# Same signature, same shape, same column names — only the data source differs.
# ---------------------------------------------------------------------------

def _yahoo_load_market_history(ticker, start, end):
    """Pre-migration shim: route through ``_yf_history_safe``."""
    def _to_dt(v):
        if isinstance(v, datetime):
            return v
        if isinstance(v, date):
            return datetime.combine(v, datetime.min.time())
        return datetime.fromisoformat(str(v)[:10])
    s_dt = _to_dt(start)
    e_dt = _to_dt(end)
    return _yf_history_safe(ticker, start=s_dt, end=e_dt)


def _run_arm(arm: str, config: dict, otm_pct: float,
             start: datetime, end: datetime) -> dict:
    """Run one arm of Gate 3 — either Yahoo or Polygon OHLCV source."""
    from shared.iron_vault import IronVault

    if arm == "yahoo":
        bt_mod.load_market_history = _yahoo_load_market_history
    else:  # polygon
        bt_mod.load_market_history = _polygon_load_market_history

    hd = IronVault.instance()
    bt = Backtester(config, historical_data=hd, otm_pct=otm_pct, seed=42)
    result = bt.run_backtest("SPY", start, end) or {}
    result["arm"] = arm
    return result


def _equity_curve(result: dict) -> pd.Series:
    """Extract a daily equity series from the backtest result."""
    # The backtester emits an 'equity_curve' list of {date, equity} dicts.
    eq = result.get("equity_curve")
    if not eq:
        # Some result shapes carry trades only; reconstruct from trades.
        trades = result.get("trades", [])
        if not trades:
            return pd.Series(dtype=float)
        df = pd.DataFrame(trades)
        df["close_date"] = pd.to_datetime(df["close_date"])
        df = df.sort_values("close_date")
        df["cum_pnl"] = df["pnl"].cumsum()
        return pd.Series(df["cum_pnl"].values, index=df["close_date"].values)
    df = pd.DataFrame(eq)
    df["date"] = pd.to_datetime(df["date"])
    return pd.Series(df["equity"].values, index=df["date"].values)


def main() -> int:
    config_path = ROOT / "configs" / "exp_400_champion_realdata.json"
    params = json.load(open(config_path))
    config = _build_config(params, starting_capital=100_000)
    otm_pct = params.get("otm_pct", 0.05)

    start = datetime(2024, 1, 1)
    end = datetime(2025, 12, 31)

    logger.info("Gate 3 — running EXP-400 champion %s..%s", start.date(), end.date())

    # Restore the module reference at the end so subsequent imports see the
    # original Polygon-backed load_market_history.
    original = bt_mod.load_market_history
    try:
        logger.info("Arm A — Yahoo (pre-migration)")
        yahoo_res = _run_arm("yahoo", config, otm_pct, start, end)
        logger.info("Arm B — Polygon (post-migration)")
        polygon_res = _run_arm("polygon", config, otm_pct, start, end)
    finally:
        bt_mod.load_market_history = original

    yahoo_eq = _equity_curve(yahoo_res)
    polygon_eq = _equity_curve(polygon_res)

    # Persist artifacts for any post-hoc review.
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    yahoo_eq.to_csv(out_dir / "gate3_baseline_yahoo.csv", header=["equity"])
    polygon_eq.to_csv(out_dir / "gate3_baseline_polygon.csv", header=["equity"])

    yahoo_trades = len(yahoo_res.get("trades", []))
    polygon_trades = len(polygon_res.get("trades", []))
    yahoo_pnl = float(yahoo_res.get("total_pnl") or yahoo_res.get("final_pnl") or 0.0)
    polygon_pnl = float(polygon_res.get("total_pnl") or polygon_res.get("final_pnl") or 0.0)

    # Trade count ±5%
    trade_diff = abs(polygon_trades - yahoo_trades)
    trade_count_pass = (yahoo_trades == 0 and polygon_trades == 0) or (
        trade_diff / max(yahoo_trades, 1) <= 0.05
    )

    # PnL ±2%
    pnl_diff_pct = (abs(polygon_pnl - yahoo_pnl) / max(abs(yahoo_pnl), 1.0)) if yahoo_pnl else 0.0
    pnl_pass = pnl_diff_pct <= 0.02

    # Equity correlation ≥0.99
    joined = pd.concat([yahoo_eq, polygon_eq], axis=1, keys=["y", "p"]).dropna()
    if len(joined) >= 5:
        corr = float(np.corrcoef(joined["y"], joined["p"])[0, 1])
    else:
        corr = float("nan")
    corr_pass = corr >= 0.99

    summary = {
        "yahoo_trades": yahoo_trades,
        "polygon_trades": polygon_trades,
        "trade_diff": trade_diff,
        "trade_count_pass": trade_count_pass,
        "yahoo_pnl": yahoo_pnl,
        "polygon_pnl": polygon_pnl,
        "pnl_diff_pct": pnl_diff_pct,
        "pnl_pass": pnl_pass,
        "equity_corr": corr,
        "corr_pass": corr_pass,
        "joined_days": len(joined),
        "gate3_pass": bool(trade_count_pass and pnl_pass and corr_pass),
    }
    print(json.dumps(summary, indent=2))

    if summary["gate3_pass"]:
        logger.info("Gate 3 PASS")
        return 0
    logger.error("Gate 3 FAIL — see summary above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
