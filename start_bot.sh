#!/bin/bash
# Start BugInTheCode with auto-restart on crash
# Maintains correct config (3 concurrent, all teams, all fixes)

set -e

REPO="/home/ryan/projects/fouler-play"
VENV="$REPO/venv/bin/python"
PIDFILE="$REPO/.bot_restart.pid"
LOGDIR="$REPO/restart_logs"

mkdir -p "$LOGDIR"

# Trap SIGTERM to clean shutdown
trap 'echo "Caught SIGTERM, shutting down"; exit 0' TERM INT

echo "[START] BugInTheCode auto-restart wrapper"
echo "[INFO] PID: $$"
echo "[INFO] Logging to: $LOGDIR"

# Write wrapper PID
echo "$$" > "$PIDFILE"

RESTART_COUNT=0
MAX_RESTARTS_PER_HOUR=12  # More than this = likely infinite loop

while true; do
    RESTART_COUNT=$((RESTART_COUNT + 1))
    RESTART_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    RESTART_LOG="$LOGDIR/restart_$RESTART_COUNT.log"
    
    echo "[$RESTART_TS] Starting bot (attempt $RESTART_COUNT)" | tee -a "$LOGDIR/restart_history.log"
    
    "$VENV" "$REPO/run.py" \
        --websocket-uri wss://sim3.psim.us/showdown/websocket \
        --ps-username BugInTheCode \
        --ps-password HeracrossBattle2026! \
        --bot-mode search_ladder \
        --pokemon-format gen9ou \
        --search-time-ms 3000 \
        --run-count 999999 \
        --save-replay always \
        --log-to-file \
        --log-level INFO \
        --max-mcts-battles 4 \
        --max-concurrent-battles 3 \
        --team-names gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-pivot,gen9/ou/fat-team-3-dondozo \
        2>&1 | tee -a "$RESTART_LOG"
    
    EXIT_CODE=$?
    EXIT_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    echo "[$EXIT_TS] Bot exited with code $EXIT_CODE (attempt $RESTART_COUNT)" | tee -a "$LOGDIR/restart_history.log"
    
    # Check restart spam
    if [ $RESTART_COUNT -gt $MAX_RESTARTS_PER_HOUR ]; then
        echo "[FATAL] Too many restart attempts ($RESTART_COUNT). Likely infinite loop. Aborting."
        echo "[FATAL] Check logs in $LOGDIR for root cause"
        exit 1
    fi
    
    # Cooldown before restart
    echo "[WAIT] Restarting in 30s..."
    sleep 30
done
