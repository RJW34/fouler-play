# Fouler Play Startup Guide

**⚠️ CRITICAL: Use ONLY these scripts to prevent duplicate processes**

## Starting the Bot

```bash
cd /home/ryan/projects/fouler-play
./start.sh
```

**What it does:**
1. Checks if bot is already running (prevents duplicates)
2. Cleans up stale PID files
3. Starts `bot_monitor.py` which launches `run.py`
4. Verifies startup with `process_manager.py status`

## Stopping the Bot

```bash
cd /home/ryan/projects/fouler-play
./stop.sh
```

**What it does:**
1. Gracefully stops via `process_manager.py stop`
2. Force-kills any lingering processes
3. Cleans up PID files

## Checking Status

```bash
cd /home/ryan/projects/fouler-play
python3 process_manager.py status
```

**Output:**
```
Tracked processes (1):

✅ RUNNING - bot_monitor (PID 12345, started 2026-02-01 04:00:00)
  └─ Child: run.py (2 workers)

Summary: 1 running, 0 dead
```

## Configuration

**Max Concurrent Battles:** 2 (set in `run.py`)

```python
MAX_CONCURRENT_BATTLES = 2
```

**Workers:**
- 2 battle workers run concurrently
- Each can handle 1 battle at a time
- Max total: 2 simultaneous battles

## ⚠️ IMPORTANT: Duplicate Prevention

**DO NOT:**
- ❌ Run `start-bot.sh` (old script)
- ❌ Run `start_bot.sh` (aggressive cleanup)
- ❌ Run `start_managed.sh` (superseded)
- ❌ Run `run.py` directly
- ❌ Run `bot_monitor.py` directly
- ❌ Start multiple times without checking status

**ALWAYS:**
- ✅ Use `./start.sh` (has duplicate detection)
- ✅ Check status first: `python3 process_manager.py status`
- ✅ Stop before restarting: `./stop.sh`

## Troubleshooting

**"Bot is already running" error:**
```bash
python3 process_manager.py status  # Check what's running
./stop.sh                          # Stop everything
./start.sh                         # Start fresh
```

**Stale PID files:**
```bash
python3 process_manager.py status  # Auto-cleans dead PIDs
```

**Too many processes:**
```bash
./stop.sh                          # Force stop everything
ps aux | grep -E "run.py|bot_monitor"  # Verify nothing running
./start.sh                         # Start clean
```

**Process stuck (high CPU, no battles):**
```bash
./stop.sh
rm -rf .pids/*
./start.sh
```

## Heartbeat Integration

Diagnostics will auto-restart ONLY if:
1. No processes are running (verified via process_manager)
2. Using the canonical `start.sh` script
3. Never spawns duplicates

## Files Involved

**Active (use these):**
- `start.sh` - Single source of truth startup
- `stop.sh` - Safe shutdown
- `process_manager.py` - Process tracking
- `run.py` - Main bot code (MAX_CONCURRENT_BATTLES=2)
- `bot_monitor.py` - Wrapper that manages run.py

**Deprecated (delete these):**
- `start-bot.sh` - Old, no process checking
- `start_bot.sh` - Aggressive cleanup, unsafe
- `start_managed.sh` - Superseded by start.sh

## Verification

After starting, verify correct configuration:
```bash
# Check for exactly 1 bot_monitor process
pgrep -fa bot_monitor | wc -l  # Should be 1

# Check for run.py workers
ps aux | grep "run.py"  # Should show 2 workers

# Verify max concurrent battles
grep "MAX_CONCURRENT_BATTLES" run.py  # Should be 2

# Check process manager
python3 process_manager.py status  # Should show 1 running process
```

If you see more than 2 workers or multiple bot_monitor processes, **something is broken - stop everything and investigate.**
