# EXP-600 Pre-Market Readiness Report
**Experiment:** EXP-600 — IBIT Adaptive
**Created by:** charles
**Check date:** 2026-03-22 (Sunday)
**Market opens:** Monday 2026-03-23 at 9:15 ET

---

## Executive Summary

**Status: ✅ READY FOR MARKET OPEN** — with one pre-open action completed.

One blocker was found and fixed during this check: a wrong env var key name
(`ALPACA_SECRET_KEY` → `ALPACA_API_SECRET`) was causing Alpaca to run in
alert-only mode. Fixed and service restarted. All other checks pass cleanly.

---

## Check Results

### 1. Config Validation

```
python scripts/preflight_check.py configs/paper_exp600.yaml
→ PREFLIGHT OK: configs/paper_exp600.yaml
```

| Field | Value | Status |
|-------|-------|--------|
| `experiment_id` | EXP-600 | ✅ |
| `paper_mode` | true | ✅ |
| `db_path` | data/exp600/pilotai_exp600.db | ✅ |
| `ticker` | IBIT | ✅ |
| `strategy.regime_mode` | ma | ✅ |
| `strategy.otm_pct` | 0.10 (10%) | ✅ |
| `strategy.spread_width` | 5 ($5) | ✅ |
| `strategy.target_dte` | 14 | ✅ |
| `strategy.min_dte / max_dte` | 10 / 21 | ✅ |
| `risk.max_risk_per_trade` | 15.0% | ✅ |
| `risk.sizing_mode` | kelly | ✅ |
| `risk.kelly_fraction` | 1.0 | ✅ |
| `risk.profit_target` | 30% | ✅ |
| `risk.stop_loss_multiplier` | 2.5x | ✅ |
| `risk.max_positions` | 5 | ✅ |
| `iron_condor.enabled` | false | ✅ |
| `straddle_strangle.enabled` | false | ✅ |
| `ml_enhanced.enabled` | false/absent | ✅ |

---

### 2. Alpaca Credentials & Account

**⚠️ BUG FOUND AND FIXED:**
`.env.exp600` had `ALPACA_SECRET_KEY` but the credential loader expects
`ALPACA_API_SECRET` (consistent with all other experiments). Added
`ALPACA_API_SECRET` alias to `.env.exp600`. Service restarted at 14:54 ET.

**Post-fix log confirmation:**
```
Alpaca connected | Account: ***JHJ0 | Status: ACTIVE | Cash: $100,000.00 | Options Level: 3
AlpacaProvider initialized (paper=True)
PositionMonitor started | profit_target=30% | SL=2.5x | manage_dte=disabled
```

| Field | Value | Status |
|-------|-------|--------|
| Account number | PA3O14JAJHJ0 | ✅ matches registry.json |
| Account status | ACTIVE | ✅ |
| Portfolio value | $100,000.00 | ✅ |
| Buying power | $200,000.00 | ✅ (2× for paper margin) |
| Options approval level | Level 3 | ✅ (spread trading enabled) |
| Pattern day trader | False | ✅ |
| Paper mode | true | ✅ (no real money at risk) |

---

### 3. IBIT Options Data Availability

Source: Polygon REST API (for data quality check) + yfinance (live scanner fallback)

**Data pipeline:** OptionsAnalyzer → yfinance fallback (no `data.polygon.api_key`
in config — same behavior as EXP-400/401/503, all use yfinance for live chains)

| Check | Result | Status |
|-------|--------|--------|
| IBIT last price | $39.78 | ✅ |
| Total options contracts (yfinance) | 395 | ✅ |
| Available expirations | 27 | ✅ |
| Expirations in DTE 10–21 range | 3 | ✅ |

**Expirations in target DTE range (10–21):**

| Expiration | DTE | Put Strikes | Call Strikes | 10% OTM Puts | 10% OTM Calls |
|------------|-----|-------------|--------------|--------------|---------------|
| 2026-04-01 | 10  | 21 | 25 | 8 | 8 |
| 2026-04-02 | 11  | 52 | 55 | 15 | 24 |
| 2026-04-10 | 19  | 44 | 52 | 15 | 21 |

**Best available at DTE≈target (Apr 10, DTE=19):**
- 10% OTM put: strike=$35.5, bid=$0.69, ask=$0.72 (4% b/a spread — acceptable)
- 10% OTM call: 21 contracts available, good liquidity

**⚠️ Liquidity note:** IBIT does not have daily expirations like SPY. The DTE
window (10–21) currently contains only 3 expirations with a gap from Apr 2 to
Apr 10. The strategy will prefer Apr 10 (DTE=19) as closest to target DTE=14
among liquid expirations. This is normal for ETF options and within the config
window — no blocker.

---

### 4. Scheduler Timing

Scheduler source: `shared/scheduler.py` — SCAN_TIMES hardcoded.

**Monday scan slots (ET):**
```
09:00  pre_market (data warm-up, no trades)
09:15  ← FIRST SCAN of the week — IBIT will be analyzed here
09:45  scan
10:00 … 15:30  every 30 min (14 scans total)
16:15  daily_report
```

