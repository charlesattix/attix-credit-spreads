#!/usr/bin/env python3
"""
sync_sentinel_data.py — Sentinel Dashboard Data Sync
=====================================================
Collects Sentinel data from sentinel_state.json, experiment DBs, and
sentinel.db, then pushes a single JSON payload to Railway.

Usage:
    # Export locally only
    python scripts/sync_sentinel_data.py

    # Export + push to Railway
    python scripts/sync_sentinel_data.py --push

    # Dry run (print JSON, no writes)
    python scripts/sync_sentinel_data.py --dry-run

Environment variables (read from .env.sync if present):
    RAILWAY_URL          — Railway app base URL
    RAILWAY_ADMIN_TOKEN  — API key (same as DASHBOARD_API_KEY on Railway)
"""

import argparse
import json
import os
import sqlite3
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STATE_PATH = PROJECT_ROOT / "sentinel_state.json"
SENTINEL_DB_PATH = PROJECT_ROOT / "sentinel" / "db" / "sentinel.db"
REGISTRY_PATH = PROJECT_ROOT / "experiments" / "registry.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "sentinel_dashboard.json"

# ---------------------------------------------------------------------------
# Load .env.sync for Railway credentials
# ---------------------------------------------------------------------------

_ENV_FILE = PROJECT_ROOT / ".env.sync"
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _load_sentinel_state() -> dict:
    """Load sentinel_state.json."""
    if not STATE_PATH.exists():
        return {}
    with open(STATE_PATH) as f:
        return json.load(f)


def _load_registry() -> dict:
    """Load experiments/registry.json."""
    if not REGISTRY_PATH.exists():
        return {}
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def _resolve_db_path(exp_id: str, state: dict, registry: dict) -> Optional[Path]:
    """Resolve the trades DB path for an experiment."""
    # Try sentinel_state → paper_config → db_path
    exp_state = state.get("experiments", {}).get(exp_id, {})
    paper_config = exp_state.get("paper_config")
    if paper_config:
        cfg_path = PROJECT_ROOT / paper_config
        if cfg_path.exists():
            try:
                import yaml
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f)
                db_path = cfg.get("db_path")
                if db_path:
                    resolved = PROJECT_ROOT / db_path
                    if resolved.exists():
                        return resolved
            except Exception:
                pass

    # Fallback: try registry
    reg_exp = registry.get("experiments", {}).get(exp_id, {})
    db_path = reg_exp.get("db_path")
    if db_path:
        resolved = PROJECT_ROOT / db_path
        if resolved.exists():
            return resolved

    # Fallback: common naming patterns
    for pattern in [
        f"data/pilotai_{exp_id.lower().replace('-', '')}.db",
        f"data/{exp_id.lower().replace('-', '_')}.db",
    ]:
        p = PROJECT_ROOT / pattern
        if p.exists():
            return p

    return None


