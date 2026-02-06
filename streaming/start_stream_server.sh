#!/bin/bash
# Start the Fouler Play stream server
# Run this before starting bot_monitor.py for auto-streaming

cd "$(dirname "$0")/.."
nohup python3 streaming/stream_server.py > /tmp/stream_server.log 2>&1 &
echo "Stream server started (PID: $!)"
echo "API: http://localhost:8777/status"
echo "Overlay: http://localhost:8777/overlay"
