
# This is Prod branch for Production. 



# Cron Job Setup Guide

## ist-utc converter
https://www.freeconvert.com/time/ist-to-utc

## Important: Cron uses UTC time, not IST!

**Convert IST to UTC:** Subtract 5 hours 30 minutes

Examples:
- 7:45 AM IST = 2:15 AM UTC
- 9:05 AM IST = 3:35 AM UTC

## Setup Steps

### 1. Create Shell Scripts

**Login Script (`run_login.sh`):**
```bash
nano /root/trading/run_login.sh
```

Content:
```bash
#!/bin/bash
export TZ='Asia/Kolkata'
source /root/trading/venv/bin/activate
cd /root/trading
python login.py >> /root/trading/login.log 2>&1
```

**Streamer Script (`run_streamer.sh`):**
```bash
nano /root/trading/run_streamer.sh
```

Content:
```bash
#!/bin/bash
export TZ='Asia/Kolkata'
source /root/trading/venv/bin/activate
cd /root/trading
python nifty_option_streamer.py >> /root/trading/streamer.log 2>&1
```

### 2. Make Scripts Executable

```bash
chmod +x /root/trading/run_login.sh
chmod +x /root/trading/run_streamer.sh
```

### 3. Set Up Cron Jobs

```bash
crontab -e
```

Add these lines (runs Mon-Fri):
```bash
15 2 * * 1-5 /root/trading/run_login.sh
35 3 * * 1-5 /root/trading/run_streamer.sh
```

This runs:
- Login at 7:45 AM IST (2:15 AM UTC)
- Streamer at 9:05 AM IST (3:35 AM UTC)

### 4. Verify Cron Jobs

```bash
crontab -l
```

## Testing

**Test manually:**
```bash
/root/trading/run_login.sh
/root/trading/run_streamer.sh
```

**For testing cron, set time 2 minutes ahead:**

1. Check current UTC time: `date -u`
2. If it's 3:45 UTC, set cron for 3:47 UTC: `47 3 * * * /root/trading/run_login.sh`
3. Wait 2 minutes
4. Check log: `cat /root/trading/login.log`

## Checking Logs

```bash
# Check if cron ran
grep CRON /var/log/syslog | tail -20

# Check login output
cat /root/trading/login.log

# Check streamer output
cat /root/trading/streamer.log

# View current cron jobs
crontab -l
```

## Cron Time Format

```
MIN HOUR DAY MONTH WEEKDAY
```

Examples:
- `15 2 * * 1-5` = 2:15 AM UTC, Monday-Friday
- `35 3 * * *` = 3:35 AM UTC, every day
- `0 6 * * 1` = 6:00 AM UTC, only Monday

## Quick Reference: IST to UTC Conversion

| IST Time | UTC Time | Cron Format |
|----------|----------|-------------|
| 7:00 AM  | 1:30 AM  | `30 1` |
| 7:45 AM  | 2:15 AM  | `15 2` |
| 8:00 AM  | 2:30 AM  | `30 2` |
| 9:00 AM  | 3:30 AM  | `30 3` |
| 9:05 AM  | 3:35 AM  | `35 3` |
| 9:15 AM  | 3:45 AM  | `45 3` |
| 10:00 AM | 4:30 AM  | `30 4` |

## Stop/Remove Cron Jobs

```bash
# Edit and delete specific lines
crontab -e

# Or remove all cron jobs
crontab -r
```
