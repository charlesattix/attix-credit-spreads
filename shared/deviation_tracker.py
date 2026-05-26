"""
Persistent live-vs-backtest deviation tracking.

Two layers:
  1. **Daily snapshots** (deviation_snapshots) — aggregate live-vs-backtest comparison.
  2. **Per-trade deviations** (trade_deviations) — INF-5: compare each closed paper
     trade against backtest expectations (credit, P&L, hold time, outcome).

Reuses functions from scripts/live_vs_backtest.py rather than duplicating logic.

CLI usage:
    python -m shared.deviation_tracker --db data/attix_champion.db --report
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.database import get_db

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).parent.parent / "configs" / "champion.json"

# ── INF-5: Per-trade deviation tracking ─────────────────────────────────────

# Backtest assumptions (defaults — match champion config)
_DEFAULT_CREDIT_RATIO = 0.35  # expected credit = spread_width * this
_DEFAULT_PROFIT_TARGET_PCT = 50.0  # expected exit at 50% of credit
_DEFAULT_HOLD_DAYS = 21  # expected avg hold (30-45 DTE, manage at ~21)

# Alert thresholds
_ALIGNMENT_THRESHOLD = 0.70  # rolling alignment score minimum
_CREDIT_DEVIATION_THRESHOLD = 0.25  # 25% avg credit deviation
_ROLLING_WINDOW = 20  # last N trades for rolling metrics


def _ensure_trade_deviations_table(conn: sqlite3.Connection) -> None:
    """Create trade_deviations table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_deviations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE NOT NULL,
            paper_credit REAL,
            expected_credit REAL,
            paper_pnl_pct REAL,
            expected_pnl_pct REAL,
            paper_hold_days REAL,
            expected_hold_days REAL,
            paper_outcome TEXT,
            expected_outcome TEXT,
            deviation_score REAL,
            timestamp TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def record_deviation(
    trade: Dict[str, Any],
    pnl: float,
    fill_price: float,
    db_path: Optional[str] = None,
    config: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    """Record a per-trade deviation after a paper trade closes.

    Called from PositionMonitor._record_close_pnl() with the position dict,
    realized P&L, and fill price.

    Args:
        trade: Position dict with keys: id, credit, short_strike, long_strike,
               contracts, entry_date, strategy_type, etc.
        pnl: Realized P&L in dollars.
        fill_price: Fill price of the closing order.
        db_path: SQLite database path.
        config: Optional config dict for custom backtest assumptions.

    Returns:
        Deviation record dict, or None if insufficient data.
    """
    trade_id = str(trade.get("id", ""))
    if not trade_id:
        logger.warning("deviation_tracker: no trade_id, skipping")
        return None

    credit = float(trade.get("credit") or 0)
    contracts = int(trade.get("contracts", 1))
    short_strike = float(trade.get("short_strike") or 0)
    long_strike = float(trade.get("long_strike") or 0)
    spread_width = abs(short_strike - long_strike) if short_strike and long_strike else 0

    if spread_width == 0:
        logger.debug("deviation_tracker: no spread width for %s, skipping", trade_id)
        return None

    # ── Paper actuals ───────────────────────────────────────────────────
    paper_credit = credit
    max_loss = spread_width * contracts * 100
    paper_pnl_pct = round(pnl / max_loss * 100, 2) if max_loss > 0 else 0.0
    paper_outcome = "win" if pnl > 0 else ("scratch" if abs(pnl) < 0.01 else "loss")

    # Hold days
    entry_date_str = trade.get("entry_date") or trade.get("created_at", "")
    paper_hold_days = 0.0
    if entry_date_str:
        try:
            entry_dt = datetime.fromisoformat(str(entry_date_str))
            paper_hold_days = round((datetime.now() - entry_dt.replace(tzinfo=None)).total_seconds() / 86400, 2)
        except (ValueError, TypeError):
            pass

    # ── Backtest expectations ───────────────────────────────────────────
    cfg = config or {}
    credit_ratio = float(cfg.get("backtest", {}).get("credit_ratio", _DEFAULT_CREDIT_RATIO))
    profit_target_pct = float(cfg.get("risk", {}).get("profit_target", _DEFAULT_PROFIT_TARGET_PCT))

    expected_credit = round(spread_width * credit_ratio, 4)
    # Expected P&L: win at profit_target, loss at -spread_width (simplified)
    # We use a blended expectation based on backtest win rate (~70% wins at +50% credit)
    expected_pnl_pct = round(profit_target_pct, 2)  # positive for expected win
    expected_hold_days = float(cfg.get("backtest", {}).get("expected_hold_days", _DEFAULT_HOLD_DAYS))
    expected_outcome = "win"  # backtest assumes winning trade at target

    # ── Deviation score (0 = perfect match, higher = worse) ─────────────
    credit_dev = abs(paper_credit - expected_credit) / expected_credit if expected_credit > 0 else 0
    pnl_dev = abs(paper_pnl_pct - expected_pnl_pct) / 100.0
    hold_dev = abs(paper_hold_days - expected_hold_days) / expected_hold_days if expected_hold_days > 0 else 0
    outcome_dev = 0.0 if paper_outcome == expected_outcome else 1.0
    deviation_score = round(
        0.3 * credit_dev + 0.3 * outcome_dev + 0.2 * pnl_dev + 0.2 * min(hold_dev, 2.0),
        4,
    )

    record = {
        "trade_id": trade_id,
        "paper_credit": round(paper_credit, 4),
        "expected_credit": expected_credit,
        "paper_pnl_pct": paper_pnl_pct,
        "expected_pnl_pct": expected_pnl_pct,
        "paper_hold_days": round(paper_hold_days, 2),
        "expected_hold_days": expected_hold_days,
        "paper_outcome": paper_outcome,
        "expected_outcome": expected_outcome,
        "deviation_score": deviation_score,
    }

    # Persist
    conn = get_db(db_path)
    try:
        _ensure_trade_deviations_table(conn)
        conn.execute(
            """
            INSERT INTO trade_deviations
                (trade_id, paper_credit, expected_credit, paper_pnl_pct, expected_pnl_pct,
                 paper_hold_days, expected_hold_days, paper_outcome, expected_outcome,
                 deviation_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_id) DO UPDATE SET
                paper_credit=excluded.paper_credit,
                expected_credit=excluded.expected_credit,
                paper_pnl_pct=excluded.paper_pnl_pct,
                expected_pnl_pct=excluded.expected_pnl_pct,
                paper_hold_days=excluded.paper_hold_days,
                expected_hold_days=excluded.expected_hold_days,
                paper_outcome=excluded.paper_outcome,
                expected_outcome=excluded.expected_outcome,
                deviation_score=excluded.deviation_score,
                timestamp=datetime('now')
            """,
            (
                trade_id, record["paper_credit"], record["expected_credit"],
                record["paper_pnl_pct"], record["expected_pnl_pct"],
                record["paper_hold_days"], record["expected_hold_days"],
                record["paper_outcome"], record["expected_outcome"],
                record["deviation_score"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "deviation_tracker: recorded %s | credit %.4f vs %.4f | outcome %s vs %s | score %.4f",
        trade_id, paper_credit, expected_credit, paper_outcome, expected_outcome, deviation_score,
    )

    # Check rolling alerts
    _check_rolling_alerts(db_path)

    return record


def get_rolling_alignment(db_path: Optional[str] = None, window: int = _ROLLING_WINDOW) -> Dict[str, Any]:
    """Compute rolling alignment metrics over the last *window* trades.

    Returns:
        Dict with alignment_score, credit_deviation, trade_count, recent_deviations.
    """
    conn = get_db(db_path)
    try:
        _ensure_trade_deviations_table(conn)
        rows = conn.execute(
            "SELECT * FROM trade_deviations ORDER BY timestamp DESC LIMIT ?",
            (window,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"alignment_score": 1.0, "credit_deviation": 0.0, "trade_count": 0, "recent_deviations": []}

    records = [dict(r) for r in rows]
    n = len(records)

    # Alignment score: % where paper_outcome matches expected_outcome
    matches = sum(1 for r in records if r["paper_outcome"] == r["expected_outcome"])
    alignment_score = matches / n

    # Credit deviation: avg(abs(paper - expected) / expected)
    credit_devs = []
    for r in records:
        exp = r.get("expected_credit") or 0
        if exp > 0:
            credit_devs.append(abs((r["paper_credit"] or 0) - exp) / exp)
    credit_deviation = sum(credit_devs) / len(credit_devs) if credit_devs else 0.0

    return {
        "alignment_score": round(alignment_score, 4),
        "credit_deviation": round(credit_deviation, 4),
        "trade_count": n,
        "recent_deviations": records,
    }


def _check_rolling_alerts(db_path: Optional[str] = None) -> None:
    """Check rolling metrics and send Telegram alerts if thresholds breached."""
    try:
        metrics = get_rolling_alignment(db_path)
        if metrics["trade_count"] < _ROLLING_WINDOW:
            return  # not enough data yet

        alerts = []
        if metrics["alignment_score"] < _ALIGNMENT_THRESHOLD:
            alerts.append(
                f"📉 Rolling alignment score: {metrics['alignment_score']:.0%} "
                f"(threshold: {_ALIGNMENT_THRESHOLD:.0%}, last {_ROLLING_WINDOW} trades)"
            )
        if metrics["credit_deviation"] > _CREDIT_DEVIATION_THRESHOLD:
            alerts.append(
                f"💰 Credit deviation: {metrics['credit_deviation']:.1%} "
                f"(threshold: {_CREDIT_DEVIATION_THRESHOLD:.0%}) — fills differ significantly from backtest"
            )

        if alerts:
            try:
                from shared.telegram_alerts import send_message
                header = "⚠️ <b>INF-5 DEVIATION ALERT</b>\n\n"
                body = "\n".join(f"• {a}" for a in alerts)
                send_message(header + body)
            except Exception as e:
                logger.warning("deviation_tracker: Telegram alert failed: %s", e)
    except Exception as e:
        logger.warning("deviation_tracker: rolling alert check failed: %s", e)


def print_report(db_path: str) -> None:
    """Print a human-readable alignment report to stdout."""
    metrics = get_rolling_alignment(db_path)
    n = metrics["trade_count"]
    print(f"\n{'='*60}")
    print(f"  INF-5 Deviation Tracker — Alignment Report")
    print(f"{'='*60}")
    print(f"  Trades analyzed:    {n}")
    if n == 0:
        print("  No trade deviations recorded yet.")
        print(f"{'='*60}\n")
        return

    print(f"  Alignment score:    {metrics['alignment_score']:.1%}"
          f"  {'✅' if metrics['alignment_score'] >= _ALIGNMENT_THRESHOLD else '❌ BELOW THRESHOLD'}")
    print(f"  Credit deviation:   {metrics['credit_deviation']:.1%}"
          f"  {'✅' if metrics['credit_deviation'] <= _CREDIT_DEVIATION_THRESHOLD else '❌ ABOVE THRESHOLD'}")

    print(f"\n  {'─'*56}")
    print(f"  Recent trades (newest first):")
    print(f"  {'Trade ID':<20} {'Paper':>7} {'Expect':>7} {'Outcome':>8} {'Match':>6} {'Score':>6}")
    print(f"  {'─'*56}")
    for r in metrics["recent_deviations"][:20]:
        tid = str(r.get("trade_id", "?"))[:18]
        match = "✅" if r["paper_outcome"] == r["expected_outcome"] else "❌"
        print(
            f"  {tid:<20} {r.get('paper_credit', 0):>7.3f} {r.get('expected_credit', 0):>7.3f} "
            f"{r.get('paper_outcome', '?'):>8} {match:>6} {r.get('deviation_score', 0):>6.3f}"
        )
    print(f"{'='*60}\n")


def record_deviation_snapshot(
    config_path: Optional[str] = None,
    db_path: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Record a live-vs-backtest deviation snapshot to SQLite.

    Loads live trades, computes metrics, optionally runs backtest,
    compares, and persists the result. Returns the snapshot dict.

    Skips backtest if < 5 live trades (stores live-only snapshot).
    Returns None if no live trades exist.
    """
    from scripts.live_vs_backtest import (
        compare_metrics,
        compute_live_metrics,
        load_live_trades,
        run_backtest_for_range,
    )

    config = Path(config_path) if config_path else DEFAULT_CONFIG

    # Load live trades and compute metrics
    trades = load_live_trades()
    live = compute_live_metrics(trades)
    n = live.get("total_trades", 0)

    if n == 0:
        logger.info("No closed trades found — skipping deviation snapshot")
        return None

    # Determine date range
    if start is None:
        start_str = live.get("start_date")
        if start_str:
            start = datetime.strptime(str(start_str)[:10], "%Y-%m-%d")
        else:
            logger.warning("Cannot determine start date for deviation snapshot")
            return None

    if end is None:
        end_str = live.get("end_date")
        end = datetime.strptime(str(end_str)[:10], "%Y-%m-%d") if end_str else datetime.now()

    snapshot_date = datetime.now().strftime("%Y-%m-%d")

    # Run backtest if enough trades
    backtest = None
    comparisons = None
    overall_status = "INFO"

    if n >= 5:
        try:
            backtest = run_backtest_for_range(config, start, end)
            comparisons = compare_metrics(live, backtest)
            overall_status = _compute_overall_status(comparisons)
        except Exception as e:
            logger.error(f"Backtest failed during deviation snapshot: {e}")

    # Build snapshot dict
    bt_combined = (backtest or {}).get("combined", {}) if backtest else {}
    snapshot = {
        "snapshot_date": snapshot_date,
        "live_trades": n,
        "bt_trades": bt_combined.get("total_trades"),
        "live_win_rate": live.get("win_rate"),
        "bt_win_rate": bt_combined.get("win_rate"),
        "live_pnl": live.get("total_pnl"),
        "bt_pnl": bt_combined.get("total_pnl"),
        "live_return_pct": live.get("return_pct"),
        "bt_return_pct": bt_combined.get("return_pct"),
        "live_profit_factor": live.get("profit_factor"),
        "bt_profit_factor": bt_combined.get("profit_factor"),
        "live_max_dd": live.get("max_drawdown"),
        "bt_max_dd": bt_combined.get("max_drawdown"),
        "overall_status": overall_status,
        "details": {
            "comparisons": comparisons,
            "live_per_strategy": live.get("per_strategy"),
            "bt_per_strategy": (backtest or {}).get("per_strategy"),
            "date_range": {"start": str(start.date()), "end": str(end.date())},
        },
    }

    # Persist — upsert on snapshot_date
    _upsert_snapshot(snapshot, db_path)

    logger.info(f"Deviation snapshot recorded: {snapshot_date} — {overall_status}")
    return snapshot


def get_deviation_history(days: int = 30, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Query deviation_snapshots for the last N days, newest first."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM deviation_snapshots WHERE snapshot_date >= ? ORDER BY snapshot_date DESC",
            (cutoff,),
        ).fetchall()
        return [_row_to_snapshot(r) for r in rows]
    finally:
        conn.close()


def get_latest_deviation(db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the most recent deviation snapshot, or None."""
    conn = get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM deviation_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        return _row_to_snapshot(row) if row else None
    finally:
        conn.close()


def check_deviation_alerts(snapshot: Dict[str, Any]) -> List[str]:
    """Examine a snapshot for WARN/FAIL metrics. Returns human-readable alert strings."""
    alerts: List[str] = []
    if not snapshot:
        return alerts

    details = snapshot.get("details") or {}
    comparisons = details.get("comparisons") or []

    for c in comparisons:
        status = c.get("status", "INFO")
        if status in ("WARN", "FAIL"):
            metric = c.get("metric", "Unknown")
            live_str = c.get("live_str", "?")
            bt_str = c.get("backtest_str", "?")
            alerts.append(f"{metric} deviation: live {live_str} vs backtest {bt_str} ({status})")

    return alerts


# ── Internal helpers ────────────────────────────────────────────────────────


def _compute_overall_status(comparisons: List[Dict[str, Any]]) -> str:
    """Derive overall status from the worst individual metric status."""
    statuses = {c.get("status", "INFO") for c in comparisons}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARN" in statuses:
        return "WARN"
    if "PASS" in statuses:
        return "PASS"
    return "INFO"


def _upsert_snapshot(snapshot: Dict[str, Any], db_path: Optional[str] = None) -> None:
    """Insert or replace a deviation snapshot (one per day)."""
    conn = get_db(db_path)
    try:
        conn.execute(
            """
            INSERT INTO deviation_snapshots
                (snapshot_date, live_trades, bt_trades, live_win_rate, bt_win_rate,
                 live_pnl, bt_pnl, live_return_pct, bt_return_pct,
                 live_profit_factor, bt_profit_factor, live_max_dd, bt_max_dd,
                 overall_status, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date) DO UPDATE SET
                live_trades=excluded.live_trades,
                bt_trades=excluded.bt_trades,
                live_win_rate=excluded.live_win_rate,
                bt_win_rate=excluded.bt_win_rate,
                live_pnl=excluded.live_pnl,
                bt_pnl=excluded.bt_pnl,
                live_return_pct=excluded.live_return_pct,
                bt_return_pct=excluded.bt_return_pct,
                live_profit_factor=excluded.live_profit_factor,
                bt_profit_factor=excluded.bt_profit_factor,
                live_max_dd=excluded.live_max_dd,
                bt_max_dd=excluded.bt_max_dd,
                overall_status=excluded.overall_status,
                details=excluded.details,
                created_at=datetime('now')
            """,
            (
                snapshot["snapshot_date"],
                snapshot.get("live_trades"),
                snapshot.get("bt_trades"),
                snapshot.get("live_win_rate"),
                snapshot.get("bt_win_rate"),
                snapshot.get("live_pnl"),
                snapshot.get("bt_pnl"),
                snapshot.get("live_return_pct"),
                snapshot.get("bt_return_pct"),
                snapshot.get("live_profit_factor"),
                snapshot.get("bt_profit_factor"),
                snapshot.get("live_max_dd"),
                snapshot.get("bt_max_dd"),
                snapshot.get("overall_status"),
                json.dumps(snapshot.get("details"), default=str),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_snapshot(row) -> Dict[str, Any]:
    """Convert a database row to a snapshot dict."""
    d = dict(row)
    if d.get("details"):
        try:
            d["details"] = json.loads(d["details"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


# ── CLI entry point ─────────────────────────────────────────────────────────

def _cli_main() -> None:
    """CLI: python -m shared.deviation_tracker --db <path> --report"""
    parser = argparse.ArgumentParser(
        description="INF-5 Deviation Tracker — paper vs backtest alignment monitoring"
    )
    parser.add_argument("--db", required=True, help="Path to experiment SQLite database")
    parser.add_argument("--report", action="store_true", help="Print alignment summary report")
    args = parser.parse_args()

    if args.report:
        print_report(args.db)
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