def _collect_trade_metrics(db_path: Path, window: int = 30) -> dict:
    """Compute rolling-window trade metrics from an experiment DB."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Total trades
        total_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM trades WHERE status LIKE 'closed%'"
        ).fetchone()
        total_closed = total_row["cnt"] if total_row else 0

        # Open trades
        open_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM trades WHERE status = 'open'"
        ).fetchone()
        total_open = open_row["cnt"] if open_row else 0

        if total_closed == 0:
            return {
                "total_trades": 0, "total_open": total_open,
                "win_rate": None, "avg_pnl": None, "total_pnl": 0,
            }

        # Rolling window
        rows = conn.execute(
            "SELECT pnl FROM trades WHERE status LIKE 'closed%' AND pnl IS NOT NULL "
            "ORDER BY exit_date DESC LIMIT ?",
            (window,),
        ).fetchall()

        pnls = [float(r["pnl"]) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        # Total PnL (all trades, not just window)
        total_pnl_row = conn.execute(
            "SELECT SUM(pnl) AS total FROM trades WHERE status LIKE 'closed%' AND pnl IS NOT NULL"
        ).fetchone()
        total_pnl = float(total_pnl_row["total"]) if total_pnl_row and total_pnl_row["total"] else 0.0

        # Peak equity from scanner_state
        peak_equity = None
        try:
            peak_row = conn.execute(
                "SELECT value FROM scanner_state WHERE key = 'peak_equity'"
            ).fetchone()
            if peak_row:
                peak_equity = float(peak_row["value"])
        except Exception:
            pass

        return {
            "total_trades": total_closed,
            "total_open": total_open,
            "window_size": len(pnls),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
            "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else None,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(abs(p) for p in losses) / len(losses), 2) if losses else None,
            "wins": len(wins),
            "losses": len(losses),
            "total_pnl": round(total_pnl, 2),
            "peak_equity": peak_equity,
        }
    finally:
        conn.close()


def _collect_gate9_lifecycle(db_path: Path) -> dict:
    """Check for stuck positions in experiment DB."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    try:
        rows = conn.execute("""
            SELECT id, status, ticker, short_strike, expiration,
                   created_at, updated_at
            FROM trades
            WHERE status NOT LIKE 'closed%%' AND status != 'failed_open'
            ORDER BY updated_at ASC
        """).fetchall()

        total_open = 0
        total_pending = 0
        stuck: List[dict] = []

        for row in rows:
            status = row["status"]
            if status == "open":
                total_open += 1
            elif status in ("pending_open", "pending_close", "needs_investigation"):
                total_pending += 1

            ref_ts = row["updated_at"] or row["created_at"]
            if not ref_ts:
                continue

            try:
                dt = datetime.fromisoformat(ref_ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                elapsed_min = (now - dt).total_seconds() / 60.0
            except (ValueError, TypeError):
                continue

            # Thresholds
            thresholds = {
                "pending_open": {"warning": 30, "critical": 120},
                "pending_close": {"warning": 30, "critical": 120},
                "needs_investigation": {"warning": 0, "critical": 240},
            }

            if status in thresholds:
                t = thresholds[status]
                if t["critical"] is not None and elapsed_min >= t["critical"]:
                    severity = "critical"
                elif elapsed_min >= t["warning"]:
                    severity = "warning"
                else:
                    continue

                stuck.append({
                    "trade_id": str(row["id"])[:24],
                    "ticker": row["ticker"] or "?",
                    "status": status,
                    "minutes": round(elapsed_min, 1),
                    "severity": severity,
                    "short_strike": row["short_strike"],
                    "expiration": row["expiration"],
                })
            elif status == "open" and elapsed_min >= 1440:  # 24h
                stuck.append({
                    "trade_id": str(row["id"])[:24],
                    "ticker": row["ticker"] or "?",
                    "status": "open_no_management",
                    "minutes": round(elapsed_min, 1),
                    "severity": "warning",
                    "short_strike": row["short_strike"],
                    "expiration": row["expiration"],
                })

        return {
            "passed": len(stuck) == 0,
            "stuck": stuck,
            "total_open": total_open,
            "total_pending": total_pending,
        }
    finally:
        conn.close()


def _collect_gate7_orphans(db_path: Path) -> dict:
    """Check orphan scan count from scanner_state."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        orphan_count = 0
        try:
            row = conn.execute(
                "SELECT value FROM scanner_state WHERE key = 'sentinel_orphan_counts'"
            ).fetchone()
            if row:
                orphan_count = int(row["value"])
        except Exception:
            pass

        # Count unmanaged trades (orphan placeholders)
        unmanaged = conn.execute(
            "SELECT COUNT(*) AS cnt FROM trades WHERE status = 'unmanaged'"
        ).fetchone()
        orphan_positions = unmanaged["cnt"] if unmanaged else 0

        # Count needs_investigation (ghost detections)
        ghosts = conn.execute(
            "SELECT COUNT(*) AS cnt FROM trades WHERE status = 'needs_investigation'"
        ).fetchone()
        ghost_count = ghosts["cnt"] if ghosts else 0

        return {
            "passed": orphan_positions == 0 and ghost_count == 0,
            "orphans": orphan_positions,
            "ghosts": ghost_count,
            "consecutive_scans": orphan_count,
        }
    finally:
        conn.close()


def _collect_alerts_from_sentinel_db() -> List[dict]:
    """Read recent alerts from sentinel.db."""
    if not SENTINEL_DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(SENTINEL_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT severity, message, experiment_id, created_at, resolved_at "
            "FROM alerts_log ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return [
            {
                "time": r["created_at"],
                "severity": r["severity"],
                "exp_id": r["experiment_id"],
                "message": r["message"],
                "resolved": r["resolved_at"] is not None,
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        conn.close()


def _find_env_file(exp_id: str) -> Optional[Path]:
    """Locate the .env file for an experiment (same logic as sentinel/monitor.py)."""
    numeric = exp_id.removeprefix("EXP-").lower()
    candidates = [PROJECT_ROOT / f".env.exp{numeric}"]
    if exp_id == "EXP-400":
        candidates.append(PROJECT_ROOT / ".env.champion")
    for c in candidates:
        if c.exists():
            return c
    return None


def _load_env_file(path: Path) -> Dict[str, str]:
    """Parse a .env file into a plain dict."""
    env: Dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def _fetch_alpaca_account_data(exp_id: str) -> Optional[dict]:
    """
    Fetch account equity and open position market values from Alpaca.

    Returns dict with:
      - alpaca_equity: total account equity (mark-to-market)
      - alpaca_cash: cash balance
      - unrealized_pnl: sum of unrealized P&L across all open positions
      - open_position_count: number of open positions
      - positions: list of {symbol, qty, market_value, unrealized_pl}
    Returns None if credentials missing or API unreachable.
    """
    env_file = _find_env_file(exp_id)
    if not env_file:
        return None

    env = _load_env_file(env_file)
    api_key = env.get("ALPACA_API_KEY")
    api_secret = env.get("ALPACA_API_SECRET") or env.get("ALPACA_SECRET_KEY")
    if not api_key or not api_secret:
        return None

    try:
        from alpaca.trading.client import TradingClient

        paper = env.get("ALPACA_PAPER", "true").lower() != "false"
        client = TradingClient(api_key, api_secret, paper=paper)

        acct = client.get_account()
        positions = client.get_all_positions()

        unrealized_pnl = sum(float(p.unrealized_pl) for p in positions)

        pos_details = []
        for p in positions:
            pos_details.append({
                "symbol": p.symbol,
                "qty": str(p.qty),
                "market_value": round(float(p.market_value), 2),
                "unrealized_pl": round(float(p.unrealized_pl), 2),
            })

        return {
            "alpaca_equity": round(float(acct.equity), 2),
            "alpaca_cash": round(float(acct.cash), 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "open_position_count": len(positions),
            "positions": pos_details,
        }
    except Exception as e:
        logger.warning("Failed to fetch Alpaca data for %s: %s", exp_id, e)
        return None


def _reconcile_pnl(
    realized_pnl: float,
    unrealized_pnl: float,
    alpaca_equity: float,
    initial_deposit: float,
) -> dict:
    """
    Compare our computed total P&L against Alpaca's equity.

    Returns reconciliation result with warning if divergence > $100.
    """
    computed_total = realized_pnl + unrealized_pnl
    alpaca_implied_pnl = alpaca_equity - initial_deposit
    divergence = computed_total - alpaca_implied_pnl

    return {
        "computed_total_pnl": round(computed_total, 2),
        "alpaca_implied_pnl": round(alpaca_implied_pnl, 2),
        "divergence": round(divergence, 2),
        "reconciled": abs(divergence) < 100.0,
        "initial_deposit": initial_deposit,
    }


def _collect_config_integrity(
    experiments: Dict[str, Any],
    experiments_state: dict,
    registry: dict,
) -> List[dict]:
    """Collect Gates 1-5 config integrity checks."""
    checks: List[dict] = []

    # Gate 1: Config Schema — verify each experiment's YAML is loadable
    schema_ok = True
    schema_detail_parts: List[str] = []
    for exp_id in sorted(experiments):
        exp_s = experiments_state.get(exp_id, {})
        paper_config = exp_s.get("paper_config")
        if paper_config:
            cfg_path = PROJECT_ROOT / paper_config
            if cfg_path.exists():
                try:
                    import yaml
                    with open(cfg_path) as f:
                        yaml.safe_load(f)
                except Exception:
                    schema_ok = False
                    schema_detail_parts.append(f"{exp_id}: invalid YAML")
            else:
                schema_ok = False
                schema_detail_parts.append(f"{exp_id}: file missing")
    checks.append({
        "check": "Config Schema",
        "status": "pass" if schema_ok else "fail",
        "detail": f"All {len(experiments)} experiment YAMLs valid" if schema_ok else "; ".join(schema_detail_parts),
    })

    # Gate 2: Registry Integrity — verify registry matches sentinel_state
    reg_exps = set(registry.get("experiments", {}).keys())
    state_exps = set(experiments_state.keys())
    active_reg = {k for k, v in registry.get("experiments", {}).items() if v.get("status") == "paper_trading"}
    missing_from_state = active_reg - state_exps
    registry_ok = len(missing_from_state) == 0
    checks.append({
        "check": "Registry Integrity",
        "status": "pass" if registry_ok else "warning",
        "detail": "Registry matches sentinel_state" if registry_ok else f"Missing from state: {', '.join(sorted(missing_from_state))}",
    })

    # Gate 3: Config Drift — fingerprint comparison
    drift_ok = True
    drift_details: List[str] = []
    for exp_id in sorted(experiments):
        exp_s = experiments_state.get(exp_id, {})
        stored_fp = exp_s.get("config_fingerprint")
        paper_config = exp_s.get("paper_config")
        if stored_fp and paper_config:
            try:
                from sentinel.state import compute_fingerprint
                current_fp = compute_fingerprint(paper_config)
                if current_fp != stored_fp:
                    drift_ok = False
                    drift_details.append(exp_id)
                    experiments[exp_id]["fingerprint_ok"] = False
            except Exception:
                drift_details.append(f"{exp_id} (error)")
        elif not stored_fp:
            pass  # Not enrolled, handled elsewhere
    checks.append({
        "check": "Config Drift",
        "status": "pass" if drift_ok else "fail",
        "detail": "No changes since certification" if drift_ok else f"Drift: {', '.join(drift_details)}",
    })

    # Gate 4: DB Schema — verify each experiment DB has expected tables
    db_ok = True
    db_count = 0
    expected_tables = {"trades", "scanner_state"}
    for exp_id in sorted(experiments):
        db_path = _resolve_db_path(exp_id, {"experiments": experiments_state}, registry)
        if db_path:
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                conn.close()
                if expected_tables.issubset(tables):
                    db_count += 1
                else:
                    db_ok = False
            except Exception:
                db_ok = False
    checks.append({
        "check": "DB Schema",
        "status": "pass" if db_ok else "warning",
        "detail": f"{db_count} databases verified" if db_ok else f"{db_count}/{len(experiments)} DBs valid",
    })

    # Gate 5: Certification — check sentinel_state for certified_at
    certified = sum(1 for e in experiments_state.values() if e.get("sentinel_certified_at"))
    total = len(experiments)
    checks.append({
        "check": "Certification",
        "status": "pass" if certified >= total else "warning",
        "detail": f"{certified}/{total} experiments certified" if certified < total else f"{total}/{total} experiments certified",
    })

    return checks


def collect_sentinel_data() -> dict:
    """Collect all Sentinel data into a single JSON payload."""
    state = _load_sentinel_state()
    registry = _load_registry()
    experiments_state = state.get("experiments", {})

    # Active experiment IDs from registry
    active_ids = [
        k for k, v in registry.get("experiments", {}).items()
        if v.get("status") == "paper_trading"
    ]

    experiments: Dict[str, Any] = {}

    for exp_id in sorted(active_ids):
        exp_state = experiments_state.get(exp_id, {})
        reg_entry = registry.get("experiments", {}).get(exp_id, {})

        # Resolve DB
        db_path = _resolve_db_path(exp_id, state, registry)

        exp_data: Dict[str, Any] = {
            "status": exp_state.get("status", "active"),
            "name": reg_entry.get("name", exp_id),
            "config_fingerprint": exp_state.get("config_fingerprint"),
            "fingerprint_ok": True,  # Default; set to False if drift detected
            "paper_config": exp_state.get("paper_config"),
        }

        # Backtest baseline
        baseline = exp_state.get("backtest_baseline")
        if baseline:
            exp_data["baseline"] = baseline

        # Alpaca account data (unrealized P&L + equity)
        alpaca = _fetch_alpaca_account_data(exp_id)
        if alpaca:
            exp_data["alpaca"] = alpaca

        # Trade metrics
        if db_path:
            exp_data["metrics"] = _collect_trade_metrics(db_path)

            # Inject Alpaca-derived fields into metrics for dashboard consumption
            realized_pnl = exp_data["metrics"].get("total_pnl", 0) or 0
            if alpaca:
                unrealized = alpaca["unrealized_pnl"]
                exp_data["metrics"]["realized_pnl"] = round(realized_pnl, 2)
                exp_data["metrics"]["unrealized_pnl"] = round(unrealized, 2)
                exp_data["metrics"]["total_pnl"] = round(realized_pnl + unrealized, 2)
                exp_data["metrics"]["alpaca_equity"] = alpaca["alpaca_equity"]

                # Reconciliation check
                initial_deposit = reg_entry.get("initial_deposit", 100_000.0)
                recon = _reconcile_pnl(
                    realized_pnl, unrealized, alpaca["alpaca_equity"], initial_deposit,
                )
                exp_data["reconciliation"] = recon
            else:
                # No Alpaca data — keep realized-only, mark unrealized as unavailable
                exp_data["metrics"]["realized_pnl"] = round(realized_pnl, 2)
                exp_data["metrics"]["unrealized_pnl"] = None

            # Gate 8 drift comparison
            if baseline and exp_data.get("metrics", {}).get("win_rate") is not None:
                metrics = exp_data["metrics"]
                bl_wr = baseline.get("win_rate", 0)
                live_wr = metrics.get("win_rate", 0)
                wr_delta = bl_wr - live_wr if live_wr is not None else 0

                # Both avg_loss values are positive (abs of losses). Guard with abs()
                # to ensure consistent sign convention regardless of source.
                bl_al = abs(baseline.get("avg_loss") or 0)
                live_al = abs(metrics.get("avg_loss") or 0)
                al_ratio = (live_al / bl_al) if bl_al and live_al else 0

                drift_alerts = []
                # Win rate drift
                if wr_delta >= 20:
                    drift_alerts.append({"metric": "win_rate", "severity": "halt", "delta": round(-wr_delta, 1)})
                elif wr_delta >= 15:
                    drift_alerts.append({"metric": "win_rate", "severity": "critical", "delta": round(-wr_delta, 1)})
                elif wr_delta >= 10:
                    drift_alerts.append({"metric": "win_rate", "severity": "warning", "delta": round(-wr_delta, 1)})

                # Avg loss drift
                if al_ratio >= 3.0:
                    drift_alerts.append({"metric": "avg_loss", "severity": "halt", "ratio": round(al_ratio, 1)})
                elif al_ratio >= 2.0:
                    drift_alerts.append({"metric": "avg_loss", "severity": "critical", "ratio": round(al_ratio, 1)})
                elif al_ratio >= 1.5:
                    drift_alerts.append({"metric": "avg_loss", "severity": "warning", "ratio": round(al_ratio, 1)})

                exp_data["gates"] = {
                    "gate8_drift": {
                        "passed": len(drift_alerts) == 0,
                        "metrics": {
                            "win_rate": live_wr,
                            "avg_loss": live_al,
                            "peak_dd_pct": metrics.get("peak_equity"),
                        },
                        "baseline": baseline,
                        "alerts": drift_alerts,
                    },
                }
            else:
                exp_data.setdefault("gates", {})

            # Gate 7
            exp_data["gates"]["gate7_orphans"] = _collect_gate7_orphans(db_path)

            # Gate 9
            exp_data["gates"]["gate9_lifecycle"] = _collect_gate9_lifecycle(db_path)
        else:
            exp_data["metrics"] = {"error": "DB not found"}
            exp_data["gates"] = {}

        experiments[exp_id] = exp_data

    # Config integrity (Gates 1-5): per-gate checks
    config_integrity = _collect_config_integrity(experiments, experiments_state, registry)

    # Alerts from sentinel.db
    alerts = _collect_alerts_from_sentinel_db()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sentinel_version": "2.0",
        "experiment_count": len(experiments),
        "experiments": experiments,
        "config_integrity": config_integrity,
        "alerts": alerts,
    }

    return payload


# ---------------------------------------------------------------------------
# Railway push
# ---------------------------------------------------------------------------

def push_to_railway(payload: dict, railway_url: str, token: str) -> bool:
    """POST the sentinel JSON to Railway's /api/admin/push-sentinel endpoint."""
    import urllib.request
    import urllib.error

    url = railway_url.rstrip("/") + "/api/admin/push-sentinel"
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": token,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            print(f"[sentinel-sync] Push OK: {result.get('pushed_at', 'unknown')}")
            return True
    except urllib.error.HTTPError as e:
        print(f"[sentinel-sync] Push FAILED: HTTP {e.code}", file=sys.stderr)
        try:
            print(f"  Response: {e.read().decode()}", file=sys.stderr)
        except Exception:
            pass
        return False
    except Exception as e:
        print(f"[sentinel-sync] Push FAILED: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Sentinel data to Railway dashboard")
    parser.add_argument("--push", action="store_true", help="Push to Railway after export")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON, no writes")
    parser.add_argument("--railway-url", default=os.environ.get("RAILWAY_URL"))
    parser.add_argument("--token", default=os.environ.get("RAILWAY_ADMIN_TOKEN"))
    args = parser.parse_args()

    print(f"[sentinel-sync] Collecting Sentinel data...")
    payload = collect_sentinel_data()
    print(f"[sentinel-sync] {payload['experiment_count']} experiments, "
          f"{len(payload.get('alerts', []))} alerts")

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    # Write locally
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"[sentinel-sync] Written to {OUTPUT_PATH}")

    # Push to Railway
    if args.push:
        railway_url = args.railway_url
        token = args.token
        if not railway_url or not token:
            print("[sentinel-sync] ERROR: --railway-url and --token required for --push",
                  file=sys.stderr)
            return 1
        ok = push_to_railway(payload, railway_url, token)
        if not ok:
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
