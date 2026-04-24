# Phase 8 Paper Trading Runbook — v8a 8-Stream Portfolio

Operational guide for running the North Star v8a paper trading deployment
on Alpaca. Covers startup, daily operations, monitoring, incident response,
and the go/no-go gate for live deployment.

**Portfolio:** 8-stream v8a + VIX ladder + Ledoit-Wolf risk-parity + 12% vol target + DD circuit breaker
**Broker:** Alpaca commission-free (paper)
**Capital:** $100K simulated, 1x leverage
**Reference backtest:** Sharpe 6.39 net Alpaca (EXP-2850), expected live 3.2-4.5

---

## 1. Prerequisites

### 1.1 Alpaca Paper Account

- [ ] Create paper trading account at <https://app.alpaca.markets>
- [ ] Generate API keys (Settings > API Keys > Generate New Key)
- [ ] Verify key is **paper** — key starts with `PK`, secret starts with a lowercase string
- [ ] Confirm options trading is enabled on the paper account

### 1.2 Environment Variables

Create `.env.exp2300` in the repo root (gitignored):

```bash
# Required — BLOCKING. Paper trading will not start without these.
ALPACA_API_KEY_PAPER=PK...
ALPACA_API_SECRET_PAPER=SK...

# Optional — enhances monitoring but not required for Phase 8 start
POLYGON_API_KEY=...              # $199/mo; required for AUM scaling, optional for Phase 8
TELEGRAM_BOT_TOKEN=...           # alerting (recommended)
TELEGRAM_CHAT_ID=...             # alerting (recommended)
```

Verify they load:

```bash
source .env.exp2300
echo "Alpaca key prefix: ${ALPACA_API_KEY_PAPER:0:4}"
# Should print: Alpaca key prefix: PK..
```

### 1.3 Software Dependencies

```bash
cd /home/node/.openclaw/workspace/pilotai-credit-spreads

# Python 3.11+ required
python3 --version

# Install / verify key packages
pip install alpaca-py pandas numpy scipy scikit-learn pyyaml yfinance

# Verify compass modules load
python3 -c "from compass.exp2690_signal_generators import GENERATOR_REGISTRY; \
             print(f'Registry OK: {len(GENERATOR_REGISTRY)} streams')"
# Expected: Registry OK: 8 streams

python3 -c "from compass.alpaca_connector import AlpacaConnector; print('Connector OK')"
```

### 1.4 Directory Structure

```bash
# These should already exist; create if missing
mkdir -p logs/exp2300
mkdir -p compass/reports/paper_signals
mkdir -p compass/logs
```

### 1.5 Configuration Files

| File | Purpose | Status |
|---|---|---|
| `configs/exp2300_north_star_v6_paper.yaml` | Master portfolio config (weights, sleeves) | Ready |
| `configs/exp2300/exp1220_spy.yaml` | SPY put credit spread sleeve | Ready |
| `configs/exp2300/xlf_cs.yaml` | XLF put credit spread sleeve | Ready |
| `configs/exp2300/xli_cs.yaml` | XLI put credit spread sleeve | Ready |
| `configs/exp2300/vol_arb.yaml` | Cross-vol IV-RV arb sleeve | Ready |
| `configs/exp2300/gld_calendar.yaml` | GLD calendar spread sleeve | Ready |
| `configs/exp2300/slv_calendar.yaml` | SLV calendar spread sleeve | Ready |
| `configs/exp2300/v5_hedge.yaml` | Crisis Alpha v5 hedge sleeve | Ready |

### 1.6 Pre-Flight Checks

Run the go/no-go gate (EXP-2670):

```bash
python3 -m compass.exp2670_paper_gonogo
# Target: OVERALL = GO (or CAUTION with only Alpaca-credential WARNs)
# Report: compass/reports/exp2670_paper_gonogo.html
```

Run the consistency audit (EXP-2900):

```bash
python3 -m compass.exp2900_v8a_consistency_audit
# Target: 36+ PASS, 0 FAIL
# Report: compass/reports/exp2900_v8a_consistency_audit.html
```

Run the dry run (EXP-2860):

```bash
python3 -m compass.exp2860_paper_dry_run
# Target: 7/7 orders validated
```

---

## 2. Starting Paper Trading

### 2.1 Smoke Test (no trades, no API calls)

```bash
./scripts/launch_exp2300.sh smoke
```

Verifies: config loads, all sleeve modules importable, weights sum to 1.0.

### 2.2 Dry Run (signals computed, no trades submitted)

```bash
./scripts/launch_exp2300.sh dry
```

Also available standalone:

