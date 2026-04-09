# Production Monitoring & Alerting Specification

**Status:** DRAFT v1 — for Phase 9 paper trading (starts today) and Phase 10 live
**Owner:** ops team (cc5 implements)
**Scope:** 8-stream v8a portfolio on Alpaca commission-free (primary) + IBKR Pro (fallback)
**References:** EXP-1890 Portfolio Risk Manager · EXP-2370 DD circuit breaker · EXP-2470 execution stack · EXP-2570 broker · EXP-2670 go/no-go gates · EXP-2830 signal generator · MASTERPLAN v11

---

## 1. Purpose

Turn the paper-trading and live-trading pipeline into a monitored production system that:
1. Detects degradation **before** it breaches hard gates
2. Compares live results against the EXP-2570 forecast on a rolling basis
3. Triggers automatic protective actions (flatten, deleverage) on hard thresholds
4. Escalates to Carlos on material events (via Telegram + email)
5. Produces a daily audit pack and a weekly executive summary

Non-goals: this spec does **not** cover the trade-decision logic (that lives in `compass/exp2830_paper_signal_generator.py`) or risk computation (that lives in `compass/portfolio_risk_manager.py`). It only covers the telemetry, alerting, and reporting layer that sits above them.

---

## 2. Key Metrics — Real-Time

The monitoring service polls Alpaca on a 60-second cadence during market hours and a 300-second cadence after hours. Every poll writes a single row to `compass/logs/metrics.jsonl` and updates the in-memory dashboard state.

### 2.1 Portfolio-level (polled every 60 s)

| Metric | Source | Computation | Update rate |
|---|---|---|---|
| `equity_usd` | Alpaca `/v2/account` | `.equity` | 60 s |
| `buying_power_usd` | Alpaca `/v2/account` | `.buying_power` | 60 s |
| `cash_usd` | Alpaca `/v2/account` | `.cash` | 60 s |
| `portfolio_value` | Alpaca `/v2/account` | `.portfolio_value` | 60 s |
| `day_pnl_usd` | Alpaca `/v2/account` | `.equity - .last_equity` | 60 s |
| `day_pnl_pct` | computed | `day_pnl_usd / last_equity` | 60 s |
| `realised_20d_vol` | local rolling series | `std(pnl_pct_series_20) × √252` | EoD only |
| `rolling_20d_sharpe` | local rolling series | `mean / std × √252` | EoD only |
| `rolling_20d_drawdown` | local rolling series | `(hwm - eq) / hwm` on 20d window | 60 s |
| `max_dd_since_start` | local persistent state | `max( (hwm - eq_t) / hwm )` | 60 s |
| `leverage_actual` | computed | `sum(abs(position_notional)) / equity` | 60 s |
| `margin_usage_pct` | Alpaca `/v2/account` | `initial_margin / equity` | 60 s |

### 2.2 Per-stream (polled every 60 s, refreshed from signal JSON daily)

For each of the 8 v8a streams, track:

| Metric | Source | Computation |
|---|---|---|
| `stream_weight_target` | `exp2830_paper_signal_config.json` | static |
| `stream_weight_actual` | derived | `stream_notional / total_notional` |
| `stream_pnl_day_usd` | Alpaca `/v2/positions` filtered by stream tag | sum of unrealised+realised |
| `stream_pnl_total_usd` | persistent state | rolling sum since paper start |
| `stream_delta` | derived from positions | sum(position_delta × multiplier) |
| `stream_gamma` | derived | sum(position_gamma × multiplier) |
| `stream_theta_day_usd` | derived | sum(position_theta × multiplier) |
| `stream_vega` | derived | sum(position_vega × multiplier) |
| `stream_contracts_open` | Alpaca positions | count |

Position-to-stream mapping uses the `stream` tag stored in each order's `client_order_id` (format `v8a:{stream}:{yyyymmdd}:{seq}`, set at submission time by the integration layer).

### 2.3 Execution quality (polled every 60 s, rolled up EoD)

From Alpaca `/v2/orders?status=all&limit=500`:

| Metric | Computation |
|---|---|
| `fill_rate_limit_at_mid_pct` | filled_limit_orders / total_limit_orders |
| `avg_fill_vs_mid_bps` | sum((fill_price - order_mid_at_entry)/mid) / n_fills × 1e4 |
| `avg_slippage_vs_model_bps` | actual bps drag − expected bps drag (from EXP-2470) |
| `partial_fills_pct` | n_partial / n_orders |
| `cancelled_orders_pct` | n_cancelled / n_submitted |
| `time_to_fill_seconds` | avg `(filled_at - submitted_at)` |

