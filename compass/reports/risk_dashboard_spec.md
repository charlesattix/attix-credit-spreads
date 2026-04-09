# Real-Time Portfolio Risk Dashboard — Technical Spec

**Status:** DRAFT v1 — for Phase 9 paper trading (2026-04-09 start)
**Companion doc:** `compass/research/monitoring_spec.md` (alerting + reports layer)
**Owner:** ops team
**Consumers:** Carlos (executive view) · ops on-call (operational view) · cc5 (implementer)

---

## 1. Purpose

A single browser-accessible dashboard that shows the **live state of the 8-stream v8a portfolio** — per-stream P&L, VIX-ladder exposure alerts, correlation regime change detection, drawdown circuit state, and position concentration limits. The dashboard reads from the same data layer as the monitoring service (§2 data flow) and updates every 60 seconds during market hours.

**Hard requirement:** every number on the dashboard traces to a concrete metric from the monitoring service. No derived or extrapolated values. No synthetic data.

**Non-goals:** this is not a P&L attribution tool, not a historical backtest explorer, and not a trade execution interface. Those live in other places.

---

## 2. Data Flow (ASCII)

```
┌─────────────────────────────────────────────────────────────────────┐
│                       DATA SOURCES (all REAL)                        │
│                                                                      │
│  Alpaca paper-api         Yahoo Finance            IronVault         │
│  /v2/account              ^VIX / ^VIX3M / ^VVIX    options_cache.db  │
│  /v2/positions            SPY/QQQ/XLF/XLI/etc      (reference only)  │
│  /v2/orders               60s poll                                    │
│  /v2/activities           (during market hours)                       │
│  WS live quotes                                                       │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                    │                           │
                    │ 60 s poll                 │ EoD snapshot
                    ▼                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              MONITORING SERVICE  (compass/monitor/*.py)              │
│                                                                      │
│   alpaca_poller   →   normalises Alpaca responses to snapshot dicts  │
│   alert_evaluator →   runs threshold checks per monitoring_spec §3   │
│   circuit_breaker →   auto-protective actions                        │
│   drift_detector  →   compares live vs EXP-2570 forecast             │
│   report_generator →  daily + weekly HTML                            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                    │                           │
                    │ write                     │ read
                    ▼                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      PERSISTED STATE                                 │
│                                                                      │
│   compass/logs/metrics.jsonl       append-only, 1 line per poll      │
│   compass/logs/state.sqlite        last-known snapshot, indexed      │
│   compass/logs/incidents/*.json    alerts fired                       │
│   compass/reports/paper_signals/   daily signal JSONs                │
│   compass/logs/paper_signals_audit.jsonl   signal audit trail         │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                    │                           │
                    │ read                      │ read
                    ▼                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     RISK DASHBOARD (this spec)                       │
│                                                                      │
│   Static HTML + vanilla JS + 1 server endpoint                       │
│                                                                      │
│   backend:  compass/dashboard/server.py  (Flask, 1 worker)           │
│      GET /api/snapshot   → current state JSON                        │
│      GET /api/history    → last 20 days of metrics (rolling)         │
│      GET /api/incidents  → last 20 incidents                         │
│                                                                      │
│   frontend: compass/dashboard/static/index.html + app.js + risk.css  │
│      - polls /api/snapshot every 60 s                                │
│      - renders 6 panels (see §4)                                     │
│      - client-side only; no build step                               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
              Ops/Carlos browser
              http://localhost:8811   (dev)
              https://pilot-monitor.internal  (prod, basic auth)
```

### Notes on the data flow

- **Single writer** (monitoring service), **single reader** (dashboard server). No write contention. SQLite is adequate at 60 s cadence with a single writer.
- The dashboard server does not call Alpaca directly. That avoids rate-limit competition with the poller and keeps the dashboard unaffected by Alpaca outages (it shows the last known state with a "stale" badge).
- Stale detection: the dashboard flags any metric older than 2 × poll cadence in red with a "⚠ STALE" badge.
- All times stored as UTC ISO-8601; rendered in the browser as ET with local fallback.

---

## 3. API Endpoints

### 3.1 `GET /api/snapshot`

Returns the current portfolio state in a single JSON blob. Called every 60 seconds by the dashboard front-end.

**Response schema:**

