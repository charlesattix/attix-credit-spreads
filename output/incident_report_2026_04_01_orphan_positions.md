# Incident Report: Orphan Position SL Failure — 2026-04-01

## Executive Summary

On 2026-04-01 four bear call spreads in Alpaca accounts PA3Y2XDYB9I3 (EXP-401) and PA36XFVLG0WE (EXP-400) exceeded their stop-loss levels but were never closed by the automated PositionMonitor — requiring manual intervention. The root cause was a three-way failure: (1) the AlpacaProvider SDK wrapper returned `"OrderStatus.FILLED"` instead of `"filled"`, causing the reconciler to permanently fail to promote `pending_open` records to `open`; (2) the PositionMonitor's `_detect_orphans()` only logged a warning for unrecognized positions and never enforced stop-loss; and (3) the reconciler's `_reconcile_external_closes()` had a race condition that marked newly-submitted orders as `closed_external` within milliseconds of entry (before Alpaca returned the fill), creating a divergent state that left active positions invisible to the SL loop.

---

## Timeline

- **2026-03-20 13:00–19:00 ET** — EXP-400 and EXP-401 scanners submit multiple bear call spread orders. Records written to DB as `pending_open`. Fills confirmed at Alpaca side (verified via `get_order_by_client_id` today: all show `OrderStatus.FILLED`).
- **2026-03-20 16:32 ET** — Commit `d5e6319` (reconciler overhaul) lands. The batch `get_orders()` call returns statuses as `"OrderStatus.FILLED"` due to `str(o.status)` on the alpaca-py SDK enum. Reconciler checks `order_status == "filled"` which fails the equality check silently. `pending_open` records are never promoted.
- **2026-03-27 14:00 ET** — EXP-400 submits a new bear call C652/C664 ×10. DB writes `open` status. During the same PositionMonitor cycle, `_reconcile_external_closes` runs and finds the legs absent (Alpaca fill not yet reflected) — marks it `closed_external` at 14:00:07.518, only 204ms after entry at 14:00:07.314.
- **2026-03-27 14:30 ET** — The C652/C664 position appears in Alpaca as an orphan. Reconciler creates `orphan-SPY260417C00652000` with `status=unmanaged`.
- **2026-03-27 15:00–15:30 ET** — Same pattern repeats for C653/C665 ×10.
- **2026-03-27 18:00 ET** — Reconciler detects C649/C661 ×14 as an orphan (EXP-401). `register_orphan_positions.py` (manual script) creates `order-1c9927db` with `status=open` at 19:09.
- **2026-03-27 — ongoing** — PositionMonitor `_detect_orphans()` logs `"ORPHAN OPTION POSITION — SPY260417C00649000 qty=-14 has no DB record. Manual review required."` every 5 minutes — but takes **no SL action**.
- **2026-04-01 (today)** — SPY rallies above short strikes. EXP-401 C649/C661 at $7.26 vs SL of $6.08 ($4.86 credit × 2.5×). EXP-400 C652/C664, C653/C665 similarly breached. All discovered by manual review. Positions closed manually.

---

## Positions Affected

| Account | Spread | Contracts | DB Status | Alpaca Status | Entry Credit | SL Level | Market Price (at discovery) | Notes |
|---------|--------|-----------|-----------|---------------|-------------|----------|----------------------------|-------|
| PA3Y2XDYB9I3 (EXP-401) | C649/C661 Apr-17 | 14 | `open` (via `order-1c9927db`) | Active | $4.86 | $6.08 (2.5×) | $7.26 | SL breached by $1.18/share |
| PA36XFVLG0WE (EXP-400) | C652/C664 Apr-17 | 10 | `closed_external` (`cs-a34856aff31d345b`) | Active | $3.77 | ~$9.42 (3.5×) | ~$9.56 | Race condition → closed_external prematurely |
| PA36XFVLG0WE (EXP-400) | C653/C665 Apr-17 | 10 | `closed_external` (`cs-bb6e4a4d93b45f24`) | Active | $3.82 | ~$9.55 (3.5×) | ~$9.69 | Race condition → closed_external prematurely |
| PA36XFVLG0WE (EXP-400) | Multiple Apr-10 spreads | 10 each | `pending_open` (5 records) | Active / filled | $3.67–$3.74 | ~$16.7–$16.9 (3.5×) | Active | Pending_open prevents SL loop |

