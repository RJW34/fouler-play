#!/bin/bash
# Report diagnostic status to Discord using clawdbot message tool

CHANNEL_ID="1466691161363054840"  # #project-fouler-play

# Run diagnostic
STATUS_OUTPUT=$(cd /home/ryan/projects/fouler-play/diagnostics && ./heartbeat-diagnostic.sh)

# Extract status
STREAM=$(echo "$STATUS_OUTPUT" | grep -o "Stream: [^|]*" | awk '{print $2}')
BOT=$(echo "$STATUS_OUTPUT" | grep -o "Bot: [^|]*" | awk '{print $2}')
OVERLAY=$(echo "$STATUS_OUTPUT" | grep -o "Overlay: [^|]*" | awk '{print $2}')
ELO=$(echo "$STATUS_OUTPUT" | grep -o "Elo: .*" | cut -d: -f2-)

# Count observed games
GAME_COUNT=$(ls /home/ryan/projects/fouler-play/research/observed-games/*.json 2>/dev/null | wc -l)

# Check observer
if pgrep -f "high-elo-observer.js" > /dev/null; then
    OBSERVER="✅ Running"
else
    OBSERVER="❌ Stopped"
fi

# Build message
MESSAGE="📊 **Fouler Play Status** ($(date '+%H:%M'))

\`\`\`
Stream:  $STREAM | Bot: $BOT | Overlay: $OVERLAY
Elo:    $ELO
\`\`\`

**Learning System:**
- Observer: $OBSERVER
- Games Collected: $GAME_COUNT

_Auto-reported from heartbeat diagnostic_"

# Send to Discord (using clawdbot CLI if available)
# This is a placeholder - actual implementation would use the gateway's message API
echo "$MESSAGE"
echo "$MESSAGE" > /tmp/discord-status-report.txt

# Note: To actually send, we'd need to integrate with clawdbot's message system
# For now, this creates the formatted message that can be manually sent or
# integrated into the heartbeat workflow
