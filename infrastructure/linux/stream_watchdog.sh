#!/bin/bash
# Stream state watchdog — keeps OBS overlay (Slot 2) from going stale.
# Runs via cron every 60s. No agent tokens needed.
#
# Checks:
# 1. Is fouler-play.service running? If not, mark state as Offline.
# 2. Is stream_status.json stale (>5 min since last update)? Reset active battles.
# 3. Is active_battles.json showing battles but service just restarted? Clean up.

set -euo pipefail

FP_DIR="/home/ryan/projects/fouler-play"
STATUS_FILE="$FP_DIR/stream_status.json"
BATTLES_FILE="$FP_DIR/active_battles.json"

# Check if fouler-play service is running
if ! systemctl --user is-active --quiet fouler-play.service 2>/dev/null; then
    # Bot is down — update state to show offline
    cat > "$STATUS_FILE" <<EOF
{
  "elo": "---",
  "wins": $(python3 -c "import json; d=json.load(open('$FP_DIR/daily_stats.json')); print(d.get('wins',0))" 2>/dev/null || echo 0),
  "losses": $(python3 -c "import json; d=json.load(open('$FP_DIR/daily_stats.json')); print(d.get('losses',0))" 2>/dev/null || echo 0),
  "status": "Offline",
  "battle_info": "Bot restarting...",
  "streaming": false,
  "stream_pid": null,
  "updated": "$(date -Iseconds)",
  "today_wins": $(python3 -c "import json; d=json.load(open('$FP_DIR/daily_stats.json')); print(d.get('wins',0))" 2>/dev/null || echo 0),
  "today_losses": $(python3 -c "import json; d=json.load(open('$FP_DIR/daily_stats.json')); print(d.get('losses',0))" 2>/dev/null || echo 0)
}
EOF
    # Clear active battles so overlay doesn't show stale battle
    echo '{"battles":[],"count":0,"max_slots":1,"updated":"'"$(date -Iseconds)"'"}' > "$BATTLES_FILE"
    exit 0
fi

# Bot is running — check if status file is stale (>5 min old)
if [ -f "$STATUS_FILE" ]; then
    LAST_MOD=$(stat -c %Y "$STATUS_FILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    AGE=$(( NOW - LAST_MOD ))
    
    if [ "$AGE" -gt 300 ]; then
        # Status hasn't been updated in 5+ min but bot is running
        # Bot might be between battles — update timestamp to keep it fresh
        python3 -c "
import json, datetime
with open('$STATUS_FILE') as f:
    data = json.load(f)
data['updated'] = datetime.datetime.now().isoformat()
if data.get('status') not in ('Battling',):
    data['status'] = 'Searching'
    data['battle_info'] = 'Searching...'
with open('$STATUS_FILE', 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null || true
    fi
fi

# Check for stale active battles (battle older than 30 min = probably stale)
if [ -f "$BATTLES_FILE" ]; then
    python3 -c "
import json, datetime, requests

with open('$BATTLES_FILE') as f:
    data = json.load(f)

battles = data.get('battles', [])
now = datetime.datetime.now()
cleaned = []
for b in battles:
    started = b.get('started', '')
    if started:
        try:
            t = datetime.datetime.fromisoformat(started)
            age_min = (now - t).total_seconds() / 60
            if age_min < 30:
                cleaned.append(b)
        except:
            cleaned.append(b)
    else:
        cleaned.append(b)

if len(cleaned) != len(battles):
    data['battles'] = cleaned
    data['count'] = len(cleaned)
    data['updated'] = now.isoformat()
    with open('$BATTLES_FILE', 'w') as f:
        json.dump(data, f, indent=2)
    
    # Signal stream server to refresh
    try:
        requests.post('http://localhost:8777/event', 
                     json={'type': 'STATE_REFRESH', 'payload': {}},
                     timeout=2)
    except:
        pass  # Stream server might be down, that's OK
" 2>/dev/null || true
fi
