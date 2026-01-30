# Gen9 OU Meta Analysis - Bot Strategic Concepts

**Goal:** Understand human-level strategic concepts that MCTS struggles with

## The Challenge for Bots

MCTS is great at:
- ✅ Damage calculations
- ✅ Type matchups
- ✅ Short-term tactics (2-3 turns ahead)

MCTS struggles with:
- ❌ Long-term strategy (10+ turn plans)
- ❌ Team composition understanding
- ❌ Metagame positioning
- ❌ Momentum/tempo concepts
- ❌ Win condition identification

---

## Gen9 OU Meta Principles (Human Knowledge)

### 1. **The "Fat vs HO" Spectrum**

**Hyper Offense (HO):**
- Setup sweepers (4-5 mons with setup moves)
- Fast tempo, minimal switching
- Screens or hazards to enable sweeps
- Win condition: Overwhelm before opponent sets up

**Fat/Balance:**
- Defensive cores (2-3 walls)
- Switch-heavy play
- Hazard stacking + chip damage
- Win condition: Outlast and wear down

**Bot Issue:** MCTS doesn't understand which playstyle it's playing. It treats all teams the same.

**Fix:** Playstyle config system we just built ✅

---

### 2. **Hazard Wars**

**Human Understanding:**
- Stealth Rock is the most important move in OU
- 50% damage to Charizard, Volcarona, etc.
- Forces switches → chip accumulation
- Setting hazards early = investment for 20+ turn payoff

**Bot Issue:** MCTS evaluates hazards based on immediate value, not long-term chip.

**Fix Needed:**
- Massively boost hazard value in scoring
- Special bonus for getting hazards up turns 1-5
- Track cumulative chip damage from hazards

---

### 3. **Defensive Core Synergy**

**Human Understanding:**
Common cores in current meta:
- **SkarmBliss:** Skarmory (physical) + Blissey (special) = unbreakable wall
- **RegeneratOR:** Slowking-Galar + Corviknight + Amoonguss (passive healing)
- **Unaware cores:** Dondozo + Clodsire (ignore stat boosts)

**Why they work:**
- Cover each other's weaknesses
- Force opponent into bad positions
- Maintain HP through switching

**Bot Issue:** Doesn't understand "switching to maintain defensive backbone."

**Fix Needed:**
- Detect defensive cores
- Reduce switch penalty when maintaining good matchups
- Value HP preservation on walls highly

---

### 4. **Offensive Pressure ("Tempo")**

**Human Understanding:**
- Staying in to attack = maintain pressure
- Switching = lose momentum (opponent gets free setup/damage)
- But bad matchups require switching

**The Balance:**
- Good players minimize switches by predicting
- Stay in on 50/50s if you have offensive pressure
- Only switch when clearly losing matchup

**Bot Issue:** Switches too predictably OR too aggressively.

**Fix Needed:**
- Offensive pressure calculation
- Stay-in bonus when you threaten OHKO/2HKO
- Switch penalty should consider "giving opponent a free turn"

---

### 5. **Setup Timing**

**Human Understanding:**
Setup moves (Swords Dance, Dragon Dance, etc.) are win buttons... **IF** you time them right.

**Good setup:**
- Opponent has 2-3 mons left
- Your checks are gone
- They're forced to switch into you
- They don't have a phazer (Whirlwind/Roar)

**Bad setup:**
- Opponent has 6 mons
- They have a phazer
- They have priority moves
- They outspeed and OHKO you

**Bot Issue:** Sets up whenever move score is good, ignoring game state.

**Fix Needed:**
- Count opponent's healthy mons
- Massive penalty vs revealed phazers
- Only boost setup when ≤3 opponent mons left

---

### 6. **Speed Tiers**

**Human Understanding:**
Speed is binary - you either outspeed or you don't.

