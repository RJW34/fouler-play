# Stream Pipeline Fix — Complete

## Problem
Overlay showed "SCANNING..." because `bot_monitor.py` had all streaming integration calls **commented out** (marked "disabled during upgrade"). Data wasn't flowing from bot → stream_server → overlay.

## Root Cause
All `update_stream_status()` and `update_daily_stats()` calls in `bot_monitor.py` were commented out at lines:
- 67-68: Import statements
- 435-440: ELO update after ladder sync
- 525: Win tracking (`update_daily_stats`)
- 536: Loss tracking (`update_daily_stats`)
- 565-574: Status update after battle end

## Fix Applied
**Commit:** `e533e37` - "fix(monitor): Re-enable stream integration for overlay data flow"

Re-enabled all streaming integration calls in `bot_monitor.py`:
```python
# Before (commented):
# from streaming.stream_integration import start_stream, stop_stream, update_stream_status
# await update_stream_status(wins=self.wins, losses=self.losses, ...)

# After (working):
from streaming.stream_integration import start_stream, stop_stream, update_stream_status
await update_stream_status(wins=self.wins, losses=self.losses, ...)
```

**Left commented:** Auto-streaming start/stop calls (manual control preferred)

## Verification
✅ All 517 tests passing  
✅ Overlay displays correctly with test data:
   - ELO: 1542
   - Today: 12W - 8L (60% winrate)
   - Active battles: 3 workers showing opponents
   - Recent results: W/L pips with streak indicators
   - Win streak: 4W 🔥

Screenshots saved in commit message.

## Data Flow (Working)
```
bot_monitor.py (detects events)
    ↓ update_stream_status()
streaming/stream_integration.py (writes JSON + sends WebSocket)
    ↓ writes to
stream_status.json + active_battles.json + daily_stats.json
    ↓ read by
streaming/stream_server.py (HTTP + WebSocket server on port 8777)
    ↓ serves via
/status, /battles, /ws endpoints
    ↓ consumed by
overlay.html (WebSocket client displays live data)
```

## Restart Required ⚠️
**bot_monitor must be restarted to activate changes**, but:
- **DO NOT restart with active battles** (causes forfeit → ELO loss)
- Wait for `active_battles.json` count to reach 0
- Use safe restart script: `scripts/safe_restart_monitor.sh`

### Manual Restart (When Safe)
```bash
cd /home/ryan/projects/fouler-play-v2

# Check battle count
cat active_battles.json | jq .count

# If count = 0, safe to restart:
kill -TERM $(cat .pids/bot_monitor.pid | jq -r .pid)

# Wait for graceful exit, then start new process
# (or use safe_restart_monitor.sh which automates this)
```

### Safe Restart Script
```bash
./scripts/safe_restart_monitor.sh
```

Features:
- Waits for active battles to finish (checks every 10s)
- 30-minute timeout (configurable)
- Graceful shutdown (SIGTERM for drain mode)
- Verifies new process started
- Uses previous command from PID file

## Current Status (as of 2026-02-06 22:45 EST)
- ✅ Fix committed and pushed
- ✅ Tests passing
- ✅ Overlay verified working with test data
- ⏳ **Waiting for 3 active battles to finish before restart**
- 📊 Stream server running on port 8777 (PID 7030)
- 🤖 Bot monitor running on old code (PID 52434)

## Next Steps
1. Wait for active_battles.json count → 0
2. Run `scripts/safe_restart_monitor.sh` to activate changes
3. Verify overlay shows live data: http://localhost:8777/overlay
4. (Optional) Test OBS battles view: http://localhost:8777/obs
5. (Future) Set up ffmpeg streaming to Twitch when ready

## Overlay URLs
- Main overlay (stats + battles): http://localhost:8777/overlay
- OBS battles (3-slot view): http://localhost:8777/obs
- Status API: http://localhost:8777/status
- Battles API: http://localhost:8777/battles
- WebSocket: ws://localhost:8777/ws

## Files Modified
- `bot_monitor.py` - Uncommented streaming integration
- `scripts/safe_restart_monitor.sh` - Created safe restart tool

## Files NOT Modified (Working Correctly)
- `streaming/stream_server.py` - Already serving data correctly
- `streaming/stream_integration.py` - Already has proper functions
- `streaming/state_store.py` - Already handles JSON files correctly
- `streaming/overlay.html` - Already displays data when available
- `streaming/obs_battles.html` - Already works
