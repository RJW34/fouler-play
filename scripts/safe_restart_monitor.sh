#!/bin/bash
# Safe restart script for bot_monitor.py
# Waits for active battles to finish before restarting

set -e

PROJ_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJ_ROOT"

BATTLES_FILE="active_battles.json"
PID_FILE=".pids/bot_monitor.pid"
MAX_WAIT=1800  # 30 minutes max wait

echo "=== Safe Bot Monitor Restart ==="
echo

# Check if monitor is running
if [ ! -f "$PID_FILE" ]; then
    echo "❌ Bot monitor not running (no PID file)"
    exit 1
fi

MONITOR_PID=$(jq -r '.pid' "$PID_FILE" 2>/dev/null || echo "")
if [ -z "$MONITOR_PID" ] || ! kill -0 "$MONITOR_PID" 2>/dev/null; then
    echo "❌ Bot monitor not running (PID $MONITOR_PID not found)"
    exit 1
fi

echo "✅ Bot monitor running (PID: $MONITOR_PID)"
echo

# Check active battles
if [ ! -f "$BATTLES_FILE" ]; then
    echo "⚠️  No battles file found, assuming safe to restart"
    BATTLE_COUNT=0
else
    BATTLE_COUNT=$(jq '.count // 0' "$BATTLES_FILE" 2>/dev/null || echo 0)
fi

echo "Active battles: $BATTLE_COUNT"
echo

if [ "$BATTLE_COUNT" -eq 0 ]; then
    echo "✅ No active battles, safe to restart"
else
    echo "⏳ Waiting for battles to finish..."
    echo "   (Max wait: ${MAX_WAIT}s, check every 10s)"
    echo
    
    WAITED=0
    while [ "$BATTLE_COUNT" -gt 0 ] && [ "$WAITED" -lt "$MAX_WAIT" ]; do
        sleep 10
        WAITED=$((WAITED + 10))
        BATTLE_COUNT=$(jq '.count // 0' "$BATTLES_FILE" 2>/dev/null || echo 0)
        echo "   [$WAITED/${MAX_WAIT}s] Active battles: $BATTLE_COUNT"
    done
    
    if [ "$BATTLE_COUNT" -gt 0 ]; then
        echo
        echo "❌ Timeout: Still $BATTLE_COUNT active battles after ${MAX_WAIT}s"
        echo "   Use --force to kill anyway (will forfeit battles)"
        exit 1
    fi
    
    echo
    echo "✅ All battles finished!"
fi

echo
echo "🔄 Restarting bot monitor..."
echo

# Send graceful shutdown (SIGTERM for drain mode)
kill -TERM "$MONITOR_PID" || true

# Wait up to 60s for graceful exit
for i in {1..60}; do
    if ! kill -0 "$MONITOR_PID" 2>/dev/null; then
        echo "✅ Bot monitor stopped gracefully"
        break
    fi
    sleep 1
done

# Force kill if still running
if kill -0 "$MONITOR_PID" 2>/dev/null; then
    echo "⚠️  Forcing kill..."
    kill -9 "$MONITOR_PID" || true
    sleep 2
fi

# Start new monitor
echo "🚀 Starting new bot monitor..."
echo

# Use the same command from PID file or default
if [ -f "$PID_FILE" ]; then
    OLD_CMD=$(jq -r '.command // ""' "$PID_FILE" 2>/dev/null || echo "")
    if [ -n "$OLD_CMD" ]; then
        echo "Using previous command:"
        echo "  $OLD_CMD"
        echo
        bash -c "$OLD_CMD &"
    else
        echo "No previous command found, starting with default..."
        nohup venv/bin/python bot_monitor.py > monitor.log 2>&1 &
    fi
else
    echo "Starting with default command..."
    nohup venv/bin/python bot_monitor.py > monitor.log 2>&1 &
fi

sleep 2

# Verify new process started
if [ -f "$PID_FILE" ]; then
    NEW_PID=$(jq -r '.pid' "$PID_FILE" 2>/dev/null || echo "")
    if [ -n "$NEW_PID" ] && kill -0 "$NEW_PID" 2>/dev/null; then
        echo "✅ Bot monitor restarted successfully (PID: $NEW_PID)"
        echo
        echo "📊 Check status:"
        echo "   tail -f monitor.log"
        echo "   curl http://localhost:8777/status | jq"
        exit 0
    fi
fi

echo "⚠️  Could not verify new process started"
echo "   Check monitor.log for errors"
exit 1
