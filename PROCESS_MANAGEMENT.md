# Fouler Play - Process Management

**Problem solved:** Bot processes weren't being tracked properly, making it hard to stop everything cleanly.

**Solution:** Comprehensive PID tracking system that catches all spawned processes and orphans.

---

## Quick Start

### Linux

**Start (managed):**
```bash
./start_managed.sh
```

**Check status:**
```bash
python3 process_manager.py status
```

**Stop everything:**
```bash
./stop_all.sh
# OR
python3 process_manager.py stop
```

### Windows (1-Click Desktop Access)

**Setup (one time):**
1. Copy `windows_start.bat` to your desktop
2. Edit the file if needed (check `PROJECT_DIR` path)
3. Double-click to start

**Stop:**
1. Copy `windows_stop.bat` to your desktop
2. Double-click to stop all processes

---

## How It Works

### Process Tracking

All spawned processes are tracked in `.pids/` directory:
- `bot_monitor.pid` - The monitor process
- `bot_main.pid` - The main bot process
- Additional PIDs for any spawned workers

Each PID file contains:
```json
{
  "pid": 12345,
  "name": "bot_main",
  "started_at": 1706755200,
  "command": "python run.py ..."
}
```

### Orphan Detection

When stopping, the system:
1. Kills all tracked PIDs
2. Scans for orphaned Python processes running fouler-play code
3. Kills orphans automatically
4. Cleans up PID files

This ensures no processes are left running in the background.

---

## Commands Reference

### process_manager.py

**Check status:**
```bash
python3 process_manager.py status
```
Shows all tracked processes (running/dead) and cleans up dead PID files.

**Stop all:**
```bash
python3 process_manager.py stop
```
Stops all tracked processes, detects orphans, kills everything.

### Manual PID Cleanup

If PID files get out of sync:
```bash
rm -rf .pids/
```

Then start fresh with `./start_managed.sh`

---

## Windows Batch Files

### windows_start.bat

- Changes to fouler-play directory
- Checks for Python and venv
- Creates venv if missing
- Installs dependencies if needed
- Starts bot_monitor.py in background
- Shows status
- **Stays running in background after you close the window**

### windows_stop.bat

- Changes to fouler-play directory
- Activates venv
- Runs `python process_manager.py stop`
- Stops ALL processes cleanly

---

## Offline Operation

**Key feature:** Bot can run when DEKU is offline!

The bot doesn't require DEKU to be online - it's fully autonomous once started.

**What happens when DEKU is offline:**
- ✅ Bot continues playing games
- ✅ Battles are tracked and logged
- ⚠️  Discord feedback won't post (webhook fails gracefully)
- ⚠️  Loss analysis won't run (requires DEKU)

When DEKU comes back online:
- Catch up on logs
- Analyze accumulated losses
- Post summary to Discord

---

## Troubleshooting

**Problem:** `windows_start.bat` says Python not found

**Solution:** Install Python or add to PATH:
```
setx PATH "%PATH%;C:\Python312"
```

---

**Problem:** Processes won't stop

**Solution:** Force kill manually:
```bash
# Linux
pkill -9 -f "fouler-play.*python"

# Windows
taskkill /F /IM python.exe
```

Then clean up:
```bash
rm -rf .pids/
```

---

**Problem:** "Virtual environment not found" on Windows

**Solution:** The batch file creates it automatically. If it fails:
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

---

## Files

| File | Purpose |
|------|---------|
| `process_manager.py` | Core tracking system |
| `start_managed.sh` | Linux startup script |
| `stop_all.sh` | Linux shutdown script |
| `windows_start.bat` | Windows 1-click startup |
| `windows_stop.bat` | Windows 1-click shutdown |
| `.pids/*.pid` | PID tracking files (auto-managed) |

---

## Integration with Existing Scripts

**Old scripts (still work):**
- `start_bot.sh` - Simple startup (no tracking)
- `monitor-elo.sh` - ELO monitoring (separate)

**New scripts (recommended):**
- `start_managed.sh` - Better tracking
- `stop_all.sh` - Clean shutdown

You can still use the old scripts, but they won't benefit from PID tracking.

---

**Status:** Production ready. Tested on Linux, ready for Windows deployment via BAKUGO.
