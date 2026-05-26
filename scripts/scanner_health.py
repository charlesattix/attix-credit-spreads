#!/usr/bin/env python3
"""scanner_health.py — deterministic liveness probe for every Attix scanner.

Purpose
-------
The morning briefing has historically been blind to scanner crashes: it queries
Alpaca directly and reports equity, while scanners can be dead, in a launchd
restart loop, or running but unable to reach Alpaca. This script provides the
*scanner-side* truth, separate from broker-side equity numbers.

Source of truth
---------------
The set of *expected* scanners is derived from ``~/Library/LaunchAgents/
com.attix.*.plist`` — the launchd configuration is what actually gets
started on boot. ``experiments.yaml`` drifts and is NOT consulted.

Per-scanner probes
------------------
For each ``com.attix.expNNN`` label we report:

  launchctl       PID + last exit code from ``launchctl list``
  process_alive   Whether the PID is actually running right now
  log_age_sec     Seconds since the scanner's log file was last written
  log_stale       True if ``log_age_sec > 1800`` during market hours
  log_tail_errors Number of ERROR/CRITICAL lines in the last 50 log lines
  circuit_breaker Last circuit-breaker state mentioned in the log tail
  alpaca_auth     200 / 401 / 403 / network-error from a /v2/account roundtrip
  position_count  Number of open positions reported by /v2/positions
  equity          Cash equity (only when /v2/account returns 200)

Cross-cutting P0 rules
----------------------
- ``BROKER HAS POSITIONS BUT SCANNER IS DEAD``
- ``LAUNCHCTL LOADED BUT PROCESS NOT RUNNING``
- ``LAUNCHCTL EXIT CODE NON-ZERO`` (crash loop indicator)
- ``LOG STALE DURING MARKET HOURS``
- ``ALPACA AUTH FAILED`` (401/403)

Usage
-----
    python3 scripts/scanner_health.py --format=html
    python3 scripts/scanner_health.py --format=json
    python3 scripts/scanner_health.py --format=text

The HTML output is suitable for direct paste into the Telegram morning briefing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import plistlib
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LAUNCHAGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
# Patterns we recognise as "attix-managed daemons":
#   com.attix.*    — paper-trading scanners (one per experiment)
#   com.attix.sentinel — sentinel watchdog (different prefix, same monitoring need)
# Each pattern is fed into Path.glob; results are deduplicated by stem.
PLIST_GLOBS = ("com.attix.*.plist", "com.attix.sentinel.plist")

# Beyond this, the log is considered stale during market hours.
LOG_STALE_SECONDS = 30 * 60

# Tail this many lines of each log for error / circuit-breaker detection.
LOG_TAIL_LINES = 50

# Alpaca HTTP timeout (per request).
ALPACA_TIMEOUT_SEC = 5

# US/Eastern market hours window. We use a coarse check that does NOT need a
# market-calendar dependency — pre-market / weekends still report metrics, but
# `log_stale` only flips True between these wall-clock bounds on weekdays.
MARKET_OPEN_HHMM = (9, 30)
MARKET_CLOSE_HHMM = (16, 0)


# ---------------------------------------------------------------------------
# Helpers — environment / launchd
# ---------------------------------------------------------------------------


def _is_market_hours(now: dt.datetime) -> bool:
    """Return True if ``now`` is on a US weekday between 9:30 and 16:00 ET.

    No holiday-calendar dependency on purpose — slightly noisy on holidays is
    preferable to a missed crash on a normal trading day.
    """
    # Convert UTC to ET (offset is approximate — DST shifts by 1h; close enough
    # for a coarse "is it market hours" check, since LOG_STALE_SECONDS is 30m).
    et = now - dt.timedelta(hours=4)  # EDT; in winter this is off by 1h
    if et.weekday() >= 5:
        return False
    hhmm = (et.hour, et.minute)
    return MARKET_OPEN_HHMM <= hhmm <= MARKET_CLOSE_HHMM


def _parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a dotenv-style file. Ignores blanks and comments."""
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return out


def _launchctl_print(label: str) -> Dict[str, Any]:
    """Run ``launchctl list <label>`` and return parsed key/value pairs.

    Returns an empty dict when launchctl reports the label is unknown.
    """
    try:
        proc = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}

    info: Dict[str, Any] = {"_raw": proc.stdout}
    # launchctl list <label> emits a plist-ish text format like:
    #   {
    #       "Label" = "com.foo";
    #       "PID" = 12345;
    #       "LastExitStatus" = 0;
    #   };
    for line in proc.stdout.splitlines():
        line = line.strip().rstrip(";")
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().strip('"')
        val = val.strip()
        if val.endswith('"') and val.startswith('"'):
            val = val[1:-1]
        if val.isdigit():
            info[key] = int(val)
        else:
            info[key] = val
    return info


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ---------------------------------------------------------------------------
# Helpers — plist parsing
# ---------------------------------------------------------------------------


