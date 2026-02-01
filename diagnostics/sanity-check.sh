#!/bin/bash
# Comprehensive sanity check for all Fouler Play systems
# Run this during heartbeats to verify everything is working

set -e

echo "=== FOULER PLAY SANITY CHECK ==="
echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

ERRORS=0

# 1. Bot processes
echo "1️⃣ Bot Processes:"
if python3 /home/ryan/projects/fouler-play/process_manager.py status | grep -q "2 running"; then
    echo "   ✅ Bot running (2 processes)"
else
    echo "   ❌ Bot NOT running properly"
    ERRORS=$((ERRORS + 1))
fi

# 2. Bot actively battling
echo ""
echo "2️⃣ Bot Activity:"
if tail -50 /home/ryan/projects/fouler-play/monitor.log | grep -q "Choice:\|MCTS"; then
    RECENT_MOVE=$(tail -50 /home/ryan/projects/fouler-play/monitor.log | grep "Choice:" | tail -1 | awk '{print $3}')
    echo "   ✅ Bot making decisions (recent: $RECENT_MOVE)"
else
    echo "   ⚠️ No recent bot activity in logs"
fi

# 3. Stream processes
echo ""
echo "3️⃣ Stream Processes:"
if ps aux | grep -q "[a]uto_stream_firefox"; then
    echo "   ✅ Stream script running"
else
    echo "   ❌ Stream script NOT running"
    ERRORS=$((ERRORS + 1))
fi

if ps aux | grep -q "[f]fmpeg.*twitch"; then
    echo "   ✅ ffmpeg streaming"
else
    echo "   ❌ ffmpeg NOT streaming"
    ERRORS=$((ERRORS + 1))
fi

# 4. Twitch live status
echo ""
echo "4️⃣ Twitch Status:"
UPTIME=$(curl -s "https://decapi.me/twitch/uptime/dekubotbygoofy" 2>/dev/null)
if echo "$UPTIME" | grep -qv "offline"; then
    echo "   ✅ Stream LIVE ($UPTIME)"
else
    echo "   ❌ Stream OFFLINE"
    ERRORS=$((ERRORS + 1))
fi

# 5. Firefox battle windows
echo ""
echo "5️⃣ Firefox Windows:"
FIREFOX_COUNT=$(ps aux | grep firefox | grep -v grep | wc -l)
if [ "$FIREFOX_COUNT" -ge 2 ]; then
    echo "   ✅ Firefox running ($FIREFOX_COUNT processes)"
else
    echo "   ⚠️ Few Firefox processes ($FIREFOX_COUNT)"
fi

# 6. High-elo observer
echo ""
echo "6️⃣ High-Elo Observer:"
if ps aux | grep -q "[h]igh-elo-observer"; then
    GAME_COUNT=$(ls /home/ryan/projects/fouler-play/research/observed-games/*.json 2>/dev/null | wc -l)
    echo "   ✅ Observer running ($GAME_COUNT games collected)"
else
    echo "   ⚠️ Observer NOT running"
fi

# 7. System resources
echo ""
echo "7️⃣ System Resources:"
LOAD=$(uptime | awk '{print $(NF-2)}' | tr -d ',')
MEM_PERCENT=$(free | grep Mem | awk '{print int($3/$2*100)}')
echo "   Load: $LOAD | Memory: ${MEM_PERCENT}%"
if (( $(echo "$LOAD > 4.0" | bc -l) )); then
    echo "   ⚠️ High system load!"
fi

# 8. Discord Webhooks (silent check - no spam)
echo ""
echo "8️⃣ Discord Webhooks: (skipped - no need to ping every heartbeat)"

echo ""
echo "=== SANITY CHECK COMPLETE ==="
if [ $ERRORS -eq 0 ]; then
    echo "✅ ALL SYSTEMS OPERATIONAL"
    exit 0
else
    echo "❌ $ERRORS CRITICAL ERRORS DETECTED"
    exit 1
fi