```json
{
  "ts_utc": "2026-04-09T19:05:00Z",
  "age_seconds": 12,
  "market": {
    "is_open": true,
    "regime": "BULL",
    "vix": 20.3,
    "vix3m": 22.5,
    "vvix": 108.0,
    "vvix_60d_pct": 0.47,
    "term_structure_ratio": 0.902,
    "adaptive_vt_exposure": 1.0
  },
  "portfolio": {
    "equity_usd": 102450.12,
    "day_pnl_usd": 340.50,
    "day_pnl_pct": 0.0033,
    "total_pnl_since_start_usd": 2450.12,
    "total_pnl_since_start_pct": 0.0245,
    "buying_power_usd": 307350.36,
    "margin_usage_pct": 0.37,
    "leverage_actual": 2.84,
    "open_contracts": 42,
    "n_positions": 14
  },
  "risk": {
    "max_dd_since_start_pct": 1.2,
    "trailing_20d_dd_pct": 0.4,
    "rolling_20d_sharpe": 5.9,
    "rolling_20d_cagr_pct": 88.2,
    "rolling_20d_vol_pct": 14.9,
    "dd_band": "GREEN",
    "circuit_state": "NORMAL",
    "mean_pairwise_corr_60d": 0.04,
    "corr_band": "GREEN"
  },
  "streams": [
    {
      "stream": "exp1220",
      "weight_target": 0.35,
      "weight_actual": 0.348,
      "open_contracts": 12,
      "day_pnl_usd": 120.00,
      "total_pnl_usd": 860.00,
      "delta": -12.4,
      "gamma": 0.18,
      "theta_day_usd": 28.0,
      "vega": -142.0,
      "concentration_band": "GREEN"
    },
    ...
  ],
  "execution": {
    "fill_rate_limit_at_mid_pct": 58.0,
    "avg_fill_vs_mid_bps": 1.8,
    "avg_slippage_vs_model_bps": -2.0,
    "avg_time_to_fill_sec": 18.0,
    "execution_band": "GREEN"
  },
  "drift": {
    "sharpe_drift_pct": -1.7,
    "cagr_drift_pct": -5.2,
    "dd_budget_used_pct": 10.0,
    "drift_band": "GREEN",
    "verdict": "ON TRACK"
  },
  "vix_ladder": {
    "current_rung": "LOW (VIX < 25)",
    "next_trigger": "VIX >= 25 → adaptive VT exposure 1.0 → 0.75",
    "distance_to_next_pct": 23.0
  },
  "alerts_active": []
}
```

### 3.2 `GET /api/history?days=20`

Returns a compact time-series for the sparkline charts on the dashboard.

**Response:**

```json
{
  "start_date": "2026-03-20",
  "end_date": "2026-04-09",
  "daily": [
    {"date": "2026-03-20", "equity": 100000.0, "pnl_pct": 0.0, "dd_pct": 0.0,
     "sharpe_20d": null, "vix": 18.1, "regime": "BULL"},
    ...
  ]
}
```

### 3.3 `GET /api/incidents?limit=20`

Returns the last 20 incident records for the incidents panel.

---

## 4. Dashboard Panels

Six panels arranged in a responsive grid. On desktop (≥ 1200 px) the layout is 3 columns × 2 rows. On mobile, single column.

### 4.1 Panel 1 — Portfolio Header

Top strip, full width, 7 big cards:

```
┌─────────────┬─────────────┬─────────────┬─────────────┬─────────────┬─────────────┬─────────────┐
│   EQUITY    │  DAY P&L    │ TOTAL P&L   │ ROLLING SH  │   MAX DD    │  LEVERAGE   │   REGIME    │
│  $102,450   │  +$340.50   │  +$2,450    │    5.90     │    1.2%     │    2.84×    │    BULL     │
│   +0.33%    │   +0.33%    │   +2.45%    │ (target 6.0)│ (budget 12) │ (target 3)  │  VIX 20.3   │
└─────────────┴─────────────┴─────────────┴─────────────┴─────────────┴─────────────┴─────────────┘
```

Each card's border colour reflects the band (green/amber/red). Hover reveals the underlying metric name and freshness timestamp.

### 4.2 Panel 2 — Per-Stream P&L Table

