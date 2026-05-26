"""
Scorer — computes daily ticker conviction signals from stored snapshots.

conviction = 0.40 × freq_score + 0.35 × persistence_score + 0.25 × quality_score

All three components are individually normalized [0, 1] within the day's ticker universe,
then combined. The result is also normalized [0, 1] for interpretable alerting thresholds.
"""

import logging
from collections import defaultdict
from datetime import date
from typing import Dict, List

from . import config, db

logger = logging.getLogger(__name__)


def _normalize(values: Dict[str, float]) -> Dict[str, float]:
    """Min-max normalize a dict of float values to [0, 1]."""
    if not values:
        return {}
    mn = min(values.values())
    mx = max(values.values())
    if mx == mn:
        return {k: 0.5 for k in values}
    return {k: (v - mn) / (mx - mn) for k, v in values.items()}


def _get_persistence(conn, ticker: str, today: date) -> int:
    """
    Count consecutive days ticker has appeared in signals ending on (but not including) today.
    Returns 0 if yesterday not in signal, N if in signal for N prior consecutive days.
    """
    rows = db.get_ticker_history(conn, ticker, days=config.PERSISTENCE_CAP_DAYS + 2)
    # rows are ordered DESC by date, skip today if present
    consecutive = 0
    prev_date = None
    def _to_date(v):
        return v if isinstance(v, date) else date.fromisoformat(str(v))

    for row in rows:
        row_date = _to_date(row["signal_date"])
        if row_date >= today:
            continue
        if prev_date is None:
            prev_date = row_date
            consecutive = 1
        else:
            # Check if consecutive calendar days (skip weekends gracefully by
            # allowing gaps of 1-3 days between trading days)
            gap = (prev_date - row_date).days
            if gap <= 3:
                consecutive += 1
                prev_date = row_date
            else:
                break
    return min(consecutive, config.PERSISTENCE_CAP_DAYS)


def compute_signals(
    snap_date: date,
    dry_run: bool = False,
) -> List[dict]:
    """
    Build ticker_signals for snap_date from strategy_snapshots stored in DB.

    Returns list of signal dicts (also written to DB unless dry_run).
    """
    with db.transaction() as conn:
        snapshots = db.get_snapshots_for_date(conn, snap_date)
        if not snapshots:
            logger.warning("No snapshots found for %s — run collector first", snap_date)
            return []

        n_total = len(snapshots)
        logger.info("Computing signals from %d snapshots for %s", n_total, snap_date)

        # ── Aggregate holdings across all portfolios ───────────────────────────
        ticker_freq: Dict[str, int] = defaultdict(int)
        ticker_weight_sum: Dict[str, float] = defaultdict(float)
        ticker_wq_sum: Dict[str, float] = defaultdict(float)  # weight × qscore

        for snap in snapshots:
            qscore = snap["composite_qscore"] or 0.0
            holdings = db.get_holdings_for_snapshot(conn, snap["id"])

            # Normalize weights within portfolio (guard against missing data)
            total_w = sum(h["weight"] or 0 for h in holdings)
            if total_w == 0:
                continue

            for h in holdings:
                ticker = h["ticker"]
                norm_w = (h["weight"] or 0) / total_w
                ticker_freq[ticker] += 1
                ticker_weight_sum[ticker] += norm_w
                ticker_wq_sum[ticker] += norm_w * qscore

        if not ticker_freq:
            logger.warning("No holdings found for %s", snap_date)
            return []

        # ── Component scores ───────────────────────────────────────────────────
        # Frequency score: fraction of portfolios holding this ticker
        freq_scores = {t: c / n_total for t, c in ticker_freq.items()}

        # Average weight per ticker (across portfolios that hold it)
        avg_weights = {t: ticker_weight_sum[t] / ticker_freq[t] for t in ticker_freq}

        # Quality-weighted score: sum(weight_i × qscore_i)
        wq_scores = {t: ticker_wq_sum[t] for t in ticker_freq}

        # ── Persistence (from historical signal table) ─────────────────────────
        persistence_raw = {
            t: _get_persistence(conn, t, snap_date)
            for t in ticker_freq
        }

        # ── Normalize each component [0, 1] ───────────────────────────────────
        n_freq = _normalize(freq_scores)
        n_persistence = {
            t: min(v, config.PERSISTENCE_CAP_DAYS) / config.PERSISTENCE_CAP_DAYS
            for t, v in persistence_raw.items()
        }
        n_wq = _normalize(wq_scores)

        # ── Combine ────────────────────────────────────────────────────────────
        raw_conviction = {
            t: (
                config.SCORE_WEIGHT_FREQ * n_freq[t]
                + config.SCORE_WEIGHT_PERSISTENCE * n_persistence[t]
                + config.SCORE_WEIGHT_QUALITY * n_wq.get(t, 0)
            )
            for t in ticker_freq
        }
        # Final normalize so scale is always [0, 1]
        conviction = _normalize(raw_conviction)

        # ── Build output rows ──────────────────────────────────────────────────
        signals = []
        for ticker, conv in conviction.items():
            signals.append({
                "signal_date": snap_date.isoformat(),
                "ticker": ticker,
                "frequency": ticker_freq[ticker],
                "total_portfolios": n_total,
                "freq_pct": freq_scores[ticker],
                "avg_weight": avg_weights[ticker],
                "weighted_qscore": wq_scores[ticker],
                "days_in_signal": persistence_raw[ticker],
                "conviction": round(conv, 6),
            })

        signals.sort(key=lambda x: x["conviction"], reverse=True)

        # ── Write to DB ────────────────────────────────────────────────────────
        if not dry_run:
            for row in signals:
                db.upsert_signal(conn, row)
            logger.info(
                "Wrote %d ticker signals for %s (top: %s conv=%.3f)",
                len(signals), snap_date,
                signals[0]["ticker"] if signals else "—",
                signals[0]["conviction"] if signals else 0,
            )
        else:
            logger.info(
                "[DRY RUN] Would write %d ticker signals for %s", len(signals), snap_date
            )

        return signals


def rebuild_all_signals(dry_run: bool = False) -> int:
    """
    Recompute ticker_signals for every date that has snapshots.
    Useful after schema changes or scoring formula updates.
    """
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT DISTINCT snapshot_date FROM strategy_snapshots ORDER BY snapshot_date"
        ).fetchall()
    dates = [
        r["snapshot_date"] if isinstance(r["snapshot_date"], date)
        else date.fromisoformat(str(r["snapshot_date"]))
        for r in rows
    ]
    logger.info("Rebuilding signals for %d dates", len(dates))
    for d in dates:
        compute_signals(d, dry_run=dry_run)
    return len(dates)
