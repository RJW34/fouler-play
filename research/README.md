# High-Elo Learning System

**Philosophy:** The bot can't just play randomly. It needs to learn from players who actually win at high elo.

## System Overview

### 1. Observer (`high-elo-observer.js`) - Enhanced
Continuously spectates gen9ou battles above 1700 elo on Pokemon Showdown.

**NEW: Team Archetype Filtering**
- Classifies teams as Fat/Bulky, Hyper Offense, Balanced
- **Only saves games with fat teams** (matches our bot's playstyle)
- Tags games: WIN_WITH_OUR_ARCHETYPE or LOSS_WITH_OUR_ARCHETYPE
- Learns from both wins (what works) AND losses (what NOT to do)

**What it captures:**
- Turn-by-turn decisions
- Switch patterns
- Move choices by situation
- Team compositions
- Game outcomes (who won)
- **Team archetype classification**

**How to run:**
```bash
cd /home/ryan/projects/fouler-play/research
npm install ws  # if not already installed
node high-elo-observer.js
```

**Output:** JSON logs saved to `observed-games/`

**Recommended:** Run in background during heartbeats to continuously collect data

### 2. Analyzer (`analyze-patterns.js`) - Enhanced
Processes observed games to extract winning patterns AND losing patterns.

**NEW: Win vs Loss Analysis**
- Compares wins and losses with our team archetype
- Identifies moves that correlate with LOSSES (avoid these!)
- Extracts common mistakes from lost games
- Filters out irrelevant team styles

**Analysis categories:**
- **Opening moves:** Which first turns win vs lose?
- **Switch patterns:** When do winners switch vs. losers?
- **Late-game plays:** Winning moves vs losing moves
- **Common mistakes:** Patterns that appear more in losses
- **Team compositions:** Which Pokemon appear on winning fat teams?

**How to run:**
```bash
node analyze-patterns.js
```

**Output:**
- `learned-patterns/learned-patterns.json` - Raw data
- `learned-patterns/summary.md` - Human-readable insights

### 3. Integration (TODO)
Feed learned patterns back into bot decision-making.

**Planned improvements:**
- Weight move selection by win rate in similar situations
- Recognize when to switch based on top player patterns
- Identify end-game scenarios and use proven strategies
- Prefer team compositions that win at high elo

## Current Understanding Gaps

Things the bot needs to learn from observation:

1. **Momentum recognition:** When is the game slipping away? When do top players switch aggressively vs. play safe?

2. **Prediction plays:** Which moves do high-elo players predict most often? (switching on suspected super-effective move, etc.)

3. **Team synergy:** Which Pokemon work well together? What are the "glue" Pokemon that enable strategies?

4. **Move prioritization:** In neutral situations, which moves do winners use most?

5. **Sacrifice patterns:** When do top players intentionally sacrifice a Pokemon for positioning?

## Usage in Bot Development

### Phase 1: Data Collection (current)
- Run observer continuously
- Collect 50-100 high-elo games
- Build baseline pattern database

### Phase 2: Pattern Recognition
- Analyze collected games
- Identify top 10 opening strategies
- Map common switch triggers
- Categorize end-game scenarios

### Phase 3: Bot Integration
- Modify battle logic to consult pattern database
- Weight decisions by observed win rates
- Add "copy top player" mode for testing
- A/B test pattern-based decisions vs. current logic

### Phase 4: Recursive Improvement
- Track bot's elo progression
- When bot plateaus, collect games at NEW elo threshold
- Update pattern database with higher-level play
- Repeat cycle

## Autonomous Learning Loop

**Every 24 hours:**
1. Run analyzer on collected games
2. Generate insights report
3. Post findings to #fouler-play channel
4. Update bot's decision weights based on patterns
5. Test changes in a few battles
6. Commit improvements if elo doesn't drop

**Every week:**
- Review bot's elo trend
- If improving: keep collecting at current threshold
- If plateauing: raise observer threshold (+100 elo)
- If declining: revert recent changes, analyze losses

## Example Insights (with Win/Loss Analysis)

```
✅ Best Opening Moves (High Win Rate):
- Corviknight: U-turn → 85% (17W / 3L)
- Clodsire: Toxic → 78% (14W / 4L)
- Ting-Lu: Stealth Rock → 72% (13W / 5L)

❌ Avoid These Openings (High Loss Rate):
- Blissey: Seismic Toss → 25% (3W / 9L) - **AVOID**
- Toxapex: Haze → 33% (2W / 4L) - **AVOID**

🚫 Common Mistakes (From Losses):
- **AVOID:** Skarmory: Whirlwind (7 losses vs 1 win)
- **AVOID:** Corviknight: Brave Bird (5 losses vs 2 wins)

Switch Patterns (Wins vs Losses):
Wins:  predicted-counter: 42, early-game: 15
Losses: predicted-counter: 28, early-game: 25
→ Losing players switch early more often

Late-Game Best Moves:
1. Extreme Speed: 92% (12W / 1L)
2. Ice Shard: 87% (13W / 2L)
3. Aqua Jet: 75% (9W / 3L)
→ Priority moves still dominate, but not all equally
```

## Integration with Diagnostics

Add to heartbeat cycle:
```bash
# Check if observer is running
if ! pgrep -f "high-elo-observer.js"; then
    cd /home/ryan/projects/fouler-play/research
    node high-elo-observer.js > observer.log 2>&1 &
fi

# Run analysis if we have new games
GAME_COUNT=$(ls observed-games/*.json 2>/dev/null | wc -l)
if [ $GAME_COUNT -gt 10 ]; then
    node analyze-patterns.js
fi
```

## Next Steps

- [ ] Test observer connection to Showdown
- [ ] Collect first 10 high-elo games
- [ ] Run initial pattern analysis
- [ ] Identify top 3 learnings
- [ ] Draft bot integration plan
- [ ] Build A/B testing framework
- [ ] Track elo before/after pattern integration

---

**Core Philosophy:** Don't just play. Learn from winners. Iterate. Improve recursively.