Left middle row, dense table:

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ STREAM      W(T)  W(A)  CTR  DAY P&L    TOTAL P&L   DELTA    THETA    CONC      │
│ exp1220     35.0  34.8   12  +$120.00   +$860.00   -12.4    $28.0    GREEN      │
│ qqq_cs      15.0  15.2    3  +$  45.00  +$210.00   - 4.1    $ 9.0    GREEN      │
│ xlf_cs      10.0  10.1   10  +$  30.00  +$180.00   - 8.2    $ 7.5    GREEN      │
│ xli_cs      10.0   9.9    4  +$  20.00  +$140.00   - 3.0    $ 5.2    GREEN      │
│ gld_cal     10.0  10.2   12  +$  50.00  +$320.00    +0.0    $12.0    GREEN      │
│ slv_cal      5.0   5.0    6  +$  15.00  +$ 90.00    +0.0    $ 4.0    GREEN      │
│ cross_vol   10.0   9.8    0   $   0.00  +$200.00    +0.0    $ 0.0    GREEN      │
│ v5_hedge     5.0   5.0    1  -$  10.00  -$ 50.00    -1.5    -$1.0    GREEN      │
│ TOTAL      100.0 100.0   48  +$270.00   +$1,950     -29.2   $64.7                │
└──────────────────────────────────────────────────────────────────────────────────┘
```

Sortable by any column. Concentration band is RED if > 50%, AMBER if 40-50%, GREEN otherwise. Per-row click opens a stream detail modal (list of open positions, entry date, DTE remaining).

### 4.3 Panel 3 — VIX-Ladder Exposure Alerts

Right middle top, vertical ladder showing where we are in the adaptive VIX vol-target schedule (EXP-2640):

```
┌─────────────────────────────────────┐
│  VIX LADDER                         │
│                                     │
│  VIX ≥ 40  │ ■■■  CRASH        0.0× │
│  VIX ≥ 35  │ ■■   HIGH STRESS  0.5× │
│  VIX ≥ 30  │ ■    ELEVATED     0.7× │
│  VIX ≥ 25  │      WATCH        0.85×│
│  VIX < 25  │ →    NORMAL       1.00×│  ← you are here
│                                     │
│  Current VIX        20.3            │
│  Distance to 25     +23.0%          │
│  Exposure cap       1.00× (full)    │
│                                     │
│  Adaptive VT rule: linear ramp      │
│  from 1.0 at VIX=25 to 0.5 at 35    │
│                                     │
└─────────────────────────────────────┘
```

Ladder flashes amber when VIX crosses into the WATCH rung, red on ELEVATED or higher.

### 4.4 Panel 4 — Correlation Regime Change

Right middle bottom, 8×8 heatmap of rolling 60-day pairwise correlations:

```
┌─────────────────────────────────────────────────────┐
│  CORRELATION HEATMAP  (60-day rolling)              │
│                                                     │
│           e1  qq  xf  xi  gl  sl  cv  v5            │
│  exp1220 [■][ ][ ][ ][ ][ ][ ][ ]   mean ρ          │
│  qqq_cs  [ ][■][ ][ ][ ][ ][ ][ ]   +0.04           │
│  xlf_cs  [ ][ ][■][ ][ ][ ][ ][ ]                   │
│  xli_cs  [ ][ ][ ][■][ ][ ][ ][ ]   band: GREEN     │
│  gld_cal [ ][ ][ ][ ][■][ ][ ][ ]                   │
│  slv_cal [ ][ ][ ][ ][ ][■][ ][ ]                   │
│  cross_v [ ][ ][ ][ ][ ][ ][■][ ]   last change:    │
│  v5_hedg [ ][ ][ ][ ][ ][ ][ ][■]   no shift >0.1   │
│                                      in past 20 days│
│  Alert: mean ρ ≥ 0.50 in stress     │
│         regime → halt new orders    │
│                                     │
└─────────────────────────────────────┘
```

Colour scale: −1 deep blue → 0 white → +1 deep red. Clicking a cell shows the pair's rolling correlation sparkline over the past 60 days. Regime-change detector fires when **any pair crosses +0.20 above its 30-day trailing mean** — this is the early-warning trigger.

### 4.5 Panel 5 — Drawdown Circuit State

Bottom left:

```
┌───────────────────────────────────────────────────┐
│  DRAWDOWN CIRCUIT                                 │
│                                                   │
│   GREEN (< 5%)   ████████████████░░░░  1.2%       │
│   AMBER (5-8%)   ░░░░░░░░░░░░░░░░░░░░              │
│   SOFT  (8-10%)  ░░░░░░░░░░░░░░░░░░░░              │
│   HARD  (10-12%) ░░░░░░░░░░░░░░░░░░░░              │
│   HALT  (≥ 12%)  ░░░░░░░░░░░░░░░░░░░░              │
│                                                   │
│   Max DD since start: 1.2%                        │
│   Trailing 20d DD:    0.4%                        │
│   EXP-2370 trigger:   3.0% flatten                │
│                                                   │
│   Circuit state: NORMAL                           │
│   Last trip:     never                            │
│   Budget used:   10.0% of 12% hard limit          │
│                                                   │
│   DD recovery forecast from EXP-2720:             │
│     after 3% DD → 5.5 days mean / 11 days worst   │
│     after 5% DD → 7.3 days mean / 11 days worst   │
│                                                   │
└───────────────────────────────────────────────────┘
```

Bar fills up from left. At 8% the bar turns amber, at 10% red, at 12% flashing red.

### 4.6 Panel 6 — Position Concentration Limits

Bottom right, bar chart of per-stream weight actual vs target:

```
┌───────────────────────────────────────────────────┐
│  POSITION CONCENTRATION                           │
│                                                   │
│  exp1220     ████████████████████  34.8 / 35.0%   │
│  qqq_cs      ███████               15.2 / 15.0%   │
│  xlf_cs      █████                 10.1 / 10.0%   │
│  xli_cs      █████                  9.9 / 10.0%   │
│  gld_cal     █████                 10.2 / 10.0%   │
│  slv_cal     ██                     5.0 /  5.0%   │
│  cross_vol   █████                  9.8 / 10.0%   │
│  v5_hedge    ██                     5.0 /  5.0%   │
│                                                   │
│  Max single weight: 34.8%    cap: 40%             │
│  Rebalance trigger: |actual − target| > 10pp      │
│  Current max drift: 0.2 pp                        │
│                                                   │
│  Concentration band: GREEN                        │
│                                                   │
└───────────────────────────────────────────────────┘
```

Bars turn amber above 40%, red above 50% (matches EXP-1890 AllocationLimiter defaults).

---

## 5. Real-Time Update Model

### 5.1 Cadence

| Layer | Cadence | Source |
|---|---|---|
| Alpaca poll | 60 s market hours / 300 s after hours | monitoring service |
| Dashboard fetch | 60 s continuous | browser polling `/api/snapshot` |
| VIX/VIX3M/VVIX | 60 s via Yahoo during market hours | monitoring service |
| Correlation matrix | EoD (16:30 ET) | drift_detector |
| Rolling 20-day metrics | EoD | drift_detector |
| Incidents panel | polled every 60 s but updates only on new incident | dashboard server |

### 5.2 Update batching

The dashboard front-end uses a single `/api/snapshot` call that returns ALL panels' data at once. This avoids N+1 round trips and keeps the UI atomically consistent (no state where panel 1 is fresh and panel 5 is stale).

If any metric in the response is older than `2 × cadence`, it is rendered with the ⚠ STALE badge and the whole header card flashes amber once.

### 5.3 WebSocket (stretch, not v1)

v1 is HTTP polling only. In v2, add a Server-Sent Events stream from `/api/stream` that pushes incremental updates when a metric actually changes. Not required for Phase 9; defer.

---

## 6. Alert Triggers Surfaced in the UI

Every alert from the monitoring service (per `compass/research/monitoring_spec.md` §3) also shows in the dashboard as a toast notification **and** in a persistent "Active alerts" strip below the header.

| Trigger category | Panel that shows it | Visual treatment |
|---|---|---|
| Drawdown band change | Panel 5 | bar colour + panel border |
| Margin breach | Panel 1 (leverage card) | card border red |
| Execution degradation | Panel 1 (add-on card) | toast + weekly report link |
| Strategy drift | Panel 1 (drift card) | colour + drift verdict text |
| Correlation regime | Panel 4 | cell highlight + strip alert |
| Concentration breach | Panel 6 | bar red |
| Circuit auto-action | Full-screen modal | flashing, requires ack |

Every alert has:
- Trigger name + threshold
- Current value
- Auto-action taken (if any)
- Link to the incident record at `/api/incidents/{id}`

---

## 7. Implementation Contract (for cc5)

Files:

```
compass/dashboard/
├── __init__.py
├── server.py                Flask app, 3 endpoints, reads from state.sqlite
├── schemas.py               JSON response dataclasses
├── state_reader.py          thin wrapper around monitoring SQLite + metrics.jsonl
├── static/
│   ├── index.html           single page, 6-panel layout, vanilla JS
│   ├── app.js               polling loop + render functions
│   ├── risk.css             CSS variables for band colours, grid
│   └── icons.svg            inline icons
└── tests/
    ├── test_server.py       endpoint response schema + stale detection
    ├── test_state_reader.py SQLite / JSONL read paths
    └── test_alerts.py       toast rendering on band change (via jsdom)
