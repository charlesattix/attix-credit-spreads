"""
ML-1: Trade Feature Logger

Logs every feature at trade entry time into a structured SQLite table
so we can train real ML models on real data. Outcome columns (outcome,
pnl_pct, hold_days) are filled when the trade closes.

Usage as CLI:
    python -m shared.feature_logger --db data/attix_champion.db --stats
"""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from shared.database import get_db

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trade_features (
    trade_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    ticker TEXT NOT NULL,
    strategy_type TEXT,
    direction TEXT,
    regime TEXT,
    vix REAL,
    vix_rank REAL,
    vix_percentile REAL,
    iv_rank REAL,
    rsi REAL,
    ma200_distance REAL,
    dte INTEGER,
    otm_pct REAL,
    spread_width REAL,
    credit_received REAL,
    max_loss REAL,
    realized_vol_20d REAL,
    realized_vol_5d REAL,
    vix_vix3m_ratio REAL,
    vol_premium_zscore REAL,
    score REAL,
    outcome TEXT,
    pnl_pct REAL,
    hold_days REAL
)
"""


class FeatureLogger:
    """Logs trade features at entry and updates outcomes at close."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        """Create the trade_features table if it doesn't exist."""
        try:
            conn = get_db(self.db_path)
            try:
                conn.execute(_CREATE_TABLE)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("FeatureLogger: failed to create table: %s", e)

    def log_entry(self, trade_id: str, features: Dict[str, Any]) -> None:
        """Log features at trade entry time. Never raises."""
        try:
            conn = get_db(self.db_path)
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO trade_features (
                        trade_id, timestamp, ticker, strategy_type, direction,
                        regime, vix, vix_rank, vix_percentile, iv_rank, rsi,
                        ma200_distance, dte, otm_pct, spread_width,
                        credit_received, max_loss, realized_vol_20d,
                        realized_vol_5d, vix_vix3m_ratio, vol_premium_zscore,
                        score
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )""",
                    (
                        trade_id,
                        features.get("timestamp", datetime.now(timezone.utc).isoformat()),
                        features.get("ticker", ""),
                        features.get("strategy_type", ""),
                        features.get("direction", ""),
                        features.get("regime", ""),
                        features.get("vix"),
                        features.get("vix_rank"),
                        features.get("vix_percentile"),
                        features.get("iv_rank"),
                        features.get("rsi"),
                        features.get("ma200_distance"),
                        features.get("dte"),
                        features.get("otm_pct"),
                        features.get("spread_width"),
                        features.get("credit_received"),
                        features.get("max_loss"),
                        features.get("realized_vol_20d"),
                        features.get("realized_vol_5d"),
                        features.get("vix_vix3m_ratio"),
                        features.get("vol_premium_zscore"),
                        features.get("score"),
                    ),
                )
                conn.commit()
                logger.info("FeatureLogger: logged entry features for %s", trade_id)
            finally:
                conn.close()
        except Exception as e:
            logger.warning("FeatureLogger: failed to log entry for %s: %s", trade_id, e)

    def log_outcome(self, trade_id: str, outcome: str, pnl_pct: float, hold_days: float) -> None:
        """Update outcome columns when a trade closes. Never raises."""
        try:
            conn = get_db(self.db_path)
            try:
                conn.execute(
                    """UPDATE trade_features
                       SET outcome = ?, pnl_pct = ?, hold_days = ?
                       WHERE trade_id = ?""",
                    (outcome, pnl_pct, hold_days, trade_id),
                )
                conn.commit()
                logger.info("FeatureLogger: logged outcome for %s: %s (%.2f%%)", trade_id, outcome, pnl_pct)
            finally:
                conn.close()
        except Exception as e:
            logger.warning("FeatureLogger: failed to log outcome for %s: %s", trade_id, e)

    def get_stats(self) -> Dict[str, Any]:
        """Return summary statistics for the trade_features table."""
        conn = get_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) as total, MIN(timestamp) as first, MAX(timestamp) as last FROM trade_features"
            ).fetchone()
            total = row["total"]
            first_ts = row["first"]
            last_ts = row["last"]

            # Class balance
            outcomes = conn.execute(
                "SELECT outcome, COUNT(*) as cnt FROM trade_features GROUP BY outcome"
            ).fetchall()
            class_balance = {r["outcome"] or "pending": r["cnt"] for r in outcomes}

            return {
                "total_features": total,
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "class_balance": class_balance,
            }
        finally:
            conn.close()


def _extract_features_from_opportunity(opp: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
    """Extract ML features from an opportunity dict and optional context.

    Args:
        opp: Opportunity/trade dict from the scanner/execution engine.
        context: Optional dict with enriched context (vix, iv_rank, rsi, etc.)
                 Typically attached to the opportunity as opp['_ml_features'].

    Returns:
        Dict of feature values ready for FeatureLogger.log_entry().
    """
    ctx = context or opp.get("_ml_features", {})

    short_strike = float(opp.get("short_strike", 0) or 0)
    current_price = float(ctx.get("current_price", 0) or opp.get("current_price", 0) or 0)
    otm_pct = None
    if current_price > 0 and short_strike > 0:
        otm_pct = round(abs(current_price - short_strike) / current_price * 100, 4)

    credit = float(opp.get("credit", 0) or opp.get("credit_per_spread", 0) or 0)
    spread_width = float(opp.get("spread_width", 0) or 0)
    max_loss = float(opp.get("max_loss", 0) or 0)
    if max_loss == 0 and spread_width > 0:
        max_loss = (spread_width - credit) * 100  # per contract

    # Direction from strategy_type
    stype = opp.get("type", opp.get("strategy_type", ""))
    direction = "bullish" if "put" in str(stype).lower() else "bearish" if "call" in str(stype).lower() else "neutral"

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": opp.get("ticker", ""),
        "strategy_type": stype,
        "direction": direction,
        "regime": ctx.get("regime", ""),
        "vix": ctx.get("vix"),
        "vix_rank": ctx.get("vix_rank"),
        "vix_percentile": ctx.get("vix_percentile"),
        "iv_rank": ctx.get("iv_rank"),
        "rsi": ctx.get("rsi"),
        "ma200_distance": ctx.get("ma200_distance"),
        "dte": opp.get("dte"),
        "otm_pct": otm_pct,
        "spread_width": spread_width,
        "credit_received": credit,
        "max_loss": max_loss,
        "realized_vol_20d": ctx.get("realized_vol_20d"),
        "realized_vol_5d": ctx.get("realized_vol_5d"),
        "vix_vix3m_ratio": ctx.get("vix_vix3m_ratio"),
        "vol_premium_zscore": ctx.get("vol_premium_zscore"),
        "score": opp.get("score"),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli_main():
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="Trade Feature Logger stats")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--stats", action="store_true", help="Show feature stats")
    args = parser.parse_args()

    fl = FeatureLogger(db_path=args.db)
    if args.stats:
        stats = fl.get_stats()
        print(f"Total logged trades:  {stats['total_features']}")
        print(f"Date range:           {stats['first_timestamp'] or 'N/A'} → {stats['last_timestamp'] or 'N/A'}")
        print(f"Class balance:")
        for outcome, cnt in stats["class_balance"].items():
            print(f"  {outcome}: {cnt}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
