# How to Test the Fixed Overlay (When Battles Finish)

## Step 1: Wait for Battles to Complete
```bash
# Check active battle count
cd /home/ryan/projects/fouler-play-v2
cat active_battles.json | jq .count
```

Wait until count = 0 (or use the monitoring script below).

## Step 2: Safe Restart
```bash
# Automated safe restart (recommended)
./scripts/safe_restart_monitor.sh

# OR manual restart:
kill -TERM $(cat .pids/bot_monitor.pid | jq -r .pid)
# Wait 10s for graceful exit
# Then check if it auto-restarted, or start manually
```

## Step 3: Verify Data Flow
```bash
# Check that stream_status.json is being written
watch -n 2 'cat stream_status.json | jq'

# You should see:
# - elo: actual ELO value (not "---")
# - wins/losses: incrementing
# - status: "Battling" or "Idle"
# - updated: recent timestamp
```

## Step 4: Test Overlay in Browser
```bash
# Open overlay
xdg-open http://localhost:8777/overlay

# Or use curl to check data
curl http://localhost:8777/status | jq
```

## Monitoring Script (Optional)
```bash
# Watch for battles to finish
while true; do
  COUNT=$(cat active_battles.json | jq -r '.count // 0')
  echo "[$(date '+%H:%M:%S')] Active battles: $COUNT"
  [ "$COUNT" -eq 0 ] && echo "✅ Ready to restart!" && break
  sleep 15
done
```

## Expected Results After Restart
1. **Stream Status Updates:**
   - ELO changes after each battle
   - Wins/losses increment correctly
   - Battle info shows current opponents

2. **Overlay Display:**
   - Top bar: Active battles count, session record, ELO
   - Worker pills: Show opponent names when active
   - Center cards: Today's stats, ELO, next fix
   - Bottom bar: Recent results with W/L pips

3. **Daily Stats:**
   - TODAY card shows actual win/loss count
   - Winrate percentage calculated
   - Win/loss streaks displayed

## URLs for Testing
- Main overlay: http://localhost:8777/overlay
- OBS battles: http://localhost:8777/obs  
- Status API: http://localhost:8777/status
- Battles API: http://localhost:8777/battles
- WebSocket test: wscat -c ws://localhost:8777/ws

## Troubleshooting
**Overlay still shows "SCANNING..."**
- Check if bot_monitor was restarted with new code
- Verify stream_status.json exists and has recent timestamp
- Check stream_server.log for errors

**ELO shows "---"**  
- Bot hasn't synced ladder stats yet (wait a few battles)
- Check bot_monitor.log for "W: X L: Y" pattern
- Verify update_stream_status() is being called (uncommented)

**Daily stats show 0-0**
- daily_stats.json doesn't exist yet (created on first win/loss)
- Check that update_daily_stats() calls are uncommented
- File is at: /home/ryan/projects/fouler-play-v2/daily_stats.json
