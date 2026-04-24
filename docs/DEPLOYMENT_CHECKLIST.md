# Deployment Checklist — North Star v8a Paper Trading

**For:** Carlos Cruz (owner) / operator
**Portfolio:** North Star v8a — 8-stream + VIX ladder + Ledoit-Wolf risk-parity
**Config:** `configs/exp2300_north_star_v6_paper.yaml`
**Launcher:** `scripts/launch_exp2300.sh`
**Last Updated:** 2026-04-24
**MASTERPLAN ref:** v13, Phase 8

---

## Current State

| Component | Status |
|---|---|
| 8-stream signal harness (EXP-2690) | READY |
| Alpaca connector (EXP-2620, 552 lines) | READY |
| VIX ladder (EXP-2820) | READY |
| Walk-forward validation (EXP-2850, net Sharpe 6.39) | PASSED |
| Consistency audit (EXP-2900, 36/39 PASS, 0 FAIL) | PASSED |
| Dry run (EXP-2860, 7/7 orders validated) | PASSED |
| Go/no-go gate (EXP-2670, 5/6 PASS) | **1 WARN: Alpaca API creds** |
| **Alpaca paper API keys** | **NOT PROVISIONED — sole blocker** |

---

## Phase 0: Before You Start

- [ ] Read this entire checklist once before doing anything
- [ ] Confirm machine has Python 3.11+ and internet access
- [ ] Confirm you have (or can create) Alpaca paper trading API credentials
- [ ] Block 1 hour for initial setup

---

## Phase 1: Provision Alpaca Paper API Keys (5 min)

1. Log in to <https://app.alpaca.markets>
2. Switch to **Paper Trading** environment (top of dashboard)
3. Go to **API Keys** → **Generate New Key**
4. Copy the **API Key ID** and **Secret Key** (secret shown only once)
5. Verify the key starts with `PK` (paper) — **NOT** `AK` (live)

Create `.env.exp2300` in the project root:

```bash
cat > .env.exp2300 << 'EOF'
# North Star v8a Paper Trading — EXP-2300
ALPACA_API_KEY_PAPER=PK_your_key_here
ALPACA_API_SECRET_PAPER=your_secret_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Optional but recommended
POLYGON_API_KEY=your_polygon_key_here
EOF
```

**Verify the key works:**

```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv('.env.exp2300')
import os, requests
r = requests.get('https://paper-api.alpaca.markets/v2/account',
    headers={'APCA-API-KEY-ID': os.environ['ALPACA_API_KEY_PAPER'],
             'APCA-API-SECRET-KEY': os.environ['ALPACA_API_SECRET_PAPER']})
if r.status_code == 200:
    d = r.json()
    print(f'OK — Account {d[\"account_number\"]}, Equity \${float(d[\"equity\"]):,.2f}')
else:
    print(f'FAIL — {r.status_code}: {r.text}')
"
```

Expected: `OK — Account ..., Equity $100,000.00`

---

## Phase 2: Pre-Flight Verification (10 min)

### 2.1 Smoke Test

```bash
./scripts/launch_exp2300.sh smoke
```

This validates:
- [x] Config file `configs/exp2300_north_star_v6_paper.yaml` loads
- [x] All 8 per-sleeve configs load (`configs/exp2300/*.yaml`)
- [x] IronVault `data/options_cache.db` is present and has data
- [x] Python imports for all 8 signal modules succeed
- [x] `.env.exp2300` credentials are readable
- [ ] Alpaca API connectivity (requires keys from Phase 1)

All checks must show `PASS`. If any show `FAIL`, fix before proceeding.

### 2.2 Dry Run (signals without orders)

```bash
./scripts/launch_exp2300.sh dry
```

This runs:
- `compass/scripts/generate_daily_signals.py --date <today>`
- Produces 1 JSON line per stream (8 total) in `compass/reports/daily_signals_<date>.jsonl`
- Validates each signal against the Alpaca connector (order construction) **without submitting**
- Verifies VIX ladder exposure computation

Expected output: 8 signal lines, 0 errors.

### 2.3 Re-Run Go/No-Go Gate

```bash
python3 -c "
from compass.exp2670_go_nogo import run_gonogo
report = run_gonogo()
for check in report['checks']:
    print(f\"  {check['status']:4s} | {check['name']}\")
print(f\"\\nOVERALL: {report.get('overall', 'CHECK')}\")
"
```

All 6 checks must show `PASS` (the Alpaca WARN should now be PASS with real keys).

---

## Phase 3: Launch Paper Trading (5 min)

### 3.1 Start

```bash
./scripts/launch_exp2300.sh start
```

This:
- Loads `.env.exp2300`
- Starts `compass/scripts/generate_daily_signals.py` daily at 09:00 ET
- Routes signals through `compass/exp2890_alpaca_connector.py` to Alpaca paper API
- Writes logs to `logs/exp2300/`
- Writes PID to `logs/exp2300/runner.pid`

### 3.2 Verify It's Running

```bash
./scripts/launch_exp2300.sh status
```

Also check:
```bash
tail -20 logs/exp2300/runner.log
```