**Current scheduler state (from log):**
```
Scheduler started — 17 slots per trading day
Startup delay: waiting 30s before first scan cycle
Next slot at 2026-03-23 09:00 ET [pre_market]
```

✅ First trade opportunity: **Monday 9:15 ET**

---

### 5. launchd Service

```
launchctl list | grep exp600
→ 20549  0  com.attix.exp600
```

| Check | Value | Status |
|-------|-------|--------|
| Loaded | yes | ✅ |
| PID | 20549 | ✅ running |
| Last exit code | 0 | ✅ |
| KeepAlive | true | ✅ auto-restarts on crash/reboot |
| ThrottleInterval | 10s | ✅ prevents rapid crash loops |
| WorkingDirectory | /Users/charlesbot/projects/attix-credit-spreads | ✅ |
| Log path | ~/logs/exp600.log | ✅ |
| plist in LaunchAgents | ✓ | ✅ will load on boot |

---

### 6. Full Pipeline Simulation

Ran outside-of-market-hours to verify the pipeline executes cleanly:

```
[1] DataCache initialized ✓
[2] CreditSpreadStrategy initialized: regime_mode=ma ✓
[3] OptionsAnalyzer: polygon=False, tradier=False, fallback=yfinance ✓
[4] IBIT price data: 251 bars, last=$39.78 ✓
[5] Technical analysis: MA50=$43.22, price_above_MA=False, trend=neutral ✓
[6] Options chain: 395 contracts retrieved via yfinance ✓
[7] MarketSnapshot built: price=$39.78, regime=bear ✓
    Signals: 0 (market closed — correct behavior ✓)
```

**Direction at open:** IBIT ($39.78) is **below MA50 ($43.22)** → regime = **bear**
→ Monday scans will look for **bear call spreads** (10% OTM above price)

**Sizing estimate at open:**
- Risk per trade: 15% × $100,000 = $15,000
- Spread width: $5 → max loss per contract = $500
- Max contracts: min(30 calc, 25 cap) = **25 contracts** = $12,500 max risk per position
- Max positions: 5 → max total portfolio risk = $62,500 (62.5% heat ceiling)
- Portfolio heat cap: 40% in config → actual max risk ≤ $40,000 at any time

---

### 7. Log Health Check

Log path: `~/logs/exp600.log`
Previous run (10:38): AlpacaProvider failed — `alert-only mode` (env bug, now fixed)
Current run (14:54): ✅ Full initialization confirmed

**Latest log tail (post-fix restart):**
```
Alpaca connected | Account: ***JHJ0 | Status: ACTIVE | Cash: $100,000.00 | Options Level: 3
All components initialized successfully
Reconciliation complete: nothing to do
PositionMonitor started | profit_target=30% | SL=2.5x | manage_dte=disabled
Scheduler started — 17 slots per trading day
Next slot at 2026-03-23 09:00 ET [pre_market]
```

No errors. No warnings after the env fix. ✅

---

### 8. Buying Power Verification

| Item | Value |
|------|-------|
| Account buying power | $200,000 |
| Required per position (worst case) | $12,500 (25 contracts × $500) |
| Required for max 5 positions | $62,500 |
| Portfolio heat cap (40%) | $40,000 effective limit |
| Buying power sufficient? | ✅ Yes — 16× headroom |

---

## Issues Found

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `.env.exp600` used `ALPACA_SECRET_KEY` instead of `ALPACA_API_SECRET` | 🔴 BLOCKER | ✅ FIXED — `ALPACA_API_SECRET` added, service restarted at 14:54 |
| 2 | No `POLYGON_API_KEY` in `.env.exp600` | ℹ️ INFO | ✅ NOT NEEDED — live scanner uses yfinance (same as all other experiments) |
| 3 | IBIT has no DTE=14 expiration — nearest is Apr 10 (DTE=19) | ℹ️ INFO | ✅ Within config window (10–21). Normal for non-SPY ETF. |

---

## Pre-Open Action Items

- [x] Fix `ALPACA_API_SECRET` env var and restart service
- [ ] Verify Telegram alerts fire at 9:15 scan (first real scan Monday)
- [ ] After first scan, check `~/logs/exp600.log` for `IBIT:` lines
- [ ] Compare EXP-600 first scan output against EXP-503 (both running same day)

---

## What Will Happen at 9:15 ET Monday

1. Scheduler fires `SLOT_SCAN`
2. `_analyze_ticker('IBIT')` called
3. yfinance fetches 1y IBIT price data
4. MA50 computed: if price still below MA50 → **bear regime** → bear call spreads only
5. yfinance fetches IBIT options chain (~395 contracts)
6. Strategy filters for DTE 10–21, 10% OTM call strikes
7. If a viable spread found (min_credit_pct=3%, score threshold met): generates signal
8. Position sizing: Kelly × 15% × equity → contract count (capped at 25)
9. If Alpaca has capacity (heat < 40%, positions < 5): order submitted
10. Telegram alert fires with trade details

*If no signal: log shows "No opportunities found for IBIT" — normal for bear day with low IV.*

---

*Report generated: 2026-03-22. Checked by: charles.*
*Next check: review ~/logs/exp600.log after Monday 9:15 ET first scan.*
