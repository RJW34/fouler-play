#!/bin/bash
# stop_all.sh - Stop ALL Fouler Play processes (managed + orphans)

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "=== Stopping Fouler Play ==="

# Use the process manager
python3 process_manager.py stop

echo ""
echo "✅ Shutdown complete"
