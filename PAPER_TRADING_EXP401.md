# Paper Trading Quickstart — EXP-401 (The Blend)

## What This Trades

**Credit spreads + straddle/strangles on SPY — two complementary strategies in a single portfolio.**

### Credit Spread Component (12% risk per trade)
- Bull market: sells bull put spreads (2% OTM, $12 wide, 15 DTE)
- Bear market: sells bear call spreads (same params)
- High IV + sideways: sells iron condors (4% put / 3% call OTM, 30 DTE)

### Straddle/Strangle Component (3% risk per trade)
- Post-event short strangles: sells straddle/strangles after FOMC/CPI events
- Targets 5 DTE, 4% OTM strikes
- Profits from IV crush after scheduled events
- 55% profit target, 45% stop loss

### Why a Blend?
Credit spreads and straddle/strangles have **low correlation** (-0.25 to +0.27). The SS component scales UP in volatile regimes where CS scales DOWN, smoothing the equity curve. In 2022 (bear market), the blend turned a -1.9% CS year into a +8.1% combined result.

**Validated performance (2020-2025):** +26.9% avg annual after slippage, -7.0% max DD, 0.951 ROBUST score.

---

## Regime Scales

Both strategies adjust position sizing based on detected market regime. These scales were optimized via staged grid search and validated out-of-sample:

| Regime | Credit Spread Scale | Straddle/Strangle Scale |
|--------|-------------------|------------------------|
| Bull | 1.0x (full size) | 1.5x |
| Bear | 0.3x (reduced) | 1.5x |
| High Vol | 0.3x (reduced) | 2.5x (scaled up) |
| Low Vol | 0.8x | 1.0x |
| Crash | 0.0x (no entry) | 0.5x |

Key insight: SS **increases** exposure in high-vol regimes (when IV crush is largest), while CS **decreases** it. This anti-correlation is what makes the blend work.

---

## Prerequisites

1. **Python 3.11+** with dependencies installed
2. **Alpaca paper trading account** with $100K balance
3. **Polygon.io API key** (for live options chain data)
4. **Telegram bot** (optional, for trade alerts)

---

## Setup (5 minutes)

### Step 1: Clone and checkout

```bash
git clone https://github.com/charlesattix/attix-credit-spreads.git
cd attix-credit-spreads
git checkout maximus/champion-config
```

### Step 2: Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### Step 3: Create environment file

```bash
cat > .env.exp401 << 'EOF'
# Alpaca Paper Trading
ALPACA_API_KEY=your_alpaca_paper_api_key_here
ALPACA_API_SECRET=your_alpaca_paper_secret_here

# Polygon (options chain data)
POLYGON_API_KEY=your_polygon_api_key_here

# Telegram Alerts (optional)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
EOF
```

Replace the placeholder values with your actual keys.

### Step 4: Smoke test (dry run)

```bash
python main.py scan --config configs/paper_exp401.yaml --env-file .env.exp401
```

This runs a single scan without placing trades. Verify:
- Alpaca connection successful
- Polygon data loading
- Regime detection working
- No errors in output

### Step 5: Start paper trading

```bash
python main.py scheduler --config configs/paper_exp401.yaml --env-file .env.exp401
```

This starts the automated scheduler:
- Scans **14 times per day** during market hours (ET weekdays)
- Opens CS trades on regime signals, SS trades on event IV crush
- Monitors positions for profit target / stop loss exits
- Sends Telegram alerts on every entry/exit (if configured)

### Step 6: Run in background (production)

```bash
# Using nohup
nohup python main.py scheduler --config configs/paper_exp401.yaml \
  --env-file .env.exp401 > logs/exp401.log 2>&1 &

# Or using screen/tmux
tmux new-session -d -s exp401 \
  'python main.py scheduler --config configs/paper_exp401.yaml --env-file .env.exp401'
```

---

## What to Expect

### Trade Frequency
- **Credit spreads:** ~3-4 trades per month
- **Straddle/strangles:** ~1-2 trades per month (event-driven, around FOMC/CPI)
- Total: **~4-6 trades per month**
- Bear markets: CS trades rare, SS trades continue (different regime response)

### Realistic Returns
- **Backtest says:** +26.9% avg/year (after slippage and commissions)
- **Walk-forward out-of-sample:** validated across 3 windows
- **Expect:** 15-25% annualized in live conditions
- **First few weeks** may show very little — need time for trades to open and close

### Position Profile
- CS trades risk **12%** of starting capital per trade
- SS trades risk **3%** of starting capital per trade
- Max **12 concurrent positions**, max 3 per ticker
- Portfolio heat capped at **40%**
- CS typical hold: **5-15 days** (exits at 55% profit or 1.25x stop)
- SS typical hold: **3-5 days** (exits at 55% profit or 45% stop)

