#!/bin/bash
# Start high-elo observer in background

cd /home/ryan/projects/fouler-play/research

# Check if already running
if pgrep -f "high-elo-observer.js" > /dev/null; then
    echo "✅ Observer already running (PID: $(pgrep -f "high-elo-observer.js"))"
    exit 0
fi

# Start in background
echo "🚀 Starting high-elo observer..."
node high-elo-observer.js > observer.log 2>&1 &

sleep 2

if pgrep -f "high-elo-observer.js" > /dev/null; then
    echo "✅ Observer started (PID: $(pgrep -f "high-elo-observer.js"))"
    echo "   Log: $(pwd)/observer.log"
    echo "   Games: $(pwd)/observed-games/"
else
    echo "❌ Observer failed to start"
    echo "Check observer.log for errors"
    exit 1
fi
