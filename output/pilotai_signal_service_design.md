# Attix Signal Service — System Design

**Version:** 1.0
**Date:** 2026-03-07
**Status:** Production

---

## Executive Summary

The Attix Signal Service is a cron-driven data pipeline that:

1. **Collects** daily snapshots of all 57 Attix strategy portfolios
2. **Scores** every ticker using a multi-factor conviction model (frequency × persistence × quality)
3. **Generates alerts** for NEW entries, STRONG conviction, EXIT signals, and notable MOVERS
4. **Delivers** alerts to Telegram with structured, actionable formatting

The system runs autonomously at market open each trading day, archives all data to SQLite, and maintains a rolling signal history that improves in predictive power as it accumulates data.

---

## API Constraints (Confirmed)

**One endpoint exists:** `POST https://ai-stag.attix.com/v2/strategy_recommendation`

All 22 alternative endpoints probed returned HTTP 404. There is no:
- Historical data endpoint
- Date-range query parameter
- `/v1/` API version with different routes

**Implication:** The system builds its own historical database by collecting and archiving daily snapshots. The persistence signal strengthens over time as the archive grows.

**Backfill status:** Not possible via API. The signal service starts building history from Day 1 of deployment. After 30+ days of collection, persistence scoring becomes meaningful.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        CRON (9:35 AM ET)                    │
└─────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     collector.py                            │
│  • Batch-fetch all 57 strategies (6 per request, sequential)│
│  • Validate response completeness                           │
│  • Write to SQLite: strategy_snapshots + snapshot_holdings  │
│  • Idempotent: safe to re-run; skips existing dates         │
└─────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      scorer.py                              │
│  • Compute today's ticker_signals from snapshot_holdings    │
│  • Join with yesterday's signals for Δconviction            │
│  • Persist: frequency, days_in_signal, conviction score     │
└─────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      alerts.py                              │
│  • Compare today vs yesterday signal state                  │
│  • Classify: NEW / STRONG / EXIT / MOVER                    │
│  • Format Telegram messages                                 │
│  • Post via Bot API (requests library, no python-telegram)  │
│  • Log to alerts table (dedup within same day)              │
└─────────────────────────────────────────────────────────────┘
```

---

## Database Schema

```sql
-- One row per strategy per day
CREATE TABLE strategy_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date  DATE    NOT NULL,
    strategy_slug  TEXT    NOT NULL,
    strategy_name  TEXT    NOT NULL,
    total_cost     REAL,
    leftover       REAL,
    n_holdings     INTEGER,
    collected_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(snapshot_date, strategy_slug)
);

-- Per-ticker holdings within each snapshot
CREATE TABLE snapshot_holdings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES strategy_snapshots(id) ON DELETE CASCADE,
    ticker      TEXT    NOT NULL,
    name        TEXT,
    price       REAL,
    quantity    INTEGER,
    weight      REAL,     -- 0.0–1.0 (Attix weights field)
    cost        REAL
);
CREATE INDEX idx_holdings_snapshot ON snapshot_holdings(snapshot_id);
CREATE INDEX idx_holdings_ticker   ON snapshot_holdings(ticker);

-- Portfolio-level quality scores per snapshot
CREATE TABLE snapshot_scores (
    snapshot_id          INTEGER PRIMARY KEY REFERENCES strategy_snapshots(id) ON DELETE CASCADE,
    value_score          REAL,
    growth_score         REAL,
    health_score         REAL,
    momentum_score       REAL,
    past_performance     REAL,
    composite_qscore     REAL   -- computed: (growth*0.35 + momentum*0.25 + value*0.20 + health*0.15 + past*0.05)
);

