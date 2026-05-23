#!/usr/bin/env python3
"""
Pre-flight key validator for pilotai-credit-spreads.

Reads .env.exp* files, hits Alpaca paper API, reports status.
Cross-references experiments/registry.json — only validates active/paused experiments.

Exit 0 = all active experiments OK
Exit 1 = at least one failure (401/403/timeout/connection error)
"""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from experiments.manager import get_manager  # noqa: E402

ALPACA_PAPER_URL = "https://paper-api.alpaca.markets/v2/account"
TIMEOUT_SECONDS = 10


def get_active_experiments(registry: dict) -> dict:
    """Return experiments with status active or paused (skip retired/completed/registered)."""
    valid_statuses = {"active", "paused"}
    result = {}
    for exp_id, exp in registry.items():
        status = exp.get("status", "")
        env_file = exp.get("env_file")
        if status in valid_statuses and env_file:
            result[exp_id] = exp
    return result


def parse_env_file(env_path: Path) -> dict:
    """Parse KEY=VALUE from .env file."""
    env = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def check_account(api_key: str, api_secret: str) -> dict:
    """Hit Alpaca paper account endpoint. Returns dict with status info."""
    req = Request(ALPACA_PAPER_URL, method="GET")
    req.add_header("APCA-API-KEY-ID", api_key)
    req.add_header("APCA-API-SECRET-KEY", api_secret)

    try:
        resp = urlopen(req, timeout=TIMEOUT_SECONDS)
        data = json.loads(resp.read().decode())
        return {
            "http_status": 200,
            "account_id": data.get("account_number", "?"),
            "equity": data.get("equity", "?"),
            "options_level": data.get("options_trading_level", data.get("options_level", "?")),
            "status": data.get("status", "?"),
            "ok": True,
        }
    except HTTPError as e:
        return {"http_status": e.code, "ok": False, "error": e.reason}
    except URLError as e:
        return {"http_status": 0, "ok": False, "error": f"connection: {e.reason}"}
    except Exception as e:
        return {"http_status": 0, "ok": False, "error": str(e)}


def main():
    registry = get_manager().all()
    active_exps = get_active_experiments(registry)

    if not active_exps:
        print("[validate_keys] No active/paused experiments found in registry.")
        return 0

    failures = []
    print(f"{'Experiment':<12} {'Account':<16} {'HTTP':<6} {'Equity':<12} {'Options':<8} {'Status'}")
    print("-" * 80)

    for exp_id, exp in sorted(active_exps.items()):
        env_file = exp["env_file"]
        env_path = PROJECT_DIR / env_file
        env = parse_env_file(env_path)

        api_key = env.get("ALPACA_API_KEY", "")
        api_secret = env.get("ALPACA_API_SECRET", "")

        if not api_key or not api_secret:
            print(f"{exp_id:<12} {'?':<16} {'—':<6} {'—':<12} {'—':<8} CRITICAL: no keys in {env_file}")
            failures.append(exp_id)
            continue

        result = check_account(api_key, api_secret)

        if result["ok"]:
            equity = f"${float(result['equity']):,.0f}" if result["equity"] != "?" else "?"
            print(f"{exp_id:<12} {result['account_id']:<16} {result['http_status']:<6} "
                  f"{equity:<12} {result['options_level']:<8} OK")
        else:
            status_str = f"CRITICAL: {result['http_status']} {result.get('error', '')}"
            print(f"{exp_id:<12} {'?':<16} {result['http_status']:<6} {'—':<12} {'—':<8} {status_str}")
            failures.append(exp_id)

    print("-" * 80)
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    else:
        print("ALL OK")
        return 0


if __name__ == "__main__":
    sys.exit(main())
