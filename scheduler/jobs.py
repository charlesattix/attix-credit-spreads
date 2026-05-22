"""
scheduler/jobs.py — All APScheduler job functions for the compass-scheduler service.

Jobs:
  job_pre_market_check()       08:00 ET Mon-Fri
  job_event_gate_check()       09:20 ET Mon-Fri
  job_signal_generator()       09:25 ET Mon-Fri
  job_monitor_poll()           Every 5 min 09:30-16:00 ET Mon-Fri
  job_circuit_breaker_check()  Every 30 min 09:00-15:30 ET Mon-Fri
  job_post_market()            16:30 ET Mon-Fri
  job_weekly_summary()         Friday 16:35 ET
  job_data_freshness_check()   17:00 ET Mon-Fri
  job_heartbeat()              Every 4 hours
  job_log_rotate()             Daily 02:00 ET
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import pytz

from scheduler.alerts import send_telegram
from scheduler.data_providers import get_spot_price, get_vix_values

LOG = logging.getLogger("scheduler.jobs")

ET = pytz.timezone("America/New_York")

# ── Data paths ───────────────────────────────────────────────────────────────
DATA_DIR    = Path(os.environ.get("COMPASS_DATA_DIR", "/data"))
SIGNALS_DIR = Path(os.environ.get("COMPASS_SIGNALS_DIR", "/data/signals"))
LOGS_DIR    = Path(os.environ.get("COMPASS_LOGS_DIR", "/data/logs"))
HEALTH_JSON = Path(os.environ.get("HEALTH_JSON_PATH", "/data/health.json"))
EG_JSON     = DATA_DIR / "event_gate.json"
CB_JSON     = DATA_DIR / "circuit_breaker.json"


# ── Helpers ──────────────────────────────────────────────────────────────────

def ts() -> str:
    """UTC timestamp string."""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def now_et() -> datetime:
    return datetime.now(ET)


def today_et() -> date:
    return now_et().date()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def job_log(job_name: str, message: str) -> None:
    log_file = LOGS_DIR / f"{job_name}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry = f"[{ts()}] {message}\n"
    with open(log_file, "a") as f:
        f.write(entry)
    LOG.info("[%s] %s", job_name, message)


def get_alpaca_client():
    """Return an Alpaca TradingClient (paper or live depending on env)."""
    from alpaca.trading.client import TradingClient
    api_key    = os.environ["ALPACA_API_KEY"]
    api_secret = os.environ["ALPACA_API_SECRET"]
    paper      = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    return TradingClient(api_key=api_key, secret_key=api_secret, paper=paper)


# ════════════════════════════════════════════════════════════════════════════
# job_pre_market_check — 08:00 ET Mon-Fri
# ════════════════════════════════════════════════════════════════════════════

def job_pre_market_check() -> None:
    """08:00 ET Mon-Fri — health check with Polygon-primary market data."""
    job_log("pre_market", "=== Pre-market check start ===")
    failures = []

    # 1. Required env vars
    for var in ["ALPACA_API_KEY", "ALPACA_API_SECRET"]:
        if not os.environ.get(var):
            failures.append(f"env {var} MISSING")
            job_log("pre_market", f"FAIL: env {var} MISSING")
        else:
            job_log("pre_market", f"PASS: env {var} set")

    polygon_key = os.environ.get("POLYGON_API_KEY", "")
    if not polygon_key:
        failures.append("POLYGON_API_KEY MISSING — Polygon is primary data source")
        job_log("pre_market", "WARN: POLYGON_API_KEY not set")
    else:
        job_log("pre_market", "PASS: env POLYGON_API_KEY set")

    # 2. Alpaca trading API connectivity
    try:
        tc = get_alpaca_client()
        acct = tc.get_account()
        equity = float(acct.equity)
        job_log("pre_market", f"PASS: Alpaca equity=${equity:,.0f}")
    except Exception as e:
        failures.append(f"Alpaca connectivity: {e}")
        job_log("pre_market", f"FAIL: Alpaca connectivity: {e}")

    # 3. Market data — Polygon primary, yfinance fallback
    spy_price = get_spot_price("SPY")
    if spy_price:
        job_log("pre_market", f"PASS: SPY=${spy_price:.2f}")
    else:
        failures.append("Market data: SPY price unavailable from all sources")
        job_log("pre_market", "FAIL: SPY price unavailable from all sources")

    # 4. Data directories
    for d in [SIGNALS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    job_log("pre_market", "PASS: data directories exist")

    # 5. Summary
    if failures:
        msg = f"[PRE-MARKET] {len(failures)} FAILURE(S):\n" + "\n".join(f"  - {f}" for f in failures)
        job_log("pre_market", f"RESULT: FAILED — {failures}")
        send_telegram(msg)
    else:
        job_log("pre_market", "=== All checks PASSED ===")


# ════════════════════════════════════════════════════════════════════════════
# job_event_gate_check — 09:20 ET Mon-Fri
# ════════════════════════════════════════════════════════════════════════════

def job_event_gate_check() -> None:
    """09:20 ET Mon-Fri — check for FOMC/CPI events and set sizing gate."""
    today_str = today_et().isoformat()
    job_log("event_gate", f"=== Event gate check for {today_str} ===")

    # Load the macro gate config if it exists (populated by a separate process or manually)
    gate_active = False
    sizing_multiplier = 1.0
    reason = "no_gate"

    # Check if there's a pre-set event gate file
    gate_override = DATA_DIR / "event_gate_override.json"
    if gate_override.exists():
        try:
            override = json.loads(gate_override.read_text())
            if override.get("active") and override.get("date") == today_str:
                gate_active = True
                sizing_multiplier = float(override.get("sizing_multiplier", 0.5))
                reason = override.get("reason", "manual_override")
        except Exception as e:
            job_log("event_gate", f"WARN: failed to parse event_gate_override.json: {e}")

    gate_data = {
        "timestamp": ts(),
        "date": today_str,
        "gate_active": gate_active,
        "sizing_multiplier": sizing_multiplier,
        "reason": reason,
    }
    write_json(EG_JSON, gate_data)

    if gate_active:
        job_log("event_gate", f"GATE ACTIVE: {reason} sizing={sizing_multiplier}x")
        send_telegram(
            f"[EVENT GATE] {today_str}: {reason} — sizing at {sizing_multiplier}x\n"
            f"Signal generator will apply this multiplier."
        )
    else:
        job_log("event_gate", "No event gate active — full sizing")


# ════════════════════════════════════════════════════════════════════════════
# job_signal_generator — 09:25 ET Mon-Fri
# ════════════════════════════════════════════════════════════════════════════

def job_signal_generator() -> None:
    """09:25 ET Mon-Fri — signal generator with data fallbacks, validation, and Alpaca retry."""
    today_str = today_et().isoformat()
    job_log("signal_generator", f"=== Signal generator START for {today_str} ===")
    start = datetime.utcnow()

    # Read event gate sizing
    sizing_multiplier = 1.0
    if EG_JSON.exists():
        try:
            gate = json.loads(EG_JSON.read_text())
            sizing_multiplier = float(gate.get("sizing_multiplier", 1.0))
        except Exception:
            pass

    try:
        from compass.exp2830_paper_signal_generator import generate_all_signals
        from scheduler.signal_validator import validate_signal_payload

        # Collect data fallback alerts during signal generation
        data_alerts: list = []

        # Generate signals (data_providers fallback chain runs inside)
        signals = generate_all_signals(
            as_of=datetime.strptime(today_str, "%Y-%m-%d").date(),
            logger=logging.getLogger("exp2830"),
            data_alerts=data_alerts,
        )

        # Send data fallback alerts early (before validation)
        if data_alerts:
            send_telegram(
                f"[DATA FALLBACK] {today_str}: {len(data_alerts)} data source issue(s):\n"
                + "\n".join(f"  - {a}" for a in data_alerts[:10])
            )

        # Validate signal payload
        is_valid, validation_errors = validate_signal_payload(signals)
        if not is_valid:
            err_text = "\n".join(f"  - {e}" for e in validation_errors)
            job_log("signal_generator", f"VALIDATION FAILED: {validation_errors}")
            send_telegram(
                f"CRITICAL: Signal validation FAILED on {today_str}\n"
                f"{len(validation_errors)} error(s):\n{err_text}\n"
                f"No orders submitted."
            )
            return

        # Apply event gate sizing
        if sizing_multiplier != 1.0:
            for stream_sig in signals.get("signals", []):
                if isinstance(stream_sig, dict) and stream_sig.get("action") == "OPEN":
                    orig = stream_sig.get("target_contracts", 0)
                    stream_sig["target_contracts"] = max(1, round(orig * sizing_multiplier))
                    stream_sig["event_gate_applied"] = True
                    stream_sig["event_gate_multiplier"] = sizing_multiplier

        # Write signal file
        signal_path = SIGNALS_DIR / f"{today_str}.json"
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.write_text(json.dumps(signals, indent=2, default=str))

        # Audit log
        audit_log = LOGS_DIR / "paper_signals_audit.jsonl"
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_log, "a") as f:
            f.write(json.dumps({
                "timestamp": ts(),
                "date": today_str,
                "signals": signals,
            }, default=str) + "\n")

        elapsed = (datetime.utcnow() - start).total_seconds()
        stream_sigs = signals.get("signals", [])
        opens = [s["stream"] for s in stream_sigs
                 if isinstance(s, dict) and s.get("action") == "OPEN"]

        job_log(
            "signal_generator",
            f"VALIDATED+COMPLETE in {elapsed:.1f}s — "
            f"streams={len(stream_sigs)} opens={len(opens)} "
            f"event_mult={sizing_multiplier}"
        )

        # ── EXP-2890 SEAM: Order submission with retry ─────────────────────
        # Replace this block when the Signal->SpreadOrder bridge (EXP-2890) is built.
        #
        #   from compass.alpaca_connector import AlpacaConnector
        #   connector = AlpacaConnector(paper_mode=True)
        #   for stream_sig in [s for s in stream_sigs if s.get("action") == "OPEN"]:
        #       for attempt in range(3):
        #           try:
        #               result = connector.submit_spread(stream_sig)
        #               job_log("signal_generator", f"Order {stream_sig['stream']}: {result}")
        #               break
        #           except Exception as e:
        #               wait = 2 ** attempt  # 2s, 4s, 8s
        #               if attempt < 2:
        #                   job_log("signal_generator", f"Alpaca retry {attempt+1}: {e}, waiting {wait}s")
        #                   time.sleep(wait)
        #               else:
        #                   job_log("signal_generator", f"Alpaca FAILED after 3 attempts: {e}")
        #                   send_telegram(f"[ORDER FAIL] {stream_sig['stream']} on {today_str}: {e}")

        job_log(
            "signal_generator",
            "NOTE: EXP-2890 bridge not wired — signals on disk only, no Alpaca orders"
        )

        send_telegram(
            f"[SIGNAL GEN] {today_str}: {len(opens)} OPEN ({', '.join(opens) or 'none'}) "
            f"| {len(stream_sigs) - len(opens)} gated | {elapsed:.0f}s"
            + (f" | EVENT GATE {sizing_multiplier}x" if sizing_multiplier != 1.0 else "")
            + (f" | DATA FALLBACK(S): {len(data_alerts)}" if data_alerts else "")
        )

    except Exception as e:
        elapsed = (datetime.utcnow() - start).total_seconds()
        tb = traceback.format_exc()
        job_log("signal_generator", f"FAILED after {elapsed:.1f}s: {e}\n{tb}")
        send_telegram(
            f"CRITICAL: EXP-2830 signal generator FAILED on {today_str}\n"
            f"{type(e).__name__}: {e}\nNo signals generated. No orders today."
        )
        raise


# ════════════════════════════════════════════════════════════════════════════
# job_monitor_poll — Every 5 min 09:30-16:00 ET
# ════════════════════════════════════════════════════════════════════════════

def job_monitor_poll() -> None:
    """Every 5 min 09:30-16:00 ET — update health.json. Polygon-primary VIX."""
    vix, _ = get_vix_values()

    equity = None
    open_positions = []
    try:
        tc = get_alpaca_client()
        acct = tc.get_account()
        equity = float(acct.equity)
        positions = tc.get_all_positions()
        open_positions = [
            {
                "symbol": str(p.symbol),
                "qty": float(p.qty),
                "unrealized_pl": float(p.unrealized_pl or 0),
                "market_value": float(p.market_value or 0),
            }
            for p in positions
        ]
    except Exception as e:
        LOG.warning("monitor_poll: Alpaca fetch failed: %s", e)

    starting_capital = float(os.environ.get("STARTING_CAPITAL", "100000"))
    pnl_total = (equity - starting_capital) if equity else None
    pnl_total_pct = (pnl_total / starting_capital * 100) if pnl_total is not None else None

    health = {
        "timestamp": ts(),
        "equity": equity,
        "starting_capital": starting_capital,
        "pnl_total": pnl_total,
        "pnl_total_pct": pnl_total_pct,
        "open_positions": len(open_positions),
        "positions": open_positions,
        "vix": vix,
    }
    write_json(HEALTH_JSON, health)


# ════════════════════════════════════════════════════════════════════════════
# job_circuit_breaker_check — Every 30 min 09:00-15:30 ET
# ════════════════════════════════════════════════════════════════════════════

def job_circuit_breaker_check() -> None:
    """Every 30 min — VIX circuit breaker. Polygon primary, yfinance fallback."""
    VIX_CRISIS_BLOCK   = 35.0
    VIX_EMERGENCY_EXIT = 45.0
    DD_HALT_PCT        = 13.0
    alerts = []

    vix, vix3m = get_vix_values()
    data_source = "L1_polygon" if vix is not None else "unknown"

    if vix is None:
        alerts.append("WARNING: VIX unavailable from all sources — circuit breaker conservative mode")
    else:
        ts_inverted = (vix > vix3m) if vix3m else False
        if vix >= VIX_EMERGENCY_EXIT:
            alerts.append(f"EMERGENCY: VIX {vix:.1f} >= {VIX_EMERGENCY_EXIT} — EXIT ALL POSITIONS")
        elif vix >= VIX_CRISIS_BLOCK:
            alerts.append(f"CRITICAL: VIX {vix:.1f} >= {VIX_CRISIS_BLOCK} — block new entries")
        elif ts_inverted:
            alerts.append(f"WARNING: VIX term structure inverted ({vix:.1f} > VIX3M {vix3m:.1f})")

    # DD check from health.json
    if HEALTH_JSON.exists():
        try:
            h = json.loads(HEALTH_JSON.read_text())
            equity    = h.get("equity")
            start_cap = h.get("starting_capital", 100_000)
            if equity and start_cap:
                dd_pct = max(0.0, (start_cap - equity) / start_cap * 100)
                if dd_pct >= DD_HALT_PCT:
                    alerts.append(
                        f"CRITICAL: Portfolio DD {dd_pct:.2f}% >= halt {DD_HALT_PCT}% — HALT"
                    )
                elif dd_pct >= DD_HALT_PCT * 0.7:
                    alerts.append(f"WARNING: DD {dd_pct:.2f}% approaching halt threshold")
        except Exception as e:
            LOG.warning("circuit_breaker: health.json parse error: %s", e)

    write_json(CB_JSON, {
        "timestamp": ts(),
        "vix": vix,
        "vix3m": vix3m,
        "ts_inverted": (vix > vix3m) if (vix and vix3m) else False,
        "data_source": data_source,
        "alerts": alerts,
    })

    if alerts:
        msg = "CIRCUIT BREAKER:\n" + "\n".join(f"  {a}" for a in alerts)
        LOG.error(msg)
        send_telegram(msg)


# ════════════════════════════════════════════════════════════════════════════
# job_post_market — 16:30 ET Mon-Fri
# ════════════════════════════════════════════════════════════════════════════

def job_post_market() -> None:
    """16:30 ET Mon-Fri — equity snapshot + daily summary."""
    today_str = today_et().isoformat()
    job_log("post_market", f"=== Post-market snapshot for {today_str} ===")

    equity = None
    positions = []
    try:
        tc = get_alpaca_client()
        acct = tc.get_account()
        equity = float(acct.equity)
        raw_positions = tc.get_all_positions()
        positions = [
            {
                "symbol": str(p.symbol),
                "qty": float(p.qty),
                "unrealized_pl": float(p.unrealized_pl or 0),
                "market_value": float(p.market_value or 0),
                "avg_entry_price": float(p.avg_entry_price or 0),
            }
            for p in raw_positions
        ]
        job_log("post_market", f"Equity: ${equity:,.2f} | Positions: {len(positions)}")
    except Exception as e:
        job_log("post_market", f"FAIL: Alpaca unreachable: {e}")
        send_telegram(f"[POST-MARKET] {today_str}: Equity snapshot failed — {e}")
        return

    # Equity journal (append)
    starting_capital = float(os.environ.get("STARTING_CAPITAL", "100000"))
    pnl = equity - starting_capital if equity else None
    pnl_pct = pnl / starting_capital * 100 if pnl is not None else None

    journal_path = Path(os.environ.get("EQUITY_JOURNAL_PATH", "/data/equity_journal.csv"))
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    if not journal_path.exists():
        journal_path.write_text("date,equity,pnl,pnl_pct,open_positions\n")
    with open(journal_path, "a") as f:
        f.write(f"{today_str},{equity:.2f},{pnl:.2f},{pnl_pct:.4f},{len(positions)}\n")

    # Read today's signal for summary
    signal_summary = "no signal file"
    signal_file = SIGNALS_DIR / f"{today_str}.json"
    if signal_file.exists():
        try:
            sigs = json.loads(signal_file.read_text())
            opens = [s["stream"] for s in sigs.get("signals", []) if s.get("action") == "OPEN"]
            no_trades = sum(1 for s in sigs.get("signals", []) if s.get("action") == "NO_TRADE")
            signal_summary = f"{len(opens)} OPEN, {no_trades} NO_TRADE"
        except Exception:
            signal_summary = "parse error"

    job_log("post_market", f"Complete: equity=${equity:,.2f} pnl={pnl_pct:+.2f}%")


# ════════════════════════════════════════════════════════════════════════════
# job_weekly_summary — Friday 16:35 ET
# ════════════════════════════════════════════════════════════════════════════

def job_weekly_summary() -> None:
    """Friday 16:35 ET — weekly performance summary via Telegram."""
    job_log("weekly_summary", "=== Weekly summary ===")

    # Read equity journal for the week
    journal_path = Path(os.environ.get("EQUITY_JOURNAL_PATH", "/data/equity_journal.csv"))
    starting_capital = float(os.environ.get("STARTING_CAPITAL", "100000"))

    lines = []
    if journal_path.exists():
        try:
            raw = journal_path.read_text().splitlines()
            # Last 7 data rows (skip header)
            lines = [r for r in raw[1:] if r.strip()][-7:]
        except Exception as e:
            job_log("weekly_summary", f"WARN: journal read failed: {e}")

    if not lines:
        send_telegram(
            f"--- Weekly Summary ---\nNo equity data available for this week."
        )
        return

    # Parse most recent equity
    try:
        last = lines[-1].split(",")
        equity_now = float(last[1])
        pnl_total = equity_now - starting_capital
        pnl_pct   = pnl_total / starting_capital * 100
    except Exception as e:
        job_log("weekly_summary", f"WARN: journal parse failed: {e}")
        send_telegram(f"--- Weekly Summary ---\nJournal parse error: {e}")
        return

    # Count signals this week
    today = today_et()
    week_opens = 0
    for i in range(7):
        d = today - timedelta(days=i)
        sf = SIGNALS_DIR / f"{d.isoformat()}.json"
        if sf.exists():
            try:
                sigs = json.loads(sf.read_text())
                week_opens += sum(
                    1 for s in sigs.get("signals", []) if s.get("action") == "OPEN"
                )
            except Exception:
                pass

    msg = (
        f"--- Weekly Summary ({today.isoformat()}) ---\n"
        f"Equity: ${equity_now:,.2f}\n"
        f"Total P&L: ${pnl_total:+,.2f} ({pnl_pct:+.2f}%)\n"
        f"Signals OPEN this week: {week_opens}\n"
        f"Trading days captured: {len(lines)}"
    )
    job_log("weekly_summary", msg)
    send_telegram(msg)


# ════════════════════════════════════════════════════════════════════════════
# job_data_freshness_check — 17:00 ET Mon-Fri
# ════════════════════════════════════════════════════════════════════════════

def job_data_freshness_check() -> None:
    """17:00 ET Mon-Fri — verify signal file and equity journal were updated today."""
    today_str = today_et().isoformat()
    failures = []

    signal_file = SIGNALS_DIR / f"{today_str}.json"
    if not signal_file.exists():
        failures.append(f"Signal file missing: {signal_file.name}")

    journal_path = Path(os.environ.get("EQUITY_JOURNAL_PATH", "/data/equity_journal.csv"))
    if journal_path.exists():
        try:
            lines = [r for r in journal_path.read_text().splitlines() if r.strip()][1:]
            if not lines or not lines[-1].startswith(today_str):
                failures.append(f"Equity journal not updated today (last entry != {today_str})")
        except Exception as e:
            failures.append(f"Equity journal parse error: {e}")
    else:
        failures.append("Equity journal file missing")

    if failures:
        msg = f"[DATA CHECK] {today_str} Failures:\n" + "\n".join(f"  - {f}" for f in failures)
        job_log("data_freshness", msg)
        send_telegram(msg)
    else:
        job_log("data_freshness", f"All data checks PASSED for {today_str}")


# ════════════════════════════════════════════════════════════════════════════
# job_heartbeat — Every 4 hours
# ════════════════════════════════════════════════════════════════════════════

def job_heartbeat() -> None:
    """Every 4 hours — send a heartbeat Telegram to prove the service is alive."""
    health = {}
    if HEALTH_JSON.exists():
        try:
            health = json.loads(HEALTH_JSON.read_text())
        except Exception:
            pass

    equity   = health.get("equity")
    vix      = health.get("vix")
    today_str = today_et().isoformat()
    last_signal = "none"

    signal_file = SIGNALS_DIR / f"{today_str}.json"
    if signal_file.exists():
        try:
            sigs = json.loads(signal_file.read_text())
            opens = [s["stream"] for s in sigs.get("signals", [])
                     if s.get("action") == "OPEN"]
            last_signal = f"{len(opens)} OPEN ({', '.join(opens)})"
        except Exception:
            last_signal = "parse error"

    equity_str = f"${equity:,.0f}" if equity else "unknown"
    vix_str    = f"{vix:.1f}" if vix else "unknown"

    send_telegram(
        f"[HEARTBEAT] North Star alive — {now_et().strftime('%Y-%m-%d %H:%M ET')}\n"
        f"Equity: {equity_str} | VIX: {vix_str}\n"
        f"Last signal ({today_str}): {last_signal}"
    )


# ════════════════════════════════════════════════════════════════════════════
# job_log_rotate — Daily 02:00 ET
# ════════════════════════════════════════════════════════════════════════════

def job_log_rotate() -> None:
    """02:00 ET daily — rotate logs older than 30 days."""
    import shutil
    cutoff = datetime.utcnow() - timedelta(days=30)
    rotated = 0

    archive_dir = LOGS_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    for log_file in LOGS_DIR.glob("*.log"):
        try:
            mtime = datetime.utcfromtimestamp(log_file.stat().st_mtime)
            if mtime < cutoff:
                dest = archive_dir / log_file.name
                shutil.move(str(log_file), str(dest))
                rotated += 1
        except Exception as e:
            LOG.warning("log_rotate: failed to rotate %s: %s", log_file, e)

    LOG.info("log_rotate: rotated %d files to archive", rotated)
