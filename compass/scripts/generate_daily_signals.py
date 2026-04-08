"""
compass/scripts/generate_daily_signals.py — Phase 9 daily signal loop.

Produces one JSON line per stream per day, machine-readable for the
paper-trading engine. Run from cron or the paper harness at ~09:25 ET.

Each row:
  {
    "date": "YYYY-MM-DD",
    "stream": "exp1220",
    "action": "OPEN" | "HOLD" | "CLOSE",
    "underlier": "SPY",
    "expiry": "YYYY-MM-DD",
    "short_strike": float,
    "long_strike": float | null,
    "width": float | null,
    "direction": "put_credit_spread" | "call_credit_spread" | "calendar" | ...,
    "size_contracts": int,
    "limit_price": float,
    "notes": str
  }
"""
from __future__ import annotations
import json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import every stream's signal function. Each module exposes
# `generate_today_signals(date: datetime.date) -> list[dict]` OR
# `build_trade_plan(...)` — we fall back through known function names.

STREAM_MODULES = [
    ("exp1220",       "compass.exp1220_standalone"),
    ("xlf_cs",        "compass.exp2200_north_star_v6"),
    ("xli_cs",        "compass.exp2200_north_star_v6"),
    ("gld_cal",       "compass.exp1770_commodity_calendars"),
    ("slv_cal",       "compass.exp1770_commodity_calendars"),
    ("vol_arb",       "compass.exp2020_cross_vol_arb"),
    ("v5_hedge",      "compass.crisis_alpha_v5"),
    ("spy_weekly_cs", "compass.exp2580_spy_weekly_cs"),
]


def _try_import(module_name: str):
    try:
        import importlib
        return importlib.import_module(module_name)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def generate_all_signals(today: datetime) -> list[dict]:
    rows = []
    for stream, mod_name in STREAM_MODULES:
        mod = _try_import(mod_name)
        if isinstance(mod, dict):
            rows.append({"date": today.strftime("%Y-%m-%d"), "stream": stream,
                         "action": "ERROR", "notes": mod["error"]})
            continue
        fn = (getattr(mod, "generate_today_signals", None)
              or getattr(mod, "build_trade_plan", None)
              or getattr(mod, "signal_for_today", None))
        if fn is None:
            rows.append({"date": today.strftime("%Y-%m-%d"), "stream": stream,
                         "action": "NO_SIGNAL_FN",
                         "notes": f"module {mod_name} has no signal function"})
            continue
        try:
            stream_rows = fn(today) or []
            for r in stream_rows:
                r.setdefault("stream", stream)
                r.setdefault("date", today.strftime("%Y-%m-%d"))
                rows.append(r)
        except Exception as e:
            rows.append({"date": today.strftime("%Y-%m-%d"), "stream": stream,
                         "action": "ERROR", "notes": f"{type(e).__name__}: {e}"})
    return rows


def main():
    today = datetime.utcnow()
    rows = generate_all_signals(today)
    out_path = ROOT / "compass" / "reports" / f"daily_signals_{today.strftime('%Y%m%d')}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"Wrote {len(rows)} rows → {out_path}")
    print(json.dumps(rows[:3], indent=2))


if __name__ == "__main__":
    main()
