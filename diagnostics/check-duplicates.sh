#!/bin/bash
# Check for duplicate Fouler Play processes
# Should ONLY ever see: 1 bot_monitor + 1 run.py with 2 workers

echo "=== Fouler Play Duplicate Check ==="

# Count bot_monitor processes
MONITOR_COUNT=$(pgrep -fc "python.*bot_monitor.py")
echo "bot_monitor processes: $MONITOR_COUNT (expected: 0 or 1)"

# Count run.py processes  
RUN_COUNT=$(pgrep -fc "python.*run.py")
echo "run.py processes: $RUN_COUNT (expected: 0 or 1)"

# Check process_manager status
echo ""
echo "Process Manager Status:"
cd /home/ryan/projects/fouler-play && python3 process_manager.py status

# Look for any suspicious duplicates
echo ""
echo "All fouler-play Python processes:"
ps aux | grep -E "fouler.*python|python.*fouler|bot_monitor|run.py" | grep -v grep | grep -v "check-duplicates"

# Verdict
echo ""
if [ "$MONITOR_COUNT" -gt 1 ] || [ "$RUN_COUNT" -gt 1 ]; then
    echo "❌ DUPLICATES DETECTED - Something is spawning multiple processes!"
    echo "   Run ./stop.sh to clean up"
    exit 1
elif [ "$MONITOR_COUNT" -eq 1 ] && [ "$RUN_COUNT" -eq 1 ]; then
    echo "✅ Process count looks good (1 monitor, 1 bot with 2 workers)"
    exit 0
elif [ "$MONITOR_COUNT" -eq 0 ] && [ "$RUN_COUNT" -eq 0 ]; then
    echo "⚠️  No processes running (bot is stopped)"
    exit 0
else
    echo "⚠️  Unexpected process state - investigate manually"
    exit 1
fi
