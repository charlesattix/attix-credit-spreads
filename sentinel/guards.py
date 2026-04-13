"""
sentinel/guards.py — Pre-scan enforcement guard.

PRIMARY ENFORCEMENT MECHANISM for SENTINEL.  Called at startup of every live
scanner.  Completes in <1 second.  Raises SystemExit on halt.

Four checks, in order:
  0. Registry status   — not active/paused → sys.exit(1)
  1. Experiment status  — halted → sys.exit(1); paused → DRY_RUN=1
  2. Config fingerprint — drift vs stored SHA-256 → halt
  3. Alpaca API health  — 401 Unauthorized → halt

Injection pattern (first 2 lines after sys.path in every scanner):

    from sentinel.guards import pre_scan_check
    pre_scan_check("EXP-800")  # halts if status=halted; sets DRY_RUN if paused

If no sentinel_state.json entry exists for the experiment, the guard passes
silently (graceful degradation — scanners work before SENTINEL is onboarded).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sentinel.state import (
    compute_fingerprint as _state_compute_fingerprint,
    set_halt as _state_set_halt,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Resolve project root from this file's location: sentinel/ is one level below root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / "sentinel_state.json"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def pre_scan_check(experiment_id: str, config_path: Optional[str] = None) -> None:
    """
    Pre-scan enforcement gate.  Must be called at the top of every scanner.

    Args:
        experiment_id: Experiment identifier as stored in sentinel_state.json
                       (e.g. "EXP-307", "EXP-700", "EXP-800").
        config_path:   Optional explicit path to the experiment's config file.
                       If omitted, the guard reads config_path from the state
                       file (stored during ``--onboard``).  If neither is
                       available, the fingerprint check is skipped.

    Raises:
        SystemExit(1): experiment is halted, config drifted, or API key dead.
    """
    # ------------------------------------------------------------------
    # 0. Registry status check (experiments/registry.json)
    # ------------------------------------------------------------------
    _check_registry_status(experiment_id)

    state = _load_state()
    exp_state = state.get("experiments", {}).get(experiment_id, {})
    status = exp_state.get("status", "active")

    # ------------------------------------------------------------------
    # 1. Sentinel state status check (sentinel_state.json)
    # ------------------------------------------------------------------
    if status == "halted":
        reason = exp_state.get("halt_reason", "no reason given")
        halted_at = exp_state.get("halted_at", "unknown time")
        msg = (
            f"🛡️ SENTINEL — SCANNER BLOCKED\n"
            f"🛑 <b>{experiment_id}</b> scan aborted at startup.\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Halted at:</b> {halted_at}\n"
            f"Scanner will not execute until manually resolved.\n"
            f"<code>scripts/run_sentinel.py --approve {experiment_id} "
            f'--reason "..."</code>'
        )
        logger.critical(
            "SENTINEL HALT: %s — %s (halted_at=%s). Aborting scan.",
            experiment_id, reason, halted_at,
        )
        _send_alert(msg)
        sys.exit(1)

    if status == "paused":
        reason = exp_state.get("pause_reason", "no reason given")
        logger.warning(
            "SENTINEL PAUSE: %s — %s — forcing DRY_RUN=1", experiment_id, reason,
        )
        os.environ["DRY_RUN"] = "1"
        _send_alert(
            f"⚠️ SENTINEL — {experiment_id} is PAUSED ({reason}). "
            f"Running in DRY_RUN mode — no orders will be submitted."
        )

    # ------------------------------------------------------------------
    # 2. Config fingerprint check
    # ------------------------------------------------------------------
    stored_fp = exp_state.get("config_fingerprint")
    if stored_fp:
        # Resolve config path: explicit arg > state file > skip
        cfg_path = config_path or exp_state.get("paper_config")
        if cfg_path:
            try:
                current_fp = _state_compute_fingerprint(cfg_path)
            except Exception:
                current_fp = None
            if current_fp and current_fp != stored_fp:
                msg = (
                    f"🛡️ SENTINEL — SCANNER BLOCKED\n"
                    f"🛑 <b>{experiment_id}</b> config drift detected at scan startup.\n"
                    f"<b>Stored:</b>  <code>{stored_fp[:24]}…</code>\n"
                    f"<b>Current:</b> <code>{current_fp[:24]}…</code>\n"
                    f"The config file has changed since certification.\n"
                    f"<code>scripts/run_sentinel.py --approve {experiment_id} "
                    f'--reason "..."</code>'
                )
                logger.critical(
                    "SENTINEL HALT: %s config fingerprint mismatch — "
                    "stored=%s current=%s. Aborting scan.",
                    experiment_id, stored_fp[:24], current_fp[:24],
                )
                try:
                    _state_set_halt(
                        experiment_id,
                        "config drift detected (scan startup fingerprint check)",
                    )
                except Exception as exc:
                    logger.error("SENTINEL: failed to persist halt for %s: %s", experiment_id, exc)
                _send_alert(msg)
                sys.exit(1)
            elif not current_fp:
                logger.warning(
                    "SENTINEL: %s fingerprint check skipped — "
                    "could not read config at %s",
                    experiment_id, cfg_path,
                )
        # stored_fp exists but no config path → can't verify, skip with warning
        elif not cfg_path:
            logger.debug(
                "SENTINEL: %s has stored fingerprint but no config_path — "
                "fingerprint check skipped",
                experiment_id,
            )

    # ------------------------------------------------------------------
    # 3. Alpaca API key health check (fast, <500 ms)
    # ------------------------------------------------------------------
    _check_alpaca_health(experiment_id)

    logger.debug("SENTINEL: %s guard passed (status=%s)", experiment_id, status)


# ---------------------------------------------------------------------------
# Gate 0 — Registry status enforcement
# ---------------------------------------------------------------------------


def _check_registry_status(experiment_id: str) -> None:
    """Block scanners for experiments not active/paused in registry.json.

    Graceful degradation: passes silently if registry can't be loaded or
    experiment is not found (scanners work before registry enrollment).
    """
    try:
        from experiments.registry import load_registry
        registry = load_registry()
    except Exception:
        return  # registry unavailable — pass silently

    exp = registry.get("experiments", {}).get(experiment_id)
    if exp is None:
        return  # not in registry — graceful degradation

    status = exp.get("status", "")
    if status in ("active", "paper_trading"):
        return  # allowed to run
    if status == "paused":
        logger.warning(
            "SENTINEL GATE 0: %s is PAUSED in registry — forcing DRY_RUN=1",
            experiment_id,
        )
        os.environ["DRY_RUN"] = "1"
        return

    # All other statuses (stopped, retired, registered, configuring, failed) → block
    msg = (
        f"🛡️ SENTINEL — GATE 0 BLOCKED\n"
        f"🛑 <b>{experiment_id}</b> registry status is <b>{status}</b>.\n"
        f"Only active or paused experiments may run scanners.\n"
        f"Update via: <code>python scripts/registry_cli.py activate {experiment_id}</code>"
    )
    logger.critical("SENTINEL GATE 0: %s status=%s — blocking scanner.", experiment_id, status)
    _send_alert(msg)
    sys.exit(1)


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    """Load sentinel_state.json.  Returns {} on missing/corrupt file."""
    try:
        if _STATE_PATH.exists():
            with open(_STATE_PATH, encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:
        logger.warning("SENTINEL: could not read %s: %s", _STATE_PATH, exc)
    return {}


    # _update_state and _compute_fingerprint are consolidated into sentinel.state


# ---------------------------------------------------------------------------
# Alpaca health check
# ---------------------------------------------------------------------------


def _check_alpaca_health(experiment_id: str) -> None:
    """
    Verify Alpaca API credentials with a GET /v2/account call.

    * 200 → healthy, continue.
    * 401 → credentials dead → HALT + Telegram alert + sys.exit(1).
    * 5xx / timeout → log warning but continue (transient server-side issue).
    * No credentials configured → skip (guard still passes).

    Uses raw requests to avoid the full SDK initialisation overhead.
    Must complete within ~500 ms (timeout=3 s but usually <200 ms on LAN).
    """
    api_key = (
        os.environ.get("ALPACA_API_KEY")
        or os.environ.get("APCA_API_KEY_ID")
    )
    api_secret = (
        os.environ.get("ALPACA_API_SECRET")
        or os.environ.get("APCA_API_SECRET_KEY")
    )

    if not api_key or not api_secret:
        logger.debug(
            "SENTINEL: %s — Alpaca credentials not set, skipping health check",
            experiment_id,
        )
        return

    paper_env = os.environ.get("ALPACA_PAPER", "true").lower()
    paper = paper_env not in ("false", "0", "no")
    base_url = (
        "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    )

    try:
        import requests as _req  # always available in this project

        resp = _req.get(
            f"{base_url}/v2/account",
            headers={
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            },
            timeout=3,
        )

        if resp.status_code == 401:
            msg = (
                f"🛡️ SENTINEL — SCANNER BLOCKED\n"
                f"🛑 <b>{experiment_id}</b> Alpaca API key is invalid (401).\n"
                f"Endpoint: <code>GET {base_url}/v2/account</code>\n"
                f"Check <code>ALPACA_API_KEY</code> / <code>ALPACA_API_SECRET</code>.\n"
                f"Fix credentials then re-run the scanner."
            )
            logger.critical(
                "SENTINEL HALT: %s Alpaca API returned 401 — credentials dead. "
                "Aborting scan.",
                experiment_id,
            )
            _send_alert(msg)
            sys.exit(1)

        if resp.status_code >= 500:
            logger.warning(
                "SENTINEL: %s Alpaca API returned %d (server-side) — "
                "continuing anyway",
                experiment_id, resp.status_code,
            )

        # 200 or other 2xx → healthy
        logger.debug(
            "SENTINEL: %s Alpaca API healthy (HTTP %d)", experiment_id, resp.status_code,
        )

    except ImportError:
        logger.debug("SENTINEL: requests not importable — skipping Alpaca health check")
    except Exception as exc:
        # Network timeout, DNS failure, etc. — transient, do not halt
        logger.warning(
            "SENTINEL: %s Alpaca health check error (%s) — continuing",
            experiment_id, exc,
        )


# ---------------------------------------------------------------------------
# Telegram dispatch
# ---------------------------------------------------------------------------


def _send_alert(message: str) -> None:
    """
    Send a Telegram alert via the project's existing infrastructure.
    Never raises — guard must not crash if Telegram is unconfigured.
    """
    try:
        from shared.telegram_alerts import send_message
        send_message(message, parse_mode="HTML")
    except ImportError:
        logger.warning("SENTINEL: shared.telegram_alerts not importable — alert skipped")
    except Exception as exc:
        logger.error("SENTINEL: Telegram dispatch failed: %s", exc)