def _read_plist(path: Path) -> Dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException):
        return {}


def _extract_env_file_arg(args: List[str]) -> Optional[str]:
    """Pull out the value following ``--env-file`` in ProgramArguments."""
    for i, a in enumerate(args[:-1]):
        if a == "--env-file":
            return args[i + 1]
    return None


# ---------------------------------------------------------------------------
# Helpers — log inspection
# ---------------------------------------------------------------------------


def _log_age_seconds(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    return max(0.0, dt.datetime.now().timestamp() - path.stat().st_mtime)


def _read_log_tail(path: Path, n: int = LOG_TAIL_LINES) -> List[str]:
    if not path.exists():
        return []
    try:
        # cheap-and-cheerful: read whole file if small, else last 64KB
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > 65536:
                fh.seek(-65536, os.SEEK_END)
            chunk = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = chunk.splitlines()
    return lines[-n:] if len(lines) > n else lines


def _count_log_errors(tail: List[str]) -> int:
    return sum(
        1 for line in tail
        if " ERROR " in line or " CRITICAL " in line or "Traceback" in line
    )


def _last_circuit_breaker_state(tail: List[str]) -> Optional[str]:
    """Find the most recent circuit-breaker state mentioned in the log tail.

    Returns one of ``NORMAL``, ``WARN``, ``HALTED``, or None if not seen.
    """
    target_states = ("HALTED", "WARN", "NORMAL")
    # Scan tail in reverse so we pick up the *most recent* state.
    for line in reversed(tail):
        upper = line.upper()
        if "CIRCUIT" not in upper:
            continue
        for state in target_states:
            if state in upper:
                return state
    return None


# ---------------------------------------------------------------------------
# Helpers — Alpaca
# ---------------------------------------------------------------------------


def _alpaca_request(env: Dict[str, str], path: str) -> Dict[str, Any]:
    """Make a GET to ``<ALPACA_BASE_URL>/<path>`` using creds from ``env``.

    Returns ``{"status": int, "body": dict|str, "error": str|None}``.
    """
    key = env.get("ALPACA_API_KEY")
    secret = env.get("ALPACA_API_SECRET")
    base = env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
    if not key or not secret:
        return {"status": None, "body": None, "error": "missing_creds"}

    url = f"{base}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    })
    try:
        with urllib.request.urlopen(req, timeout=ALPACA_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
            return {"status": resp.status, "body": body, "error": None}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": None, "error": f"http_{e.code}"}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"status": None, "body": None, "error": f"network:{type(e).__name__}"}


# ---------------------------------------------------------------------------
# Per-scanner probe
# ---------------------------------------------------------------------------


def probe(plist_path: Path, project_root: Path) -> Dict[str, Any]:
    """Run every check against one scanner's plist and return a result dict."""
    plist = _read_plist(plist_path)
    label = plist.get("Label") or plist_path.stem
    exp_id = label.replace("com.attix.", "").replace("com.attix.", "")

    # Determine the lifecycle mode of this plist:
    #   keepalive    → process should always be running (KeepAlive=true)
    #   periodic     → process runs every StartInterval seconds and exits
    #   on_demand    → run-at-load only, no schedule
    # The "process not running" P0 alert only applies in keepalive mode.
    keep_alive_raw = plist.get("KeepAlive")
    if keep_alive_raw is True or isinstance(keep_alive_raw, dict):
        lifecycle = "keepalive"
    elif plist.get("StartInterval"):
        lifecycle = "periodic"
    else:
        lifecycle = "on_demand"

    args: List[str] = plist.get("ProgramArguments") or []
    env_file_rel = _extract_env_file_arg(args)
    env_path = (project_root / env_file_rel) if env_file_rel else None
    env = _parse_env_file(env_path) if env_path else {}

    log_path_str = plist.get("StandardOutPath") or plist.get("StandardErrorPath")
    log_path = Path(log_path_str).expanduser() if log_path_str else None
    log_age = _log_age_seconds(log_path) if log_path else None
    log_tail = _read_log_tail(log_path) if log_path else []

    launchctl = _launchctl_print(label)
    pid = launchctl.get("PID")
    last_exit = launchctl.get("LastExitStatus")
    alive = _pid_alive(pid) if pid else False

    now = dt.datetime.utcnow()
    market_hours = _is_market_hours(now)
    log_stale = (
        log_age is not None
        and log_age > LOG_STALE_SECONDS
        and market_hours
    )

    acct = _alpaca_request(env, "/v2/account") if env else {
        "status": None, "body": None, "error": "no_env_file",
    }
    positions: Dict[str, Any] = {"status": None, "body": None, "error": "skipped"}
    if acct.get("status") == 200:
        positions = _alpaca_request(env, "/v2/positions")

    pos_count = 0
    if positions.get("status") == 200 and isinstance(positions.get("body"), list):
        pos_count = len(positions["body"])

    equity = None
    if acct.get("status") == 200 and isinstance(acct.get("body"), dict):
        try:
            equity = float(acct["body"].get("equity") or 0.0)
        except (TypeError, ValueError):
            equity = None

    return {
        "exp_id": exp_id,
        "label": label,
        "plist_path": str(plist_path),
        "env_file": env_file_rel,
        "log_path": str(log_path) if log_path else None,
        "lifecycle": lifecycle,
        "launchctl_loaded": bool(launchctl),
        "launchctl_pid": pid,
        "launchctl_last_exit": last_exit,
        "process_alive": alive,
        "log_age_sec": log_age,
        "log_stale": log_stale,
        "log_tail_errors": _count_log_errors(log_tail),
        "circuit_breaker": _last_circuit_breaker_state(log_tail),
        "alpaca_auth_status": acct.get("status"),
        "alpaca_auth_error": acct.get("error"),
        "position_count": pos_count,
        "equity": equity,
        "market_hours": market_hours,
    }


# ---------------------------------------------------------------------------
# Cross-cutting analysis
# ---------------------------------------------------------------------------


def _build_alerts(results: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Derive P0/P1 alerts across all scanners."""
    alerts: List[Dict[str, str]] = []
    for r in results:
        exp = r["exp_id"]
        # P0: positions exist but scanner is not running
        if r["position_count"] > 0 and not r["process_alive"]:
            alerts.append({
                "level": "P0",
                "exp_id": exp,
                "message": (
                    f"BROKER HAS {r['position_count']} POSITIONS BUT SCANNER IS DEAD"
                ),
            })
        # P0: launchctl says loaded but no live process
        # (only meaningful for keepalive lifecycles — periodic daemons are
        # expected to be absent between scheduled runs)
        if (
            r["launchctl_loaded"]
            and not r["process_alive"]
            and r.get("lifecycle") == "keepalive"
        ):
            alerts.append({
                "level": "P0",
                "exp_id": exp,
                "message": "LAUNCHCTL LOADED BUT PROCESS NOT RUNNING",
            })
        # P0: Alpaca auth failed
        if r["alpaca_auth_status"] in (401, 403):
            alerts.append({
                "level": "P0",
                "exp_id": exp,
                "message": f"ALPACA AUTH FAILED ({r['alpaca_auth_status']})",
            })
        # P1: launchctl exit code non-zero (crash loop)
        if r["launchctl_last_exit"] not in (None, 0):
            alerts.append({
                "level": "P1",
                "exp_id": exp,
                "message": (
                    f"LAUNCHCTL EXIT CODE {r['launchctl_last_exit']} "
                    "(possible crash loop)"
                ),
            })
        # P1: log stale during market hours
        if r["log_stale"]:
            age_min = (r["log_age_sec"] or 0) / 60.0
            alerts.append({
                "level": "P1",
                "exp_id": exp,
                "message": f"LOG STALE ({age_min:.0f} min) DURING MARKET HOURS",
            })
        # P1: errors in tail
        if r["log_tail_errors"] >= 3:
            alerts.append({
                "level": "P1",
                "exp_id": exp,
                "message": f"{r['log_tail_errors']} ERROR/CRITICAL lines in log tail",
            })
        # P1: circuit breaker non-NORMAL
        cb = r["circuit_breaker"]
        if cb and cb != "NORMAL":
            alerts.append({
                "level": "P1",
                "exp_id": exp,
                "message": f"CIRCUIT BREAKER = {cb}",
            })
    return alerts


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_json(results: List[Dict[str, Any]], alerts: List[Dict[str, str]]) -> str:
    return json.dumps({"scanners": results, "alerts": alerts}, indent=2, default=str)


def render_text(results: List[Dict[str, Any]], alerts: List[Dict[str, str]]) -> str:
    lines: List[str] = []
    lines.append("=" * 64)
    lines.append("SCANNER HEALTH REPORT")
    lines.append("=" * 64)
    if alerts:
        lines.append("")
        lines.append(f"ALERTS ({len(alerts)}):")
        for a in alerts:
            lines.append(f"  [{a['level']}] {a['exp_id']}: {a['message']}")
    else:
        lines.append("")
        lines.append("ALERTS: none")
    lines.append("")
    lines.append(
        f"{'exp':<10} {'pid':>7} {'alive':>5} {'logΔ':>6} "
        f"{'auth':>5} {'pos':>4} {'cb':>7} equity"
    )
    lines.append("-" * 64)
    for r in results:
        age = "—" if r["log_age_sec"] is None else f"{int(r['log_age_sec'] // 60)}m"
        equity = "—" if r["equity"] is None else f"${r['equity']:,.0f}"
        lines.append(
            f"{r['exp_id']:<10} "
            f"{str(r['launchctl_pid'] or '—'):>7} "
            f"{('Y' if r['process_alive'] else 'N'):>5} "
            f"{age:>6} "
            f"{str(r['alpaca_auth_status'] or '—'):>5} "
            f"{r['position_count']:>4} "
            f"{str(r['circuit_breaker'] or '—'):>7} "
            f"{equity}"
        )
    return "\n".join(lines)


def render_html(results: List[Dict[str, Any]], alerts: List[Dict[str, str]]) -> str:
    """Render an HTML fragment for the morning briefing.

    Designed to drop in *between* existing briefing sections. Inherits the
    briefing's CSS classes (``.alert``, ``.ok``, ``.pos``, ``.neg``, ``.flat``).
    """
    parts: List[str] = []
    parts.append('<h2>🩺 Scanner Health</h2>')

    if alerts:
        for a in alerts:
            cls = "alert"  # P0 and P1 both use the alert style for now
            parts.append(
                f'<div class="{cls}"><b>[{a["level"]}] {a["exp_id"]}</b> — '
                f'{a["message"]}</div>'
            )
    else:
        parts.append('<div class="ok"><b>✅ All scanners healthy</b></div>')

    parts.append("<table>")
    parts.append(
        "<tr><th>Experiment</th><th>PID</th><th>Alive</th><th>Log age</th>"
        "<th>Alpaca</th><th>Positions</th><th>Circuit</th><th>Equity</th></tr>"
    )
    for r in results:
        if r["log_age_sec"] is None:
            age = "—"
        else:
            mins = int(r["log_age_sec"] // 60)
            age_cls = "neg" if r["log_stale"] else "flat"
            age = f'<span class="{age_cls}">{mins}m</span>'

        alive_cls = "pos" if r["process_alive"] else "neg"
        alive = f'<span class="{alive_cls}">{"Y" if r["process_alive"] else "N"}</span>'

        auth = r["alpaca_auth_status"]
        if auth == 200:
            auth_html = '<span class="pos">200</span>'
        elif auth in (401, 403):
            auth_html = f'<span class="neg">{auth}</span>'
        elif auth is None:
            auth_html = f'<span class="neg">{r["alpaca_auth_error"] or "—"}</span>'
        else:
            auth_html = f'<span class="flat">{auth}</span>'

        equity = "—" if r["equity"] is None else f"${r['equity']:,.0f}"

        cb = r["circuit_breaker"] or "—"
        cb_cls = (
            "pos" if cb == "NORMAL" else
            "neg" if cb in ("HALTED", "WARN") else
            "flat"
        )
        cb_html = f'<span class="{cb_cls}">{cb}</span>'

        parts.append(
            f"<tr><td>{r['exp_id']}</td>"
            f"<td>{r['launchctl_pid'] or '—'}</td>"
            f"<td>{alive}</td>"
            f"<td>{age}</td>"
            f"<td>{auth_html}</td>"
            f"<td>{r['position_count']}</td>"
            f"<td>{cb_html}</td>"
            f"<td>{equity}</td></tr>"
        )
    parts.append("</table>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--format", choices=("json", "html", "text"), default="text")
    ap.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Project root used to resolve relative --env-file paths from plists.",
    )
    args = ap.parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    plists_set = {}
    for pat in PLIST_GLOBS:
        for p in LAUNCHAGENTS_DIR.glob(pat):
            plists_set.setdefault(p.stem, p)
    plists = sorted(plists_set.values())
    if not plists:
        msg = f"No plists matching {PLIST_GLOBS} in {LAUNCHAGENTS_DIR}"
        print(msg, file=sys.stderr)
        return 2

    results = [probe(p, project_root) for p in plists]
    alerts = _build_alerts(results)

    if args.format == "json":
        print(render_json(results, alerts))
    elif args.format == "html":
        print(render_html(results, alerts))
    else:
        print(render_text(results, alerts))

    # Exit code: 0 if no P0 alerts, 1 if any P0
    has_p0 = any(a["level"] == "P0" for a in alerts)
    return 1 if has_p0 else 0


if __name__ == "__main__":
    sys.exit(main())
