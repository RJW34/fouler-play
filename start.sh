#!/bin/bash
# Fouler Play - Single Source of Truth Startup
# This is the ONLY script that should start the bot

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "=== Fouler Play Startup ==="

# Check for existing processes (prevent duplicates)
if python3 process_manager.py status 2>/dev/null | grep -q "running"; then
    echo ""
    echo "❌ Bot is already running!"
    echo ""
    python3 process_manager.py status
    echo ""
    echo "To restart:"
    echo "  python3 process_manager.py stop"
    echo "  ./start.sh"
    exit 1
fi

# Clean up any stale PID files
if [ -d ".pids" ] && [ "$(ls -A .pids 2>/dev/null)" ]; then
    echo "Cleaning stale PID files..."
    python3 process_manager.py status > /dev/null  # Auto-cleanup
fi

# Start bot monitor (which starts run.py)
echo "Starting bot monitor..."
nohup python3 -u bot_monitor.py > bot_monitor_output.log 2>&1 &
MONITOR_PID=$!

# Write PID file
mkdir -p .pids
python3 -c "
import json, time
with open('.pids/bot_monitor.pid', 'w') as f:
    json.dump({
        'pid': $MONITOR_PID, 
        'name': 'bot_monitor',
        'started_at': time.time()
    }, f)
"

echo "✅ Bot monitor started (PID $MONITOR_PID)"
sleep 2

# Verify startup
python3 process_manager.py status

echo ""
echo "✅ Fouler Play is running"
echo ""
echo "Commands:"
echo "  python3 process_manager.py status  - Check status"
echo "  python3 process_manager.py stop    - Stop bot"
echo "  tail -f bot_monitor_output.log     - View logs"
echo ""
echo "⚠️  IMPORTANT: Max 2 concurrent battles"
echo "    If you see >2 workers, something is broken"
