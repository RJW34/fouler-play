# Fat Team Implementation Plan

**Goal:** Get bot to 1700+ ELO using fat/defensive teams  
**Timeline:** 3 weeks  
**Top Player Request:** Fast team testing vs strong players

## Phase 1: Core Infrastructure (This Week)

### ✅ Completed
- [x] Team analysis (3 fat teams from top player)
- [x] Team file conversion to bot format
- [x] Playstyle configuration system created
- [x] Config support for playstyle selection

### 🔨 In Progress
- [ ] Integrate playstyle config into MCTS scoring
- [ ] Implement fat-specific heuristics
- [ ] Test teams on ladder

### Implementation Checklist

#### 1. Switch Penalty Reduction (CRITICAL)
**Current Problem:** Bot probably switches too little  
**Fix Needed:** Reduce switch penalty for fat teams

**Files to modify:**
- `fp/search/main.py` (MCTS scoring)
- Look for switch penalty calculations
- Apply `playstyle_config.switch_penalty_multiplier` (0.6 for fat)

**Impact:** Bot will switch more aggressively to maintain good matchups

---

#### 2. Recovery Move Valuation
**Current Problem:** Recovery moves undervalued  
**Fix Needed:** Boost recovery scoring for fat teams

**Implementation:**
- Detect recovery moves (Roost, Recover, Soft-Boiled, etc.)
- Apply `recovery_value_multiplier` (1.5 for fat)
- Consider HP threshold (recover at 60%, not 20%)

**Impact:** Bot maintains defensive backbone instead of saccing walls

---

#### 3. Hazard Priority
**Current Problem:** Generic hazard valuation  
**Fix Needed:** Fat teams NEED hazards to chip

**Implementation:**
- Detect hazard moves (Spikes, Stealth Rock)
- Apply `hazard_value_multiplier` (1.5 for fat)
- Prioritize getting hazards up early

**Impact:** Bot establishes win condition through chip damage

---

#### 4. Pivot Move Intelligence
**Current Problem:** Bot doesn't understand pivot value  
**Fix Needed:** Value U-turn/Parting Shot for momentum + scouting

**Implementation:**
- Detect pivot moves (u-turn, volt-switch, parting-shot, etc.)
- Apply `pivot_value_multiplier` (1.6 for fat)
- Bonus for gaining information on opponent's team

**Impact:** Bot maintains momentum and scouts safely

---

#### 5. Ability-Aware Play

##### A. Unaware Detection (CRITICAL for Team 3)
**Problem:** Bot will waste setup vs Dondozo  
**Fix:**
- Detect Unaware ability
- Massive penalty for setup moves vs Unaware mons
- Flag: "Don't Swords Dance vs Dondozo!"

**Affected Mons:** Dondozo, Quagsire, Skeledirge, Clefable

##### B. Regenerator Awareness
**Problem:** Bot doesn't value Regenerator healing  
**Fix:**
- Detect Regenerator ability (Slowking-Galar, Corviknight)
- Reduce switch penalty for Regen mons (free 33% heal)
- Switch Regen mons even at 50% HP

**Impact:** Maximize passive healing from pivoting

##### C. Pressure Stalling (Kyurem Sub/Protect)
**Problem:** Bot doesn't understand PP stalling  
**Fix:**
- Detect Pressure ability + Sub/Protect combo
- Value stalling vs low-PP moves
- Kyurem can drain opponent's resources

---

#### 6. Setup Patience
**Current Problem:** May setup too early  
**Fix Needed:** Only setup when opponent is weakened

**Implementation:**
- Count opponent's healthy mons
- Reduce `setup_value_multiplier` when opponent has 4+ healthy
- Only boost setup when opponent has ≤2 healthy mons

**Example:** Don't Swords Dance Gliscor turn 3 when they have 6 mons

---

## Phase 2: Advanced Heuristics (Week 2)

### Phazing Intelligence
- Detect phazing moves (Whirlwind, Roar)
- Use vs setup sweepers to reset boosts
- Don't setup vs known phazers

### Contact Punishment
- Rocky Helmet, Iron Barbs, Rough Skin awareness
- Penalty for contact moves vs these

### Status Spreading
- Value Toxic on mons that wall threats
- Thunder Wave for speed control
- Don't status vs Substitute

### Late-Game Setup Detection
- When to pull the trigger on setup sweeps
- Gliscor SD, Gholdengo NP, Blissey CM timing

---

## Phase 3: Ladder Testing & Iteration (Week 3)

### Testing Protocol
1. Run each team for 20 battles on ladder
2. Track ELO performance per team
3. Analyze all losses via replay analyzer
4. Identify most common mistakes
5. Tune heuristics based on data
6. Repeat

### Success Metrics
- **ELO Target:** 1700+
- **Win Rate:** 55%+ vs 1600+ opponents
- **Consistency:** Same team archetype maintains ELO across sessions
- **Speed:** 2-3 battles/hour (faster than human testing)

### Top Player Feedback
- Share replays with top player
- Get feedback on decision quality
- Identify strategic mistakes
- Implement suggestions

---

## Code Changes Required

### Files to Modify

1. **`fp/search/main.py`**
   - Import playstyle config
   - Apply multipliers to scoring functions
   - Switch penalty, recovery value, hazard value, etc.

2. **`fp/run_battle.py`**
   - Load team playstyle at battle start
   - Pass playstyle to MCTS searcher

3. **`bot_monitor.py`**
   - Already handles loss analysis ✅
   - Will auto-detect mistakes with new heuristics

4. **`run.py`**
   - Pass playstyle from command-line args
   - Auto-detect from team name if `--playstyle auto`

---

## Testing Commands

### Test Team 1 (Stall)
```bash
python run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username LEBOTJAMESXD001 \
  --ps-password LeBotPassword2026! \
  --bot-mode search_ladder \
  --pokemon-format gen9ou \
  --team-name fat-team-1-stall \
  --playstyle fat \
  --search-time-ms 2000 \
  --run-count 20 \
  --save-replay on_loss
```

### Test Team 2 (Pivot-Heavy)
```bash
python run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username LEBOTJAMESXD001 \
  --ps-password LeBotPassword2026! \
  --bot-mode search_ladder \
  --pokemon-format gen9ou \
  --team-name fat-team-2-pivot \
  --playstyle fat \
  --search-time-ms 2000 \
  --run-count 20 \
  --save-replay on_loss
```

### Test Team 3 (Dondozo)
```bash
python run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username LEBOTJAMESXD001 \
  --ps-password LeBotPassword2026! \
  --bot-mode search_ladder \
  --pokemon-format gen9ou \
  --team-name fat-team-3-dondozo \
  --playstyle fat \
  --search-time-ms 2000 \
  --run-count 20 \
  --save-replay on_loss
```

---

## Next Steps (Immediate)

1. **Fix switch detection bug** (currently stuck on forced switches)
2. **Integrate playstyle into MCTS** (scoring multipliers)
3. **Test Team 1 on ladder** (10 battles)
4. **Analyze losses** (replay analyzer will catch mistakes)
5. **Iterate based on data**

---

## Questions for Top Player

1. **Feedback mechanism:** How should we share replays for review?
2. **Priority teams:** Should we focus on one team first or test all 3?
3. **ELO threshold:** At what ELO does the bot become useful for testing?
4. **Decision quality:** What types of mistakes are most critical to fix first?

---

**Status:** Infrastructure ready, heuristics need implementation  
**ETA for testing:** This weekend (after heuristic integration)  
**ETA for 1700+:** 2-3 weeks with iteration