-- Computed daily ticker conviction signals (materialized)
CREATE TABLE ticker_signals (
    signal_date      DATE    NOT NULL,
    ticker           TEXT    NOT NULL,
    frequency        INTEGER NOT NULL,  -- # portfolios holding today
    total_portfolios INTEGER NOT NULL,  -- total strategies polled that day (57)
    freq_pct         REAL    NOT NULL,  -- frequency / total_portfolios
    avg_weight       REAL    NOT NULL,  -- mean normalized weight across holding portfolios
    weighted_qscore  REAL    NOT NULL,  -- sum(weight_i × qscore_i)
    days_in_signal   INTEGER NOT NULL,  -- consecutive days in signal (persistence)
    conviction       REAL    NOT NULL,  -- final score [0,1]
    PRIMARY KEY(signal_date, ticker)
);
CREATE INDEX idx_signals_date       ON ticker_signals(signal_date);
CREATE INDEX idx_signals_ticker     ON ticker_signals(ticker);
CREATE INDEX idx_signals_conviction ON ticker_signals(signal_date, conviction DESC);

-- Alert history (dedup + audit trail)
CREATE TABLE alerts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_date        DATE    NOT NULL,
    alert_type        TEXT    NOT NULL,  -- NEW | STRONG | EXIT | MOVER_UP | MOVER_DOWN
    ticker            TEXT    NOT NULL,
    conviction_before REAL,
    conviction_after  REAL,
    days_in_signal    INTEGER,
    message           TEXT,
    telegram_sent     INTEGER DEFAULT 0,
    sent_at           TIMESTAMP,
    UNIQUE(alert_date, alert_type, ticker)
);

-- Collection run log (audit + error tracking)
CREATE TABLE collection_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        DATE    NOT NULL,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    status          TEXT    NOT NULL,  -- SUCCESS | PARTIAL | FAILED
    strategies_ok   INTEGER DEFAULT 0,
    strategies_fail INTEGER DEFAULT 0,
    error_msg       TEXT,
    duration_sec    REAL
);
```

---

## Conviction Score Formula

```
conviction(ticker, date) = normalize(
    0.40 × freq_score +
    0.35 × persistence_score +
    0.25 × quality_weighted_score
)
```

Where:
- **`freq_score`** = `frequency / 57` (fraction of portfolios holding ticker today)
- **`persistence_score`** = `min(days_in_signal, 30) / 30` (caps at 30 days)
- **`quality_weighted_score`** = `Σ(portfolio_qscore_i × weight_i)` normalized across all tickers that day

The persistence component means a ticker that has been held for 30+ consecutive days scores 0.35 on that dimension alone — rewarding the API's "conviction through time" rather than just today's snapshot.

**QScore formula (portfolio quality):**
```
qscore = 0.35×growth + 0.25×momentum + 0.20×value + 0.15×health + 0.05×past_performance
         (all on 0-5 scale from Attix stock_score)
```

---

## Alert Types & Thresholds

| Alert | Trigger | Telegram Emoji | Priority |
|-------|---------|---------------|----------|
| `NEW` | Ticker appears in signal for first time (or after >5-day absence) | 🆕 | Medium |
| `STRONG` | conviction ≥ 0.70 for ≥ 3 consecutive days | 🔥 | High |
| `EXIT` | Ticker drops from ALL portfolios (freq → 0) | 🚪 | High |
| `MOVER_UP` | conviction rises ≥ +0.15 in a single day | 📈 | Medium |
| `MOVER_DOWN` | conviction falls ≥ −0.15 in a single day | 📉 | Medium |

**Daily digest** (always sent at end of collection): top-10 tickers by conviction with scores, regardless of changes.

---

## Telegram Message Formats

### NEW Entry
```
🆕 NEW SIGNAL — $ATI
Strategy: Attix Consensus
Conviction: 0.62 | Freq: 9/57 (15.8%)
Portfolios: momentum-investing, growth-investing +7 more
Quality weighted: 0.44
Action: Monitor for entry
```

### STRONG Conviction
```
🔥 STRONG — $VICR (Day 12)
Conviction: 0.74 ▲ (was 0.71)
Freq: 8/57 | Avg Weight: 6.0%
Top portfolios: momentum, growth, sector-rotation
Quality score: 0.74 (high)
```

### EXIT Signal
```
🚪 EXIT — $RELY
Dropped from ALL portfolios today
Was: Conviction 0.45 | 3 portfolios
Held for: 18 days
Action: Consider reducing/closing position
```

### Daily Digest
```
📊 Attix Signal Digest — 2026-03-07