### 2.4 Backtest-forecast drift (computed EoD)

| Metric | Computation |
|---|---|
| `sharpe_drift_pct` | `(rolling_20d_sharpe - 6.00) / 6.00 × 100` |
| `cagr_drift_pct` | `(annualised_20d_return - 93.0) / 93.0 × 100` |
| `dd_budget_used_pct` | `max_dd_since_start / 0.12 × 100` |
| `cumulative_pnl_vs_forecast_bps` | actual cumulative − forecast cumulative |

---

## 3. Alert Thresholds

Every metric has **three bands**: GREEN / AMBER / RED. GREEN logs only, AMBER sends a Telegram notification, RED pages Carlos and may auto-flatten.

### 3.1 Drawdown

| Band | Threshold | Action |
|---|---|---|
| GREEN | `max_dd_since_start < 5%` | log only |
| AMBER | `5% ≤ dd < 8%` | Telegram: "DD watch" |
| RED — soft | `8% ≤ dd < 10%` | Telegram + email: "emergency deleverage to 0.5×" |
| RED — hard | `10% ≤ dd < 12%` | Telegram + email + auto-flatten half of open contracts |
| RED — halt | `dd ≥ 12%` | Telegram + email + SMS + auto-flatten ALL positions + trading halt until Carlos approval |

These thresholds match EXP-1890's `DrawdownCircuitBreaker` (soft 10% / hard 12%) plus an added 5%/8% early-warning layer.

### 3.2 Trailing 20-day circuit (EXP-2370)

Separate from the max-DD circuit above. Tracks a 20-day rolling DD from local peak.

| Band | Threshold | Action |
|---|---|---|
| GREEN | `trailing_20d_dd < 2%` | log only |
| AMBER | `2% ≤ trailing_20d_dd < 3%` | Telegram: "trailing DD watch" |
| RED | `trailing_20d_dd ≥ 3%` | **auto-flatten** for 20 trading days, alert Carlos |

### 3.3 Margin usage

| Band | Threshold | Action |
|---|---|---|
| GREEN | `margin_usage_pct < 40%` | log only |
| AMBER | `40% ≤ margin < 60%` | Telegram |
| RED | `margin ≥ 60%` | halt new orders, alert Carlos |

At 3× target leverage, margin usage should sit at ~35–45% during normal operation. Above 60% indicates the vol target scale misfired or a position moved against us.

### 3.4 Execution quality degradation

| Metric | GREEN | AMBER | RED |
|---|---|---|---|
| fill rate limit-at-mid | ≥ 50% | 30–50% | < 30% |
| avg fill vs mid | ≤ 2 bps | 2–5 bps | > 5 bps |
| slippage vs model | ≤ 0 bps | 0–10 bps | > 10 bps |
| time to fill | < 30 s | 30–120 s | > 120 s |

**RED on execution quality** means one of the EXP-2470 techniques has broken. Action: switch broker routing to IBKR Pro fallback after 3 consecutive RED days, and alert Carlos immediately.

### 3.5 Strategy drift from backtest

| Metric | GREEN | AMBER | RED |
|---|---|---|---|
| `sharpe_drift_pct` | ±15% | ±15% to ±25% | ±25%+ |
| `cagr_drift_pct` | ±20% | ±20% to ±35% | ±35%+ |
| `dd_budget_used_pct` | < 50% | 50–75% | > 75% |

AMBER and RED trigger a weekly drift review with Carlos. Three consecutive weeks at RED = mandatory Phase 9 re-gate.

### 3.6 Correlation regime change

Polled once per day from the local rolling stream returns:

| Band | Condition | Action |
|---|---|---|
| GREEN | mean pairwise ρ among 8 streams < 0.30 | log |
| AMBER | 0.30 ≤ ρ < 0.50 | Telegram "correlation watch" |
| RED | ρ ≥ 0.50 | Telegram + halt new positions for 1 day, manual review |

Matches EXP-1890 `CorrelationMonitor` threshold.

### 3.7 Position concentration

| Band | Condition | Action |
|---|---|---|
| GREEN | max per-stream weight ≤ 40% | log |
| AMBER | 40–50% | log + weekly review |
| RED | > 50% | block new orders in that stream, alert Carlos |

