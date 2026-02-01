#!/bin/bash
# ============================================
# Fouler Play - Standalone "No Claude" Runner
# ============================================
# Zero AI agent dependency. Pure heuristic bot.
# Works on any machine with Python 3.10+
#
# Usage: ./standalone.sh
# (will prompt for account name & password)
# ============================================

set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo ""
echo "  ⚔️  FOULER PLAY - Standalone Mode"
echo "  Pokemon Showdown Gen9 OU Battle Bot"
echo "  No Claude/AI agent required"
echo ""

# --- Prompt for credentials ---
if [ -z "$PS_USERNAME" ]; then
    read -p "  Showdown username: " PS_USERNAME
fi
if [ -z "$PS_PASSWORD" ]; then
    read -sp "  Showdown password (blank if none): " PS_PASSWORD
    echo ""
fi

# --- Config ---
WEBSOCKET="${PS_WEBSOCKET_URI:-wss://sim3.psim.us/showdown/websocket}"
FORMAT="${PS_FORMAT:-gen9ou}"
TEAM="${PS_TEAM:-gen9/ou/fat-team-1-stall}"
CONCURRENT="${PS_CONCURRENT:-2}"
SEARCH_TIME="${PS_SEARCH_TIME_MS:-3000}"

echo ""
echo "  Account:    $PS_USERNAME"
echo "  Format:     $FORMAT"
echo "  Concurrent: $CONCURRENT battles"
echo "  Server:     $WEBSOCKET"
echo ""

# --- Check Python ---
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found. Install Python 3.10+ first."
    exit 1
fi

# --- Setup venv if needed ---
if [ ! -d "venv" ]; then
    echo "  Setting up virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    echo "  Installing dependencies..."
    pip install -r requirements.txt
    echo "  ✅ Dependencies installed"
else
    source venv/bin/activate
fi

# --- Build password arg ---
PW_ARG=""
if [ -n "$PS_PASSWORD" ]; then
    PW_ARG="--ps-password $PS_PASSWORD"
fi

# --- Start battle API server in background ---
echo "  Starting battle API on port 8777..."
python3 -u streaming/stream_server.py > /dev/null 2>&1 &
API_PID=$!

# --- Launch bot workers ---
echo "  Launching $CONCURRENT battle workers..."
PIDS=()
for i in $(seq 1 $CONCURRENT); do
    python3 -u run.py \
        --websocket-uri "$WEBSOCKET" \
        --ps-username "$PS_USERNAME" \
        $PW_ARG \
        --bot-mode search_ladder \
        --pokemon-format "$FORMAT" \
        --team-name "$TEAM" \
        --search-time-ms "$SEARCH_TIME" \
        --run-count 999999 \
        --save-replay always \
        --log-level INFO &
    PIDS+=($!)
    echo "  Worker $i started (PID ${PIDS[-1]})"
    sleep 2
done

echo ""
echo "  ✅ Fouler Play running! $CONCURRENT workers laddering as $PS_USERNAME"
echo "  Battle API: http://$(hostname -I | awk '{print $1}'):8777/battles"
echo ""
echo "  Press Ctrl+C to stop all workers."
echo ""

# --- Cleanup on exit ---
cleanup() {
    echo ""
    echo "  Stopping workers..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    kill "$API_PID" 2>/dev/null
    echo "  ✅ All stopped."
    exit 0
}
trap cleanup SIGINT SIGTERM

# --- Wait for workers ---
wait
