#!/bin/bash
# Fouler Play Stream Controller
# Opens current battles in Chrome, starts ffmpeg to Twitch
# Run this as: ./streaming/stream_controller.sh

set -e
cd "$(dirname "$0")/.."

STREAM_KEY=$(cat /home/ryan/Desktop/twitchstreamingkey.txt)
CHROME_DATA="/tmp/chrome-stream"
DISPLAY=:0
export DISPLAY

CURRENT_BATTLE=""
FFMPEG_PID=""

cleanup() {
    echo "[CTRL] Shutting down..."
    [ -n "$FFMPEG_PID" ] && kill $FFMPEG_PID 2>/dev/null
    killall -9 chrome 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

get_current_battle() {
    # Read from bot_monitor's output (via /proc/fd if file is deleted)
    local pid=$(pgrep -f "bot_monitor.py" | head -1)
    if [ -n "$pid" ] && [ -f "/proc/$pid/fd/1" ]; then
        tail -c 50000 /proc/$pid/fd/1 2>/dev/null | grep -o "battle-gen9ou-[0-9]*" | tail -1
    fi
}

open_battle() {
    local battle=$1
    echo "[CTRL] Opening battle: $battle"
    
    # Close old Chrome
    killall -9 chrome 2>/dev/null
    sleep 2
    
    # Open new Chrome with battle
    google-chrome --no-first-run --no-sandbox --ozone-platform=x11 \
        --disable-extensions --disable-sync --no-default-browser-check \
        --user-data-dir="$CHROME_DATA" \
        --window-size=1920,1080 --window-position=0,0 \
        --start-fullscreen \
        "https://play.pokemonshowdown.com/#${battle}" &>/dev/null &
    
    sleep 5
    
    # Ensure fullscreen
    xdotool search --name "Showdown" windowactivate --sync 2>/dev/null
    xdotool key F11 2>/dev/null
}

start_stream() {
    echo "[CTRL] Starting Twitch stream..."
    ffmpeg -y \
        -f x11grab -video_size 1920x1080 -framerate 15 -i :0 \
        -f lavfi -i anullsrc=r=44100:cl=stereo \
        -c:v libx264 -preset veryfast -b:v 2500k -maxrate 2500k -bufsize 5000k \
        -pix_fmt yuv420p -g 30 \
        -c:a aac -b:a 128k -ar 44100 \
        -f flv "rtmp://live.twitch.tv/app/${STREAM_KEY}" \
        > /tmp/ffmpeg-stream.log 2>&1 &
    FFMPEG_PID=$!
    echo "[CTRL] ffmpeg started (PID $FFMPEG_PID)"
}

# Start the stream
start_stream
sleep 3

# Check ffmpeg is alive
if ! kill -0 $FFMPEG_PID 2>/dev/null; then
    echo "[CTRL] ERROR: ffmpeg failed to start!"
    cat /tmp/ffmpeg-stream.log
    exit 1
fi

echo "[CTRL] 🔴 Stream is live! Monitoring for battles..."

# Main loop - watch for battle changes
while true; do
    NEW_BATTLE=$(get_current_battle)
    
    if [ -n "$NEW_BATTLE" ] && [ "$NEW_BATTLE" != "$CURRENT_BATTLE" ]; then
        CURRENT_BATTLE="$NEW_BATTLE"
        open_battle "$CURRENT_BATTLE"
    fi
    
    # Check ffmpeg health
    if ! kill -0 $FFMPEG_PID 2>/dev/null; then
        echo "[CTRL] ffmpeg died, restarting..."
        start_stream
        sleep 3
    fi
    
    sleep 10
done
