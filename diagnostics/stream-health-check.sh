#!/bin/bash
# Fouler Play Stream Health Diagnostic
# Verifies: stream status, visual quality, bot activity, elo tracking

TWITCH_URL="https://twitch.tv/dekubotbygoofy"
SHOWDOWN_USER="dekubotbygoofy"
LOG_FILE="/home/ryan/projects/fouler-play/diagnostics/health-check.log"

echo "=== Fouler Play Health Check $(date) ===" | tee -a "$LOG_FILE"

# 1. Check ffmpeg process
echo -n "Stream process: " | tee -a "$LOG_FILE"
if pgrep -f "ffmpeg.*rtmp.*twitch" > /dev/null; then
    echo "✅ Running" | tee -a "$LOG_FILE"
else
    echo "❌ NOT RUNNING" | tee -a "$LOG_FILE"
    exit 1
fi

# 2. Check Twitch stream status via API
echo -n "Twitch live status: " | tee -a "$LOG_FILE"
STREAM_STATUS=$(curl -s "$TWITCH_URL" | grep -o "isLiveBroadcast" || echo "offline")
if [[ "$STREAM_STATUS" == *"isLiveBroadcast"* ]]; then
    echo "✅ LIVE" | tee -a "$LOG_FILE"
else
    echo "❌ OFFLINE" | tee -a "$LOG_FILE"
fi

# 3. Take stream screenshot for visual verification
echo -n "Stream visual check: " | tee -a "$LOG_FILE"
SCREENSHOT="/home/ryan/projects/fouler-play/diagnostics/stream-snapshot-$(date +%Y%m%d-%H%M%S).png"
# Use streamlink to capture a frame
timeout 10 streamlink "$TWITCH_URL" best -o "$SCREENSHOT" --hls-duration 2 2>/dev/null
if [ -f "$SCREENSHOT" ]; then
    echo "✅ Captured ($SCREENSHOT)" | tee -a "$LOG_FILE"
else
    echo "⚠️  Could not capture" | tee -a "$LOG_FILE"
fi

# 4. Check bot activity on Pokemon Showdown
echo -n "Bot activity: " | tee -a "$LOG_FILE"
BOT_STATUS=$(curl -s "https://play.pokemonshowdown.com/~~showdown/action.php?act=ladderget&output=json" | grep -o "$SHOWDOWN_USER" || echo "not found")
if [[ "$BOT_STATUS" == *"$SHOWDOWN_USER"* ]]; then
    echo "✅ Active on ladder" | tee -a "$LOG_FILE"
else
    echo "⚠️  Not visible on ladder" | tee -a "$LOG_FILE"
fi

# 5. Get current elo
echo -n "Current elo: " | tee -a "$LOG_FILE"
ELO=$(curl -s "https://play.pokemonshowdown.com/~~showdown/action.php?act=ladderget&output=json" | jq -r ".[] | select(.username==\"$SHOWDOWN_USER\") | .elo" 2>/dev/null || echo "N/A")
echo "$ELO" | tee -a "$LOG_FILE"

# 6. Check overlay health
echo -n "Overlay status: " | tee -a "$LOG_FILE"
if curl -s http://localhost:3001/overlay > /dev/null 2>&1; then
    echo "✅ Serving" | tee -a "$LOG_FILE"
else
    echo "❌ Not responding" | tee -a "$LOG_FILE"
fi

echo "=== End Health Check ===" | tee -a "$LOG_FILE"
echo "" >> "$LOG_FILE"
