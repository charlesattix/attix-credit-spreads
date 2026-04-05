# IronVault Data Inventory

**Last Updated:** 2026-04-05
**Database:** `data/options_cache.db` (948 MB)
**Source:** Polygon.io historical options data

---

## Database Summary

| Metric | Count |
|--------|-------|
| Total option contracts | 258,606 |
| Total daily bars | 6,045,762 |
| Total intraday bars | 1,591,036 |
| Database size | 948 MB |

---

## Per-Ticker Coverage

### Option Contracts

| Ticker | Contracts | Expirations | First Exp | Last Exp | Status |
|--------|-----------|-------------|-----------|----------|--------|
| **SPY** | 193,272 | 646 | 2020-01-29 | 2026-06-30 | **FULL** — weeklies + monthlies |
| **XLI** | 17,287 | 320 | 2020-01-03 | 2026-06-18 | **FULL** |
| **GLD** | ~13,100+ | 200+ | 2020-01-17 | **2024-06-07+** | **BACKFILLING** — actively fetching to Dec 2025 |
| **XLF** | 9,256 | 320 | 2020-01-03 | 2026-06-30 | **FULL** |
| **QQQ** | 9,194 | 98 | 2020-01-03 | **2023-04-21** | **GAP** — missing May 2023+ |
| **TLT** | 9,185 | 181 | 2020-01-17 | **2024-07-19** | **GAP** — missing Aug 2024+ |
| **SOXX** | 3,460 | 70 | 2020-07-17 | 2026-06-18 | Partial |
| **XLK** | 2,680 | 242 | 2020-01-17 | 2026-06-18 | Partial (few strikes) |
| **XLE** | 1,757 | 181 | 2020-04-17 | 2026-06-30 | Partial (few strikes) |

### Daily Bars (with volume > 0)

| Ticker | Daily Bars | Trading Days | First Date | Last Date |
|--------|------------|-------------|------------|-----------|
| SPY | 4,423,353 | 1,732 | 2019-03-04 | 2026-04-02 |
| QQQ | 386,814 | 832 | 2020-01-02 | 2023-04-21 |
| XLF | 243,583 | 1,571 | 2020-01-02 | 2026-04-02 |
| XLI | 200,761 | 1,571 | 2020-01-02 | 2026-04-02 |
| TLT | 185,357 | 1,144 | 2020-01-02 | 2024-07-19 |
| GLD | 154,290 | 1,058 | 2020-01-02 | 2024-03-15 |
| SOXX | 37,229 | 804 | 2020-02-06 | 2026-04-02 |
| XLE | 20,542 | 1,359 | 2020-01-23 | 2026-04-02 |
| XLK | 18,702 | 1,431 | 2020-01-02 | 2026-04-02 |

### Intraday Bars (5-minute)

| Ticker | Bars | Days | First | Last |
|--------|------|------|-------|------|
| SPY | 1,426,178 | 1,540 | 2020-01-02 | 2026-02-24 |

SPY has 5-minute intraday bars from 09:30 to 16:00 (~78 bars/day).
No intraday data for other tickers.

---

## Critical Data Gaps

### 1. GLD Options — BACKFILL IN PROGRESS

- **Status:** Actively fetching via `fetch_sector_options.py`
- **Progress:** Extended from 2024-03-15 → 2024-06-07+ (and growing)
- **Target:** Through Dec 2025 (~8,290 total contracts)
- **Rate:** ~200 contracts per 5 minutes (~2,400/hr)
- **ETA:** ~3 hours from start for complete coverage

### 2. QQQ Options — Missing May 2023 to Present (35 months)

- **Impact:** Cross-asset pairs with QQQ are limited to pre-Apr 2023
- **Size of gap:** ~35 months (~3,500+ contracts)
- **Severity:** HIGH — QQQ is a key diversification ticker
- **Backfill estimate:** ~2 hours

### 3. TLT Options — Missing Aug 2024 to Present (8 months)

- **Impact:** TLT Iron Condors and TLT-based pairs can't test recent data
- **Size of gap:** ~8 months (~800 contracts)
- **Severity:** MEDIUM — TLT data covers through Jul 2024 (4.5 years)
- **Backfill estimate:** ~20 min

### 4. SPY Intraday — Missing Mar 2026 to Present

- **Impact:** Intraday strategies (EXP-1000, gap fade) lack most recent data
- **Severity:** LOW — 5+ years of intraday data available

---

## Polygon API Status

| Parameter | Value |
|-----------|-------|
| API Key | Set in `.env` (starts with `y3y07kPI...`) |
| Key Status | **ACTIVE** — daily bars work, market status works |
| Options Contract Enumeration | **NOT AVAILABLE** — returns 0 results (likely Starter tier) |
| Individual Contract Bars | **WORKS** — can fetch specific contract OHLCV |
| Rate Limit | ~5 calls/sec (free/starter tier) |

### Backfill Strategy

The current Polygon tier can fetch daily bars for individual contracts but
cannot enumerate new contracts. To backfill:

**Option A: Upgrade to Options tier**
- Enables `/v3/reference/options/contracts` enumeration
- Cost: ~$200/month
- Time to backfill: ~3 hours for all gaps

**Option B: Manual contract enumeration**
- Build OCC symbols from known strike grids + expiration dates
- Use `/v2/aggs/ticker/{OCC_SYMBOL}/range/1/day/{from}/{to}`
- Works with current tier but requires strike guessing

**Option C: Alternative data source**
- CBOE DataShop, OptionMetrics, or IVolatility
- Higher cost but complete data

---

## Backfill Scripts

| Script | Purpose | Status |
|--------|---------|--------|
| `scripts/backfill_polygon_cache.py` | SPY option daily bars | Complete (SPY up to date) |
| `scripts/fetch_sector_options.py` | Multi-ticker option fetch | Available but needs Options tier |
| `scripts/iron_vault_setup.py` | DB setup and validation | Working |

### Running a Backfill

```bash
# If Polygon Options tier is available:
python3 scripts/fetch_sector_options.py --ticker GLD --start 2024-03-16 --end 2025-12-31
python3 scripts/fetch_sector_options.py --ticker TLT --start 2024-07-20 --end 2025-12-31
python3 scripts/fetch_sector_options.py --ticker QQQ --start 2023-04-22 --end 2025-12-31

# SPY is already up to date — no backfill needed
```

---

## Data Quality Notes

1. **All option prices are from Polygon** — real market data, not synthetic
2. **Volume data available** for SPY (reliable), sector ETFs (partial)
3. **Open Interest** is NULL for most contracts (Polygon starter tier limitation)
4. **IronVault policy:** cache miss returns `None` (trade skipped), NEVER falls back to synthetic
5. **Split/adjustment:** Polygon handles corporate actions automatically
6. **Expiration types:** Mix of monthlies (3rd Friday) and weeklies (every Friday for SPY)
