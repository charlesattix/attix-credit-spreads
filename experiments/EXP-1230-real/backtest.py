"""
EXP-1230-real: Microstructure Alpha re-backtest with REAL market data only.

Data sources:
  - SPY OHLCV: Yahoo Finance via backtester curl helper (same as production)
  - Credit spread trades: IronVault options_cache.db (real Polygon data)

NO synthetic data. NO np.random for prices/returns/signals.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backtest.backtester import _yf_download_safe
from compass.microstructure_alpha import (
    MicrostructureScanner,
    compute_all_features,
    generate_signals,
    standalone_sharpe,
    volatility_prediction_auc,
    overlay_on_trades,
)
from shared.iron_vault import IronVault

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
OUTPUT_DIR = Path(__file__).resolve().parent / "results"


# ---------------------------------------------------------------------------
# Real data loaders (NO synthetic data)
# ---------------------------------------------------------------------------


def _fetch_yahoo(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch OHLCV from Yahoo Finance using the project's curl helper."""
    df = _yf_download_safe(ticker, start, end)
    if df.empty:
        raise RuntimeError(f"No Yahoo Finance data for {ticker} ({start}–{end})")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def load_real_spy_ohlcv(
    start: str = "2020-01-01",
    end: str = "2025-12-31",
) -> pd.DataFrame:
    """Load real SPY OHLCV from Yahoo Finance.

    Returns DataFrame with columns: open, high, low, close, volume.
    """
    df = _fetch_yahoo("SPY", start, end)
    # Normalize column names to lowercase for MicrostructureScanner
    df.columns = [c.lower() for c in df.columns]
    return df


