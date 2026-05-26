# Railway Migration Guide
**Date:** 2026-02-19
**Goal:** Copy local database to Railway and make it the primary trading instance

## Current Status
- Local: 10 trades, $89,790 balance
- Railway: 0 trades, $100,000 balance
- Code: ✅ In sync (commit 7c003c1)

## Step 1: Login to Railway CLI
```bash
cd /Users/charlesbot/projects/attix-credit-spreads
railway login
```

## Step 2: Link to Your Project
```bash
railway link
# Select: attix-credit-spreads-production
```

## Step 3: Upload Database to Railway Volume
```bash
# Copy local database to Railway's persistent volume
railway run --service=web cp data/pilotai.db /app/data/pilotai.db
```

OR manually via Railway Dashboard:
1. Go to Railway Dashboard → attix-credit-spreads-production
2. Go to Volumes → attix-data
3. Upload `data/pilotai.db` to `/app/data/pilotai.db`

## Step 4: Verify Upload
```bash
curl https://attix-production.up.railway.app/api/paper-trades | python3 -c "import sys, json; d=json.load(sys.stdin); print('Trades:', d['stats']['total_trades'], '| Balance:', d['stats']['balance'])"
```

Should show: `Trades: 10 | Balance: 89790.8`

## Step 5: Configure Scheduled Scans on Railway

### Option A: Railway Cron Job (Recommended)
Add to railway.toml:
```toml
[cron]
schedule = "*/30 9-16 * * 1-5"  # Every 30 min during market hours (EST)
command = "python3 main.py scan"
```

### Option B: External Cron (e.g., GitHub Actions)
Create `.github/workflows/scheduled-scan.yml`:
```yaml
name: Scheduled Trading Scan
on:
  schedule:
    - cron: '0,30 14-21 * * 1-5'  # Every 30 min, 9:30am-4pm EST (UTC+5)
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Railway Scan
        run: |
          curl -X POST https://attix-production.up.railway.app/api/scan \
            -H "Authorization: Bearer ${{ secrets.API_AUTH_TOKEN }}"
```

## Step 6: Stop Local Scans

### Disable Local Cron Jobs
```bash
# If using crontab
crontab -l | grep -v "attix-credit-spreads" | crontab -

# If using launchd (macOS)
launchctl list | grep attix
launchctl unload ~/Library/LaunchAgents/com.attix.scanner.plist
```

### Stop Local Next.js Server (if running)
```bash
# Find process
ps aux | grep "next dev" | grep -v grep

# Kill it
pkill -f "next dev"
```

## Step 7: Verify Railway is Primary

✅ Railway shows 10 trades
✅ Railway running scheduled scans
✅ Local server stopped
✅ All future scans happen on Railway

## Rollback Plan (if needed)
Local database is backed up at:
- `data/attix_backup_20260219_143853.db`

To restore locally:
```bash
cp data/attix_backup_20260219_143853.db data/pilotai.db
```

## URLs
- **Railway Dashboard:** https://attix-production.up.railway.app
- **My Trades:** https://attix-production.up.railway.app/my-trades
- **Health Check:** https://attix-production.up.railway.app/api/health

## Notes
- Railway volume is persistent across deploys
- Database backups happen automatically every scan
- If Railway redeploys, data is preserved in volume