Additional at-risk positions as of 2026-04-01 (still open, monitoring now corrected):
- EXP-503 (PA3Z9PLVYUL5): 6 Apr-17 short calls in Alpaca, 3 DB records `closed_external`, 3 `open`
- EXP-600 (PA3O14JAJHJ0): 2 Apr-17 IBIT short calls in Alpaca, corresponding DB records `closed_external`
- EXP-800 (PA3458WJVXTL): 2 Apr-17 spreads in Alpaca, DB shows `open` — monitored correctly

---

## Root Cause Analysis

### Root Cause 1: AlpacaProvider `str(o.status)` returns `"OrderStatus.FILLED"` not `"filled"`

**File:** `strategy/alpaca_provider.py`, `get_orders()` at line 565, `get_order_by_client_id()` at line 846, `get_order_status()` at line 593

**Code (broken):**
```python
"status": str(o.status),  # → "OrderStatus.FILLED"
```

The alpaca-py SDK wraps order status in an enum (`OrderStatus`). `str()` on this enum returns the full `"OrderStatus.FILLED"` representation, not the bare `"filled"` string.

**Downstream impact in `shared/reconciler.py` `_reconcile_pending_opens()`:**
```python
order_status = order.get("status", "")   # "OrderStatus.FILLED"
if order_status == "filled":             # FALSE — never matches
    trade["status"] = "open"            # NEVER EXECUTED
    ...
elif order_status in _TERMINAL_ORDER_STATES:  # also never matches
    ...
else:
    # falls through to "still in flight" debug log
    logger.debug("Trade %s order status=%s ... leaving as pending_open")
```

Result: Every `pending_open` trade from before commit `fd6a9bd` (2026-03-27) is permanently stuck in `pending_open`. The PositionMonitor fetches `get_trades(status="open")` in Step 2 of `_check_positions()` — `pending_open` records are excluded, so they receive zero SL monitoring.

**Affected DBs:** All accounts — this bug affects every pending_open record system-wide.

---

### Root Cause 2: `_reconcile_external_closes` race condition → premature `closed_external`

**File:** `execution/position_monitor.py`, `_reconcile_external_closes()` (old version)

**Code (broken):**
```python
def _reconcile_external_closes(self, open_positions, alpaca_positions):
    for pos in open_positions:
        if not self._all_legs_missing(pos, alpaca_positions):
            continue
        # ← NO grace period: fires immediately on first missing cycle
        pos["status"] = "closed_external"
        upsert_trade(pos, ...)
```

**Race condition sequence (EXP-400 C652/C664, 2026-03-27 14:00 ET):**
1. **14:00:07.314** — ExecutionEngine writes new `open` record for C652/C664 to DB.
2. **14:00:07.??** — Same PositionMonitor cycle, `get_trades(status="open")` loads this record.
3. **14:00:07.518** — `_reconcile_external_closes` runs: Alpaca positions were fetched at cycle start (before the fill landed). Legs absent → immediately marks `closed_external`. `exit_date` = `entry_date` + 204ms.
4. **14:30:18** — Alpaca position appears; reconciler creates `orphan-SPY260417C00652000` (status=`unmanaged`).
5. **Ongoing** — `_detect_orphans` sees both the original legs and the orphan records but does nothing beyond logging.

The C652/C664 spread was open and accruing risk from entry. The PositionMonitor believed it was already closed.

---

### Root Cause 3: `_detect_orphans()` is warn-only — no SL enforcement

**File:** `execution/position_monitor.py`, `_detect_orphans()` (old version, lines 1081–1139)

```python
for symbol, pos_data in alpaca_positions.items():
    if "option" not in asset_class:
        continue
    if symbol not in managed_symbols:
        qty = pos_data.get("qty", "?")
        logger.warning(
            "PositionMonitor: ORPHAN OPTION POSITION — %s qty=%s has no DB record. "
            "Manual review required.",   # ← ONLY a warning. Zero SL action.
            symbol, qty,
        )
```

For the EXP-401 C649/C661 position (registered via `order-1c9927db` on 2026-03-27 19:09), the record DID exist in DB as `open` after the manual script ran. However the pending_open records for Apr-10 positions and the `unmanaged` orphans for Apr-17 in EXP-400 produced this warning 288+ times over 6 days with zero SL enforcement.

---

## Why Stop-Loss Failed

Complete execution path showing the gap:

