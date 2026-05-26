"""
Collector — fetches all 57 Attix strategies and archives to SQLite.

Sequential batching (6 slugs per request) to avoid staging server timeouts.
Idempotent: re-running on the same date updates existing rows.
"""

import logging
import time
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import requests

from . import config, db

logger = logging.getLogger(__name__)


# ── API fetch ─────────────────────────────────────────────────────────────────

def fetch_batch(slugs: List[str], retries: int = 2) -> Tuple[List[dict], List[str]]:
    """
    POST a batch of slugs to the Attix API.

    Returns:
        (strategies, failed_slugs)
        strategies: list of strategy dicts from user_recommendation
        failed_slugs: slugs that were requested but not returned
    """
    headers = {
        "Content-Type": "application/json",
        "x-api-key": config.API_KEY,
    }
    payload = {"strategy_slugs": slugs}

    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                config.API_URL,
                json=payload,
                headers=headers,
                timeout=config.API_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            strategies = data.get("user_recommendation", [])
            returned_slugs = {s["strategy_slug"] for s in strategies}
            failed = [s for s in slugs if s not in returned_slugs]
            return strategies, failed

        except requests.RequestException as e:
            if attempt < retries:
                wait = 5 * (attempt + 1)
                logger.warning(
                    "Batch fetch attempt %d/%d failed (%s). Retrying in %ds.",
                    attempt + 1, retries + 1, e, wait,
                )
                time.sleep(wait)
            else:
                logger.error("Batch fetch failed after %d attempts: %s", retries + 1, e)
                return [], slugs  # all failed

    return [], slugs


def fetch_all_strategies() -> Tuple[List[dict], List[str]]:
    """
    Fetch all 57 strategies in sequential batches.

    Returns:
        (all_strategies, all_failed_slugs)
    """
    all_strategies: List[dict] = []
    all_failed: List[str] = []
    slugs = config.ALL_SLUGS
    batch_size = config.API_BATCH_SIZE

    batches = [slugs[i:i + batch_size] for i in range(0, len(slugs), batch_size)]
    logger.info(
        "Fetching %d strategies in %d batches of %d",
        len(slugs), len(batches), batch_size,
    )

    for i, batch in enumerate(batches, 1):
        logger.debug("Batch %d/%d: %s", i, len(batches), batch)
        strategies, failed = fetch_batch(batch)
        all_strategies.extend(strategies)
        all_failed.extend(failed)
        if i < len(batches):
            time.sleep(1)  # polite pacing between sequential requests

    logger.info(
        "Fetched %d strategies, %d slugs failed", len(all_strategies), len(all_failed)
    )
    if all_failed:
        logger.warning("Failed slugs: %s", all_failed)

    return all_strategies, all_failed


# ── QScore computation ────────────────────────────────────────────────────────

def compute_qscore(stock_score: dict) -> float:
    """Weighted composite quality score from Attix stock_score (0-5 scale each)."""
    return (
        config.QSCORE_GROWTH    * stock_score.get("growth", 0)
        + config.QSCORE_MOMENTUM  * stock_score.get("momentum", 0)
        + config.QSCORE_VALUE     * stock_score.get("value", 0)
        + config.QSCORE_HEALTH    * stock_score.get("health", 0)
        + config.QSCORE_PAST      * stock_score.get("past_performance", 0)
    )


# ── Storage ───────────────────────────────────────────────────────────────────

def store_strategies(
    strategies: List[dict],
    snapshot_date: date,
    dry_run: bool = False,
) -> int:
    """
    Write strategies to DB. Returns count of successfully stored strategies.
    """
    if dry_run:
        logger.info("[DRY RUN] Would store %d strategies for %s", len(strategies), snapshot_date)
        return len(strategies)

    stored = 0
    with db.transaction() as conn:
        for strat in strategies:
            slug = strat.get("strategy_slug", "")
            name = strat.get("strategy_name", slug)
            holdings = strat.get("candidate_asset", [])
            scores = strat.get("stock_score", {})
            qscore = compute_qscore(scores)

            try:
                snapshot_id = db.upsert_snapshot(
                    conn,
                    snapshot_date,
                    slug,
                    name,
                    strat.get("total_cost", 0),
                    strat.get("leftover", 0),
                    len(holdings),
                )
                db.insert_holdings(conn, snapshot_id, holdings)
                db.upsert_scores(conn, snapshot_id, scores, qscore)
                stored += 1
            except Exception as e:
                logger.error("Failed to store strategy %s: %s", slug, e)

    logger.info("Stored %d/%d strategies for %s", stored, len(strategies), snapshot_date)
    return stored


# ── Collection run log ────────────────────────────────────────────────────────

def log_collection_run(
    run_date: date,
    started_at: datetime,
    status: str,
    strategies_ok: int,
    strategies_fail: int,
    error_msg: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    if dry_run:
        return
    completed_at = datetime.utcnow()
    duration = (completed_at - started_at).total_seconds()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO collection_log
                   (run_date, started_at, completed_at, status,
                    strategies_ok, strategies_fail, error_msg, duration_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_date.isoformat(),
                started_at.isoformat(),
                completed_at.isoformat(),
                status,
                strategies_ok,
                strategies_fail,
                error_msg,
                duration,
            ),
        )


# ── Main entry ────────────────────────────────────────────────────────────────

def run_collection(
    snapshot_date: Optional[date] = None,
    force: bool = False,
    dry_run: bool = False,
) -> Dict:
    """
    Full collection run.

    Args:
        snapshot_date: Override date (default: today)
        force: Re-fetch even if snapshot already exists for this date
        dry_run: Print what would happen without writing to DB or calling API

    Returns:
        dict with run summary
    """
    snap_date = snapshot_date or date.today()
    started_at = datetime.utcnow()

    # Check if already collected today
    if not force and not dry_run:
        with db.transaction() as conn:
            if db.date_has_snapshot(conn, snap_date):
                logger.info(
                    "Snapshot for %s already exists (use --force to overwrite)", snap_date
                )
                return {"status": "skipped", "date": snap_date.isoformat()}

    logger.info("Starting collection for %s (dry_run=%s)", snap_date, dry_run)

    try:
        strategies, failed_slugs = fetch_all_strategies()
    except Exception as e:
        logger.exception("Collection failed during fetch")
        log_collection_run(
            snap_date, started_at, "FAILED", 0, len(config.ALL_SLUGS),
            str(e), dry_run,
        )
        return {"status": "failed", "error": str(e)}

    stored = store_strategies(strategies, snap_date, dry_run=dry_run)

    status = "SUCCESS" if not failed_slugs else "PARTIAL"
    log_collection_run(
        snap_date, started_at, status,
        stored, len(failed_slugs),
        f"Failed slugs: {failed_slugs}" if failed_slugs else None,
        dry_run,
    )

    result = {
        "status": status,
        "date": snap_date.isoformat(),
        "strategies_fetched": len(strategies),
        "strategies_stored": stored,
        "failed_slugs": failed_slugs,
        "duration_sec": (datetime.utcnow() - started_at).total_seconds(),
    }
    logger.info("Collection complete: %s", result)
    return result
