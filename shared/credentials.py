"""
credentials.py — Bulletproof credential and portfolio discovery helper.

CRITICAL: env var names vs Alpaca HTTP header names are DIFFERENT:
  .env files store:  ALPACA_API_KEY  and  ALPACA_API_SECRET
  Alpaca HTTP uses:  APCA-API-KEY-ID and  APCA-API-SECRET-KEY

Never report keys as missing without first trying get_all_portfolios() or
check_portfolio(). Keys are always in .env.exp<NNN> files, not exported
to the shell environment.

Usage:
    from shared.credentials import get_all_portfolios, check_portfolio, portfolio_summary

    # Check all accounts
    print(portfolio_summary())

    # Check one account
    result = check_portfolio(".env.exp036")
    if result["ok"]:
        print(result["equity"])
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
ALPACA_BASE = "https://paper-api.alpaca.markets"
POLYGON_BASE = "https://api.polygon.io"
_TIMEOUT = 15  # seconds

# Authoritative experiment → paper account ID mapping
KNOWN_ACCOUNTS: Dict[str, str] = {
    "exp036": "PA3D6UPXF5F2",
    "exp059": "PA3LP867WNGU",
    "exp154": "PA3UNOV58WGK",
    "exp305": "PA3W9FZKK6XD",
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_env_file(env_path: Path) -> Dict[str, str]:
    """Parse KEY=value pairs from a .env file. Handles comments and quotes."""
    result: Dict[str, str] = {}
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                val = val[1:-1]
            result[key] = val
    except OSError:
        pass
    return result


def _exp_name_from_path(env_path: Path) -> str:
    """Extract experiment name from env file path: .env.exp036 → exp036"""
    m = re.search(r'\.env\.(.+)$', env_path.name)
    return m.group(1) if m else env_path.name


def _alpaca_get(endpoint: str, api_key: str, api_secret: str):
    """GET from Alpaca paper API. Returns parsed JSON or None on failure."""
    url = f"{ALPACA_BASE}{endpoint}"
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID":     api_key,    # Alpaca header (not the env var name)
        "APCA-API-SECRET-KEY": api_secret,
        "Accept":              "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.warning("Alpaca %s failed: %s", url, exc)  # SECURITY AUDIT #14
        return None


def _polygon_valid(api_key: str) -> bool:
    """Return True if Polygon API key is valid."""
    url = (f"{POLYGON_BASE}/v2/aggs/ticker/SPY/range/1/day"
           f"/2024-01-02/2024-01-02?apiKey={api_key}")
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return int(data.get("resultsCount", 0)) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all_portfolios() -> List[Dict]:
    """Discover all experiment portfolios from .env.exp* files.

    Returns a list of dicts, one per experiment:
        env_file      Path  — .env.exp036 etc.
        experiment    str   — "exp036"
        account_id    str   — Alpaca paper account ID
        alpaca_key    str   — ALPACA_API_KEY value
        alpaca_secret str   — ALPACA_API_SECRET value
        polygon_key   str   — POLYGON_API_KEY value
        db_path       Path  — data/pilotai_<exp>.db
    """
    portfolios = []
    for env_file in sorted(PROJECT_DIR.glob(".env.exp*")):
        env = _parse_env_file(env_file)
        exp = _exp_name_from_path(env_file)
        portfolios.append({
            "env_file":      env_file,
            "experiment":    exp,
            "account_id":    KNOWN_ACCOUNTS.get(exp, "unknown"),
            "alpaca_key":    env.get("ALPACA_API_KEY", ""),
            "alpaca_secret": env.get("ALPACA_API_SECRET", ""),
            "polygon_key":   env.get("POLYGON_API_KEY", ""),
            "db_path":       PROJECT_DIR / "data" / f"pilotai_{exp}.db",
        })
    return portfolios


def check_portfolio(env_file) -> Dict:
    """Validate Alpaca connectivity for one .env file.

    Returns dict with keys:
        ok            bool
        experiment    str
        account_id    str
        equity        float
        cash          float
        buying_power  float
        unrealized_pl float
        status        str
        positions     list
        error         str | None
    """
    env_path = Path(env_file)
    env = _parse_env_file(env_path)
    exp = _exp_name_from_path(env_path)
    api_key    = env.get("ALPACA_API_KEY", "")
    api_secret = env.get("ALPACA_API_SECRET", "")

    if not api_key or not api_secret:
        return {"ok": False, "experiment": exp,
                "error": f"ALPACA_API_KEY or ALPACA_API_SECRET not found in {env_path.name}"}

    acct = _alpaca_get("/v2/account", api_key, api_secret)
    if acct is None:
        return {"ok": False, "experiment": exp,
                "error": f"Alpaca API unreachable (key={api_key[:8]}...)"}

    positions = _alpaca_get("/v2/positions", api_key, api_secret) or []
    return {
        "ok":            True,
        "experiment":    exp,
        "account_id":    acct.get("account_number", KNOWN_ACCOUNTS.get(exp, "?")),
        "equity":        float(acct.get("equity", 0) or 0),
        "cash":          float(acct.get("cash", 0) or 0),
        "buying_power":  float(acct.get("buying_power", 0) or 0),
        "unrealized_pl": float(acct.get("unrealized_pl", 0) or 0),
        "status":        acct.get("status", "?"),
        "positions":     positions,
        "error":         None,
    }


def portfolio_summary(verbose: bool = True) -> str:
    """Return a formatted status string for all discovered accounts."""
    portfolios = get_all_portfolios()
    if not portfolios:
        return "No .env.exp* files found — check project root"

    lines = [f"Attix Portfolio Summary ({len(portfolios)} experiments)", "=" * 58]
    all_ok = True

    for p in portfolios:
        r = check_portfolio(p["env_file"])
        if not r["ok"]:
            all_ok = False
            lines.append(f"\n[FAIL] {p['experiment']}: {r['error']}")
            continue

        sign = "+" if r["unrealized_pl"] >= 0 else ""
        lines.append(f"\n[OK]  {r['experiment']}  account={r['account_id']}  status={r['status']}")
        lines.append(f"      Equity:         ${r['equity']:>12,.2f}")
        lines.append(f"      Cash:           ${r['cash']:>12,.2f}")
        lines.append(f"      Buying Power:   ${r['buying_power']:>12,.2f}")
        lines.append(f"      Unrealized P&L: {sign}${r['unrealized_pl']:>10,.2f}")

        pos = r.get("positions", [])
        if not pos:
            lines.append("      Positions:  none")
        elif verbose:
            lines.append(f"      Positions ({len(pos)}):")
            for p2 in pos:
                sym  = p2.get("symbol", "?")
                side = p2.get("side", "?")
                qty  = p2.get("qty", "?")
                mv   = float(p2.get("market_value", 0) or 0)
                upl  = float(p2.get("unrealized_pl", 0) or 0)
                s    = "+" if upl >= 0 else ""
                lines.append(f"        {sym:<28} {side:<5} qty={qty:<5} mv=${mv:>10,.2f}  uPnL={s}${upl:>8,.2f}")
        else:
            lines.append(f"      Positions: {len(pos)} open")

    lines += ["", "=" * 58, "ALL OK" if all_ok else "SOME ACCOUNTS FAILED"]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(portfolio_summary())
    failed = sum(1 for p in get_all_portfolios() if not check_portfolio(p["env_file"])["ok"])
    sys.exit(1 if failed else 0)