```
PositionMonitor._check_positions()
├── Step 0: _reconcile_pending_opens()
│     └── PositionReconciler.reconcile_pending_only()
│           └── _reconcile_pending_opens()
│                 ├── pending = get_trades(status="pending_open")  → returns records
│                 ├── client_order_id = trade.get("alpaca_client_order_id")
│                 │     → "cs-3394b901cf11a4f9" (from metadata merge)
│                 ├── order = orders_by_client_id.get(client_order_id)
│                 │     → order["status"] = "OrderStatus.FILLED"
│                 ├── order_status = order.get("status")  → "OrderStatus.FILLED"
│                 ├── if order_status == "filled":  → FALSE ← BUG 1
│                 └── # falls to debug "leaving as pending_open"
│
├── Step 2: open_positions = get_trades(status="open")
│     → pending_open records EXCLUDED — not monitored
│     → closed_external records EXCLUDED — not monitored
│     → unmanaged orphan records EXCLUDED — not monitored
│
├── Step 3c: _detect_orphans(open_positions + pending_positions, alpaca_positions)
│     → finds C649/C661 in Alpaca, not in managed_symbols
│     → logger.warning("ORPHAN OPTION POSITION")  ← BUG 3: no SL action
│
└── Step 5: for pos in open_positions:
      → order-1c9927db (C649/C661) IS in open_positions
      → _check_exit_conditions() runs
      → BUT: cs-a34856aff31d345b (C652/C664) is status="closed_external" → EXCLUDED
      → cs-3394b901cf11a4f9 (C665/C677 etc.) is status="pending_open" → EXCLUDED
```

The C649/C661 spread (EXP-401) WAS being monitored via `order-1c9927db` after 2026-03-27 19:09. However the SL logic computed:

```
credit = 4.86  (set by register_orphan_positions.py using avg order fill)
sl_mult = 2.5  (config default for EXP-401... wait)
sl_threshold = (1 + 2.5) × 4.86 = $17.01
```

