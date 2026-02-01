#!/bin/bash
# Fouler Play - Safe Stop Script

cd "$( dirname "${BASH_SOURCE[0]}" )"

echo "=== Stopping Fouler Play ==="

python3 process_manager.py stop

# Double-check nothing is left
sleep 1
if pgrep -f "fouler.*run.py" > /dev/null; then
    echo "⚠️  Some processes still running, forcing cleanup..."
    pkill -9 -f "fouler.*run.py"
    pkill -9 -f "bot_monitor"
fi

echo "✅ Fouler Play stopped"
