# Attix Macro Intelligence API

REST API for macro snapshot data — sector RS rankings, macro scores, event calendars, and eligible underlyings.

**Base URL:** `http://localhost:8420`
**OpenAPI Docs:** `http://localhost:8420/docs`
**ReDoc:** `http://localhost:8420/redoc`

---

## Running the server

```bash
# From project root
python3 api/macro_api.py                 # default port 8420
python3 api/macro_api.py --port 8421     # custom port
python3 api/macro_api.py --reload        # dev mode (auto-reload)

# Via uvicorn directly
uvicorn api.macro_api:app --port 8420 --reload
```

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MACRO_API_KEYS` | No | `dev-attix-macro-2026` | Comma-separated valid API keys |
| `POLYGON_API_KEY` | Yes (weekly job) | — | Polygon.io API key |
| `PILOTAI_DATA_DIR` | No | `./data` | Override data directory path |

```bash
# .env example
MACRO_API_KEYS=prod-key-abc123,partner-key-xyz789
POLYGON_API_KEY=your_polygon_key_here
```

---

## Authentication

All endpoints except `/api/v1/health` require an API key passed as a request header:

```
X-API-Key: dev-attix-macro-2026
```

**HTTP 401** — missing or invalid key
**HTTP 429** — rate limit exceeded (100 req/min per key)

---

## Endpoints

### Health

#### `GET /api/v1/health`
No authentication required.

```bash
curl http://localhost:8420/api/v1/health
```

```json
{
  "status": "ok",
  "version": "1.0.0",
  "snapshot_count": 323,
  "latest_date": "2026-03-06",
  "db_path": "/path/to/data/macro_state.db",
  "timestamp": "2026-03-07T19:00:00Z"
}
```

---

### Snapshots

#### `GET /api/v1/macro/snapshot/latest`
Full weekly snapshot: sector rankings, macro score, events.

```bash
curl -H "X-API-Key: dev-attix-macro-2026" \
  http://localhost:8420/api/v1/macro/snapshot/latest
```

```json
{
  "date": "2026-03-06",
  "spy_close": 561.23,
  "top_sector_3m": "XLE",
  "top_sector_12m": "ITA",
  "leading_sectors": ["ITA", "XLI"],
  "lagging_sectors": ["XLK", "XLC", "XLRE"],
  "macro_score": {
    "overall": 60.4,
    "growth": 45.2,
    "inflation": 85.1,
    "fed_policy": 61.3,
    "risk_appetite": 50.0,
    "regime": "NEUTRAL_MACRO",
    "as_of_date": "2026-03-06",
    "indicators": {
      "vix": 23.75,
      "t10y2y": 0.56,
      "hy_oas_pct": 3.08,
      "cpi_yoy_pct": 2.87,
      "fedfunds": 3.72
    }
  },
  "sector_rankings": [
    {
      "ticker": "XLE", "name": "Energy", "category": "sector",
      "rs_3m": 24.9, "rs_12m": 31.2, "rrg_quadrant": "Improving",
      "rank_3m": 1, "rank_12m": 1, "close": 95.40
    }
  ],
  "upcoming_events": [
    {
      "event_date": "2026-03-12", "event_type": "CPI",
      "description": "CPI Release (2026-02) — Mar 12, 2026",
      "days_out": 5, "scaling_factor": 1.0
    }
  ],
  "event_scaling": 1.0
}
```

#### `GET /api/v1/macro/snapshot/{date}`
Snapshot for a specific Friday (YYYY-MM-DD). Coverage: 2020-01-03 to present.

```bash
curl -H "X-API-Key: dev-attix-macro-2026" \
  http://localhost:8420/api/v1/macro/snapshot/2022-06-17

curl -H "X-API-Key: dev-attix-macro-2026" \
  http://localhost:8420/api/v1/macro/snapshot/2020-03-20
```

#### `GET /api/v1/macro/history?from=YYYY-MM-DD&to=YYYY-MM-DD`
Lightweight summaries for a date range (max 3 years). Returns date, SPY close, top sector, macro score.

```bash
curl -H "X-API-Key: dev-attix-macro-2026" \
  "http://localhost:8420/api/v1/macro/history?from=2022-01-01&to=2022-12-31"
```

```json
[
  {
    "date": "2022-01-07",
    "spy_close": 456.20,
    "top_sector_3m": "XLE",
    "top_sector_12m": "XLE",
    "macro_overall": 58.3,
    "regime": "NEUTRAL_MACRO"
  }
]
```

---

### Sectors

#### `GET /api/v1/macro/sectors`
Current sector RS rankings with RRG quadrants.

```bash
# All sectors and thematic ETFs
curl -H "X-API-Key: dev-attix-macro-2026" \
  http://localhost:8420/api/v1/macro/sectors

# SPDR sectors only
curl -H "X-API-Key: dev-attix-macro-2026" \
  "http://localhost:8420/api/v1/macro/sectors?category=sector"

# Thematic ETFs only (SOXX, XBI, PAVE, ITA)
curl -H "X-API-Key: dev-attix-macro-2026" \
  "http://localhost:8420/api/v1/macro/sectors?category=thematic"
```

**RRG Quadrant meanings:**
| Quadrant | RS Ratio | RS Momentum | Signal |
|----------|----------|-------------|--------|
| Leading | > avg | > avg | Strong — in favor, momentum building |
| Weakening | > avg | < avg | Caution — still strong but momentum fading |
| Improving | < avg | > avg | Watch — underperforming but momentum turning |
| Lagging | < avg | < avg | Avoid — underperforming with negative momentum |

---

### Macro Score

#### `GET /api/v1/macro/score`
Current 4-dimension macro score.

```bash
curl -H "X-API-Key: dev-attix-macro-2026" \
  http://localhost:8420/api/v1/macro/score
