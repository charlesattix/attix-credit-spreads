# yfinance → Polygon Migration Notes

## Phase 0 — Reconnaissance (2026-05-22)

### Polygon endpoint verification
All endpoints return data successfully for 2026-04-22..2026-05-22 window (23 trading days).

**Indices key (`POLYGON_INDICES_API_KEY`):**
- `I:VIX` — status: DELAYED, 23 bars
- `I:VIX3M` — status: DELAYED, 23 bars
- `I:SPX` — status: DELAYED, 23 bars

(DELAYED status is expected — daily aggregates are EOD; not real-time. Sufficient for OHLCV history.)

**Stocks key (`POLYGON_API_KEY`):**
- SPY, QQQ, IWM, TLT — OK, 23 bars each
- Sector ETFs: XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLU, XLB, XLRE, XLC — OK, 23 bars each

### Baseline test suite (`pytest tests/ --tb=no -q`)
- **3434 passed**
- **14 failed** (pre-existing — unrelated to migration):
  - `test_exp800_fixes.py::test_marks_expired_trades`
  - `test_live_snapshot.py::test_empty_data_handled`
  - `test_macro_api.py` — 9 failures (regime endpoint returning 503/422)
  - `test_sentinel_orchestrator.py` — 2 failures (exception handler severity)
- 5 skipped, 8 xfailed, 6 xpassed
- Duration: 84s