```bash
python3 -m compass.exp2830_paper_signal_generator --dry-run
# Prints 8 signal rows to stdout (one per stream)
```

Check for:
- [ ] All 8 streams produce signals (OPEN, NO_TRADE, or SKIP — not ERROR)
- [ ] Strike/expiry values look reasonable vs current market
- [ ] No `ImportError` or `KeyError` in output

### 2.3 Start Paper Trading

```bash
./scripts/launch_exp2300.sh start
```

What happens:
1. Loads `.env.exp2300` credentials
2. Validates `ALPACA_API_KEY_PAPER` and `ALPACA_API_SECRET_PAPER` are set (refuses to start if missing)
3. Launches `compass.exp2300_portfolio_runner` in background
4. Writes PID to `logs/exp2300/runner.pid`
5. Logs to `logs/exp2300/runner.log`

Verify it's running:

```bash
./scripts/launch_exp2300.sh status
```

### 2.4 Daily Signal Generation (Standalone)

If running the signal generator separately (e.g., via cron at 09:25 ET):

```bash
# Generate signals for today
python3 -m compass.exp2830_paper_signal_generator

# Generate signals for a specific date
python3 -m compass.exp2830_paper_signal_generator --date 2026-04-24

# Outputs:
#   compass/reports/paper_signals/signals_YYYY-MM-DD.json
#   compass/logs/paper_signals_audit.jsonl (append)
```

Alternative: the EXP-2690 registry driver (lighter, no regime overlays):

```bash
python3 compass/scripts/generate_daily_signals.py --date 2026-04-24
# Output: compass/reports/daily_signals_YYYYMMDD.jsonl
```

### 2.5 Cron Setup (Mac Studio)

```bash
# Edit crontab
crontab -e

# Add these lines (all times ET):
# 09:25 ET — generate daily signals
25 13 * * 1-5 cd /home/node/.openclaw/workspace/pilotai-credit-spreads && \
  python3 -m compass.exp2830_paper_signal_generator >> logs/exp2300/signals.log 2>&1

# 16:35 ET — end-of-day reconciliation (future: paper_monitor_dashboard)
35 20 * * 1-5 cd /home/node/.openclaw/workspace/pilotai-credit-spreads && \
  python3 -m compass.paper_monitor_dashboard >> logs/exp2300/monitor.log 2>&1
```

### 2.6 Stopping Paper Trading

```bash
./scripts/launch_exp2300.sh stop
```

### 2.7 Viewing Logs

```bash
./scripts/launch_exp2300.sh logs
# or directly:
tail -f logs/exp2300/runner.log
```

---

## 3. Monitoring and Alerts

### 3.1 Daily Monitoring Checklist

**Morning (09:00-09:30 ET — before signals fire):**

| Check | How | Expected |
|---|---|---|
| Process alive | `./scripts/launch_exp2300.sh status` | RUNNING |
| Last signal date | `ls -lt compass/reports/paper_signals/ \| head -3` | Yesterday's date |
| VIX level | Yahoo Finance / any terminal | If VIX > 30: expect reduced sizing via ladder |
| Alpaca account | <https://app.alpaca.markets> dashboard | Equity ~$100K, no unexpected positions |

**After signals fire (09:30-10:00 ET):**

| Check | How | Expected |
|---|---|---|
| Today's signals | `cat compass/reports/paper_signals/signals_$(date +%Y-%m-%d).json` | 8 stream entries |
| Signal quality | Count OPEN vs NO_TRADE vs SKIP | Typical: 2-4 OPEN, rest NO_TRADE |
| Audit log | `tail -8 compass/logs/paper_signals_audit.jsonl` | Today's date, no ERROR actions |

**End of day (16:30+ ET):**

| Check | How | Expected |
|---|---|---|
| Fills | Alpaca dashboard > Orders | All submitted orders filled or cancelled |
| Daily P&L | Alpaca dashboard > Portfolio | Within expected range |
| Drawdown | Compare equity to high-water mark | < 5% (circuit triggers at 3% soft) |

### 3.2 Key Metrics to Track

Maintain a tracking spreadsheet or log (`logs/exp2300/daily_metrics.csv`):

| Metric | How to Compute | Frequency |
|---|---|---|
| Daily return | `(equity_today - equity_yesterday) / equity_yesterday` | Daily |
| Cumulative return | `equity_today / 100000 - 1` | Daily |
| Rolling 20-day Sharpe | `mean(daily_returns[-20:]) / std(daily_returns[-20:]) * sqrt(252)` | Daily |
| Max drawdown | `1 - equity / equity.cummax()` | Daily |
| Open positions count | Alpaca API / dashboard | Daily |
| Fill rate | `filled_orders / submitted_orders` | Weekly |
| Per-stream P&L | Attribution by sleeve (via Alpaca tags or signal logs) | Weekly |
| Pairwise correlation | Rolling 20-day between streams | Weekly |

