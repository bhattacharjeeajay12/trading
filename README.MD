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

## Adding a New Strategy

What runs at trading time is fully driven by `strategies_list` in
[strategy/config.py](strategy/config.py). Each strategy declares which
data-aggregation mechanism it consumes, so a new strategy can either reuse an
existing aggregator or ship its own.

### Steps

1. **Write the strategy class** in a new file under [strategy/](strategy/),
   subclassing `Strategy`. Set `self.name` to a unique string and implement
   `run(df)` so it sets `self.signal` to `1` for BUY or `0` otherwise.

2. **(Only if the strategy needs a new aggregator)** Add the aggregator:
   - Subclass `BaseAggregator` in [AggregatedData.py](AggregatedData.py) and
     implement `run(tick) -> pd.DataFrame`.
   - Register the class in `AGGREGATOR_REGISTRY` at the bottom of that file.
   - Add a descriptive entry to `aggregators_list` in
     [strategy/config.py](strategy/config.py).

3. **Register the strategy class** in `STRATEGY_REGISTRY` in
   [strategy/registry.py](strategy/registry.py).

4. **Declare the strategy in config** by adding an entry to `strategies_list`
   in [strategy/config.py](strategy/config.py):

   ```python
   "VolumeSpikeStrategy": {
       "strategy_name": "VolumeSpikeStrategy",
       "aggregator": "TickWindowAggregator",
       "aggregator_params": {"window_ticks": 100},
       "strategy_params": {"volume_multiplier": 3.0},
       "is_deployed": True,
   },
   ```

That's it -- the pipeline picks it up at next start. No edits to
`nifty_option_streamer.py`, `DataDecisionPipeline.py`, or `DataKeeper.py` are
needed.

### Runtime behaviour to be aware of

- **Single buy per tick.** Deployed strategies are evaluated in
  `strategies_list` insertion order. The first strategy returning
  `signal == 1` opens the trade; remaining strategies are skipped for that
  tick. Re-order keys in the config to change priority.
- **Disabling a strategy.** Flip `is_deployed` to `False` (or remove the key).
  Pipeline logs `Strategy disabled in config | name=...` at startup, and any
  aggregator no longer required by a deployed strategy is dropped from the
  per-symbol set.
- **Aggregator deduplication.** If two deployed strategies request the same
  aggregator with the same params, `DataKeeper` builds it once per symbol and
  shares it.
- **Per-trade traceability.** Logs always carry the firing strategy:
  `BUY ORDER PLACED | ... | strategy=MomentBasedStrategy` and
  `POSITION CLOSED | ... | strategy=MomentBasedStrategy`.
