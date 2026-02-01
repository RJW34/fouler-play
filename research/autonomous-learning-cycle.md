# Autonomous Learning Cycle

**Goal:** Recursively improve bot by continuously learning from high-elo players.

## System Architecture

```
High-Elo Games (Showdown)
         ↓
   Observer (always running)
         ↓
   Game Logs (JSON)
         ↓
   Analyzer (periodic)
         ↓
   Pattern Database
         ↓
   Bot Decision Weights
         ↓
   Elo Performance Tracking
         ↓
   [Raise threshold] → Loop back
```

## Heartbeat Integration

### Every Heartbeat (30 min)
```bash
# Check observer status
if ! pgrep -f "high-elo-observer.js" > /dev/null; then
    cd /home/ryan/projects/fouler-play/research
    ./start-observer.sh
fi
```

### When 10+ Games Collected
```bash
cd /home/ryan/projects/fouler-play/research
GAME_COUNT=$(ls observed-games/*.json 2>/dev/null | wc -l)

if [ $GAME_COUNT -ge 10 ]; then
    echo "🎯 Analyzing $GAME_COUNT high-elo games..."
    node analyze-patterns.js
    
    # Post insights to Discord
    # (integrate with message tool)
fi
```

### Weekly Review
- Compare bot's current elo to baseline
- If elo increased → keep current patterns
- If elo plateaued → raise observer threshold (+100 elo)
- If elo decreased → revert recent changes

## Pattern Application (Future)

### Phase 1: Manual Integration
1. Analyze collected games
2. Identify top 3 learnings
3. Manually code improvements into bot
4. Test in 10 battles
5. Commit if successful

### Phase 2: Automated Weighting
1. Bot reads `learned-patterns.json`
2. Weights move selection by observed win rates
3. Applies switch patterns from top players
4. Uses late-game strategies automatically

### Phase 3: Self-Improvement Loop
1. Bot tracks its own game outcomes
2. Compares its decisions to high-elo patterns
3. Auto-adjusts weights based on success
4. Raises learning threshold as it improves

## Current Status

- ✅ Observer running (PID 91076)
- ✅ Connected to Showdown
- ✅ Monitoring gen9ou games >1700 elo
- ⏳ Waiting for first game capture
- 📊 Analysis triggers at 10 games

## Autonomous Actions

**I will automatically:**
1. Restart observer if it crashes
2. Run analysis when sufficient games collected
3. Post insights to #fouler-play channel
4. Track elo trends weekly
5. Propose bot improvements based on learnings

**I won't automatically:**
- Modify bot code (requires testing)
- Change elo threshold (needs discussion)
- Delete collected games (preserve learning data)

## Success Metrics

**Short-term (1 week):**
- Collect 50+ high-elo games
- Generate first pattern analysis
- Identify top 5 winning strategies

**Medium-term (1 month):**
- Integrate 3 learned patterns into bot
- Track elo improvement post-integration
- Build historical pattern database

**Long-term (3 months):**
- Bot consistently plays at learned level
- Automatic pattern updates as meta shifts
- Self-improving decision weights

---

**Philosophy:** The bot isn't "done" when it works. It's done when it learns and improves autonomously.