Expected: `[INFO] EXP-2300 paper trading started` and signal generation output.

### 3.3 Confirm First Signal Cycle

After market open on the first trading day:
```bash
# Check for signal generation
ls -la compass/reports/daily_signals_*.jsonl | tail -1

# Inspect signals
cat compass/reports/daily_signals_$(date +%Y%m%d).jsonl
```

Each line should have: `stream`, `action` (OPEN/CLOSE/HOLD), `ticker`, `legs`, `confidence`.

---

## Phase 4: Daily Monitoring Routine (5 min/day)

### Every Trading Day

| # | Check | Command | Expected |
|---|-------|---------|----------|
| 1 | Process alive | `./scripts/launch_exp2300.sh status` | `RUNNING` |
| 2 | Signals generated today | `ls compass/reports/daily_signals_$(date +%Y%m%d).jsonl` | File exists |
| 3 | Alpaca positions | See health check below | 0-8 open positions |
| 4 | Rolling Sharpe | See monitoring script below | > 2.0 (20-day rolling) |
| 5 | Drawdown | Compare equity to high-water mark | < 12% |

### Health Check Command

```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv('.env.exp2300')
import os, requests
headers = {'APCA-API-KEY-ID': os.environ['ALPACA_API_KEY_PAPER'],
           'APCA-API-SECRET-KEY': os.environ['ALPACA_API_SECRET_PAPER']}

# Account
r = requests.get('https://paper-api.alpaca.markets/v2/account', headers=headers)
d = r.json()
equity = float(d['equity'])
print(f'Equity: \${equity:,.2f}')
print(f'Buying Power: \${float(d[\"buying_power\"]):,.2f}')

# Positions
r = requests.get('https://paper-api.alpaca.markets/v2/positions', headers=headers)
positions = r.json()
print(f'Open positions: {len(positions)}')
for p in positions:
    pnl = float(p['unrealized_pl'])
    print(f'  {p[\"symbol\"]:20s} qty={p[\"qty\"]:>4s}  P&L=\${pnl:+,.2f}')
"
```

---

## Phase 5: Abort Triggers

If ANY of these fire, **stop paper trading** and investigate:

| Trigger | Threshold | How Detected | Action |
|---------|-----------|-------------|--------|
| Drawdown | ≥ 12% | Alpaca equity vs HWM | `./scripts/launch_exp2300.sh stop` + investigate |
| Rolling Sharpe | < 2.0 for 5 consecutive days | Manual calculation | Reduce to 1 stream (SPY only) |
| Fill quality | > 5¢ off NBBO on > 20% of orders | Alpaca fill reports | Switch to IBKR |
| Rule Zero violation | Any synthetic data in signals | Code review | Immediate halt |
| System crash | 3 consecutive days of no signals | Log review | Fix + restart |

### Emergency Stop

```bash
# Graceful
./scripts/launch_exp2300.sh stop

# Flatten all positions immediately
python3 -c "
from dotenv import load_dotenv; load_dotenv('.env.exp2300')
import os, requests
headers = {'APCA-API-KEY-ID': os.environ['ALPACA_API_KEY_PAPER'],
           'APCA-API-SECRET-KEY': os.environ['ALPACA_API_SECRET_PAPER']}
r = requests.delete('https://paper-api.alpaca.markets/v2/positions',
                     headers=headers, params={'cancel_orders': 'true'})
print(f'Flatten: {r.status_code} — {r.text}')
"
```

---

## Phase 6: Weekly Review (30 min)

Every Friday after market close:

### 6.1 Performance vs Reference

| Metric | Reference (EXP-2850) | Acceptable Range | Your Value |
|--------|---------------------|-----------------|------------|
| 20-day rolling Sharpe | 6.39 | > 3.0 | ______ |
| Cumulative return | 118% ann. | positive after week 2 | ______ |
| Max drawdown | 5.1% | < 12% | ______ |
| Trades this week | ~3-5 per stream | > 0 total | ______ |
| Signal generation | 8 lines/day | 8/8 every day | ______ |

### 6.2 Per-Stream Check

| Stream | Source | Expected Freq | Check |
|--------|--------|--------------|-------|
| exp1220 (SPY spreads) | IronVault | 1-2 trades/month | Signals in JSONL |
| xlf_cs | IronVault | 1-2 trades/month | Signals in JSONL |
| xli_cs | IronVault | 1-2 trades/month | Signals in JSONL |
| qqq_cs | IronVault | 1-2 trades/month | Signals in JSONL |
| gld_cal | Yahoo + futures | Weekly rebalance | Advisory signal |
| slv_cal | Yahoo + futures | Weekly rebalance | Advisory signal |
| cross_vol | IronVault + Yahoo | Weekly (Mondays) | Advisory signal |
| v5_hedge | Yahoo multi-ETF | Daily regime check | Advisory signal |

### 6.3 Document

Create `logs/exp2300/weekly/YYYY-WW.md` with: P&L, notable trades, any incidents, decision (continue/adjust/pause).

---

## Phase 7: Graduation to Live (After 4+ Weeks)

