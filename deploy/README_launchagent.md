# EXP-1220 LaunchAgent Deployment (macOS)

This document explains how to install the `com.pilotai.exp1220.plist`
LaunchAgent on Charles's Mac Studio to run the paper trading scanner
once per weekday at 09:35 America/New_York.

## Prerequisites

1. The repo is checked out at a known path (e.g. `/Users/charles/pilotai`).
2. `python3` is available on `PATH` (Homebrew, macOS system, or pyenv).
3. `pip3 install -r requirements.txt` (or at minimum `pip3 install alpaca-py pyyaml`).
4. `.env` file at repo root with `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.

## Step-by-step install

1. Edit `deploy/com.pilotai.exp1220.plist` and replace every occurrence of
   `/Users/charles/pilotai` with the actual checkout path.

2. Copy the plist to the LaunchAgents directory:

   ```
   cp deploy/com.pilotai.exp1220.plist ~/Library/LaunchAgents/
   ```

3. Run the smoke test first to verify config + connectivity:

   ```
   cd /Users/charles/pilotai
   set -a && source .env && set +a
   python3 scripts/run_exp1220.py --smoke-test
   ```

   Expected output: `SMOKE TEST PASSED` (warnings are OK).

4. Load the LaunchAgent:

   ```
   launchctl unload ~/Library/LaunchAgents/com.pilotai.exp1220.plist 2>/dev/null
   launchctl load   ~/Library/LaunchAgents/com.pilotai.exp1220.plist
   ```

5. Verify it is registered:

   ```
   launchctl list | grep pilotai
   ```

6. Trigger an immediate test run (does not wait for 09:35):

   ```
   launchctl start com.pilotai.exp1220
   ```

7. Check logs and health:

   ```
   tail -f /Users/charles/pilotai/logs/exp1220.log
   cat    /Users/charles/pilotai/logs/exp1220_health.json
   ```

## Timing notes

- `StartCalendarInterval` uses the Mac's local time. If the Mac is in
  `America/New_York`, `Hour=9 Minute=35` is 09:35 ET. Verify with `date`.
- The scan only runs on weekdays (Monday through Friday).
- If the Mac is sleeping at 09:35, the LaunchAgent will run at the next
  wake. For reliability, set the Mac to never sleep, or use `pmset`
  to wake at 09:30.

## Health check

Charles should monitor `logs/exp1220_health.json`. It contains:

- `status`: `ok`, `warning`, `error`, or `halted`
- `last_run`: ISO timestamp of last run
- `details`: open positions, last entry date, current mode
- `error`: error string if status is not ok

Run the health check manually anytime:

```
python3 scripts/run_exp1220.py --health
```

Exit code: 0 = ok, 1 = warning, 2 = error.

## Troubleshooting

- **`launchctl load` fails**: the plist has wrong permissions. Run
  `chmod 644 ~/Library/LaunchAgents/com.pilotai.exp1220.plist`.
- **Scanner never runs**: check `logs/exp1220_launchd.err.log` for load
  errors. Usually a wrong path in `WorkingDirectory` or `ProgramArguments`.
- **Alpaca auth errors**: verify `.env` has correct `ALPACA_API_KEY` and
  `ALPACA_SECRET_KEY` and that the `bash -lc` in the plist actually
  sources them (the plist already does).
- **VIX fetch fails**: the scanner falls back to a default of 20.0. The
  retry logic tries 3 times with exponential backoff before giving up.

## Uninstall

```
launchctl unload ~/Library/LaunchAgents/com.pilotai.exp1220.plist
rm              ~/Library/LaunchAgents/com.pilotai.exp1220.plist
```
