#!/bin/bash
# Battle Stats Event Watcher
# Watches battle_stats.json for changes, triggers performance analysis immediately
# Runs as background daemon

set -e

WATCH_FILE="/home/ryan/projects/fouler-play/battle_stats.json"
LOG_FILE="/home/ryan/projects/fouler-play/logs/battle-watcher.log"
LAST_BATTLE_COUNT=0
DISCORD_WEBHOOK="https://discord.com/api/webhooks/1467010283741384849/$(grep 'DISCORD_WEBHOOK' /home/ryan/projects/fouler-play/.env | cut -d= -f2)"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

trigger_performance_analysis() {
    local current_count=$1
    local delta=$((current_count - LAST_BATTLE_COUNT))
    
    log "Battle count changed: $LAST_BATTLE_COUNT → $current_count (delta: +$delta)"
    
    # Only trigger on meaningful deltas (avoid noise)
    if [ $delta -lt 1 ]; then
        return
    fi
    
    # Extract latest stats
    local overall_wr=$(jq '.wins / .battles' "$WATCH_FILE" 2>/dev/null || echo "unknown")
    local dondozo_wr=$(jq '.teams[2].wins / .teams[2].battles' "$WATCH_FILE" 2>/dev/null || echo "unknown")
    local stall_wr=$(jq '.teams[0].wins / .teams[0].battles' "$WATCH_FILE" 2>/dev/null || echo "unknown")
    
    log "Performance snapshot: Overall $overall_wr% | Dondozo $dondozo_wr% | Stall $stall_wr%"
    
    # Fire event to Discord (if webhook configured)
    if [ -n "$DISCORD_WEBHOOK" ] && [ "$DISCORD_WEBHOOK" != "https://discord.com/api/webhooks/1467010283741384849/" ]; then
        curl -s -X POST "$DISCORD_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"content\": \"🎯 Battle update: $current_count total, +$delta new | WR: $overall_wr%\"}" \
            2>/dev/null || true
    fi
    
    # Update last known count
    LAST_BATTLE_COUNT=$current_count
}

log "Battle stats watcher started (watching: $WATCH_FILE)"

# Initial count
if [ -f "$WATCH_FILE" ]; then
    LAST_BATTLE_COUNT=$(jq '.battles' "$WATCH_FILE" 2>/dev/null || echo 0)
    log "Initial battle count: $LAST_BATTLE_COUNT"
fi

# Watch for changes using inotifywait
if command -v inotifywait >/dev/null 2>&1; then
    log "Using inotifywait for real-time monitoring"
    inotifywait -m -e modify "$WATCH_FILE" | while read path action file; do
        if [ -f "$WATCH_FILE" ]; then
            current=$(jq '.battles' "$WATCH_FILE" 2>/dev/null || echo "$LAST_BATTLE_COUNT")
            if [ "$current" != "$LAST_BATTLE_COUNT" ]; then
                trigger_performance_analysis "$current"
            fi
        fi
    done
else
    log "inotifywait not found, falling back to polling (5sec intervals)"
    while true; do
        if [ -f "$WATCH_FILE" ]; then
            current=$(jq '.battles' "$WATCH_FILE" 2>/dev/null || echo "$LAST_BATTLE_COUNT")
            if [ "$current" != "$LAST_BATTLE_COUNT" ]; then
                trigger_performance_analysis "$current"
            fi
        fi
        sleep 5
    done
fi