### Minimum Requirements (ALL must pass)

- [ ] 4+ consecutive weeks (20+ trading days) of paper data collected
- [ ] Rolling 20-day Sharpe within ±15% of EXP-2570 net 6.00 forecast
- [ ] Zero abort triggers fired during paper window
- [ ] EXP-2670 go/no-go re-run returns **OVERALL=GO**
- [ ] Carlos sign-off on headline number (Alpaca 6.39 vs IBKR 5.20 vs expected-live 3.2-4.5)
- [ ] Secondary broker (IBKR Pro) account funded and API-wired as fallback
- [ ] Dollar-notional sizing patch applied (integer-contract sizing is fine for ≤ $1M)

### Live Scaling Schedule

| Tranche | Capital | Leverage | Gate |
|---------|---------|----------|------|
| T0 Paper | $100K sim | 1× | This checklist Phase 7 |
| T1 Live | $25K | 1× | T0 ±15% hold, 4 weeks |
| T2 | $100K | 2× | T1 ±15% hold, 4 weeks |
| T3 | $1M | 2× | T2 hold + no live DD > 8% + Polygon added, 8 weeks |
| T4 | $10M | 3× | T3 hold + SLV ≤ 3% weight, 8 weeks |
| T5 | $50M | 3× | T4 hold + new high-capacity sleeve, 12 weeks |

### To Switch to Live

1. Change `mode: paper` → `mode: live` in `configs/exp2300_north_star_v6_paper.yaml`
2. Create `.env.exp2300.live` with **live** Alpaca keys (starts with `AK`, not `PK`)
3. Start with T1 capital ($25K, 1× leverage) — do NOT skip tranches
4. Monitor daily with heightened attention for the first 2 weeks

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start | `./scripts/launch_exp2300.sh start` |
| Stop | `./scripts/launch_exp2300.sh stop` |
| Status | `./scripts/launch_exp2300.sh status` |
| Logs | `./scripts/launch_exp2300.sh logs` |
| Smoke test | `./scripts/launch_exp2300.sh smoke` |
| Dry run | `./scripts/launch_exp2300.sh dry` |
| Flatten all | See Phase 5 emergency stop |
| Generate signals manually | `python3 compass/scripts/generate_daily_signals.py --date YYYY-MM-DD` |

---

## Key File Paths

| File | Purpose |
|------|---------|
| `configs/exp2300_north_star_v6_paper.yaml` | Master portfolio config |
| `configs/exp2300/*.yaml` | Per-sleeve configs (7 files) |
| `.env.exp2300` | **Alpaca paper credentials (YOU CREATE THIS)** |
| `compass/scripts/generate_daily_signals.py` | Daily signal generator (8 streams) |
| `compass/exp2690_signal_generators.py` | Signal registry (8 functions) |
| `compass/exp2620_alpaca_connector.py` | Alpaca paper API integration (552 lines) |
| `compass/vix_ladder.py` | VIX exposure ladder (EXP-2820) |
| `compass/portfolio_risk_manager.py` | Cross-stream sizer + circuit breakers |
| `compass/metrics.py` | Canonical Sharpe/CAGR/DD calculations |
| `scripts/launch_exp2300.sh` | Launcher (smoke/dry/start/stop/status/logs) |
| `logs/exp2300/` | Logs, PID file, health snapshots |
| `data/options_cache.db` | IronVault option database (276K contracts) |

---

## Portfolio Architecture Reference

```
8 Streams (all real IronVault + Yahoo data)
├── exp1220    SPY put credit spread 28 DTE 5% OTM
├── xlf_cs     XLF delta-targeted put credit spread
├── xli_cs     XLI delta-targeted put credit spread
├── qqq_cs     QQQ put credit spread 28 DTE 5% OTM
├── gld_cal    GLD − GC=F futures roll harvest
├── slv_cal    SLV − SI=F futures roll harvest
├── cross_vol  SPY/QQQ/IWM/EEM IV−RV arbitrage
└── v5_hedge   13-ETF CTA with stress gate

Overlays
├── Ledoit-Wolf risk-parity weights (EXP-2450)
├── 12% annual vol target (EXP-2600)
├── VIX ladder: 9-breakpoint step-linear (EXP-2820)
│   VIX ≤20→1.0×  25→0.9×  30→0.75×  35→0.6×
│   40→0.5×  50→0.35×  60→0.25×  70→0.15×  >70→0.0×
├── DD circuit breaker: 3% soft / 12% hard (EXP-2370)
└── Execution stack: limit-at-mid + patient + combo (EXP-2470)
    Net drag: 890 bps/yr (Alpaca commission-free)
```

**North Star v8a NET performance (EXP-2850):**
Pooled Sharpe 6.39, median fold 7.18, CAGR 118%, max DD 5.1%.
Expected live (0.5-0.7× decay): Sharpe 3.2-4.5, CAGR 60-80%.

---

*This checklist supersedes all previous deployment documents (EXP-880, Ultimate Portfolio, Combined Portfolio). The only valid deployment target is North Star v8a via `scripts/launch_exp2300.sh`.*
