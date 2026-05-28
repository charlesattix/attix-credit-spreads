#!/usr/bin/env python3
"""backfill_equity_history.py — one-shot equity-curve backfill + dashboard push.

Populates each experiment's durable ``equity_history`` table with the full
inception→now daily curve from Alpaca's portfolio-history API, then pushes the
curve to the dashboard so the chart renders immediately (any market state).
Idempotent — safe to re-run.

Usage:
    python scripts/backfill_equity_history.py                 # all active experiments
    python scripts/backfill_equity_history.py --exp EXP-3311  # one experiment
    python scripts/backfill_equity_history.py --no-push       # backfill DB only
    python scripts/backfill_equity_history.py --period all    # widen history window

Per-experiment Alpaca creds come from the registry's env_file (.env.exp*) or,
failing that, Railway env vars (ALPACA_API_KEY_EXP<SUFFIX> / SECRET). The
dashboard URL + key come from RAILWAY_SERVICE_ATTIX_DASHBOARD_URL +
DASHBOARD_API_KEY (or --dashboard-url / --token).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from shared.equity_backfill import backfill_equity_history, push_portfolio_snapshot  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_equity")

VOLUME_MOUNT = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").rstrip("/")


def _resolve_db(db_path: str) -> str:
    if VOLUME_MOUNT and db_path.startswith("data/"):
        return str(Path(VOLUME_MOUNT) / db_path[len("data/"):])
    return str(PROJECT_DIR / db_path) if not os.path.isabs(db_path) else db_path


def _load_env_file(rel: str) -> dict:
    out: dict = {}
    p = PROJECT_DIR / rel
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("'\"")
    return out


def _creds(exp: dict) -> tuple[str, str]:
    """Alpaca key/secret for an experiment: env_file first, then Railway env."""
    fv = _load_env_file(exp.get("env_file", "") or "")
    key = fv.get("ALPACA_API_KEY", "")
    secret = fv.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        suffix = exp["id"].replace("-", "").upper()  # EXP-3311 -> EXP3311
        key = key or os.environ.get(f"ALPACA_API_KEY_{suffix}", "")
        secret = secret or os.environ.get(f"ALPACA_API_SECRET_{suffix}", "")
    return key, secret


def _active_experiments() -> list[dict]:
    data = json.loads((PROJECT_DIR / "experiments" / "registry.json").read_text())
    exps = data if isinstance(data, list) else data.get("experiments", list(data.values()))
    if not isinstance(exps, list):
        exps = list(exps.values())
    return [e for e in exps if e.get("status") == "active"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", help="single experiment id (default: all active)")
    ap.add_argument("--period", default="1A", help="Alpaca history period (default 1A)")
    ap.add_argument("--no-push", action="store_true", help="backfill DB only, skip dashboard push")
    ap.add_argument("--dashboard-url", default=os.environ.get("RAILWAY_SERVICE_ATTIX_DASHBOARD_URL", "")
                    or "https://attix-production.up.railway.app")
    ap.add_argument("--token", default=os.environ.get("DASHBOARD_API_KEY", ""))
    args = ap.parse_args()

    experiments = _active_experiments()
    if args.exp:
        experiments = [e for e in experiments if e.get("id") == args.exp]
        if not experiments:
            log.error("Experiment %s not found / not active", args.exp)
            return 1

    total = 0
    for exp in experiments:
        eid = exp["id"]
        db = _resolve_db(exp.get("db_path", f"data/pilotai_{eid.replace('-', '_').lower()}.db"))
        key, secret = _creds(exp)
        if not key or not secret:
            log.warning("%s: no Alpaca creds (env_file=%s) — skipping", eid, exp.get("env_file"))
            continue
        n = backfill_equity_history(eid, db, key, secret, period=args.period)
        total += n
        log.info("%s: backfilled %d points -> %s", eid, n, db)
        if not args.no_push and args.token and args.dashboard_url:
            ok = push_portfolio_snapshot(eid, db, args.dashboard_url, args.token, key, secret)
            log.info("%s: dashboard push %s", eid, "ok" if ok else "skipped/failed")

    log.info("Done — %d experiment(s), %d total points.", len(experiments), total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