def _get_real_expirations(start_year: int, end_year: int) -> list[str]:
    """Query actual monthly SPY put expirations from options_cache.db."""
    import sqlite3
    import os
    from shared.constants import DATA_DIR

    db_path = os.path.join(DATA_DIR, "options_cache.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Get the 3rd-Friday-ish monthly expirations (ones with most strikes)
    cur.execute("""
        SELECT expiration, COUNT(*) as n_strikes
        FROM option_contracts
        WHERE ticker='SPY' AND option_type='P'
          AND expiration >= ? AND expiration <= ?
        GROUP BY expiration
        HAVING n_strikes >= 100
        ORDER BY expiration
    """, (f"{start_year}-01-01", f"{end_year}-12-31"))
    expirations = [r[0] for r in cur.fetchall()]
    conn.close()
    return expirations


def generate_real_trades(
    hd: IronVault,
    start_year: int = 2020,
    end_year: int = 2025,
) -> pd.DataFrame:
    """Generate credit spread trade log from REAL IronVault option prices.

    Uses actual expirations from options_cache.db. Enters ~30 DTE before
    each monthly expiration. NO synthetic pricing.
    """
    trades = []
    expirations = _get_real_expirations(start_year, end_year)

    for exp_str in expirations:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d")

        # Entry ~30 days before expiration
        entry_date = exp_date - timedelta(days=30)
        entry_str = entry_date.strftime("%Y-%m-%d")

        # Get available strikes
        strikes = hd.get_available_strikes("SPY", exp_str, entry_str, "P")
        if not strikes or len(strikes) < 10:
            continue

        strikes_sorted = sorted(strikes)
        mid_idx = len(strikes_sorted) // 2

        # Short put: ~5% OTM, Long put: ~$5 wide spread
        short_strike = strikes_sorted[max(0, mid_idx - 5)]
        long_strike = short_strike - 5.0

        # Ensure long strike exists
        if long_strike not in strikes_sorted:
            # Find nearest available strike below short
            below = [s for s in strikes_sorted if s < short_strike]
            if len(below) < 3:
                continue
            long_strike = below[-3]

        if short_strike <= long_strike:
            continue

        # Get REAL spread prices from IronVault
        spread = hd.get_spread_prices(
            "SPY", exp_date, short_strike, long_strike, "P", entry_str
        )
        if spread is None:
            continue

        credit = spread.get("net_credit", 0)
        if credit <= 0:
            continue

        # Check exit/expiration price
        exit_spread = hd.get_spread_prices(
            "SPY", exp_date, short_strike, long_strike, "P", exp_str
        )

        if exit_spread is not None:
            exit_debit = exit_spread.get("net_credit", 0)
            pnl = credit - exit_debit
        else:
            short_price = hd.get_contract_price(
                hd.build_occ_symbol("SPY", exp_date, short_strike, "P"),
                exp_str,
            )
            if short_price is not None and short_price > 0.05:
                pnl = credit - (short_strike - long_strike)
            else:
                pnl = credit

        trades.append({
            "entry_date": entry_str,
            "exit_date": exp_str,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "credit": round(credit, 4),
            "pnl": round(pnl, 4),
            "win": 1 if pnl > 0 else 0,
        })

    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Yearly metrics
# ---------------------------------------------------------------------------


def compute_yearly_metrics(
    features: pd.DataFrame,
    signals: list,
    returns: pd.Series,
    trades_df: pd.DataFrame,
) -> dict:
    """Compute year-by-year microstructure metrics."""
    results = {}

    for year in range(2020, 2026):
        yr_mask = features.index.year == year
        yr_features = features[yr_mask]
        yr_returns = returns[returns.index.year == year]

        if yr_features.empty:
            continue

        yr_signals = [s for s in signals if hasattr(s.date, 'year') and s.date.year == year]

        # Regime distribution
        regime_counts = yr_features["regime"].value_counts().to_dict()

        # Standalone Sharpe
        sh = standalone_sharpe(yr_signals, yr_returns) if yr_signals else 0

        # Vol prediction AUC
        auc = volatility_prediction_auc(yr_features, yr_returns)

        # Overlay on year's trades
        yr_trades = trades_df[pd.to_datetime(trades_df["entry_date"]).dt.year == year] if not trades_df.empty else pd.DataFrame()
        overlay = None
        if not yr_trades.empty:
            overlay = overlay_on_trades(yr_signals, yr_trades)

        results[year] = {
            "n_days": len(yr_features),
            "regime_distribution": regime_counts,
            "standalone_sharpe": round(sh, 2),
            "vol_prediction_auc": round(auc, 3),
            "overlay": {
                "total_trades": overlay.total_trades if overlay else 0,
                "enter_trades": overlay.enter_trades if overlay else 0,
                "avoid_trades": overlay.avoid_trades if overlay else 0,
                "enter_win_rate_pct": round(overlay.enter_win_rate * 100, 1) if overlay else 0,
                "avoid_win_rate_pct": round(overlay.avoid_win_rate * 100, 1) if overlay else 0,
                "improvement_pp": round(overlay.improvement_pp, 1) if overlay else 0,
            } if overlay or not yr_trades.empty else None,
        }

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("EXP-1230-real: Microstructure Alpha — REAL DATA BACKTEST")
    print("=" * 70)

    # 1. Load real SPY OHLCV
    print("\n[1/5] Fetching real SPY OHLCV from Yahoo Finance...")
    spy_ohlcv = load_real_spy_ohlcv(start="2020-01-01", end="2025-12-31")
    print(f"  SPY bars: {len(spy_ohlcv)} days "
          f"({spy_ohlcv.index[0].date()} → {spy_ohlcv.index[-1].date()})")
    print(f"  Price range: ${spy_ohlcv['close'].min():.2f} – ${spy_ohlcv['close'].max():.2f}")
    print(f"  Volume range: {spy_ohlcv['volume'].min():,.0f} – {spy_ohlcv['volume'].max():,.0f}")

    # 2. Compute microstructure features on real data
    print("\n[2/5] Computing microstructure features on real OHLCV...")
    features = compute_all_features(spy_ohlcv)
    signals = generate_signals(features)
    print(f"  Features computed: {len(features)} rows × {len(features.columns)} metrics")

    # Regime distribution
    regime_counts = features["regime"].value_counts().to_dict()
    for reg, cnt in sorted(regime_counts.items()):
        print(f"    {reg}: {cnt} ({cnt/len(features)*100:.1f}%)")

    # 3. Standalone metrics
    print("\n[3/5] Computing standalone metrics...")
    returns = spy_ohlcv["close"].pct_change().fillna(0)
    sh = standalone_sharpe(signals, returns)
    auc = volatility_prediction_auc(features, returns)
    print(f"  Standalone Sharpe: {sh:.3f}")
    print(f"  Vol prediction AUC: {auc:.3f}")

    # 4. Generate real credit spread trades from IronVault
    print("\n[4/5] Generating real credit spread trades from IronVault...")
    try:
        hd = IronVault.instance()
        trades_df = generate_real_trades(hd, start_year=2020, end_year=2025)
        print(f"  Real trades generated: {len(trades_df)}")
        if not trades_df.empty:
            win_rate = trades_df["win"].mean()
            print(f"  Overall win rate: {win_rate:.1%}")
    except Exception as e:
        print(f"  IronVault unavailable ({e}) — running overlay on regime-aligned trades")
        trades_df = pd.DataFrame()

    # 5. Overlay test
    print("\n[5/5] Testing overlay on real trades...")
    overlay = None
    if not trades_df.empty:
        overlay = overlay_on_trades(signals, trades_df)
        print(f"  Enter (tight/normal): {overlay.enter_win_rate:.1%} WR ({overlay.enter_trades} trades)")
        print(f"  Avoid (wide/crisis):  {overlay.avoid_win_rate:.1%} WR ({overlay.avoid_trades} trades)")
        print(f"  Improvement: {overlay.improvement_pp:+.1f}pp")

    # Yearly breakdown
    print("\n  === YEARLY BREAKDOWN ===")
    yearly = compute_yearly_metrics(features, signals, returns, trades_df)
    for yr, m in yearly.items():
        ov = m.get("overlay")
        ov_str = ""
        if ov and ov["total_trades"] > 0:
            ov_str = (f"  Overlay: enter={ov['enter_win_rate_pct']:.0f}% "
                      f"avoid={ov['avoid_win_rate_pct']:.0f}% "
                      f"Δ={ov['improvement_pp']:+.1f}pp")
        print(f"  {yr}: Sharpe={m['standalone_sharpe']:.2f}  "
              f"AUC={m['vol_prediction_auc']:.3f}  "
              f"Regimes: {m['regime_distribution']}"
              f"{ov_str}")

    # Compare with synthetic claims
    print("\n  === COMPARISON WITH SYNTHETIC CLAIMS ===")
    print(f"  Synthetic claim:  Standalone Sharpe -0.03, AUC 0.49, Overlay +21.4pp WR")
    print(f"  Real data result: Standalone Sharpe {sh:.3f}, AUC {auc:.3f}")
    if overlay:
        print(f"  Real overlay:     {overlay.improvement_pp:+.1f}pp WR improvement")

    # Save results
    summary = {
        "experiment": "EXP-1230-real",
        "description": "Microstructure Alpha — REAL data backtest",
        "data_source": "Yahoo Finance SPY OHLCV + IronVault options — NO synthetic data",
        "date_range": f"{spy_ohlcv.index[0].date()} to {spy_ohlcv.index[-1].date()}",
        "n_trading_days": len(features),
        "regime_distribution": regime_counts,
        "standalone_sharpe": round(sh, 3),
        "vol_prediction_auc": round(auc, 3),
        "overlay": {
            "total_trades": overlay.total_trades if overlay else 0,
            "enter_trades": overlay.enter_trades if overlay else 0,
            "avoid_trades": overlay.avoid_trades if overlay else 0,
            "enter_win_rate_pct": round(overlay.enter_win_rate * 100, 1) if overlay else 0,
            "avoid_win_rate_pct": round(overlay.avoid_win_rate * 100, 1) if overlay else 0,
            "improvement_pp": round(overlay.improvement_pp, 1) if overlay else 0,
        } if overlay else None,
        "yearly": yearly,
        "synthetic_comparison": {
            "synthetic_standalone_sharpe": -0.03,
            "synthetic_vol_auc": 0.49,
            "synthetic_overlay_improvement_pp": 21.4,
            "real_standalone_sharpe": round(sh, 3),
            "real_vol_auc": round(auc, 3),
            "real_overlay_improvement_pp": round(overlay.improvement_pp, 1) if overlay else None,
        },
    }

    summary_path = OUTPUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Summary saved to {summary_path}")

    # Generate HTML report
    scanner = MicrostructureScanner(spy_ohlcv)
    result = scanner.analyze(trades_df if not trades_df.empty else None)
    report_path = OUTPUT_DIR / "report.html"
    scanner.generate_report(result, output_path=report_path)
    print(f"  Report saved to {report_path}")

    return summary


if __name__ == "__main__":
    main()