### 3.3 VIX Ladder Behavior

The VIX ladder (EXP-2820/2850) automatically scales position sizes based on VIX level. No manual intervention needed.

| VIX Level | Scale Factor | What You'll See |
|---|---|---|
| <= 20 | 1.00x | Normal sizing |
| 25 | 0.90x | Slight reduction |
| 30 | 0.75x | Noticeably fewer contracts |
| 35 | 0.60x | Roughly half normal |
| 40 | 0.50x | Half normal |
| 50 | 0.35x | Minimal new positions |
| 60 | 0.25x | Near-flat |
| 70 | 0.15x | Near-flat |
| > 70 | 0.00x | **Full halt** — no new entries |

### 3.4 Circuit Breaker Behavior

| Trigger | Action | Recovery |
|---|---|---|
| DD hits 3% (soft) | Reduce gross exposure 50% | Auto-recovers when DD < 2% |
| DD hits 12% (hard) | **FULL HALT** — flatten all positions | Manual restart required after review |

### 3.5 Telegram Alerts (if configured)

Set up via `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env.exp2300`. Alerts include:
- Trade signals generated (daily summary)
- Risk alerts (DD approaching limits)
- System errors (data feed failures, API issues)

---

## 4. Weekly Review Process

Every Friday after market close. Log results in `logs/exp2300/weekly/YYYY-WW.md`.

### 4.1 Performance vs. Backtest Reference

| Metric | Backtest Reference | Acceptable Range | Hard Reject |
|---|---|---|---|
| 20-day rolling Sharpe | 6.39 (EXP-2850 net) | 3.5 - 8.0 | < 2.0 for 5 consecutive days |
| Annualized CAGR | 118% (EXP-2850 net) | 50% - 150% | < 30% |
| Max DD | 5.12% pooled (EXP-2850) | < 8% | >= 12% (auto-halt) |
| Win rate | 88% (EXP-1220 baseline) | > 60% | < 40% over 20+ trades |
| Total trades/week | ~6-10 expected | 3-15 | 0 for full week (investigate) |
| Fill rate (limit-at-mid) | >= 50% (EXP-2470) | 40-80% | < 30% |

### 4.2 Execution Quality

| Check | Method | Target |
|---|---|---|
| Slippage vs. mid | Compare fill price to NBBO mid at signal time | < 3 cents/contract |
| Alpaca vs. IBKR NBBO | Cross-reference (if IBKR account available) | < 5 cents divergence |
| Order latency | Signal timestamp vs. fill timestamp | < 5 minutes (95th pct) |
| Rejected orders | Alpaca order status log | < 5% rejection rate |

### 4.3 Stream-Level Attribution

Check each of the 8 streams individually:

| Stream | Expected Behavior |
|---|---|
| exp1220 (SPY) | Highest trade count; ~35% of P&L contribution |
| qqq_cs (QQQ) | Similar profile to SPY; ~15% contribution |
| xlf_cs (XLF) | Fewer trades; lower correlation to SPY |
| xli_cs (XLI) | Fewer trades; low correlation |
| gld_cal (GLD) | Calendar spreads; near-zero correlation to equity streams |
| slv_cal (SLV) | Calendar spreads; lowest weight (5%) |
| cross_vol | Weekly cadence (Mondays only); IV-RV pairs |
| v5_hedge | Mostly dormant; activates when VIX > 25 or term structure inverts |

### 4.4 Weekly Review Template

```markdown
# Week N Review — YYYY-MM-DD to YYYY-MM-DD

## Performance
- Weekly return: X.XX%
- Cumulative return (since start): X.XX%
- Rolling 20d Sharpe: X.XX
- Max DD this week: X.XX%
- Trades: N (W wins, L losses)

## Stream Attribution
| Stream | Trades | Win Rate | P&L |
|--------|--------|----------|-----|
| ...    |        |          |     |

## Execution
- Fill rate: XX%
- Avg slippage: X.X cents
- Rejected orders: N

## Incidents
- (none / describe any alerts, outages, or anomalies)

## Decision: CONTINUE / ADJUST / PAUSE
```

---

## 5. Abort Triggers

Any one of these flattens immediately. **Do not override.**

