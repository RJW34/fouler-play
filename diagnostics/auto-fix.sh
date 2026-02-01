#!/bin/bash
# Automated fixes for common Fouler Play issues
# ⚠️ CRITICAL: Never spawn duplicates - always check first

LOG_FILE="/home/ryan/projects/fouler-play/diagnostics/auto-fix.log"

echo "=== Auto-Fix Run $(date) ===" | tee -a "$LOG_FILE"

# Check if bot process died
cd /home/ryan/projects/fouler-play || exit 1

# Use process_manager to check actual state (avoids duplicate spawns)
if python3 process_manager.py status 2>/dev/null | grep -q "0 running"; then
    echo "Bot not running, restarting..." | tee -a "$LOG_FILE"
    
    # Use canonical start script (has duplicate detection)
    if [ -f "./start.sh" ]; then
        ./start.sh >> "$LOG_FILE" 2>&1 &
        echo "Bot restarted via start.sh" | tee -a "$LOG_FILE"
    else
        echo "ERROR: No start.sh found - manual intervention needed" | tee -a "$LOG_FILE"
    fi
else
    # Bot is running - check if stuck
    BOT_PID=$(pgrep -f "run.py" | head -1)
    if [ -n "$BOT_PID" ]; then
        CPU_USE=$(ps -p "$BOT_PID" -o %cpu= | awk '{print int($1)}')
        if [ "$CPU_USE" -lt 1 ]; then
            echo "Bot running but idle (CPU: ${CPU_USE}%) - may be stuck" | tee -a "$LOG_FILE"
            # Don't auto-restart stuck processes - log for investigation
        fi
    fi
fi

# Check if stream process died
if ! pgrep -f "ffmpeg.*rtmp.*twitch" > /dev/null; then
    echo "Stream process dead" | tee -a "$LOG_FILE"
    # Streaming is separate - don't auto-restart yet
fi

# Check overlay health
if ! curl -s http://localhost:3001/overlay > /dev/null 2>&1; then
    echo "Overlay not responding" | tee -a "$LOG_FILE"
fi

echo "=== Auto-Fix Complete ===" | tee -a "$LOG_FILE"
echo "" >> "$LOG_FILE"