```

Runtime: a Flask app on port 8811 started by the same systemd unit that runs the monitoring service (or by a sibling unit). Single worker, single thread — no concurrency required at 60 s cadence.

**Authentication:** basic auth in front of the prod deployment via nginx reverse proxy. Credentials stored in `~/.pilot-monitor-htpasswd`. Dev mode runs on localhost without auth.

**Memory budget:** < 100 MB. The dashboard server is stateless aside from a tiny LRU cache of the last snapshot and the last 20 days of history. All persistence belongs to the monitoring service.

**Deployment:** ship as part of the same release as the monitoring service. They are versioned together.

**Logging:** stdout only (Flask default). Nginx handles access logs.

**Testing gate before Phase 9 launch:**
- `/api/snapshot` returns valid JSON matching schema in §3.1 — unit tested
- Dashboard loads in < 1 second on an empty SQLite (initial state) — smoke tested
- 60-second auto-refresh actually redraws changed numbers — jsdom snapshot test
- All 6 panels render correctly with dummy data — visual regression via screenshot comparison
- Staleness badge triggers after 2 × cadence — unit tested

---

## 8. Observability of the Dashboard Itself

The dashboard server is monitored by the monitoring service (circular but intentional). Specifically:

- Heartbeat: `GET /api/health` returns `{"status": "ok", "ts": ...}` — polled by the monitoring service every 60 s
- If `/api/health` fails for > 3 consecutive minutes, the monitoring service sends a Telegram alert to ops
- If the monitoring service itself crashes (a higher-order problem), a cron-based watchdog at `scripts/monitor_heartbeat_check.sh` checks that `compass/logs/metrics.jsonl` has a fresh line within the last 5 minutes during market hours, and alerts ops otherwise

This creates two independent alert paths so a bug in the monitor can't silently hide itself.

---

## 9. What Carlos Actually Sees

When Carlos opens the dashboard at 10:00 ET on a normal day, he should see:

1. **Seven green header cards** (equity / day P&L / total P&L / rolling Sharpe / max DD / leverage / regime)
2. **Per-stream table** where every row is green and the TOTAL day P&L matches the header
3. **VIX ladder** showing "you are here" at the NORMAL rung
4. **Correlation heatmap** with all off-diagonal cells faint (near zero)
5. **Drawdown bar** in the GREEN band, < 5% used
6. **Concentration bars** all near their targets
7. Zero active alerts

On any day this is not what Carlos sees, he should be able to diagnose the issue in under 60 seconds by looking at which panel turned amber/red and clicking through to the incident record.

**The dashboard is a diagnostic tool, not a decision tool.** Decisions go through the weekly gate review, not the live UI.

---

## 10. Future Extensions (v2+)

- **Scenario stress panel:** push-button "what if VIX spikes to 40 right now?" — runs the EXP-1890 risk manager with stressed inputs
- **Trade approval queue:** if `require_approval` is ever enabled, show pending trades and a click-to-approve flow (not v1; v1 is fully automatic)
- **Mobile app:** iOS-only PWA using the same `/api/snapshot` endpoint
- **Historical replay:** scrub a timeline slider to see dashboard state at any past minute
- **Carlos-mode simplified view:** collapse panels 2-6 into a single "status: OK / ATTENTION / HALT" headline card, accessible at `/exec`

All future extensions are **deferred to post-Phase 9**. Phase 9's dashboard must ship with exactly the six panels in §4 and nothing more. Scope discipline is critical here — the monitoring spec does the heavy lifting; the dashboard is the face of that data.

---

*This spec is the single source of truth for the real-time risk dashboard UI and API. Any change requires updating this document AND the implementing code in `compass/dashboard/` in the same commit.*
