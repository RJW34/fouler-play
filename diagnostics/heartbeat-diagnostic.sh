#!/bin/bash
# Heartbeat diagnostic - quick status check for regular monitoring

SHOWDOWN_USER="dekubotbygoofy"
ELO_CACHE="/home/ryan/projects/fouler-play/diagnostics/elo-cache.txt"

# Quick status checks
STREAM_UP=$(pgrep -f "ffmpeg.*rtmp.*twitch" > /dev/null && echo "✅" || echo "❌")
BOT_RUNNING=$(pgrep -f "node.*src/index" > /dev/null && echo "✅" || echo "❌")
OVERLAY_UP=$(curl -s http://localhost:3001/overlay > /dev/null 2>&1 && echo "✅" || echo "❌")

# Get current elo
CURRENT_ELO=$(curl -s "https://play.pokemonshowdown.com/~~showdown/action.php?act=ladderget&output=json" | jq -r ".[] | select(.username==\"$SHOWDOWN_USER\") | .elo" 2>/dev/null || echo "N/A")

# Compare with cached elo
PREV_ELO="N/A"
if [ -f "$ELO_CACHE" ]; then
    PREV_ELO=$(cat "$ELO_CACHE")
fi

# Save current elo
echo "$CURRENT_ELO" > "$ELO_CACHE"

# Calculate change
ELO_CHANGE=""
if [[ "$CURRENT_ELO" != "N/A" && "$PREV_ELO" != "N/A" ]]; then
    DIFF=$((CURRENT_ELO - PREV_ELO))
    if [ $DIFF -gt 0 ]; then
        ELO_CHANGE=" (+$DIFF)"
    elif [ $DIFF -lt 0 ]; then
        ELO_CHANGE=" ($DIFF)"
    fi
fi

# Output compact status
echo "Stream: $STREAM_UP | Bot: $BOT_RUNNING | Overlay: $OVERLAY_UP | Elo: ${CURRENT_ELO}${ELO_CHANGE}"

# If anything is down, trigger auto-fix
if [[ "$STREAM_UP" == "❌" || "$BOT_RUNNING" == "❌" || "$OVERLAY_UP" == "❌" ]]; then
    /home/ryan/projects/fouler-play/diagnostics/auto-fix.sh
fi
