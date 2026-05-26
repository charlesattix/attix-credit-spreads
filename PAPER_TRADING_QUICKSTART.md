# 🚀 Paper Trading Quickstart — Champion Config

## What This Trades

**Regime-adaptive credit spreads + iron condors on SPY.**

- Bull market → sells bull put spreads (2% OTM, $12 wide, 15 DTE)
- Bear market → sells bear call spreads (same params)
- High IV + sideways → sells iron condors (4% put / 3% call OTM, 30 DTE)
- Regime detected via 80-day MA + RSI + VIX structure

**Backtested performance (2020-2025):** +32.7% avg annual, -12.1% max DD, 0.870 ROBUST score.

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
git clone https://github.com/charlesattix/pilotai-credit-spreads.git
cd pilotai-credit-spreads
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
cat > .env.champion << 'EOF'
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
python main.py scan --config configs/paper_champion.yaml --env-file .env.champion
```

This runs a single scan without placing trades. Verify:
- ✅ Alpaca connection successful
- ✅ Polygon data loading
- ✅ Regime detection working
- ✅ No errors in output

### Step 5: Start paper trading

```bash
python main.py scheduler --config configs/paper_champion.yaml --env-file .env.champion
```

This starts the automated scheduler:
- Scans **14 times per day** during market hours (ET weekdays)
- Opens trades when entry signals fire
- Monitors positions for profit target / stop loss exits
- Sends Telegram alerts on every entry/exit (if configured)

### Step 6: Run in background (production)

```bash
# Using nohup
nohup python main.py scheduler --config configs/paper_champion.yaml \
  --env-file .env.champion > logs/champion.log 2>&1 &

# Or using screen/tmux
tmux new-session -d -s champion \
  'python main.py scheduler --config configs/paper_champion.yaml --env-file .env.champion'
```

---

## What to Expect

### Trade Frequency
- **~3-4 trades per month** (41 trades/year average in backtest)
- Some months may have 6-8 trades, others 1-2
- 2022-style bear markets: very few trades (system stays out)

### Realistic Returns
- **Backtest says:** +32.7% avg/year
- **Walk-forward out-of-sample:** +9.5% avg/year
- **Expect:** somewhere in between, ~15-25% annualized
- **First few weeks** may show very little — need time for trades to open and close

### Position Profile
- Each trade risks **8.5%** of starting capital (credit spread) or **3.5%** (iron condor)
- Max **10 concurrent positions**, max 2 per ticker
- Portfolio heat capped at **40%**
- Typical hold time: **5-15 days** (exits at 55% profit or 1.25x stop)

---

## Monitoring

### Daily Check
```bash
python main.py dashboard --config configs/paper_champion.yaml --env-file .env.champion
```

### View Trade Log
```bash
sqlite3 data/attix_champion.db "SELECT * FROM trades ORDER BY opened_at DESC LIMIT 20;"
```

### Telegram Alerts (if configured)
You'll receive:
- 📈 **Entry alert** — new trade opened with strike, expiry, credit, risk
- 📉 **Exit alert** — trade closed with P&L, exit reason
- 📊 **Daily summary** — end-of-day P&L and open positions

---

## Safety Rails (Built In)

| Safety Feature | What It Does |
|----------------|-------------|
| `paper_mode: true` | Blocks all live API URLs — cannot accidentally trade real money |
| Kill switch | Halt all trading via DB flag or Telegram command |
| Drawdown CB (40%) | Blocks new entries if portfolio drops 40% from peak |
| Portfolio heat cap (40%) | Max 40% of capital at risk at any time |
| Position limits | Max 10 positions, max 2 per ticker |
| Write-ahead logging | Crash recovery — no orphaned orders |
| Isolated DB | Each experiment gets its own database |

---

## Kill Criteria — When to Stop

### 🔴 HARD STOP
- Drawdown exceeds 30%
- 5+ consecutive losses
- System enters trades it shouldn't (regime mismatch)
- Any execution error

### 🟡 PAUSE after 4 weeks if:
- Win rate below 60%
- Annualized return tracking below 10%
- Trade count 50%+ off from expectations

### 🟢 SUCCESS after 8 weeks:
- Win rate >70%
- Positive cumulative P&L
- Max drawdown <20%
- → Ready to discuss live capital

---

## Key Files

| File | What It Is |
|------|-----------|
| `configs/paper_champion.yaml` | Paper trading config (this experiment) |
| `configs/champion.json` | Raw champion params from optimization |
| `output/regime_adaptive_validation.json` | Full backtest validation results |
| `output/champion_report.html` | Formatted performance report |
| `output/champion_trade_log.json` | Per-trade backtest log |
| `output/paper_trading_proposal.html` | Detailed deployment proposal |
| `MASTERPLAN.md` | Project strategy and architecture |

---

## Troubleshooting

### "No trades being placed"
- Check regime: system stays out in bear markets (by design)
- Check IV rank: iron condors need IV rank > 45
- Check DTE: may not find options at exact target DTE
- Run `python main.py scan --config configs/paper_champion.yaml --env-file .env.champion` manually to see scan output

### "Alpaca connection failed"
- Verify API keys in `.env.champion`
- Ensure you're using **paper** keys (not live)
- Check Alpaca status: https://status.alpaca.markets

### "Polygon data errors"
- Verify POLYGON_API_KEY is set
- Check Polygon subscription tier (need options data access)
- Options data requires at least Polygon Starter plan

---

*Generated by Maximus Cruz — March 12, 2026* 🛡️
