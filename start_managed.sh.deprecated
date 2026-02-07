#!/bin/bash
# start_managed.sh - Start Fouler Play with proper process tracking

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "=== Fouler Play Startup (Managed) ==="
echo "Working directory: $SCRIPT_DIR"

# Check if already running
if [ -d ".pids" ] && [ "$(ls -A .pids 2>/dev/null)" ]; then
    echo "⚠️  Detected existing PID files. Checking status..."
    python3 process_manager.py status
    
    read -p "Stop existing processes and restart? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        python3 process_manager.py stop
        sleep 2
    else
        echo "Aborted. Use 'python3 process_manager.py stop' to stop existing processes."
        exit 1
    fi
fi

# Create PID directory
mkdir -p .pids

# Start bot monitor
echo "Starting bot monitor..."
nohup python3 -u bot_monitor.py > bot_monitor_output.log 2>&1 &
MONITOR_PID=$!

# Write monitor PID
python3 -c "
import json
with open('.pids/bot_monitor.pid', 'w') as f:
    json.dump({'pid': $MONITOR_PID, 'name': 'bot_monitor', 'started_at': $(date +%s)}, f)
"

echo "Bot monitor started (PID $MONITOR_PID)"
sleep 2

# Show status
python3 process_manager.py status

echo ""
echo "✅ Fouler Play started successfully"
echo ""
echo "Commands:"
echo "  python3 process_manager.py status  - Show process status"
echo "  python3 process_manager.py stop    - Stop all processes"
echo ""
echo "Logs:"
echo "  tail -f bot_monitor_output.log"
