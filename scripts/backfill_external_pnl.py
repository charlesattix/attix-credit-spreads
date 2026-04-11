#!/usr/bin/env python3
"""
Backfill PnL for existing closed_external trades that have NULL pnl.

For each qualifying trade:
  1. Query Alpaca account activities (OPEXP/FILL) to find the actual close event.
  2. If an OPEXP match is found → expired worthless: pnl = credit × contracts × 100 − commission.
  3. If expiration date has passed and credit > 0 → fallback expiration estimate (same formula).
  4. Otherwise → set pnl_needs_review = True and skip (manual reconciliation needed).

Only updates records where:
  - status = 'closed_external'
  - pnl IS NULL (or 0 — see --include-zero flag)

Usage:
    python3 scripts/backfill_external_pnl.py [--db PATH] [--dry-run] [--include-zero]
    python3 scripts/backfill_external_pnl.py --dry-run         # preview without writing
    python3 scripts/backfill_external_pnl.py --include-zero    # also fix pnl=0 records
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from shared.database import close_trade, get_db, get_trades

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alpaca helpers (optional — only used if ALPACA_API_KEY is set)
# ---------------------------------------------------------------------------

def _build_alpaca_client():
    """Return a minimal Alpaca activities client, or None if creds are missing."""
    api_key = os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID")
    api_secret = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY")
    paper = os.environ.get("ALPACA_PAPER", "true").lower() != "false"
    if not api_key or not api_secret:
        logger.warning("Alpaca credentials not found — activity lookup disabled")
        return None
    try:
        from strategy.alpaca_provider import AlpacaProvider
        return AlpacaProvider(api_key=api_key, api_secret=api_secret, paper=paper)
    except Exception as e:
        logger.warning("AlpacaProvider init failed (%s) — activity lookup disabled", e)
        return None


def _query_activities(alpaca, ticker: str, since: Optional[str]) -> Dict[str, List[Dict]]:
    """Return {"OPEXP": [...], "FILL": [...]} activity lists matching *ticker*."""
    result: Dict[str, List[Dict]] = {"OPEXP": [], "FILL": []}
    for act_type in ("OPEXP", "FILL"):
        try:
            acts = alpaca.get_account_activities(activity_type=act_type, since=since)
            result[act_type] = [
                a for a in acts
                if str(a.get("symbol", "")).upper().startswith(ticker.upper())
            ]
        except Exception as e:
            logger.debug("Activity query %s for %s failed: %s", act_type, ticker, e)
    return result


# ---------------------------------------------------------------------------
# PnL estimation
# ---------------------------------------------------------------------------

def _entry_commission(pos: Dict, commission_per_contract: float) -> float:
    spread_type = str(pos.get("strategy_type", pos.get("type", ""))).lower()
    num_legs = 4 if "condor" in spread_type else 2
    contracts = int(pos.get("contracts", 1))
    return commission_per_contract * contracts * num_legs


def _estimate_pnl(
    pos: Dict,
    activities: Optional[Dict[str, List[Dict]]],
    commission_per_contract: float,
) -> Optional[float]:
    """Return estimated PnL in dollars, or None if undetermined."""
    credit = float(pos.get("credit") or 0)
    contracts = int(pos.get("contracts", 1))
    entry_comm = _entry_commission(pos, commission_per_contract)

    # Strategy A: Alpaca OPEXP activity match → expired worthless
    if activities:
        for act in activities.get("OPEXP", []):
            pnl = credit * contracts * 100 - entry_comm
            logger.debug("  OPEXP match: sym=%s → pnl=%.2f", act.get("symbol"), pnl)
            return pnl
        # Strategy A: FILL activity → use net_amount
        for act in activities.get("FILL", []):
            net = act.get("net_amount")
            if net is not None:
                try:
                    pnl = float(net)
                    logger.debug("  FILL match: sym=%s net_amount=%.2f", act.get("symbol"), pnl)
                    return pnl
                except (TypeError, ValueError):
                    pass

    # Strategy B: expiration-date fallback
    exp_str = str(pos.get("expiration", "")).split(" ")[0]
    try:
        exp_date = datetime.fromisoformat(exp_str)
        if exp_date.tzinfo is None:
            exp_date = exp_date.replace(tzinfo=timezone.utc)
        expired = datetime.now(timezone.utc) > exp_date
    except (ValueError, TypeError):
        expired = False

    if expired and credit > 0:
        pnl = credit * contracts * 100 - entry_comm
        logger.debug("  Expiration fallback (exp=%s) → pnl=%.2f [estimated]", exp_str, pnl)
        return pnl

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default=None, help="Path to trades DB (default: shared.database default)")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing to DB")
    parser.add_argument("--include-zero", action="store_true", help="Also update records where pnl=0")
    parser.add_argument("--commission", type=float, default=0.65, help="Commission per contract (default: 0.65)")
    args = parser.parse_args()

    trades = get_trades(status="closed_external", path=args.db)
    logger.info("Found %d closed_external trade(s) in DB", len(trades))

    candidates = []
    for t in trades:
        pnl_val = t.get("pnl")
        if pnl_val is None:
            candidates.append(t)
        elif args.include_zero and float(pnl_val) == 0.0:
            candidates.append(t)

    logger.info("%d trade(s) need PnL backfill", len(candidates))
    if not candidates:
        logger.info("Nothing to do.")
        return

    alpaca = _build_alpaca_client()

    updated = 0
    needs_review = 0

    for pos in candidates:
        pos_id = pos.get("id", "?")
        ticker = pos.get("ticker", "?")
        credit = float(pos.get("credit") or 0)
        contracts = int(pos.get("contracts", 1))
        exp_str = str(pos.get("expiration", "")).split(" ")[0]
        since = pos.get("entry_date")

        logger.info(
            "[%s] %s | credit=%.4f | contracts=%d | exp=%s",
            pos_id, ticker, credit, contracts, exp_str,
        )

        activities = _query_activities(alpaca, ticker, since) if alpaca else None
        pnl = _estimate_pnl(pos, activities, args.commission)

        if pnl is not None:
            estimated = activities is None or (
                not activities.get("OPEXP") and not activities.get("FILL")
            )
            tag = " [estimated]" if estimated else ""
            logger.info("  → pnl=%.2f%s", pnl, tag)
            if not args.dry_run:
                # close_trade preserves closed_external status (fixed in database.py)
                close_trade(pos_id, pnl, "closed_external", path=args.db)
                if estimated:
                    conn = get_db(args.db)
                    try:
                        metadata = pos.get("metadata") or {}
                        if isinstance(metadata, str):
                            try:
                                metadata = json.loads(metadata)
                            except Exception:
                                metadata = {}
                        metadata["pnl_estimated"] = True
                        conn.execute(
                            "UPDATE trades SET metadata=?, updated_at=datetime('now') WHERE id=?",
                            (json.dumps(metadata), pos_id),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                logger.info("  ✓ Updated")
            else:
                logger.info("  (dry-run — not written)")
            updated += 1
        else:
            logger.warning("  → PnL undetermined — marking pnl_needs_review")
            if not args.dry_run:
                conn = get_db(args.db)
                try:
                    metadata = pos.get("metadata") or {}
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except Exception:
                            metadata = {}
                    metadata["pnl_needs_review"] = True
                    conn.execute(
                        "UPDATE trades SET metadata=?, updated_at=datetime('now') WHERE id=?",
                        (json.dumps(metadata), pos_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            needs_review += 1

    logger.info("Done. updated=%d  needs_review=%d", updated, needs_review)
    if args.dry_run:
        logger.info("(dry-run — no changes written)")


if __name__ == "__main__":
    main()