Critical speed tiers in Gen9 OU:
- **Base 50 (Dondozo, Ting-Lu):** Slow walls
- **Base 100 (Zapdos, Iron Valiant):** Middle ground
- **Base 130 (Dragapult):** Fast attackers  
- **Base 148 (Zamazenta):** Very fast threats
- **Scarfed mons:** +50% speed (fastest on field)

**What matters:**
- If you outspeed, you move first
- If they outspeed, they move first
- Ties broken by random chance

**Bot Issue:** May not prioritize outspeeding when it's critical (e.g., vs setup sweepers).

**Fix Needed:**
- Speed comparison in scoring
- Massive bonus for outspeeding + OHKOing
- Priority move awareness (Sucker Punch, Aqua Jet always go first)

---

### 7. **Coverage vs STAB**

**Human Understanding:**
- **STAB** (Same Type Attack Bonus) = 1.5x damage
- Coverage moves = Hit super-effective targets

**The Trap:**
- Clicking coverage when STAB does more damage
- Example: Zamazenta vs Gliscor
  - Ice Fang (coverage, 4x super-effective) = ~200 damage
  - Close Combat (STAB, neutral) = ~250 damage
  - Close Combat wins!

**Bot Issue:** May overvalue type advantage, undervalue STAB power.

**Fix Needed:**
- Actual damage calculation > type chart
- Don't click coverage if STAB does more

---

### 8. **The "50/50" Prediction Game**

**Human Understanding:**
High-level play involves predictions:
- "They'll switch, so I'll use setup move"
- "They'll attack, so I'll switch out"
- "They'll predict my switch, so I'll stay in and attack"

**The Meta-Game:**
- Good players predict switches
- Great players predict predictions
- 1800+ ELO = constant mind games

**Bot Issue:** MCTS doesn't predict switches well. It assumes opponent plays optimally.

**Fix (Hard):**
- Model opponent switching patterns
- Track opponent's previous decisions
- Adjust probabilities based on player tendencies
- This requires reinforcement learning or Bayesian inference

**Realistic Fix:**
- Play conservatively (assume opponent plays well)
- Don't make "hard reads" (risky predictions)
- Focus on solid play, not outpredicting

---

### 9. **Item Recognition**

**Human Understanding:**
Items are game-changing:
- **Choice Scarf:** +50% speed, locked into one move
- **Choice Band/Specs:** +50% attack/sp.atk, locked into one move
- **Assault Vest:** +50% sp.def, can't use status moves
- **Leftovers:** 6.25% HP per turn
- **Heavy-Duty Boots:** Immune to hazards

**Critical Inferences:**
- If they used two different moves → not Choice item
- If they used status move → not Assault Vest
- If they switch a lot with hazards up → likely Heavy-Duty Boots

**Bot Issue:** Slow to infer items, doesn't update strategy.

**Fix Needed:**
- Item inference from move usage
- Update threat assessment when item revealed
- Special handling for Choice-locked mons

---

### 10. **Win Conditions**

**Human Understanding:**
Every team has a **win condition** - how it actually wins games.

**Fat team win conditions:**
1. **Hazard chip** - Stealth Rock + Spikes = opponent loses 25-50% HP per switch
2. **Status spreading** - Toxic on walls = they die slowly
3. **PP stalling** - Pressure + Sub/Protect = drain their moves
4. **Late-game setup** - When opponent is weakened, sweep with Gliscor/Gholdengo

**Bot Issue:** Plays turn-by-turn without overall plan.

**Fix Needed:**
- Identify team's win condition at start
- Bias decisions toward win condition
- Track progress (e.g., "hazards up, 3 mons poisoned, opponent has 2 mons left → setup time")

---

## Immediate Improvements for Fat Team

### Priority 1: Hazard Priority
```python
# If no hazards on opponent's side and turn < 10:
hazard_move_score *= 3.0  # CRITICAL to get hazards up
```

### Priority 2: Switch Aggression
```python
# For fat teams:
switch_penalty *= 0.6  # Switch 40% more freely
```

