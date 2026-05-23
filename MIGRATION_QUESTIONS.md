# Migration Questions for Carlos

## Q1 — Dividend-adjustment behavior change (Phase 1 finding)

**RESOLUTION (2026-05-22, Carlos via Charles):** ACCEPTED. Polygon's splits-only
adjustment is the more correct semantics for an options-trading system — option
strikes are not dividend-adjusted, so historical MAs/RSI on splits-only series
compare like-for-like to live strike levels. The previous yfinance behavior was
a latent bug that this migration silently fixes. No code change.

---

**Context:** The previous `DataCache` used `yfinance.Ticker(t).history(period='1y')`,
which defaults to `auto_adjust=True`. yfinance with `auto_adjust=True` applies
**both split and dividend back-adjustment** to historical OHLC.

Polygon's `adjusted=true` (the default we send) applies **split adjustment only**.

**Measured impact (2026-05-22, last 120 trading days, Close):**
- SPY: yfinance-default vs Polygon ≈ **0.57% constant offset** before recent
  dividend dates
- TLT: yfinance-default vs Polygon ≈ **1.89% constant offset** before recent
  distributions
- ^VIX, ^VIX3M, ^GSPC: no dividends → identical (after one ^VIX 2026-02-06
  vendor data discrepancy)

**Apples-to-apples test (`yfinance auto_adjust=False` vs Polygon):**
all four reference tickers agree within 0.1% on every bar except one ^VIX
day (vendor mismatch — not the migration's doing).

**The semantic change:** after this migration, every alert/scanner reading
historical SPY/TLT/sector-ETF prices will see prices ~0.5–2% higher in the
past than before. This shifts all backward-looking indicators (MA20, MA50,
MA200, RSI14, percent-change) by the same fraction.

**Decision needed:** is this acceptable? My read:

1. **For options/strikes**, split-only is more correct — option strikes are not
   dividend-adjusted, so MAs computed on split-only series compare like-for-like
   to live strike levels. This argues **the previous behavior was a latent bug**
   and the migration silently fixes it.
2. **For threshold triggers** (e.g. "price > MA200"), the constant offset is in
   the same direction on both numerator and denominator → ratios are unchanged.
   So the bull/bear regime detection should be unaffected.
3. **For absolute price comparisons** (e.g. "price < $X"), magnitudes shift.
   I don't see any such comparison in the live path, but worth a sanity check.

**My recommendation:** accept the change (it's the more correct semantics for
options analysis). Document it in `MIGRATION_NOTES.md`. No code change.

If you want strict bit-identical behavior, the alternative is to apply the
yfinance dividend adjustment manually — but that re-introduces a yfinance
dependency on the live path, defeating the migration.

---

## Q2 — Phase 2 BLOCKED: Polygon plan lacks upcoming earnings dates

**Probed endpoints (2026-05-22):**

| Endpoint | Status | Notes |
|---|---|---|
| `/vX/reference/tickers/{T}/events` | OK | Only `ticker_change` events — no earnings |
| `/vX/reference/tickers/{T}/earnings` | 404 | Endpoint does not exist |
| `/v1/meta/symbols/{T}/earnings` | 404 | Legacy endpoint removed |
| `/benzinga/v1/earnings?ticker={T}` | 403 NOT_AUTHORIZED | Subscription required |
| `/benzinga/v1/earnings/calendar` | 404 | Endpoint shape unknown / inaccessible |
| `/vX/reference/financials?ticker={T}` | OK | Filing dates of **past** quarters only |

**Conclusion:** the current Polygon plan does not include forward-looking
earnings dates. The Benzinga-backed earnings endpoints require a plan
upgrade.

Per the brief — "If the Polygon plan does not include upcoming earnings
dates, stop and write a note to MIGRATION_QUESTIONS.md. Do not invent a
fallback." — I am stopping Phase 2 and NOT modifying
`shared/earnings_calendar.py`.

**Decision needed (one of):**
1. **Upgrade the Polygon plan** to include Benzinga earnings, then
   re-trigger Phase 2.
2. **Leave `earnings_calendar.py` on yfinance** for now (accept the
   one-file remaining yfinance dependency on the live path). This means
   `import yfinance as yf` will remain inside the module — Phase 5
   (delete yfinance from requirements.txt) cannot complete cleanly until
   this is resolved.
3. **Wire in a different earnings source** (e.g. FMP, Alpaca, NASDAQ
   calendar). Outside the brief — would need explicit scope expansion.

Phase 3 (scanner fallback removal) does NOT depend on Phase 2 output and
proceeded independently.

I am NOT touching `shared/earnings_calendar.py` in this session.

---

## Q3 — `get_ticker_obj` callers outside the live OHLCV path

**Status:** resolved without escalation, but worth flagging.

`shared/data_cache.py::get_ticker_obj` is referenced by two live-path files:

- `compass/features.py:447` — uses `stock.calendar` (earnings date). Wrapped in
  `try/except`; gracefully degrades to `days_to_earnings=999` if it raises.
- `strategy/options_analyzer.py:118` — uses `stock.options` and
  `stock.option_chain(...)` as a fallback when neither Tradier nor Polygon
  options providers are configured. Wrapped in `try/except`; degrades to empty
  DataFrame.

After Phase 1, both callers will silently lose access to those features. In
production both paths have alternatives configured (Polygon options client,
earnings_calendar.py), so this is fine. **I did not modify those files** —
Phase 3 explicitly enumerates the five alerts/* scanners and not strategy/ or
compass/. Flagging here so Phase 5 can address fully if desired.

The Phase-2 rewrite of `shared/earnings_calendar.py` makes the compass
fallback path doubly redundant.