Matches EXP-1890 `AllocationLimiter` default caps.

---

## 4. Circuit Breaker Triggers

Auto-protective actions the monitor can take without human approval:

| Trigger | Action | Cooldown | Alert |
|---|---|---|---|
| `max_dd ≥ 8%` | Cut leverage to 0.5× (cancel new orders, do not flatten existing) | until DD < 5% | Telegram + email |
| `max_dd ≥ 10%` | Flatten 50% of open contracts, cut leverage to 0.5× | 4 hours | Telegram + email |
| `max_dd ≥ 12%` | **Flatten ALL positions, halt trading** | until Carlos re-approves | Telegram + email + SMS |
| `trailing_20d_dd ≥ 3%` (EXP-2370) | Flatten everything, pause for 20 trading days | 20 trading days | Telegram + email |
| `margin_usage ≥ 60%` | Halt new orders | until margin < 45% | Telegram |
| `corr_regime ≥ 0.50` | Halt new orders for 1 day | 24 h | Telegram |
| `execution quality RED 3 days` | Switch to IBKR Pro broker | until Alpaca fills recover | Telegram + email |
| `data provider outage (IronVault > 5 min)` | Halt new signals | until recovery | Telegram |
| `alpaca api 5xx > 3 min` | Halt new orders, existing stays | until recovery | Telegram |

**Any auto-flatten action generates an incident record** at `compass/logs/incidents/{yyyymmdd-hhmmss}.json` with the trigger, action taken, open positions at time of trigger, and market snapshot.

---

## 5. Alpaca API Integration

### 5.1 Endpoints used

All endpoints live at `https://paper-api.alpaca.markets/v2/` (paper) or `https://api.alpaca.markets/v2/` (live).

| Endpoint | Method | Purpose | Poll cadence |
|---|---|---|---|
| `/v2/account` | GET | equity, buying power, margin | 60 s market hours |
| `/v2/positions` | GET | open positions, unrealised P&L, Greeks | 60 s market hours |
| `/v2/orders?status=all&limit=500` | GET | all recent orders for fill-rate + slippage | 60 s market hours |
| `/v2/account/activities?activity_types=FILL,DIV,INT` | GET | realised trade log for reconciliation | EoD (16:30 ET) |
| `/v2/clock` | GET | market open/close status | 300 s |
| `/v2/options/contracts` | GET | look up option contract details by symbol | on-demand |
| Market data `/v1beta1/options/quotes/{symbol}` | GET | live NBBO for the EXP-2470 slippage calc | 60 s market hours |
| WebSocket `wss://stream.data.alpaca.markets/v2/sip` | WS | live trade/quote stream for active positions | persistent |

### 5.2 Rate limits

Alpaca allows 200 requests/minute on the basic tier. Our budget:

```
60 s poll × 4 endpoints         = 4/min  for market-hours polling
Options quotes: ~16 positions   = 16/min (every 60 s)
EoD activities                  = 1/day
on-demand contract lookups      = ~5/min peak during order placement
----------------------------------------------------
Total peak usage                ~25 rpm (well under 200 rpm limit)
```

### 5.3 Authentication

```python
from alpaca.trading.client import TradingClient
from alpaca.data.historical.option import OptionHistoricalDataClient

trading = TradingClient(
    api_key=os.environ["ALPACA_PAPER_API_KEY"],
    secret_key=os.environ["ALPACA_PAPER_SECRET"],
    paper=True,
)
data = OptionHistoricalDataClient(
    api_key=os.environ["ALPACA_PAPER_API_KEY"],
    secret_key=os.environ["ALPACA_PAPER_SECRET"],
)
```

Credentials are **never** committed; live via env vars read by systemd service or cron.

### 5.4 Expected poll loop (pseudocode)

```python
while True:
    now = datetime.utcnow()
    clock = trading.get_clock()
    is_market_hours = clock.is_open
    cadence = 60 if is_market_hours else 300

    try:
        acct = trading.get_account()
        positions = trading.get_all_positions()
        orders = trading.get_orders(filter=GetOrdersRequest(status="all", limit=500))

        snapshot = build_snapshot(acct, positions, orders, now)
        write_metrics_jsonl(snapshot)
        update_dashboard_state(snapshot)

        alerts = evaluate_alerts(snapshot)
        for a in alerts:
            handle_alert(a)          # telegram/email/auto-flatten
    except AlpacaAPIError as e:
        record_api_failure(e)
        maybe_halt_on_outage()

    time.sleep(cadence)
```