### Priority 3: Setup Patience
```python
# Count opponent's healthy mons
opp_healthy = count_mons_above_60_hp(opponent_team)
if setup_move and opp_healthy >= 4:
    move_score *= 0.3  # Don't setup when they're healthy
```

### Priority 4: Unaware Detection
```python
# vs Dondozo, Clodsire, Quagsire:
if opponent_ability == "unaware":
    if move in setup_moves:
        move_score = 0  # NEVER setup vs Unaware
```

### Priority 5: Regenerator Value
```python
# Slowking-Galar, Corviknight, Toxapex, Amoonguss:
if self_ability == "regenerator":
    switch_penalty *= 0.5  # Switch freely for 33% heal
```

---

## Long-Term Strategic Concepts (Hardest for Bots)

1. **Team matchup understanding** - "My team loses to their Volcarona"
2. **Endgame planning** - "Save Dondozo for their last mon"
3. **Resource management** - "Don't burn Tera early"
4. **Opponent scouting** - "They probably have Earthquake"
5. **Adaptation** - "This player loves hard reads, play safe"

These require:
- Game-to-game learning
- Opponent modeling
- Strategic planning beyond move evaluation

**For 1700+ ELO, we need at least some of these.**

---

## Action Items

### This Week
- [ ] Implement Priority 1-5 heuristics
- [ ] Test on ladder with Team 1
- [ ] Collect loss data + your feedback

### Next Week
- [ ] Add item inference
- [ ] Improve speed tier awareness
- [ ] Win condition tracking

### Week 3
- [ ] Opponent switch prediction (basic)
- [ ] Team matchup understanding
- [ ] Advanced setup timing

---

## Expert Feedback (Ryan)

### 1. Hazard Priority
**Answer:** Not always, but **sometimes vs SR-weak Pokemon or teams with no hazard control** it's worth prioritizing.

**Implementation:**
```python
# Detect SR-weak opponent mons (4x weak: Volcarona, Charizard, etc.)
sr_weak_count = count_stealth_rock_weak_pokemon(opponent_team)
opponent_has_removal = has_defog_or_rapid_spin(opponent_team)

if sr_weak_count >= 2 or not opponent_has_removal:
    hazard_priority_boost = 2.5  # High priority
else:
    hazard_priority_boost = 1.2  # Normal priority
```

### 2. Setup Timing
**Answer:** "All counters should be eliminated" - but **very game-state and matchup dependent.**

**Key Insight:** This is complex and situational. Can't reduce to simple rules. Need case-by-case evaluation.

### 3. Switch Aggression (Fat Teams)
**Answer:** Switch when:
- Taking too much damage
- Can improve positioning without taking too much damage

**Key Insight:** Also matchup-dependent. Not a simple formula.

### 4. Critical Speed Tier
**Answer:** **Base 110 (max speed Ogerpon-Wellspring)**

**Implementation:**
```python
# Key speed benchmarks:
SPEED_BENCHMARKS = {
    110: "Ogerpon-Wellspring (max speed)",
    130: "Dragapult",
    148: "Zamazenta",
    # etc.
}

# Check if we outspeed critical threats
if our_speed > 110:  # Outspeed Ogerpon
    speed_advantage_bonus = 1.5
```

### 5. Coverage Traps
**Answer:** (Question too vague)

**Lesson Learned:** Need to ask about specific scenarios, not general concepts.

---

## Better Questions (Scenario-Based)

Instead of vague strategy questions, focus on **specific game states:**

1. "Turn 1, opponent leads with Gliscor. Should Blissey set Stealth Rock or Calm Mind?"
2. "Gliscor at 40% HP vs healthy Zamazenta. Stay in and Protect, or switch to Skarmory?"
3. "Opponent has 3 mons left (Dondozo, Corviknight, Slowking-G). Safe to Swords Dance with Gliscor?"

**These will come from turn review system - real scenarios from actual battles!** 🪲
