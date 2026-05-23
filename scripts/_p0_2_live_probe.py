"""P0-2 live probe: verify Polygon key routing actually authorizes index tickers.

Run from repo root with .env loaded:
    python3 scripts/_p0_2_live_probe.py

Probes each ticker through `_pick_key` so we exercise the same routing
that scheduler/data_providers.py now uses.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.polygon_client import _pick_key  # noqa: E402


def probe(ticker: str) -> tuple[int, str]:
    """Hit Polygon /v2/aggs for `ticker` using the routed key."""
    api_key = _pick_key(ticker)
    if not api_key:
        return -1, "no_key_routed"
    end = datetime.utcnow()
    start = end - timedelta(days=5)
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
        f"/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
    )
    params = {"adjusted": "true", "sort": "desc", "limit": 1, "apiKey": api_key}
    try:
        resp = requests.get(url, params=params, timeout=15)
        return resp.status_code, resp.text[:80].replace("\n", " ")
    except Exception as e:
        return -2, f"exception: {e}"


def main():
    tickers = ["SPY", "QQQ", "I:VIX", "I:VIX3M", "I:VVIX", "I:SKEW"]
    print(f"{'ticker':<10}{'routed_key':<22}{'status':<10}body_snippet")
    print("-" * 100)
    for t in tickers:
        api_key = _pick_key(t)
        which = (
            "POLYGON_INDICES_API_KEY"
            if t.upper().startswith("I:")
            else "POLYGON_API_KEY"
        )
        masked = (api_key[:4] + "…" + api_key[-4:]) if api_key else "<empty>"
        status, body = probe(t)
        marker = "✓" if status == 200 else "✗"
        print(f"{t:<10}{which:<22}{status:<6}{marker:<4}{body}")


if __name__ == "__main__":
    main()