### Straddle/Strangle Mechanics
- **Long straddle/strangle (pre-event):** buys call + put before scheduled events, profits from large moves
- **Short straddle/strangle (post-event):** sells call + put after events, profits from IV crush
- Both legs face the same direction (unlike credit spreads where one leg is short and one is long)
- Debit positions (long): P&L = current value - debit paid
- Credit positions (short): P&L = credit received - cost to close

---

## Monitoring

### Daily Check
```bash
python main.py dashboard --config configs/paper_exp401.yaml --env-file .env.exp401
```

### View Trade Log
```bash
sqlite3 data/pilotai_exp401.db "SELECT * FROM trades ORDER BY opened_at DESC LIMIT 20;"
```

### Telegram Alerts (if configured)
You'll receive:
- Entry alert — new trade opened with strategy type, strikes, credit/debit, risk
- Exit alert — trade closed with P&L, exit reason
- Daily summary — end-of-day P&L and open positions

---

## Safety Rails (Built In)

| Safety Feature | What It Does |
|----------------|-------------|
| `paper_mode: true` | Blocks all live API URLs — cannot accidentally trade real money |
| Kill switch | Halt all trading via DB flag or Telegram command |
| Drawdown CB (40%) | Blocks new entries if portfolio drops 40% from peak |
| Portfolio heat cap (40%) | Max 40% of capital at risk at any time |
| Position limits | Max 12 positions, max 3 per ticker |
| Crash regime (CS=0x) | Credit spreads stop entirely during crash regime |
| Write-ahead logging | Crash recovery — no orphaned orders |
| Isolated DB | EXP-401 gets its own database (`pilotai_exp401.db`) |

---

## Kill Criteria — When to Stop

### HARD STOP
- Drawdown exceeds 25%
- 5+ consecutive losses across both strategies
- System enters trades it shouldn't (regime mismatch, wrong event timing)
- Any execution error or leg mismatch

### PAUSE after 4 weeks if:
- Win rate below 55%
- Annualized return tracking below 8%
- SS trades not firing around events (data issue)
- CS trades significantly diverging from champion-only performance

### SUCCESS after 8 weeks:
- Win rate >65%
- Positive cumulative P&L
- Max drawdown <15%
- Both CS and SS components contributing
- Ready to discuss live capital

---

## Comparison: Champion vs EXP-401

| Metric | Champion (CS only) | EXP-401 (CS + SS) |
|--------|-------------------|-------------------|
| Avg annual return | +32.7% | +26.9% (after slippage) |
| Max drawdown | -12.1% | -7.0% |
| ROBUST score | 0.870 | 0.951 |
| Worst year (2022) | -1.9% | +8.1% |
| Strategies | 1 | 2 |
| Risk per CS trade | 8.5% | 12% |
| Risk per SS trade | — | 3% |
| Regime-adaptive | Yes | Yes (both strategies) |

EXP-401 trades more total return for significantly better drawdown and robustness. The blend's 0.951 ROBUST score is the highest validated configuration.

---

## Key Files

| File | What It Is |
|------|-----------|
| `configs/paper_exp401.yaml` | Paper trading config (this experiment) |
| `configs/champion.json` | Raw champion params from optimization |
| `output/regime_switching_results.json` | Regime scale optimization results |
| `shared/strategy_adapter.py` | Signal/trade conversion (handles straddle legs) |
| `execution/execution_engine.py` | Order submission (two single-leg orders for S/S) |
| `execution/position_monitor.py` | Position monitoring (debit + credit P&L logic) |
| `MASTERPLAN.md` | Project strategy and architecture |

---

## Troubleshooting

### "No straddle/strangle trades being placed"
- SS trades are **event-driven** — they only fire around FOMC/CPI dates
- Check that IV data is available for the target expiration
- Run a manual scan to verify SS signals: `python main.py scan --config configs/paper_exp401.yaml --env-file .env.exp401`

### "No credit spread trades being placed"
- Check regime: system stays out in crash regimes (CS scale = 0.0x)
- Check IV rank: iron condors need IV rank > 45
- Run `python main.py scan` manually to see scan output

### "Alpaca connection failed"
- Verify API keys in `.env.exp401`
- Ensure you're using **paper** keys (not live)
- Check Alpaca status: https://status.alpaca.markets

### "Polygon data errors"
- Verify POLYGON_API_KEY is set
- Check Polygon subscription tier (need options data access)
- Options data requires at least Polygon Starter plan

### "Straddle close order failed"
- Straddle/strangles close as two single-leg orders (call + put)
- If one leg fails, the system attempts to cancel the other (rollback)
- Check Alpaca logs for the specific leg that failed

---

*Generated for EXP-401 deployment — March 2026*