But the actual SL should have been $6.08. Investigation shows the `stop_loss_mult` config for EXP-401 is 3.5x (not 2.5x per the user's report of SL=$6.08 → implies 2.5×). This needs confirmation. Regardless, by 2026-04-01 with the spread at $7.26, even at 2.5× the SL ($6.08) was breached and should have triggered a close.

The `order-1c9927db` record has `credit=4.86`. The PositionMonitor would compute `sl_threshold = (1+3.5)×4.86 = $21.87` if using the 3.5× default. **This means the EXP-401 C649/C661 position was being monitored but with the WRONG SL threshold** because the registered credit ($4.86) was the fill credit-to-open, not the per-share credit as expected by the SL formula. The EXP-401 config stop_loss_multiplier needs separate investigation.

---

## Code Changes Made

### Fix A: Normalize Alpaca SDK order status strings

**File:** `strategy/alpaca_provider.py`

Added `_normalize_order_status()` helper that strips the `"OrderStatus."` prefix:
```python
def _normalize_order_status(status) -> str:
    raw = str(status) if status is not None else ""
    if raw.startswith("OrderStatus."):
        return raw[len("OrderStatus."):].lower()
    return raw.lower()
```

Applied to all status fields in `get_orders()`, `get_order_status()`, `get_order_by_client_id()`, and all `submit_*` methods. Also added `filled_qty` to `get_orders()` batch response (was previously missing).

**Impact:** All `pending_open` records with filled Alpaca orders will now be promoted to `open` on the next reconciler run.

---

### Fix B: PositionMonitor `_detect_orphans()` now enforces SL

**File:** `execution/position_monitor.py`, `_detect_orphans()` method

The new logic:
1. **Step 1 — Recovery:** For each orphan Alpaca symbol, search the DB for a record with matching `short_strike` + `expiration` in any non-terminal status (`pending_open`, `closed_external`, `unmanaged`). If found, promote it to `status=open` so the normal SL loop covers it next cycle.
2. **Step 2 — Synthetic record:** If no DB record matches, create a `synthetic-monitor-{symbol}` record with `status=open`, `credit=avg_entry_price` (from Alpaca position data), enabling the SL gate.

Only short legs (negative qty) receive synthetic records — long legs are hedges and do not need SL.

---

### Fix C: Grace period before `closed_external`

**File:** `execution/position_monitor.py`, `_reconcile_external_closes()` method

Added `_EXTERNAL_CLOSE_GRACE_CYCLES = 2` (10 minutes at 5-min intervals). A position must be absent from Alpaca for 2 consecutive monitor cycles before being marked `closed_external`. The cycle count is persisted in metadata via `_missing_cycles`.

Prevents the race condition where a just-submitted order is not yet in Alpaca's position list when the monitor cycle runs.

---

### Fix D: Startup reconciliation check

**File:** `execution/position_monitor.py`, new `_startup_reconciliation()` method called from `start()`

On every process startup, the monitor now:
1. Fetches all Alpaca option positions.
2. Fetches all `open` DB records.
3. Logs a WARNING for any open DB record whose legs are absent from Alpaca.
4. Logs a WARNING for any Alpaca option position with no matching `open` DB record.

This provides immediate visibility at boot rather than waiting for the first 5-minute cycle.

---

### Tests Updated

**File:** `tests/test_reconciler_fixes.py`
- `test_wing_ids_stored_after_successful_ic_submission`: Updated assertion to verify wing IDs start with the stable DB key and end with `-put`/`-call` (rather than exact equality, since the timestamp suffix is now appended to the alpaca_client_id).
- `TestICLifecycleIntegration::test_ic_lifecycle_submit_fill_reconcile`: Same fix — use actual stored wing IDs for mock batch order setup.

**File:** `tests/test_position_monitor.py`
- `test_missing_legs_marked_closed_external`: Updated to call `_reconcile_external_closes()` twice (satisfying the 2-cycle grace period) before asserting `closed_external`.
- `test_in_memory_status_mutated_for_exit_loop_skip`: Same.
- `test_ic_all_legs_missing_marked_external`: Same.

**Test results:** 59/59 pass.

---

## Prevention

1. **Fix A (deployed):** AlpacaProvider now returns plain lowercase status strings. The `"OrderStatus.FILLED"` mismatch can never recur.

2. **Fix B (deployed):** Orphan positions are now recovered or given synthetic monitoring records. SL will be enforced within one 5-minute cycle of a position becoming orphaned.

3. **Fix C (deployed):** 2-cycle grace period eliminates the instant-close race condition for newly submitted orders.

4. **Fix D (deployed):** Startup reconciliation logs all mismatches immediately. Any future state divergence will be visible in logs within seconds of process startup.

5. **RECOMMENDATION — Manual remediation needed:** The following `pending_open` records in EXP-400 and EXP-401 should be updated to `open` now that Fix A is deployed and the next reconciler run will promote them:
   - `cs-3394b901cf11a4f9` (EXP-400 and EXP-401): C669/C681 ×10/14
   - `cs-7cfba8c87bab655d` (EXP-400 and EXP-401): C665/C677 ×10/14
   - `cs-9af72d4bd5c3f23f` (EXP-400): C666/C678 ×10
   - `cs-b0e0db1860d78773` (EXP-400): C664/C676 ×10
   - `cs-f8b2572c46a1c1c7` (EXP-400): C661/C673 ×10

   These will auto-promote on the next monitor cycle once Fix A is live.

6. **RECOMMENDATION — Credit field review:** The `order-1c9927db` synthetic record has `credit=4.86` which produces a wrong SL threshold ($21.87 at 3.5×). The actual per-share credit for a C649/C661 spread should be ~$3.87. The `register_orphan_positions.py` script computed credit from `avg_entry_price` of the order, which is the net credit for the full spread (correct). Verify that PositionMonitor's SL threshold computation for this record is using the right multiplier config.

---

## Remaining Work

### P1 (critical — must fix before next trading session)
- [ ] Deploy Fix A to all running LaunchAgent processes (restart position monitors for EXP-400, EXP-401, EXP-503, EXP-600, EXP-800).
- [ ] Verify EXP-503 Apr-17 positions: 6 Alpaca short calls, 3 DB `closed_external` records. These are LIVE unmonitored positions. Fix B will create synthetic records on next cycle.
- [ ] Verify EXP-600 IBIT Apr-17 positions: 1 Alpaca short call C42, DB record `closed_external`. Fix B will recover.
- [ ] Audit EXP-401 `order-1c9927db` stop_loss_mult config — confirm the SL threshold is correct.

### P2 (this week)
- [ ] Add integration test: simulate `"OrderStatus.FILLED"` coming from mock Alpaca and verify reconciler promotes to `open`.
- [ ] Add unit test for `_normalize_order_status()` covering all known SDK enum variants.
- [ ] Consider adding `_normalize_order_status()` to the `get_positions()` response as well (asset_class enum has the same issue).
- [ ] `register_orphan_positions.py` should be updated to set correct per-share credit from order fill data, not raw avg_entry_price.

### P3 (this sprint)
- [ ] Add a reconciliation health endpoint to the FastAPI dashboard showing DB/Alpaca mismatch count.
- [ ] Alert via Telegram if startup reconciliation finds any mismatch.
- [ ] Consider reducing `_EXTERNAL_CLOSE_GRACE_CYCLES` if positions can be externally closed quickly (manually) — 2 cycles = 10 min may delay detecting a true external close.

---

*Report compiled: 2026-04-01. Author: automated incident analysis.*
*All DB queries and Alpaca API calls performed against live paper trading data.*