EQUITY SIGNAL (Top 10):
 1. $ATI   Conv: 0.94 | F: 9/57 | Day: 45
 2. $VICR  Conv: 0.74 | F: 8/57 | Day: 42
 3. $MU    Conv: 0.50 | F: 6/57 | Day: 38
 ...

GOLD HEDGE SIGNAL:
 1. $IAU   Conv: 0.53 | F: 4/57
 2. $NEM   Conv: 0.44 | F: 3/57

Market: SPY -1.5% (3mo) | VIX: —
Data: 2026-03-07 | 57/57 strategies OK
```

---

## File Structure

```
pilotai-credit-spreads/
├── pilotai_signal/
│   ├── __init__.py
│   ├── config.py          # API keys, DB path, thresholds (env-based)
│   ├── db.py              # Schema creation, connection, helpers
│   ├── collector.py       # Fetches all 57 strategies → DB
│   ├── scorer.py          # Builds ticker_signals from snapshots
│   ├── alerts.py          # Alert classification + Telegram delivery
│   └── cli.py             # Entry point: `python -m pilotai_signal`
├── scripts/
│   └── run_signal_service.sh   # Cron wrapper
└── data/
    └── pilotai_signal.db  # SQLite database (auto-created)
```

---

## Cron Schedule

```bash
# pilotai_signal cron (add via: crontab -e)
# Runs at 9:35 AM ET Mon–Fri (market open + 5 min for price settling)
35 9 * * 1-5 /path/to/pilotai-credit-spreads/scripts/run_signal_service.sh >> /path/to/logs/signal_service.log 2>&1

# Optional: 4:00 PM ET digest after close
0 16 * * 1-5 /path/to/pilotai-credit-spreads/scripts/run_signal_service.sh --digest-only >> /path/to/logs/signal_service.log 2>&1
```

---

## Backfill Strategy (API Limitation)

The Attix staging API has no historical endpoint — all 22 alternative paths return 404. Historical backfill is therefore **not possible via API**.

**What we do instead:**

1. **Archive immediately from Day 1.** Every collection run creates a permanent record.
2. **Synthetic bootstrap:** The initial collection run loads the snapshot from today into the DB. `days_in_signal = 0` for all tickers on Day 1.
3. **Persistence score matures over 30 days.** The first month has a "training wheels" period where frequency and quality drive conviction; persistence adds weight progressively.
4. **One-time historical import** (optional): If the team can export historical strategy weights from the Attix database directly, the schema supports bulk import via `snapshot_date` override.

---

## Operational Runbook

```bash
# First-time setup
cd /Users/charlesbot/projects/pilotai-credit-spreads
python -m pilotai_signal init          # Create DB schema

# Run collection (also runs scorer + alerts)
python -m pilotai_signal collect       # Full run

# Rebuild signal scores only (no API calls)
python -m pilotai_signal score         # Recompute ticker_signals from existing snapshots

# Send digest manually
python -m pilotai_signal digest        # Post daily digest to Telegram

# View current top signals
python -m pilotai_signal show          # Print top-20 tickers by conviction

# Check collection history
python -m pilotai_signal status        # Show last N collection runs

# Dry run (no DB writes, no Telegram)
python -m pilotai_signal collect --dry-run
```

---

## Dependencies

All from `requirements.txt` (no new heavy deps):
- `requests` — API calls + Telegram Bot API (already in project)
- `sqlite3` — stdlib, no install needed

Optional (not required):
- `python-dotenv` — load `.env` for local dev

---

## Environment Variables

```bash
# Required
PILOTAI_API_KEY=cZZP6he1Qez8Lb6njh6w5vUe
TELEGRAM_BOT_TOKEN=<your-bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>

# Optional overrides
PILOTAI_DB_PATH=/path/to/pilotai_signal.db   # default: data/pilotai_signal.db
PILOTAI_API_URL=https://ai-stag.attix.com/v2/strategy_recommendation
PILOTAI_BATCH_SIZE=6                           # slugs per API request
PILOTAI_REQUEST_TIMEOUT=90                     # seconds per batch
```
