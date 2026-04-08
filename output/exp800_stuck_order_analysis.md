# EXP-800 Stuck Pending Order — Root Cause Analysis & Fix

**Date**: 2026-03-30
**Trade ID**: `cs-exp800-7114f3bb77d8`
**Stuck since**: 2026-03-28 13:15 UTC (9:15 AM ET)
**Resolved**: 2026-03-30 (manually cleared + code fix applied)

---

## The Symptom

Every EXP-800 scan since 2026-03-28 09:15 ET produced:

```
Order: {"status": "duplicate", "client_order_id": "cs-exp800-7114f3bb77d8",
        "message": "trade already exists with status=pending_open"}
```

The scanner found a valid candidate (SPY Apr-17 bear_call, short=647/long=659)
each scan but could never submit because the duplicate check blocked it.

---

## DB State at Time of Investigation

```
id      : cs-exp800-7114f3bb77d8
status  : pending_open          ← never transitioned
ticker  : SPY
type    : bear_call
short   : 647.0 / long: 659.0
exp     : 2026-04-17
contracts: 5
credit  : 4.86
entry   : 2026-03-28T13:15:01.156377+00:00
alpaca_client_order_id: None    ← never written to Alpaca
alpaca_status         : None    ← Alpaca never received the order
```

**Alpaca account** (`PA3D44G9ZYRC`): zero positions, zero orders. The order was
never submitted to Alpaca.

---

## Root Cause

### Bug A — Early return after DB write doesn't update status

`ExecutionEngine.submit_opportunity()` follows a "write-DB-first" pattern designed
to prevent orphaned Alpaca orders on crash. The flow is:

```
1. Duplicate check
2. Pre-submission CB check (safe — before DB write)
3. DB write → status = "pending_open"     ← record written here
4. Feature logging (non-fatal)
5. Dry-run check
6. Market hours check (Alpaca clock)      ← RETURNS HERE if closed ← BUG
7. Post-submission CB check               ← same bug
8. Submit to Alpaca
```

At step 6, when `is_open=False`, the code returned `{"status": "market_closed"}`
**without updating the DB status from `pending_open`**. The record stayed
`pending_open` indefinitely.

The trade was written at **09:15 ET on a Monday** — 15 minutes before market
open (9:30 ET). The market_closed guard fired correctly, but the DB was not
cleaned up.

The same bug existed for `drawdown_cb_tripped` returns at step 7.

### Bug B — Duplicate check has no stale-pending recovery

The duplicate check excluded only `("rejected", "cancelled", "failed_open")`:

```python
if existing and existing.get("status") not in ("rejected", "cancelled", "failed_open"):
    return {"status": "duplicate", ...}   # pending_open → blocked forever
```

Once a `pending_open` record existed, every subsequent scan was blocked with
no path to recovery short of manual DB intervention.

---

## The Fix

### Immediate remediation
Updated the stuck trade directly in SQLite:
```sql
UPDATE trades
SET status='failed_open',
    exit_reason='stale_pending_open: market_closed_at_submission_9:15ET_2026-03-28_manually_cleared'
WHERE id='cs-exp800-7114f3bb77d8';
```

### Code changes — `execution/execution_engine.py`

**1. Added `_mark_pending_failed(client_id, reason)` helper**
Updates a `pending_open` record to `failed_open` atomically. Called before any
early return that happens after the DB write.

**2. Patched `market_closed` return**
```python
# Before (buggy):
if is_open is False:
    return {"status": "market_closed", ...}   # DB left as pending_open

# After (fixed):
if is_open is False:
    self._mark_pending_failed(client_id, f"market_closed: next_open={next_open}")
    return {"status": "market_closed", ...}
```

**3. Patched `drawdown_cb_tripped` return** — same fix applied.

**4. Patched `dry_run` return** — same fix applied.
(dry_run with an Alpaca provider initialised would also leave `pending_open` records.)

**5. Added stale-pending recovery to the duplicate check** (defense-in-depth)
Any `pending_open` record older than 60 minutes is treated as stale: it is
automatically transitioned to `failed_open` and the submission is retried.
This catches any future `pending_open` that slips through an unhandled path.

```python
PENDING_STALE_MINUTES = 60
if existing_status == "pending_open":
    age_minutes = (now - entry_dt).total_seconds() / 60
    if age_minutes > PENDING_STALE_MINUTES:
        upsert_trade({"id": client_id, "status": "failed_open",
                      "exit_reason": f"stale_pending_open: {age_minutes:.0f}min"}, ...)
        existing_status = "failed_open"   # allow submission to proceed
```

---

## Order Lifecycle (corrected)

```
pending_open  → submitted  → open  → closed_profit / closed_loss
                                   → closed_external
            ↘ failed_open          (Alpaca rejected, exception, market_closed,
                                    drawdown_cb, dry_run, stale recovery)
            ↘ dry_run              (legacy path — now also clears DB)
```

The key invariant: **every `pending_open` record must transition to another
status before `submit_opportunity()` returns.** Crashes are the only legitimate
exception (the write-DB-first pattern handles that: the reconciler/monitor
will find the `pending_open` and check Alpaca to resolve it).

---

## Verification

After the immediate DB fix and code change, a dry-run confirmed the scanner
produces a candidate normally with no duplicate block:

```json
{
  "status": "dry_run",
  "opp": {
    "ticker": "SPY",
    "type": "bear_call",
    "expiration": "2026-04-17",
    "short_strike": 647.0,
    "long_strike": 659.0,
    "credit": 2.95,
    "contracts": 4,
    "kelly_pct": 4.0,
    "kelly_note": "cb_tier0: full Kelly=4.0%"
  }
}
```

---

## Prevention

| Scenario | Before | After |
|----------|--------|-------|
| Market closed at scan time | `pending_open` forever | `failed_open` immediately |
| Drawdown CB trips after DB write | `pending_open` forever | `failed_open` immediately |
| Dry-run with Alpaca connected | `pending_open` forever | `failed_open` immediately |
| Any other unhandled path | `pending_open` forever | Stale recovery after 60 min |

The 60-minute stale threshold is conservative: EXP-800 scans every 30 minutes,
so at most one missed scan before automatic recovery.
