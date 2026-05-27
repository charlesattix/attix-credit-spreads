# Central Data Architecture — Design Doc

**Author:** Claude (requested by Carlos)
**Date:** 2026-04-20
**Status:** PROPOSAL

## Problem

Experiment data is fragmented across 5 sources:
1. **Alpaca API** — live equity, positions, orders
2. **SQLite DBs** (`data/pilotai_expNNN.db`) — trade history, entry/exit details
3. **registry.json** — experiment config, status, account mapping
4. **sentinel_state.json** — health/alerts state
5. **validate_keys.py output** — key health (ephemeral, not persisted)

After account resets, old trade data in DBs becomes misleading (shows losses from a now-deleted account). The dashboard has no concept of "this experiment was reset on date X."

## Design: Dashboard as Single Source of Truth

### Core Principle

The Railway dashboard (`attix-production.up.railway.app`) becomes the **authoritative view** of all experiment state. The sync pipeline remains the data transport, but gains awareness of resets and key health.

### 1. Reset Tracking

**Add to registry.json** per experiment:

```json
{
  "EXP-400": {
    "reset_history": [
      {
        "date": "2026-04-20",
        "old_account_id": "PA36XFVLG0WE",
        "new_account_id": "PA3ZSXZ5JNEM",
        "reason": "account_reset",
        "starting_equity": 100000
      }
    ]
  }
}
```

**Sync script changes:**
- Include `reset_history` in the push payload
- When building trade stats, partition trades into epochs:
  - `pre_reset`: trades with `entry_date < reset_date`
  - `post_reset`: trades with `entry_date >= reset_date`
- Headline metrics (equity curve, P&L, win rate) use **post-reset data only**
- Pre-reset data available under a "History" toggle

### 2. Live Alpaca Data (always fresh)

Current flow works well:
```
Mac cron (5min) → sync_dashboard_data.py → reads .env.exp* → hits Alpaca API → pushes to Railway
```

**No changes needed** — the sync script already fetches live equity/positions using the keys from `.env.exp*` files, and registry.json already has the correct account IDs.

**Enhancement:** Add `last_synced_at` timestamp to the push payload so the dashboard can show data staleness.

### 3. Key Health Visibility

**Add to sync payload:**

```json
{
  "key_health": {
    "validated_at": "2026-04-20T16:28:00Z",
    "results": {
      "EXP-400": {"status": "ok", "http_code": 200},
      "EXP-800": {"status": "ok", "http_code": 200}
    },
    "all_ok": true
  }
}
```

**Implementation:**
- `sync_dashboard_data.py` already validates keys (it calls Alpaca). Surface the HTTP status in the export.
- Dashboard shows a green/red indicator per experiment.
- If any key returns 401/403, the experiment card shows "KEYS DEAD" badge.

### 4. Dashboard UI Changes

| Section | Source | Notes |
|---------|--------|-------|
| Equity (headline) | Alpaca API (via sync) | Always live, from correct account |
| P&L / Win Rate | SQLite DB | Post-reset only (filter by `entry_date >= reset_date`) |
| Trade History | SQLite DB | Tab: "Current" (post-reset) / "Archive" (pre-reset, greyed) |
| Positions | Alpaca API (via sync) | Live open positions |
| Key Health | validate_keys output | Green/red badge per experiment |
| Last Sync | Push timestamp | "Data as of 2 min ago" |
| Reset Banner | registry.json | "Reset on Apr 20 — metrics start fresh" |

### 5. Implementation Plan

| Step | Effort | Description |
|------|--------|-------------|
| 1 | 30 min | Add `reset_history` to registry.json for the 4 reset experiments |
| 2 | 1 hr | Modify `sync_dashboard_data.py` to partition trades by reset date, include key_health |
| 3 | 1 hr | Modify `web_dashboard/data.py` to filter trades by reset epoch |
| 4 | 30 min | Add reset banner + key health badge to `web_dashboard/html.py` |
| 5 | 15 min | Push updated code → Railway auto-deploys |

**Total: ~3 hours**

### 6. What NOT to Change

- **SQLite DBs stay as-is.** Don't delete pre-reset trades — they're useful for backtesting validation.
- **No new database.** The current SQLite + JSON export + Railway push is sufficient for 6 experiments.
- **No real-time websockets.** 5-minute sync cadence is fine for paper trading.
- **Registry.json stays the config source of truth.** Dashboard reads it, never writes it.

### 7. File Changes Summary

```
experiments/registry.json        — add reset_history per experiment
scripts/sync_dashboard_data.py   — partition by reset, add key_health
scripts/validate_keys.py         — (already done) add --json output mode
web_dashboard/data.py            — filter trades by epoch
web_dashboard/html.py            — reset banner, key badge, staleness indicator
```