```

```json
{
  "overall": 60.4,
  "growth": 45.2,
  "inflation": 85.1,
  "fed_policy": 61.3,
  "risk_appetite": 50.0,
  "regime": "NEUTRAL_MACRO",
  "indicators": {
    "cfnai_3m": 0.18,
    "payrolls_3m_avg_k": 165.3,
    "cpi_yoy_pct": 2.87,
    "core_cpi_yoy_pct": 3.12,
    "breakeven_5y": 2.46,
    "t10y2y": 0.56,
    "fedfunds": 3.72,
    "vix": 23.75,
    "hy_oas_pct": 3.08
  }
}
```

**Regime thresholds:**
- `BULL_MACRO` — overall ≥ 65 — favorable, full risk allocation
- `NEUTRAL_MACRO` — overall 45–65 — mixed signals, standard sizing
- `BEAR_MACRO` — overall < 45 — adverse, reduce directional exposure

---

### Events

#### `GET /api/v1/macro/events`
Upcoming FOMC, CPI, and NFP events with position scaling factors.

```bash
# Default: 14-day horizon
curl -H "X-API-Key: dev-attix-macro-2026" \
  http://localhost:8420/api/v1/macro/events

# Extended: 30-day horizon
curl -H "X-API-Key: dev-attix-macro-2026" \
  "http://localhost:8420/api/v1/macro/events?horizon_days=30"
```

```json
[
  {
    "event_date": "2026-03-12",
    "event_type": "CPI",
    "description": "CPI Release (2026-02) — Mar 12, 2026",
    "days_out": 5,
    "scaling_factor": 1.0
  },
  {
    "event_date": "2026-03-19",
    "event_type": "FOMC",
    "description": "FOMC Rate Decision — Mar 19, 2026",
    "days_out": 12,
    "scaling_factor": 1.0
  }
]
```

**Scaling factor schedule:**

| Event | T-5 | T-4 | T-3 | T-2 | T-1 | T-0 |
|-------|-----|-----|-----|-----|-----|-----|
| FOMC  | 1.00 | 0.90 | 0.80 | 0.70 | 0.60 | 0.50 |
| CPI   | 1.00 | 1.00 | 1.00 | 1.00 | 0.75 | 0.65 |
| NFP   | 1.00 | 1.00 | 1.00 | 1.00 | 0.80 | 0.75 |

The trading system uses `min()` across all active events as the composite factor.

---

### Trading

#### `GET /api/v1/macro/eligible?regime=BULL`
Eligible underlyings for credit spread trading given regime.

```bash
# Bull regime
curl -H "X-API-Key: dev-attix-macro-2026" \
  "http://localhost:8420/api/v1/macro/eligible?regime=BULL"

# Neutral (default)
curl -H "X-API-Key: dev-attix-macro-2026" \
  http://localhost:8420/api/v1/macro/eligible

# Bear regime
curl -H "X-API-Key: dev-attix-macro-2026" \
  "http://localhost:8420/api/v1/macro/eligible?regime=BEAR"
```

```json
{
  "tickers": ["SPY", "QQQ", "IWM", "XLE", "XLI"],
  "macro_score": 60.4,
  "macro_regime": "NEUTRAL_MACRO",
  "event_scaling": 1.0,
  "as_of_date": "2026-03-06"
}
```

---

## Error responses

| Status | Meaning |
|--------|---------|
| `200` | Success |
| `401` | Invalid or missing `X-API-Key` |
| `404` | No data for requested date/resource |
| `422` | Invalid query parameters (bad date format, etc.) |
| `429` | Rate limit exceeded (100 req/min) |
| `500` | Internal server error |

All errors return JSON: `{"detail": "Human-readable message"}`

---

## Running as a background service

### Simple background process

```bash
# Start
nohup python3 api/macro_api.py --port 8420 > logs/macro_api.log 2>&1 &
echo $! > /tmp/macro_api.pid

# Stop
kill $(cat /tmp/macro_api.pid)
```

### launchd (macOS)

Create `~/Library/LaunchAgents/com.attix.macro-api.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" ...>
<plist version="1.0">
<dict>
  <key>Label</key><string>com.attix.macro-api</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/pilotai-credit-spreads/api/macro_api.py</string>
    <string>--port</string><string>8420</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/macro_api.log</string>
  <key>StandardErrorPath</key><string>/tmp/macro_api_err.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.attix.macro-api.plist
```

---

## Python client example

```python
import requests

BASE = "http://localhost:8420"
HEADERS = {"X-API-Key": "dev-attix-macro-2026"}

# Get latest snapshot
snap = requests.get(f"{BASE}/api/v1/macro/snapshot/latest", headers=HEADERS).json()
print(f"Macro score: {snap['macro_score']['overall']} ({snap['macro_score']['regime']})")
print(f"Top sector:  {snap['top_sector_3m']}")

# Get eligible underlyings for current regime
eligible = requests.get(
    f"{BASE}/api/v1/macro/eligible",
    headers=HEADERS,
    params={"regime": "NEUTRAL"},
).json()
print(f"Eligible: {eligible['tickers']}")
print(f"Event scaling: {eligible['event_scaling']:.2f}x")

# Historical range
history = requests.get(
    f"{BASE}/api/v1/macro/history",
    headers=HEADERS,
    params={"from": "2022-01-01", "to": "2022-12-31"},
).json()
print(f"2022 snapshots: {len(history)}")
```
