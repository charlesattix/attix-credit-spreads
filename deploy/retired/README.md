# Retired LaunchAgents

Plists that once ran experiments which have since been retired. Kept for
historical reference and re-deploy if an experiment is revived.

## EXP-700 — `com.pilotai.exp700.plist`

- **Retired:** 2026-05-20 (registry status `retired` since 2026-04-10)
- **Superseded by:** EXP-800
- **Retired reason:** Alpaca API keys revoked (401) after 2 trading days /
  10 trades. ML-filtered champion never re-stood-up. Sentinel Gate 0 began
  blocking every scan slot (`status=retired — blocking scanner`) and the
  launchd job has been failing with `exit=1` at every market-hours fire
  since.
- **Ops actions performed at retirement:**

  ```
  launchctl bootout gui/$(id -u)/com.pilotai.exp700
  rm ~/Library/LaunchAgents/com.pilotai.exp700.plist
  ```

- **Left untouched** (so historical analysis still works):
  - `scripts/exp700_ml_scanner.py` (referenced by `sentinel/gates_data_quality.py`
    and `tests/test_sentinel_g22_producers.py`)
  - `data/exp700/pilotai_exp700.db` (10 historical trades)
  - `configs/paper_exp700.yaml`, `.env.exp700` (kept for replay)

If EXP-700 ever needs to be revived, this plist can be re-installed at
`~/Library/LaunchAgents/` and reloaded — but the API keys in `.env.exp700`
will need to be regenerated first.