Implemented as `compass/monitor/alpaca_poller.py` (cc5 to write, see §9).

---

## 6. Reports

### 6.1 Daily report (EoD 16:45 ET)

File: `compass/reports/daily/daily_{yyyymmdd}.html` + `.json` + one line in `compass/logs/daily_reports.jsonl`

**Contents:**

1. **Header card:** date, market status, regime (BULL/HIGH_VOL/…), VIX/VIX3M, days since paper start
2. **P&L summary:**
   - Day P&L (USD and %)
   - WTD P&L, MTD P&L, since-inception P&L
   - Realised vs unrealised split
3. **Per-stream P&L table:**
   - stream | contracts open | day P&L | since-inception P&L | weight actual vs target
4. **Risk metrics:**
   - Max DD since start
   - Current trailing 20-day DD
   - Rolling 20-day Sharpe
   - Rolling 20-day CAGR annualised
   - Pairwise correlation heatmap (8×8)
5. **Execution quality:**
   - Fill rate (limit-at-mid)
   - Avg slippage vs mid
   - Avg time-to-fill
   - Cancelled orders count
6. **Backtest drift:**
   - Sharpe drift %
   - CAGR drift %
   - DD budget used %
   - Verdict: WITHIN FORECAST / AMBER DRIFT / RED DRIFT
7. **Alerts triggered today** (any AMBER or RED)
8. **Tomorrow's plan:**
   - Signals that will be generated at 09:00 ET tomorrow
   - Any scheduled events (FOMC, opex, earnings) in the window
9. **Footer:** audit hash + config version + code version

### 6.2 Weekly report (Friday 17:00 ET)

File: `compass/reports/weekly/weekly_{yyyymmdd}.html`

**Contents:** everything in the daily report aggregated to week, plus:

1. **Gate report card** — 10 EXP-2670 gates with current values and pass/fail for the 20-day window ending today
2. **Drift visualisation** — line chart of realised Sharpe vs 6.00 forecast, and realised DD vs 4.2% forecast
3. **Incidents log** — every alert from the past 7 days with trigger, action, resolution
4. **Cost attribution** — actual bps of drag this week vs EXP-2420 model (bid-ask / slippage / commission breakdown)
5. **Stream attribution** — per-stream Sharpe this week, correlation to EXP-1220
6. **Carlos-facing decision summary:** "continue paper / continue paper with concern / recommend Phase 9 re-gate / recommend live tranche advance"
7. **Action items** for the week ahead

Weekly reports are emailed to Carlos + ops every Friday at 17:00 ET. The on-call engineer owns the action items.

### 6.3 Incident report (on trigger)

File: `compass/logs/incidents/{yyyymmdd-hhmmss}.json`

Content: trigger name, threshold, actual value, portfolio snapshot at trigger, actions taken, auto vs human-approved, resolution time, learnings.

---

## 7. Backtest-to-Live Comparison Dashboard

A standalone view at `compass/reports/live_vs_backtest.html` regenerated nightly.

### 7.1 Top KPIs (header row)

| | Backtest forecast | Live realised | Drift | Band |
|---|---|---|---|---|
| Sharpe (20d rolling) | 6.00 | … | +/− % | GREEN/AMBER/RED |
| CAGR (20d annualised) | 93% | … | +/− % | … |
| Max DD | 4.2% expected | … | +/− pp | … |
| Total trades | 30-50 / 40d | … | count | … |
| Fill rate | 50%+ | … | +/− pp | … |
| Net Sharpe expected live | 3.5-4.5 | … | within range? | … |

### 7.2 Time-series panels

1. **Equity curve:** actual (solid) vs EXP-2570 forecast (dashed) on same axis, % of days inside ±15% band annotated
2. **Rolling 20-day Sharpe:** actual vs 6.00 horizontal line
3. **Drawdown curve:** actual underwater plot with 3%/5%/8%/12% horizontal thresholds marked
4. **Cost drag:** daily actual bps vs EXP-2420 modelled bps (stacked bar: bid-ask + commission + slippage)
5. **Per-stream P&L contribution:** normalised 100% stacked area chart

### 7.3 Drift diagnostics

Automated natural-language verdict generated daily from the drift metrics:

> **Day 14 of 40 · Sharpe 5.8 vs 6.00 target (−3.3% drift, GREEN) · DD 2.1% vs 4.2% budget (within) · Fill rate 57% (above floor) · Execution A+B+C+D deliver −487 bps vs −503 bps modelled (within 3% of EXP-2470) · VERDICT: ON TRACK FOR LIVE TRANSITION**

### 7.4 Red-flag detector

Separate panel at the bottom that only shows when one or more red flags are active. Each flag links to the incident report and recommends an action.

---

## 8. Escalation Matrix

| Severity | Auto action | Who gets notified | Response time |
|---|---|---|---|
| GREEN | none | log only | — |
| AMBER | Telegram | ops on-call | 1 hour |
| RED — soft | deleverage 0.5× | ops on-call + Carlos via Telegram | 15 min |
| RED — hard | flatten 50% | ops on-call + Carlos via Telegram + email | 5 min |
| RED — halt | flatten 100%, halt | ops + Carlos via Telegram + email + SMS | immediate |
| CRITICAL (data outage, broker down) | halt new orders | ops + Carlos + DevOps | immediate |

**Telegram channel setup:**
- `#pilot-ai-monitor` — all GREEN/AMBER traffic (high volume, muted)
- `#pilot-ai-alerts` — RED only (always unmuted)
- `#pilot-ai-incidents` — auto-flatten and outage events only (always unmuted, Carlos on both alerts and incidents)

**SMS escalation:** triggered only on RED-halt or CRITICAL. Uses Twilio with Carlos's primary number as the single recipient for the first 30 days.

---

## 9. Implementation Plan (for cc5)

Files to create:

```
compass/monitor/
├── __init__.py
├── alpaca_poller.py          main poll loop + metric extraction
├── alert_evaluator.py        threshold checks, band classification
├── circuit_breaker.py        auto-flatten / deleverage actions
├── telegram_client.py        wrap the Telegram Bot API
├── email_client.py           SMTP wrapper (use SES or Mailgun)
├── sms_client.py             Twilio wrapper
├── incident_logger.py        write incident JSON to compass/logs/incidents/
├── report_generator.py       daily + weekly HTML reports
├── drift_detector.py         backtest-vs-live comparison computation
└── dashboard_state.py        in-memory state + SQLite persistence
```

Runtime: a single Python service started by `systemctl start pilot-monitor.service` (systemd unit file committed at `scripts/pilot-monitor.service`). The service:

1. Reads env vars `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TWILIO_*`, `SMTP_*`
2. Starts the Alpaca poll loop (60 s / 300 s cadence per §5.4)
3. Writes every snapshot to `compass/logs/metrics.jsonl`
4. Evaluates alerts on every snapshot
5. At 16:45 ET, generates the daily report
6. At Friday 17:00 ET, generates the weekly report
7. On SIGTERM, flushes state and exits cleanly

**Testing contract:**
- Unit tests for every threshold in §3 — known-input → expected band
- Integration test against a replay file of historical Alpaca responses
- Dry-run mode: `--dry-run` prevents any Alpaca writes (orders) and any Telegram/email/SMS sends, prints instead

**Deployment contract:**
- Runs on the Mac Studio (or equivalent Linux host)
- Target restart time < 10 seconds
- Memory footprint < 200 MB
- CPU < 5% average
- Logs rotate at 100 MB, 30-day retention
- Incident files never rotate (small volume, audit requirement)

---

## 10. Open Questions (route to Carlos before Phase 10)

1. **Secondary broker cutover policy:** automatic on 3 consecutive RED execution days, or manual only?
2. **SMS escalation beyond day 30:** keep Carlos-only, or expand to secondary on-call after Phase 9 graduation?
3. **Data outage threshold:** 5 minutes for IronVault and 3 minutes for Alpaca — should this halt ALL strategies or only open new positions?
4. **Reporting frequency on live day 1-10:** should daily become hourly during the first two weeks?
5. **Audit trail retention:** indefinite for incidents, 30-day rotation for metrics — acceptable for compliance?
6. **Heartbeat alerts:** if the monitor itself crashes, do we need a separate watchdog to alert on missing heartbeats? **Recommendation: yes, a cron job that alerts if no metrics row has been written in the last 5 minutes during market hours.**

---

*This spec is the single source of truth for the monitoring layer. Any change to thresholds, cadences, or escalation paths must be reflected here before the code change is merged.*