| Trigger | Detection | Action |
|---|---|---|
| DD >= 12% | Automatic (circuit breaker) | All positions closed. Do not restart until root cause analysis. |
| Rolling 4-week Sharpe < 2.0 for 5 consecutive days | Manual check (daily metric log) | Pause trading. Review with Carlos. |
| Alpaca fills > 5 cents off IBKR NBBO on > 20% of orders | Weekly execution review | Switch to IBKR. Investigate Alpaca PFOF. |
| Any Rule Zero violation | Code review / audit | Immediately halt. Investigate synthetic data contamination. |
| Zero trades for 5+ consecutive trading days | Signal log check | Investigate signal generator. May indicate data feed failure. |

### Manual Kill Switch

```bash
# Option 1: Graceful stop via launcher
./scripts/launch_exp2300.sh stop

# Option 2: Close all Alpaca positions directly
python3 -c "
from compass.alpaca_connector import AlpacaConnector
conn = AlpacaConnector(paper_mode=True)
conn.close_all_positions()
print('All positions closed')
"

# Option 3: Nuclear — via Alpaca dashboard
# https://app.alpaca.markets > Positions > Close All
```

After any kill switch activation:
1. Do NOT restart immediately
2. Document in `logs/exp2300/incidents/YYYY-MM-DD.md`
3. Determine root cause (market event vs. system bug vs. strategy failure)
4. Review with Carlos before resuming

---

## 6. Failure Scenarios and Recovery

### Data Feed Failure (Yahoo Finance)

**Symptoms:** Signals show `action: BLOCKED` with `insufficient data` notes.
**Impact:** No new trades opened; existing positions unaffected.
**Recovery:** Self-heals when Yahoo is back. If prolonged (> 4h during market hours), check `yfinance` package version and Yahoo API status.

### Alpaca API Outage

**Symptoms:** `ConnectionError` or `HTTPError` in `logs/exp2300/runner.log`.
**Impact:** Orders not submitted; signals still generated.
**Recovery:** Check <https://status.alpaca.markets>. Existing positions are safe (held at Alpaca). After recovery, verify position state matches signal log.

### Signal Generator Crash

**Symptoms:** No signal file for today in `compass/reports/paper_signals/`.
**Impact:** No trades for the day.
**Recovery:**
1. Check `logs/exp2300/signals.log` for the error
2. Run manually: `python3 -m compass.exp2830_paper_signal_generator --dry-run`
3. Fix the issue, then run without `--dry-run`

### VIX Spike / Flash Crash

**Symptoms:** Multiple streams produce SKIP signals; VIX ladder scaling kicks in.
**Impact:** System automatically reduces exposure. This is correct behavior.
**Recovery:** Do not intervene. The VIX ladder and DD circuit handle this. Monitor DD. If DD hits 12% hard circuit, system auto-flattens.

---

## 7. Go/No-Go Criteria for Live Trading

Graduate from paper to live when ALL of the following are met. These map to the MASTERPLAN Phase 9 gates.

### Minimum Duration

- [ ] **G1:** >= 4 consecutive weeks (20 trading days) of paper trading completed
- [ ] At least 30 round-trip trades executed across all streams

### Performance Gates

- [ ] **G2:** Rolling 20-day Sharpe within +/-15% of 6.39 forecast (floor 5.43, ceiling 7.35) — OR sustained above 3.5 (expected-live floor)
- [ ] **G3:** Rolling 20-day annualized CAGR within +/-20% of 118% (floor 94%, ceiling 142%)
- [ ] **G4:** Max DD <= 8% (expected <= 5.12% with circuit + VIX ladder)
- [ ] **G5:** Circuit breaker <= 1 spurious HALT in 20 days

### Execution Quality Gates

- [ ] **G6:** Limit-at-mid fill rate >= 50% (EXP-2470 technique A)
- [ ] **G7:** Slippage >= 25% lower vs. open-of-day baseline (EXP-2470 technique B)
- [ ] **G8:** Alpaca fills within +/-3 cents/contract of IBKR NBBO (no PFOF tax)

### Risk Gates

- [ ] **G9:** Rolling pairwise correlation < 0.50 in any stress period (EXP-1890 CorrelationMonitor)
- [ ] **G10:** Zero manual trade overrides

### Operational Gates

- [ ] Zero unrecovered system crashes in last 14 days
- [ ] All weekly reviews completed and documented
- [ ] Kill switch tested (manually triggered and verified at least once)
- [ ] EXP-2670 go/no-go re-run returns **OVERALL = GO**

### Pre-Live Prerequisites (before first live dollar)

