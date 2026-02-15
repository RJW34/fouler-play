# Pre-Flight Verification System

## Overview

`pre_flight_check.py` is a comprehensive verification tool that **MUST** be run before every batch to catch configuration issues before they cause problems during battle runs.

## What It Checks

### 1. Quota Verification ✅
- Validates quota calculation logic matches `run.py`
- Tests with known values: 15/3, 20/3, 999999/3
- Verifies quotas sum to total run_count
- Shows expected per-worker battle distribution

**Why it matters:** Prevents workers from running wrong number of battles or freezing due to quota math errors.

### 2. Team Rotation ✅
- Parses `TEAM_NAMES` from `.env`
- Verifies each team file/directory exists
- Validates Pokemon Showdown format
- Simulates team assignment for 3 workers × 15 battles

**Why it matters:** Catches missing or corrupted team files before battles start. Prevents crashes mid-batch.

### 3. Config Sanity ✅
- `PS_USERNAME` and `PS_PASSWORD` set?
- `BOT_LOG_LEVEL` valid (DEBUG/INFO/WARNING/ERROR/CRITICAL)?
- `MAX_MCTS_BATTLES` > 0?
- `PS_FORMAT` is valid (gen9ou)?

**Why it matters:** Prevents authentication failures and invalid configurations.

### 4. Battle Stats ✅
- Does `battle_stats.json` exist and parse correctly?
- Shows win/loss totals and per-team breakdowns
- Flags if any worker ran more battles than expected (quota violation)

**Why it matters:** Detects corrupted stats files and quota enforcement issues.

## Usage

### Standalone
```bash
# Use config from .env
python3 pre_flight_check.py

# Override run count and worker count
python3 pre_flight_check.py --run-count 15 --num-workers 3
```

### Integration with launch.py

Add to the top of `launch.py`:

```python
from pre_flight_check import run_pre_flight_check

def main():
    # ... (existing arg parsing) ...
    
    # PRE-FLIGHT CHECK - refuse to start if it fails
    if not run_pre_flight_check(run_count=args.battles, num_workers=args.concurrent):
        print("\n[ERROR] Pre-flight check failed. Fix issues above before launching.")
        sys.exit(1)
    
    # ... (rest of launch logic) ...
```

This ensures **no batch can start** if configuration is broken.

## Exit Codes

- **0**: All checks passed ✅
- **1**: One or more checks failed ❌

## Example Output

### Success ✅
```
============================================================
PRE-FLIGHT VERIFICATION
============================================================

1. QUOTA VERIFICATION
  ✅ 15 battles / 3 workers = [5, 5, 5] (sum=15)
  ✅ 20 battles / 3 workers = [7, 7, 6] (sum=20)
  ✅ 999999 battles / 3 workers = [333333, 333333, 333333] (sum=999999)

  Batch Config:
    Run count: 15
    Workers: 3
    Per-worker quotas: [5, 5, 5]
    Expected total battles: 15

2. TEAM ROTATION
  Found 3 configured teams:
  ✅ Team 0: gen9/ou/fat-team-1-stall (file)
  ✅ Team 1: gen9/ou/fat-team-2-pivot (file)
  ✅ Team 2: gen9/ou/fat-team-3-dondozo (file)

  Simulated Assignment (3 workers × 15 battles):
    Worker 0: ['fat-team-1-stall', 'fat-team-1-stall', ...]
    Worker 1: ['fat-team-2-pivot', 'fat-team-2-pivot', ...]
    Worker 2: ['fat-team-3-dondozo', 'fat-team-3-dondozo', ...]

3. CONFIG SANITY
  ✅ PS_USERNAME: BugInTheCode
  ✅ PS_PASSWORD: Her***************
  ✅ BOT_LOG_LEVEL: INFO
  ✅ MAX_MCTS_BATTLES: 1
  ✅ PS_FORMAT: gen9ou

4. BATTLE STATS
  ✅ battle_stats.json is valid JSON
  ✅ Found 321 recorded battles
    Total battles: 321
    Wins: 187
    Losses: 134
    Win rate: 58.3%

  Per-Team Stats:
    fat-team-1-stall: 61W / 53L (53.5%)
    fat-team-2-pivot: 52W / 51L (50.5%)
    fat-team-3-dondozo: 74W / 30L (71.2%)

============================================================
SUMMARY
============================================================
✅ Quota Verification
✅ Team Rotation
✅ Config Sanity
✅ Battle Stats

✅ ALL PRE-FLIGHT CHECKS PASSED
Ready to launch batch.
```

### Failure ❌
```
3. CONFIG SANITY
  ❌ PS_USERNAME: NOT SET
  ❌ MAX_MCTS_BATTLES: 0 (must be > 0)

============================================================
SUMMARY
============================================================
✅ Quota Verification
✅ Team Rotation
❌ Config Sanity
✅ Battle Stats

Failures:
  ❌ PS_USERNAME not configured
  ❌ MAX_MCTS_BATTLES must be > 0, got 0

❌ PRE-FLIGHT CHECK FAILED
Fix the issues above before running the batch.
```

## Test Cases Built-In

The quota verification tests these known cases:
- **15 battles / 3 workers** → `[5, 5, 5]` ✅
- **20 battles / 3 workers** → `[7, 7, 6]` ✅
- **999999 battles / 3 workers** → `[333333, 333333, 333333]` ✅

If any fail, the script refuses to proceed.

## When to Run

**ALWAYS run before:**
- Starting a new batch
- After changing `.env` configuration
- After modifying team files
- After system crashes or restarts

**Integration recommendation:** Make it automatic in `launch.py` so no one can accidentally skip it.

## Importable API

```python
from pre_flight_check import run_pre_flight_check

# Returns True if all checks pass, False otherwise
passed = run_pre_flight_check(run_count=15, num_workers=3)
if not passed:
    sys.exit(1)
```
