#!/bin/bash
# Fouler Play - Autonomous Mode (No Claude Required)
# Runs the bot with auto-restart on crash. Zero AI agent dependency.
# Usage: ./autonomous.sh [--account LEBOTJAMESXD005]
#
# This script keeps the bot alive indefinitely. Kill it with:
#   ./stop.sh  (or kill the PID in .pids/autonomous.pid)

set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

ACCOUNT="${1:-LEBOTJAMESXD005}"
RESTART_DELAY=10  # seconds between restart attempts
MAX_RAPID_RESTARTS=5  # max restarts within RAPID_WINDOW
RAPID_WINDOW=300  # 5 minutes

echo "=== Fouler Play Autonomous Mode ==="
echo "Account: $ACCOUNT"
echo "Auto-restart: ON (${RESTART_DELAY}s delay)"
echo "Kill with: ./stop.sh or kill $$"
echo ""

# Write our PID
mkdir -p .pids
echo $$ > .pids/autonomous.pid

# Track rapid restarts
declare -a restart_times=()

cleanup() {
    echo ""
    echo "[$(date)] Autonomous mode shutting down..."
    ./stop.sh 2>/dev/null || true
    rm -f .pids/autonomous.pid
    exit 0
}
trap cleanup SIGTERM SIGINT

while true; do
    # Check for rapid restart loop
    now=$(date +%s)
    # Filter to only recent restarts
    new_times=()
    for t in "${restart_times[@]}"; do
        if (( now - t < RAPID_WINDOW )); then
            new_times+=("$t")
        fi
    done
    restart_times=("${new_times[@]}")

    if (( ${#restart_times[@]} >= MAX_RAPID_RESTARTS )); then
        echo "[$(date)] ❌ Too many rapid restarts (${#restart_times[@]} in ${RAPID_WINDOW}s). Pausing 5 minutes..."
        sleep 300
        restart_times=()
    fi

    # Stop any existing instances
    ./stop.sh 2>/dev/null || true
    sleep 2

    echo "[$(date)] Starting bot as $ACCOUNT..."
    
    # Start the bot via start.sh
    ./start.sh
    
    # Wait for bot_monitor to exit (means crash or normal exit)
    MONITOR_PID=$(cat .pids/bot_monitor.pid 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['pid'])" 2>/dev/null || echo "")
    
    if [ -n "$MONITOR_PID" ]; then
        echo "[$(date)] Bot monitor PID: $MONITOR_PID — watching..."
        # Wait for process to die
        while kill -0 "$MONITOR_PID" 2>/dev/null; do
            sleep 5
        done
        echo "[$(date)] ⚠️ Bot monitor (PID $MONITOR_PID) exited. Restarting in ${RESTART_DELAY}s..."
    else
        echo "[$(date)] ⚠️ Could not find bot monitor PID. Restarting in ${RESTART_DELAY}s..."
    fi

    restart_times+=("$(date +%s)")
    sleep "$RESTART_DELAY"
done