- [ ] Secondary broker (IBKR Pro) account funded and API-wired as fallback
- [ ] Dollar-notional sizing patch applied (integer-contract sizing is a sub-$1M accuracy issue)
- [ ] Carlos sign-off on the advertised Sharpe headline (6.39 Alpaca vs. 5.20 IBKR vs. 3.2-4.5 expected-live)
- [ ] Live account funded with T1 allocation ($25K at 1x leverage)

### Scaling Schedule (Post-Live)

| Tranche | Capital | Leverage | Gate | Duration |
|---|---|---|---|---|
| T0 Paper | $100K sim | 1x | Phase 8 4-week pass | 4 weeks min |
| T1 Live | $25K | 1x | T0 +/-15% hold | 4 weeks |
| T2 | $100K | 2x | T1 +/-15% hold | 4 weeks |
| T3 | $1M | 2x | T2 hold + no live DD > 8% + Polygon added | 8 weeks |
| T4 | $10M | 3x | T3 hold + SLV <= 3% weight | 8 weeks |
| T5 | $50M | 3x | T4 hold + new high-capacity sleeve | 12 weeks |

---

## Appendix A: 8-Stream Portfolio Summary

| # | Stream | Underlier | Strategy | Weight | Cadence |
|---|---|---|---|---|---|
| 1 | exp1220 | SPY | Put credit spread 28 DTE 5% OTM | 35% | ~Weekly |
| 2 | qqq_cs | QQQ | Put credit spread 28 DTE 5% OTM | 15% | ~Weekly |
| 3 | xlf_cs | XLF | Put credit spread 28 DTE | 10% | ~Weekly |
| 4 | xli_cs | XLI | Put credit spread 28 DTE | 10% | ~Weekly |
| 5 | gld_cal | GLD | Calendar spread 30/60 DTE | 10% | ~Monthly |
| 6 | slv_cal | SLV | Calendar spread 30/60 DTE | 5% | ~Monthly |
| 7 | cross_vol | SPY/QQQ/IWM/EEM | IV-RV relative value pairs | 10% | Weekly (Mon) |
| 8 | v5_hedge | SPY + VIX | Tail puts + VIX calls (hedge) | 5% | Weekly (Mon) |

Mean pairwise correlation: +0.016 (effectively independent diversification).

## Appendix B: Key File Paths

| File | Purpose |
|---|---|
| `.env.exp2300` | Credentials (gitignored) |
| `configs/exp2300_north_star_v6_paper.yaml` | Master portfolio config |
| `configs/exp2300/*.yaml` | Per-sleeve configs |
| `scripts/launch_exp2300.sh` | Launcher (smoke/dry/start/stop/status/logs) |
| `compass/exp2830_paper_signal_generator.py` | Daily signal generator |
| `compass/exp2690_signal_generators.py` | Signal registry (GENERATOR_REGISTRY) |
| `compass/scripts/generate_daily_signals.py` | Lightweight signal driver |
| `compass/alpaca_connector.py` | Alpaca API integration (EXP-2890) |
| `compass/exp2670_paper_gonogo.py` | Go/no-go checklist |
| `compass/exp2900_v8a_consistency_audit.py` | Consistency audit |
| `compass/vix_ladder.py` | VIX ladder helper |
| `compass/portfolio_risk_manager.py` | Risk manager (EXP-1890) |
| `compass/reports/paper_signals/` | Daily signal JSON output |
| `compass/logs/paper_signals_audit.jsonl` | Append-only audit trail |
| `logs/exp2300/runner.log` | Runner process log |
| `logs/exp2300/runner.pid` | Runner PID file |

## Appendix C: Experiment Cross-Reference

| Topic | Key Experiment | Report |
|---|---|---|
| Baseline backtest (v8a + VIX ladder) | EXP-2850 | `compass/reports/exp2850_v8a_with_vix_ladder.json` |
| Walk-forward robustness | EXP-2730 | `compass/reports/exp2730_wf_robustness_v8a_net.json` |
| Transaction cost model | EXP-2420 | `compass/reports/exp2420_transaction_costs.json` |
| DD circuit breaker | EXP-2370 | `compass/reports/exp2370_dd_circuit_breaker.json` |
| Execution optimization | EXP-2470 | `compass/reports/exp2470_execution_optimization.json` |
| Flash crash protection | EXP-2820 | `compass/reports/exp2820_flash_crash_protection.json` |
| Go/no-go checklist | EXP-2670 | `compass/reports/exp2670_paper_gonogo.json` |
| Consistency audit | EXP-2900 | `compass/reports/exp2900_v8a_consistency_audit.json` |
| DD recovery analysis | EXP-2720 | `compass/reports/exp2720_dd_recovery.json` |
| Literature survey (decay) | EXP-2760 | `compass/reports/exp2760_literature_survey.md` |
